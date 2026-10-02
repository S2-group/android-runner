import os.path as op
import os
import subprocess
import time
import csv

from AndroidRunner.Plugins.Profiler import Profiler


class ConfigError(Exception):
    pass


class Garbagecollection(Profiler):
    def __init__(self, config, paths):
        super(Garbagecollection, self).__init__(config, paths)
        self.output_dir = ''
        self.logcat_output = ''
        self.profile = False

    def start_profiling(self, device, **kwargs):
        self.profile = True
        self.logcat_output = '{}logcat_{}_{}.txt'.format(self.output_dir, device.id, time.strftime('%Y.%m.%d_%H%M%S'))

    def stop_profiling(self, device, **kwargs):
        self.profile = False

    def collect_results(self, device, path=None):
        # Patched 2026-06-30:
        # Bug 1: /mnt/sdcard/ is blocked by scoped storage on Android 13+ —
        #   logcat -f returns 'Permission denied', the pull silently grabs
        #   nothing, GC count comes out 0. Fix: write to /data/local/tmp/
        #   which is accessible to the adb shell user on every Pixel tested
        #   (P3 Android 12, P6 + P9 Android 16).
        # Bug 2: 'AllocSpace objects,' check no longer matches — modern
        #   Android logs "GC freed 320KB AllocSpace bytes, 0(0B) LOS objects"
        #   ('bytes', not 'objects', for AllocSpace). Simplify to just
        #   'GC freed'.
        # Bug 3: AR's Adb.pull() returns the private `_ADB__output` of the
        #   underlying adb-py library, which is None on newer adb versions
        #   even when the pull succeeds (the success message "1460189 bytes
        #   in 0.5s" goes through stderr and the library's check for "bytes
        #   in" doesn't fire reliably). Don't trust the pull return value —
        #   check the actual local file instead.
        device_path = '/data/local/tmp/ar_gc_logcat.txt'
        device.shell('logcat -f {} -d'.format(device_path))

        # Bug 4 2026-06-30: AR's `device.pull` -> `Adb.pull` -> `pyand.ADB.run_cmd`
        # uses `shlex.split(cmd)` to parse the command. With paths containing
        # spaces (`Master Experiment`, `Pixel 9-W` etc.), the local destination
        # path gets split into multiple arguments and adb silently rejects the
        # pull. The output_dir for every cell in this thesis has at least 2
        # space-containing components, so the AR plugin path NEVER produces a
        # local file. Bypass the pyand wrapper entirely by invoking `adb pull`
        # via subprocess.run with the path as a separate argument (no shell
        # parsing involved).
        try:
            subprocess.run(
                ['adb', '-s', device.id, 'pull', device_path, self.logcat_output],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.SubprocessError as ex:
            self.logger.critical(
                'GC: adb pull failed: %s. expected_local=%s',
                ex, self.logcat_output
            )
            return

        if not op.isfile(self.logcat_output) or os.path.getsize(self.logcat_output) == 0:
            self.logger.critical(
                'GC: local file missing or empty after pull (expected %s) — cannot gather GC calls.',
                self.logcat_output
            )
            return

        device.shell('rm -f {}'.format(device_path))

        collections_filename = 'collections_{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S'))
        total_filename = 'total_{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S'))
        collections_count = 0

        with open(self.logcat_output) as logcat:
            with open(op.join(self.output_dir, collections_filename), 'w+') as output:
                lines = logcat.readlines()
                for i, line in enumerate(lines):
                    if 'GC freed' in line:
                        output.write(line)
                        collections_count += 1
        with open(op.join(self.output_dir, total_filename), 'a') as output:
            writer = csv.writer(output)
            writer.writerow(['garbage_collection_count'])
            writer.writerow([collections_count])
        os.remove(self.logcat_output)

    def set_output(self, output_dir):
        self.output_dir = output_dir

    def dependencies(self):
        return []

    def load(self, device):
        return

    def unload(self, device):
        return

    def aggregate_subject(self):
        # Patched 2026-06-30 -- was writing 'delayed_frames' as the header
        # (copy-paste from the frametimes plugin), making the GC aggregator's
        # output unparseable.
        with open(op.join(self.output_dir, 'all_garbage_collection_counts.csv'), 'w+') as output:
            writer = csv.writer(output)
            writer.writerow(['garbage_collection_count'])
            for output_file in os.listdir(self.output_dir):
                if output_file.startswith("total"):
                    writer.writerow([int(open(op.join(self.output_dir, output_file)).readlines()[1])])

    def aggregate_end(self, data_dir, output_file):
        return

    def aggregate_final(self, data_dir):
        return
