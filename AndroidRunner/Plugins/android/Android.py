import csv
import logging
import os
import os.path as op
import threading
import time
import timeit
from collections import OrderedDict
from functools import reduce

from AndroidRunner import util
from AndroidRunner import Tests
from AndroidRunner.Plugins.Profiler import Profiler


class Android(Profiler):
    def __init__(self, config, paths):
        super(Android, self).__init__(config, paths)
        self.output_dir = ''
        self.paths = paths
        self.profile = False
        available_data_points = ['cpu', 'mem']
        self.interval = float(Tests.is_integer(
            config.get('sample_interval', 0))
        ) / 1000
        self.data_points = config['data_points']
        invalid_data_points = [
            dp for dp in config['data_points'] if dp not in set(available_data_points)]
        if invalid_data_points:
            self.logger.warning(
                'Invalid data points in config: {}'.format(invalid_data_points))
        self.data_points = [dp for dp in config['data_points']
                            if dp in set(available_data_points)]
        self.data = [['datetime'] + self.data_points]
        self.lock = threading.Lock()

    @staticmethod
    def get_cpu_usage(device):
        """Get CPU usage in percentage"""
        # return device.shell('dumpsys cpuinfo | grep TOTAL | cut -d" " -f1').strip()[:-1]
        shell_result = device.shell('dumpsys cpuinfo | grep TOTAL')
        shell_splitted = shell_result.split('%')[0]
        if '.-' in shell_splitted:
            shell_splitted = shell_splitted.replace('.-', '.')
        return shell_splitted
        # return device.shell('dumpsys cpuinfo | grep TOTAL').split('%')[0]

    @staticmethod
    def get_mem_usage(device, app):
        """Get memory usage in KB for app, if app is None system usage is used"""
        if not app:
            # return device.shell('dumpsys meminfo | grep Used | cut -d" " -f5').strip()[1:-1]
            # return device.shell('dumpsys meminfo | grep Used').split()[2].strip()[1:-1].replace(",", ".")
            # https://stackoverflow.com/questions/23175809/str-translate-gives-typeerror-translate-takes-one-argument-2-given-worked-i
            return device.shell('dumpsys meminfo | grep Used').translate(str.maketrans('', '', '(kB,K')).split()[2]
        else:
            # Issue 20 fix (2026-06-21): Android 13+ changed `dumpsys meminfo <app>`
            # output format. `grep TOTAL` matches the "TOTAL PSS:" line cleanly on
            # Android 12 but on 13+ the matching line may have different tokens,
            # OR the grep returns empty entirely. The fallback `dumpsys meminfo <app>`
            # without grep then returned the WHOLE dumpsys output, and `.split()[1]`
            # parsed the literal string "Memory" from the "Applications Memory Usage"
            # header. That string got written into the per-cell CSV as the mem value,
            # and AR's aggregator later crashed with `float('Memory')` ValueError.
            # Fix: tolerate the format variation by trying multiple grep patterns
            # and validating that token[1] is numeric before returning it; on
            # failure return '0' (data point lost but cell completes successfully).
            for pat in ['TOTAL PSS', 'TOTAL', 'Pss Total']:
                result = device.shell(
                    "dumpsys meminfo {} | grep '{}'".format(app, pat))
                if result and result.strip():
                    break
            if not result or not result.strip():
                # Last-resort: extract any line with a numeric Pss-like value.
                raw = device.shell('dumpsys meminfo {}'.format(app))
                if 'No process found' in raw:
                    raise Exception('Android Profiler: {}'.format(raw))
                # Look for a line starting with "TOTAL" followed by digits.
                import re as _re
                m = _re.search(r'TOTAL\s*[A-Za-z:]*\s+(\d+)', raw)
                if m:
                    return m.group(1)
                return '0'
            # Try to find the first numeric token in the matched line.
            tokens = ' '.join(result.strip().split()).split()
            for t in tokens:
                if t.isdigit():
                    return t
            return '0'

    def start_profiling(self, device, **kwargs):
        self.profile = True
        self.data = [['datetime'] + self.data_points]
        app = kwargs.get('app', None)
        self.get_data(device, app)

    def get_data(self, device, app):
        """Runs the profiling methods every self.interval seconds in a separate thread"""
        self.lock.acquire()
        try:
            if not self.profile:
                return
            start = timeit.default_timer()
            try:
                device_time = device.shell('date -u')
                row = [device_time]
                if 'cpu' in self.data_points:
                    row.append(self.get_cpu_usage(device))
                if 'mem' in self.data_points:
                    row.append(self.get_mem_usage(device, app))
                self.data.append(row)
            except Exception:
                # Transient errors (e.g. "No process found for <pkg>" during a brief
                # terminate_app+activate_app recovery in the interaction script) must
                # NOT leak the lock — otherwise stop_profiling() will deadlock and
                # teardown hangs forever. Swallow and let the next Timer retry.
                pass
            end = timeit.default_timer()
            interval = max(float(0), self.interval - max(0, int(end - start)))
        finally:
            self.lock.release()
        if self.profile:
            threading.Timer(interval, self.get_data, args=(device, app)).start()

    def stop_profiling(self, device, **kwargs):
        self.lock.acquire()
        self.profile = False
        self.lock.release()

    def collect_results(self, device):
        filename = '{}_{}.csv'.format(
            device.id, time.strftime('%Y.%m.%d_%H%M%S'))
        with open(op.join(self.output_dir, filename), 'w+') as f:
            writer = csv.writer(f)
            for row in self.data:
                writer.writerow(row)

    def set_output(self, output_dir):
        self.output_dir = output_dir

    def dependencies(self):
        return []

    def load(self, device):
        return

    def unload(self, device):
        return

    def aggregate_subject(self):
        filename = os.path.join(self.output_dir, 'Aggregated.csv')
        subject_rows = list()
        subject_rows.append(self.aggregate_android_subject(self.output_dir))

        util.write_to_file(filename, subject_rows)

    def aggregate_end(self, data_dir, output_file):
        rows = self.aggregate_final(data_dir)

        util.write_to_file(output_file, rows)

    @staticmethod
    def aggregate_android_subject(logs_dir):
        def add_row(accum, new):
            row = {k: v + float(new[k]) for k, v in list(accum.items())
                   if k not in ['Component', 'count']}
            count = accum['count'] + 1
            return dict(row, **{'count': count})

        runs = []
        for run_file in [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]:
            with open(os.path.join(logs_dir, run_file), 'r') as run:
                reader = csv.DictReader(run)
                init = dict(
                    {fn: 0 for fn in reader.fieldnames if fn != 'datetime'}, **{'count': 0})
                run_total = reduce(add_row, reader, init)
                runs.append(
                    {k: v / run_total['count'] for k, v in list(run_total.items()) if k != 'count'})
        if not runs:
            logging.warning(
                "aggregate_android_subject: no per-run rows found in %s; returning empty aggregate",
                logs_dir)
            return OrderedDict()
        runs_total = reduce(
            lambda x, y: {k: v + y[k] for k, v in list(x.items())}, runs)
        return OrderedDict(
            sorted(list({'android_' + k: v / len(runs) for k, v in list(runs_total.items())}.items()), key=lambda x: x[0]))

    def aggregate_final(self, data_dir):
        rows = []
        for device in util.list_subdir(data_dir):
            row = OrderedDict({'device': device})
            device_dir = os.path.join(data_dir, device)
            for subject in util.list_subdir(device_dir):
                row.update({'subject': subject})
                subject_dir = os.path.join(device_dir, subject)
                if os.path.isdir(os.path.join(subject_dir, 'android')):
                    row.update(self.aggregate_android_final(
                        os.path.join(subject_dir, 'android')))
                    rows.append(row.copy())
                else:
                    for browser in util.list_subdir(subject_dir):
                        row.update({'browser': browser})
                        browser_dir = os.path.join(subject_dir, browser)
                        if os.path.isdir(os.path.join(browser_dir, 'android')):
                            row.update(self.aggregate_android_final(
                                os.path.join(browser_dir, 'android')))
                            rows.append(row.copy())
        return rows

    @staticmethod
    def aggregate_android_final(logs_dir):
        for aggregated_file in [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]:
            if aggregated_file == "Aggregated.csv":
                with open(os.path.join(logs_dir, aggregated_file), 'r') as aggregated:
                    reader = csv.DictReader(aggregated)
                    row_dict = OrderedDict()
                    for row in reader:
                        for f in reader.fieldnames:
                            row_dict.update({f: row[f]})
                    return OrderedDict(row_dict)
        logging.getLogger(__name__).warning(
            "Android.aggregate_android_final: no Aggregated.csv in %s; returning empty row", logs_dir)
        return OrderedDict()