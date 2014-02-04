import unittest
import libvirt
import os
import sys
import subprocess
import shutil
import re
import time

from testrunner import permutations, expandPermutations

IMAGEDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'tmp')
IMAGESIZE = '1M'

_blockdevs = {}

def _patch_subprocess():
    def check_output(cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        output = p.communicate()[0]
        if p.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, output)
        return output
    subprocess.check_output = check_output


def setUpBlockDevs(nr):
    global _blockdevs
    cmd = ['losetup', '-f']
    for i in xrange(0, nr):
        output = subprocess.check_output(cmd)
        if not output.startswith('/dev/loop'):
            raise ValueError("Unexpected output from losetup: %s" %
                             output)
        dev = output[:-1]  # Strip trailing newline
        dd = ['dd', 'if=/dev/zero', 'of=%s/block%i' % (IMAGEDIR, i),
              'bs=1M', 'count=2']
        outf = open('/dev/null', 'w')
        try:
            subprocess.check_call(dd, stdout=outf, stderr=outf)
        finally:
            outf.close()
        _blockdevs.append(dev)
    print _blockdevs


def setUpModule():
    if not hasattr(subprocess, 'check_output'):
        _patch_subprocess()


def get_image_path(imagename, relative, block):
    if block:
        return _blockdevs[imagename]
    elif relative:
        return "%s.img" % imagename
    else:
        return os.path.join(IMAGEDIR, "%s.img" % imagename)


def create_block_dev(name):
    global _blockdevs
    cmd = ['losetup', '-f']
    output = subprocess.check_output(cmd)
    if not output.startswith('/dev/loop'):
        raise ValueError("Unexpected output from losetup: %s" % output)
        dev = output[:-1]  # Strip trailing newline
        if not os.path.exists(dev):
            raise Exception("Missing loop device.  To fix this please "
                            "run 'mknod -m 0660 %s b 7 %s' and retry." %
                            (dev, dev[9:]))
        fname = "%s/%s.img" % (IMAGEDIR, name)
        dd = ['dd', 'if=/dev/zero', 'of=%s' % fname, 'bs=1M', 'count=2']
        losetup = ['losetup', dev, fname]
        outf = open('/dev/null', 'w')
        try:
            subprocess.check_call(dd, stdout=outf, stderr=outf)
            subprocess.check_call(losetup, stdout=outf, stderr=outf)
        finally:
            outf.close()
        _blockdevs[name] = dev


def create_image(name, backing=None, fmt='qcow2', backingFmt='qcow2',
                 relative=False, block=False):
    if not os.path.exists(IMAGEDIR):
        os.mkdir(IMAGEDIR, 0755)

    cwd = os.getcwd()
    os.chdir(IMAGEDIR)
    outf = open('/dev/null', 'w')

    try:
        if block:
            create_block_dev(name)
        imagefile = get_image_path(name, relative, block)
        cmd = ['qemu-img', 'create', '-f', fmt]
        if backing:
            backingfile = get_image_path(backing, relative, block)
            #if not os.path.exists(backingfile):
            #    raise ValueError("Backing file %s does not exist" %
            #                     backingfile)
            cmd.extend(['-b', backingfile, '-F', backingFmt, imagefile])
        else:
            cmd.extend([imagefile, IMAGESIZE])
        subprocess.check_call(cmd, stdout=outf, stderr=outf)
        os.chmod(imagefile, 0666)
    finally:
        os.chdir(cwd)
        outf.close()

    # Always return the absolute path to the image so it can be
    # passed along to libvirt and other functions which don't deal with
    # relative paths
    return get_image_path(name, False, block)


def cleanup_images():
    loopdevs = _blockdevs.values()
    if loopdevs:
        cmd = ['losetup', '-d']
        cmd.extend(loopdevs)
        outf = open('/dev/null', 'w')
        try:
            subprocess.check_call(cmd, stdout=outf, stderr=outf)
        finally:
            outf.close()
    if os.path.exists(IMAGEDIR):
        shutil.rmtree(IMAGEDIR)


def write_image(imagefile, offset, length, pattern):
    assert os.path.exists(imagefile)
    outf = open('/dev/null', 'w')

    write_cmd = "write -P %i %i %i" % (pattern, offset, length)
    cmd = ['qemu-io', '-c', write_cmd, imagefile]
    try:
        subprocess.check_call(cmd, stdout=outf, stderr=outf)
    finally:
        outf.close()


def verify_image(imagefile, offset, length, pattern):
    assert os.path.exists(imagefile)

    read_cmd = "read -P %i -s 0 -l %i %i %i" % (pattern, length, offset,
                                                length)
    cmd = ['qemu-io', '-c', read_cmd, imagefile]
    output = subprocess.check_output(cmd)
    if 'Pattern verification failed' in output:
        return False
    return True


def verify_backing_file(imagePath, baseName, relative=False, block=False):
    if baseName:
        basePath = get_image_path(baseName, relative, block)
    else:
        basePath = None
    cmd = ['qemu-img', 'info', imagePath]
    output = subprocess.check_output(cmd)
    m = re.search('^backing file: (\S*)', output, re.M)
    if m:
        baseMatch = m.group(1)
    else:
        baseMatch = None
    return bool(basePath == baseMatch)


def verify_image_format(imagePath, expectedFmt):
    cmd = ['qemu-img', 'info', imagePath]
    output = subprocess.check_output(cmd)
    m = re.search('^file format: (\S*)', output, re.M)
    if m:
        return bool(m.group(1) == expectedFmt)
    return False


def libvirt_connect():
    return libvirt.open('qemu:///system')


def wait_block_job(dom, path, jobType, timeout=10):
    i = 0
    while i < timeout:
        info = dom.blockJobInfo(path, 0)
        if not info:
            return True
        assert(info['type'] == jobType)
        if info['cur'] == info['end']:
            return True
        time.sleep(1)
        i = i + 1
    return False


def create_vm(name, image_name, block=False):
    imagefile = get_image_path(image_name, relative=False, block=block)
    xml = '''
    <domain type='kvm'>
      <name>%(name)s</name>
      <memory unit='MiB'>256</memory>
      <vcpu>1</vcpu>
      <os>
        <type arch='x86_64'>hvm</type>
      </os>
      <devices>
        <disk type='file' device='disk'>
          <driver name='qemu' type='qcow2' backing_format='qcow2'/>
          <source file='%(imagefile)s' />
          <target dev='vda' bus='virtio' />
        </disk>
      </devices>
    </domain>
    ''' % {'name': name, 'imagefile': imagefile}

    conn = libvirt_connect()
    return conn.createXML(xml, 0)


@expandPermutations
class TestLiveMerge(unittest.TestCase):
    def tearDown(self):
        cleanup_images()

    @permutations([[relPath, baseFmt]
                   for relPath in (True, False)
                   for baseFmt in ('raw', 'qcow2')])
    def test_forward_merge_one_to_active(self, relPath, baseFmt):
        """
        Forward Merge One to Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge S1 >> S2
        Final image chain:  BASE---S2
        """
        base_file = create_image('BASE', fmt=baseFmt)
        write_image(base_file, 0, 3072, 1)
        s1_file = create_image('S1', 'BASE', relative=relPath,
                               backingFmt=baseFmt)
        write_image(s1_file, 1024, 2048, 2)
        s2_file = create_image('S2', 'S1', relative=relPath)
        write_image(s2_file, 2048, 1024, 3)

        dom = create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockRebase(s2_file, base_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_image(s2_file, 0, 1024, 1))
        self.assertTrue(verify_image(s2_file, 1024, 1024, 2))
        self.assertTrue(verify_image(s2_file, 2048, 1024, 3))
        self.assertTrue(verify_backing_file(base_file, None))
        self.assertTrue(verify_backing_file(s2_file, 'BASE',
                                            relative=relPath,
                                            block=False))
        self.assertTrue(verify_image_format(s1_file, 'qcow2'))

    def test_forward_merge_all_to_active(self):
        """
        Forward Merge All to Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge (BASE + S1) >> S2
        Final image chain:  S2
        """
        create_image('BASE')
        create_image('S1', 'BASE')
        s2_file = create_image('S2', 'S1')

        dom = create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockRebase(s2_file, None, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file(s2_file, None))

    def test_backward_merge_from_active(self):
        """
        Backward Merge One from Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge S1 << S2
        Final image chain:  BASE---S1
        """
        base_file = create_image('BASE')
        s1_file = create_image('S1', 'BASE')
        s2_file = create_image('S2', 'S1')

        dom = create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockCommit(s2_file, s1_file, s2_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file(base_file, None))
        self.assertTrue(verify_backing_file(s1_file, 'BASE',
                                            relative=False,
                                            block=False))

    @permutations([[relPath, baseFmt]
                   for relPath in (True, False)
                   for baseFmt in ('raw', 'qcow2')])
    def test_backward_merge_from_inactive(self, relPath, baseFmt):
        """
        Backward Merge One from Inactive Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge BASE << S1
        Final image chain:  BASE---S2
        """
        base_file = create_image('BASE', fmt=baseFmt)
        s1_file = create_image('S1', 'BASE', relative=relPath,
                               backingFmt=baseFmt)
        s2_file = create_image('S2', 'S1', relative=relPath)
        self.assertTrue(verify_backing_file(base_file, None))
        self.assertTrue(verify_backing_file(s1_file, 'BASE',
                                            relative=relPath,
                                            block=False))
        self.assertTrue(verify_backing_file(s2_file, 'S1',
                                            relative=relPath,
                                            block=False))

        dom = create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockCommit('vda', base_file, s1_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file(base_file, None))
        self.assertTrue(verify_backing_file(s2_file, 'BASE',
                                            relative=relPath,
                                            block=False))
        self.assertTrue(verify_image_format(base_file, baseFmt))

