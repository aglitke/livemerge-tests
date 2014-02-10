# Test proceedure:
# - Create a new Fedora 20 VM in (BASE.img)
# - Create the desired Volume chain
# - Start Monitoring all volume chain images
#   - Once per second, collect the highest offset written
# - Start VM
# - Wait for active layer to grow by 100M
# - Execute live merge operation and wait for completion
#   - Limit bandwidth in order to exacerbate convergence
# - Notify monitoring threads to stop and wait
# - Print image size information
#
# Test scenarios:
# - BlockRebase BASE >> S1
# - BlockCommit BASE << S1

import threading
import time
import unittest

import utils

touch_script = '''
#!/bin/bash
seek=0
while true; do
    sleep 1
    dd if=/dev/zero of=/blob conv=notrunc bs=100M count=1 seek=$seek
    seek=$((seek + 1))
done
'''

class ImageWatcher(threading.Thread):
    def __init__(self, fname, statList, stopEvent):
        threading.Thread.__init__(self)
        self.fname = fname
        self.statList = statList
        self.stopEvent = stopEvent

    def work(self):
        data = (time.time(), utils.get_image_end_offset(self.fname))
        self.statsList.append(data)

    def run(self):
        while not self.stopEvent.is_set():
            self.work()
            time.sleep(1)


class TestVolumeGrowth(unittest.TestCase):
    def tearDown(self):
        pass #utils.cleanup_images()

    def _print_results(self, test, stats):
        print "%s - Results\n\n" % test
        for label, data in stats.items():
            start = data[0][0]
            print "%4s:" % label
            times, values = zip(*data)
            t_fmt = ["%4i" % int(t - start) for t in times].join()
            v_fmt = ["%4i" % v for v in values].join()
            print "%4s:%s" % (label, t_fmt)
            print "     %s" % v_fmt


    def test_commit(self):
        print "Creating VM image"
        base_file = utils.get_image_path('BASE', False, False)
        utils.build_vm('BASE', touch_script, '10G')
        s1_file = utils.create_image('S1', 'BASE', size='10G')

        # Monitor the image sizes
        stats = {'BASE': [], 'S1': []}
        stopEvent = threading.Event()
        base_watcher = ImageWatcher(base_file, stats['BASE'], stopEvent)
        base_watcher.start()
        s1_watcher = ImageWatcher(s1_file, stats['S1'], stopEvent)
        s1_watcher.start()

        # Run the test
        print "Starting VM"
        dom = utils.create_vm('livemerge-test', 'S1')
        # TODO: Start a livemerge

        try:
            dom.blockCommit(s1_file, base_file, s1_file, 0, 0)
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT
            self.assertTrue(utils.wait_block_job(dom, s1_file, flags))
        finally:
            dom.destroy()


        ## Wait until image has grown enough
        #print "Sampling"
        #while True:
            #end = utils.get_image_end_offset(s1_file) / 1024 / 1024
            #print "S1 is using %i MB" % end
            #if end >= 5 * 1024:
                #break
            #time.sleep(5)

        # Stop the test
        print "Cleaning up"
        stopEvent.set()
        base_watcher.join()
        s1_watcher.join()

        # Print results
        self._print_results(stats)

    def runTest(self):
        pass

if __name__ == '__main__':
    t = TestVolumeGrowth()
    t.test_commit()
