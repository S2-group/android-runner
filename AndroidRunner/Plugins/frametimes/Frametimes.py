import os.path as op
import os
import time
import timeit
import threading
import csv

from AndroidRunner.Plugins.Profiler import Profiler


class ConfigError(Exception):
    pass


class Frametimes(Profiler):
    def __init__(self, config, paths):
        super(Frametimes, self).__init__(config, paths)
        self.output_dir = ''
        self.paths = paths
        self.profile = False
        self.interval = float(self.is_integer(config.get('sample_interval', 0))) / 1000
        self.data = set()

    def get_frame_times(self, device, app):
        result = device.shell(
            'dumpsys gfxinfo {} framestats | sed -n /--PROFILEDATA---/,/--PROFILEDATA---/p'.format(app))

        if 'No process found' in result:
            raise Exception('FrameTimes Profiler: {}'.format(result))

        # Android 14+ added 10 new framestats columns. Old format (14 cols):
        # IntendedVsync at index 1, FrameCompleted at index 13. New format (24 cols):
        # IntendedVsync at index 2, FrameCompleted at index 17. Hard-coding 1 and 13
        # on newer Android picks FrameTimelineVsyncId (an ID, ~7 digits) and SyncQueued
        # (a mid-frame timestamp), producing frame_time values of ~24 trillion ns —
        # nonsense. Parse the header to find the right indices. Patched 2026-06-30.
        lines = result.split('\n')
        iv_idx, fc_idx = self._find_columns(lines)

        filteredResult = filter(
            lambda row: not row.startswith('Flags') and row != '---PROFILEDATA---' and row.strip(),
            (line.strip() for line in lines),
        )

        return map(
            lambda stats: self.extract_frame_start_end(stats.split(','), iv_idx, fc_idx),
            filteredResult,
        )

    def _find_columns(self, lines):
        """Locate IntendedVsync + FrameCompleted column indices from the header.

        Returns (intended_vsync_idx, frame_completed_idx). Falls back to the legacy
        (1, 13) if the header doesn't match — older Android or unrecognised format.
        """
        for line in lines:
            line = line.strip()
            if line.startswith('Flags,') and 'IntendedVsync' in line and 'FrameCompleted' in line:
                cols = [c.strip() for c in line.split(',')]
                try:
                    return cols.index('IntendedVsync'), cols.index('FrameCompleted')
                except ValueError:
                    pass
        # Legacy fallback (Android 13 and earlier)
        return 1, 13

    def extract_frame_start_end(self, frame_times, iv_idx=1, fc_idx=13):
        try:
            return [int(frame_times[iv_idx]), int(frame_times[fc_idx])]
        except (IndexError, ValueError):
            return [0, 0]

    def start_profiling(self, device, **kwargs):
        self.profile = True
        self.data = set()
        app = kwargs.get('app', None)
        self.get_data(device, app)

    def get_data(self, device, app):
        """Runs the profiling methods every self.interval seconds in a separate thread"""
        start = timeit.default_timer()
        for frame in self.get_frame_times(device, app):
            self.data.add(tuple(frame))
        end = timeit.default_timer()
        # timer results could be negative
        interval = max(float(0), self.interval - max(0, end - start))
        if self.profile:
            threading.Timer(interval, self.get_data, args=(device, app)).start()

    def stop_profiling(self, device, **kwargs):
        self.profile = False

    def collect_results(self, device, path=None):
        times_filename = 'frame_times_{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S'))
        delayed_filename = 'delayed_{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S'))
        delayed_count = 0

        with open(op.join(self.output_dir, times_filename), 'w+') as f:
            writer = csv.writer(f)
            writer.writerow(['frame_start', 'frame_end', 'frame_time', 'is_delayed'])
            for row in self.data:
                frame_time = row[1] - row[0]
                # https://developer.android.com/topic/performance/vitals/render
                # TL;DR; A frame is considered as delayed whenever it took more than 16m to render
                if row[1] - row[0] > 16000000:
                    writer.writerow([row[0], row[1], frame_time, True])
                    delayed_count += 1
                else:
                    writer.writerow([row[0], row[1], frame_time, False])
        self.data = set()

        with open(op.join(self.output_dir, delayed_filename), 'w+') as f:
            writer = csv.writer(f)
            writer.writerow(['delayed_frames_count'])
            writer.writerow([delayed_count])

    def set_output(self, output_dir):
        self.output_dir = output_dir

    def dependencies(self):
        return []

    def load(self, device):
        return

    def unload(self, device):
        return

    def aggregate_subject(self):
        self.aggregate_delayed_frames()
        self.aggregate_frame_times()

    def aggregate_delayed_frames(self):
        with open(op.join(self.output_dir, 'all_delayed_frame_counts.csv'), 'w+') as output:
            writer = csv.writer(output)
            writer.writerow(['delayed_frames'])
            for output_file in os.listdir(self.output_dir):
                if output_file.startswith("delayed_"):
                    writer.writerow([int(open(op.join(self.output_dir, output_file)).readlines()[1])])

    def aggregate_frame_times(self):
        with open(op.join(self.output_dir, 'all_frame_times.csv'), 'w+') as output:
            writer = csv.writer(output)
            writer.writerow(['frame_time'])
            for output_file in os.listdir(self.output_dir):
                if output_file.startswith("frame_times_"):
                    for row in open(op.join(self.output_dir, output_file)).readlines()[1:]:
                        writer.writerow([int(row.split(',')[2])])

    def aggregate_end(self, data_dir, output_file):
        return

    def aggregate_final(self, data_dir):
        return

    def is_integer(self, number, minimum=0):
        if not isinstance(number, int):
            raise ConfigError('%s is not an integer' % number)
        if number < minimum:
            raise ConfigError('%s should be equal or larger than %i' % (number, minimum))
        return number
