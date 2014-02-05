import unittest
import libvirt
import os
import sys
import subprocess

from testrunner import permutations, expandPermutations
import utils

# Create a 3-D matrix of test permutations.  It does not make sense to
# use relative paths with block devices so we exclude those combos.
liveMergePermutations = [[relPath, baseFmt, block]
                         for relPath in (True, False)
                         for baseFmt in ('raw', 'qcow2')
                         for block in (True, False)
                         if not (block and relPath)
                        ]


def setUpModule():
    utils.patch_subprocess()


@expandPermutations
class TestLiveMerge(unittest.TestCase):
    def tearDown(self):
        utils.cleanup_images()

    @permutations(liveMergePermutations)
    def test_forward_merge_one_to_active(self, relPath, baseFmt, block):
        """
        Forward Merge One to Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge S1 >> S2
        Final image chain:  BASE---S2
        """
        base_file = utils.create_image('BASE', fmt=baseFmt, block=block)
        utils.write_image(base_file, 0, 3072, 1)
        s1_file = utils.create_image('S1', 'BASE', relative=relPath,
                                     backingFmt=baseFmt, block=block)
        utils.write_image(s1_file, 1024, 2048, 2)
        s2_file = utils.create_image('S2', 'S1', relative=relPath,
                                     block=block)
        utils.write_image(s2_file, 2048, 1024, 3)

        dom = utils.create_vm('livemerge-test', 'S2', block=block)
        try:
            dom.blockRebase(s2_file, base_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(utils.wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(utils.verify_image(s2_file, 0, 1024, 1))
        self.assertTrue(utils.verify_image(s2_file, 1024, 1024, 2))
        self.assertTrue(utils.verify_image(s2_file, 2048, 1024, 3))
        self.assertTrue(utils.verify_backing_file(base_file, None))
        self.assertTrue(utils.verify_backing_file(s2_file, 'BASE',
                                                  relative=relPath,
                                                  block=block))
        self.assertTrue(utils.verify_image_format(s1_file, 'qcow2'))

    def test_forward_merge_all_to_active(self):
        """
        Forward Merge All to Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge (BASE + S1) >> S2
        Final image chain:  S2
        """
        utils.create_image('BASE')
        utils.create_image('S1', 'BASE')
        s2_file = utils.create_image('S2', 'S1')

        dom = utils.create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockRebase(s2_file, None, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL
            self.assertTrue(utils.wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(utils.verify_backing_file(s2_file, None))

    def test_backward_merge_from_active(self):
        """
        Backward Merge One from Active Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge S1 << S2
        Final image chain:  BASE---S1
        """
        base_file = utils.create_image('BASE')
        s1_file = utils.create_image('S1', 'BASE')
        s2_file = utils.create_image('S2', 'S1')

        dom = utils.create_vm('livemerge-test', 'S2', block=False)
        try:
            dom.blockCommit(s2_file, s1_file, s2_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(utils.wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(utils.verify_backing_file(base_file, None))
        self.assertTrue(utils.verify_backing_file(s1_file, 'BASE',
                                                  relative=False,
                                                  block=False))

    @permutations(liveMergePermutations)
    def test_backward_merge_from_inactive(self, relPath, baseFmt, block):
        """
        Backward Merge One from Inactive Layer

        Create image chain: BASE---S1---S2
        Start VM
        Merge BASE << S1
        Final image chain:  BASE---S2
        """
        base_file = utils.create_image('BASE', fmt=baseFmt, block=block)
        s1_file = utils.create_image('S1', 'BASE', relative=relPath,
                                     backingFmt=baseFmt, block=block)
        s2_file = utils.create_image('S2', 'S1', relative=relPath,
                                     block=block)
        self.assertTrue(utils.verify_backing_file(base_file, None))
        self.assertTrue(utils.verify_backing_file(s1_file, 'BASE',
                                                  relative=relPath,
                                                  block=block))
        self.assertTrue(utils.verify_backing_file(s2_file, 'S1',
                                                  relative=relPath,
                                                  block=block))

        dom = utils.create_vm('livemerge-test', 'S2', block=block)
        try:
            dom.blockCommit('vda', base_file, s1_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(utils.wait_block_job(dom, s2_file, flags))
        finally:
            dom.destroy()

        self.assertTrue(utils.verify_backing_file(base_file, None))
        self.assertTrue(utils.verify_backing_file(s2_file, 'BASE',
                                                  relative=relPath,
                                                  block=block))
        self.assertTrue(utils.verify_image_format(base_file, baseFmt))

