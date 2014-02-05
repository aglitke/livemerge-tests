import os
import subprocess
import shutil
import re
import libvirt
import time

IMAGEDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'tmp')
IMAGESIZE = '100M'

_blockdevs = {}


def patch_subprocess():
    def check_output(cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        output = p.communicate()[0]
        if p.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, output)
        return output
    if not hasattr(subprocess, 'check_output'):
        subprocess.check_output = check_output


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
    dd = ['dd', 'if=/dev/zero', 'of=%s' % fname, 'bs=%s' % IMAGESIZE, 'count=1']
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
    global _blockdevs
    loopdevs = _blockdevs.values()
    #subprocess.check_call(['losetup', '-l'])
    if loopdevs:
        cmd = ['losetup', '-d']
        cmd.extend(loopdevs)
        subprocess.check_call(cmd)
    _blockdevs = {}
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
    diskType, srcAttr = (('file', 'file'), ('block', 'dev'))[block]
    srcAttr
    xml = '''
    <domain type='kvm'>
      <name>%(name)s</name>
      <memory unit='MiB'>256</memory>
      <vcpu>1</vcpu>
      <os>
        <type arch='x86_64'>hvm</type>
      </os>
      <devices>
        <disk type='%(diskType)s' device='disk'>
          <driver name='qemu' type='qcow2' backing_format='qcow2'/>
          <source %(srcAttr)s='%(imagefile)s' />
          <target dev='vda' bus='virtio' />
        </disk>
      </devices>
    </domain>
    ''' % {'name': name, 'imagefile': imagefile, 'diskType': diskType,
           'srcAttr': srcAttr}

    conn = libvirt_connect()
    return conn.createXML(xml, 0)
