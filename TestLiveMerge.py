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

_blockdevs = []

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


def tearDownBlockDevs():
    cmd = ['losetup', '-d']
    cmd.extend(_blockdevs)
    outf = open('/dev/null', 'w')
    try:
        subprocess.check_call(cmd, stdout=outf, stderr=outf)
    finally:
        outf.close()


def setUpModule():
    if not hasattr(subprocess, 'check_output'):
        _patch_subprocess()
    setUpBlockDevs(3)


def tearDownModule():
    tearDownBlockDevs()


def get_image_path(imagename, relative=False, block=False):
    if block:
        pass
    elif relative:
        return "%s.img" % imagename
    else:
        return os.path.join(IMAGEDIR, "%s.img" % imagename)


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
            backingfile = get_image_path(backing, relative)
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


def cleanup_images():
    if os.path.exists(IMAGEDIR):
        shutil.rmtree(IMAGEDIR)


def write_image(name, offset, length, pattern):
    imagefile = get_image_path(name)
    assert os.path.exists(imagefile)
    outf = open('/dev/null', 'w')

    write_cmd = "write -P %i %i %i" % (pattern, offset, length)
    cmd = ['qemu-io', '-c', write_cmd, imagefile]
    try:
        subprocess.check_call(cmd, stdout=outf, stderr=outf)
    finally:
        outf.close()


def verify_image(name, offset, length, pattern):
    imagefile = get_image_path(name)
    assert os.path.exists(imagefile)

    read_cmd = "read -P %i -s 0 -l %i %i %i" % (pattern, length, offset,
                                                length)
    cmd = ['qemu-io', '-c', read_cmd, imagefile]
    output = subprocess.check_output(cmd)
    if 'Pattern verification failed' in output:
        return False
    return True


def libvirt_connect():
    return libvirt.open('qemu:///system')


def wait_block_job(dom, imageName, jobType, timeout=10):
    path = get_image_path(imageName)
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


def verify_backing_file(imageName, baseName, relative=False):
    imagePath = get_image_path(imageName)
    if baseName:
        basePath = get_image_path(baseName, relative)
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


def verify_image_format(imageName, expectedFmt):
    imagePath = get_image_path(imageName)
    cmd = ['qemu-img', 'info', imagePath]
    output = subprocess.check_output(cmd)
    m = re.search('^file format: (\S*)', output, re.M)
    if m:
        return bool(m.group(1) == expectedFmt)
    return False


def create_vm(name, image_name):
    imagefile = get_image_path(image_name)
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
        create_image('BASE', fmt=baseFmt)
        write_image('BASE', 0, 3072, 1)
        create_image('S1', 'BASE', relative=relPath, backingFmt=baseFmt)
        write_image('S1', 1024, 2048, 2)
        create_image('S2', 'S1', relative=relPath)
        write_image('S2', 2048, 1024, 3)

        dom = create_vm('livemerge-test', 'S2')
        try:
            disk = get_image_path('S2')
            base = get_image_path('BASE')
            dom.blockRebase(disk, base, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(wait_block_job(dom, 'S2', flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_image('S2', 0, 1024, 1))
        self.assertTrue(verify_image('S2', 1024, 1024, 2))
        self.assertTrue(verify_image('S2', 2048, 1024, 3))
        self.assertTrue(verify_backing_file('BASE', None))
        self.assertTrue(verify_backing_file('S2', 'BASE',
                                            relative=relPath))
        self.assertTrue(verify_image_format('S1', 'qcow2'))

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
        create_image('S2', 'S1')

        dom = create_vm('livemerge-test', 'S2')
        try:
            disk = get_image_path('S2')
            dom.blockRebase(disk, None, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(wait_block_job(dom, 'S2', flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file('S2', None))

    def test_backward_merge_from_active(self):
        """
        Backward Merge One from Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge S1 << S2
        Final image chain:  BASE---S1
        """
        create_image('BASE')
        create_image('S1', 'BASE')
        create_image('S2', 'S1')

        dom = create_vm('livemerge-test', 'S2')
        try:
            disk = get_image_path('S2')
            base = get_image_path('S1')
            top = disk
            dom.blockCommit(disk, base, top, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(wait_block_job(dom, 'S2', flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file('BASE', None))
        self.assertTrue(verify_backing_file('S1', 'BASE'))

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
        create_image('BASE', fmt=baseFmt)
        create_image('S1', 'BASE', relative=relPath, backingFmt=baseFmt)
        create_image('S2', 'S1', relative=relPath)
        self.assertTrue(verify_backing_file('BASE', None))
        self.assertTrue(verify_backing_file('S1', 'BASE',
                                            relative=relPath))
        self.assertTrue(verify_backing_file('S2', 'S1',
                                            relative=relPath))

        dom = create_vm('livemerge-test', 'S2')
        try:
            disk = get_image_path('S2')
            base = get_image_path('BASE')
            top = get_image_path('S1')
            dom.blockCommit('vda', base, top, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(wait_block_job(dom, 'S2', flags))
        finally:
            dom.destroy()

        self.assertTrue(verify_backing_file('BASE', None))
        self.assertTrue(verify_backing_file('S2', 'BASE',
                                            relative=relPath))
        self.assertTrue(verify_image_format('BASE', baseFmt))

