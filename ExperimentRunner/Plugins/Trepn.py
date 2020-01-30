import csv
import errno
import json
import os
import os.path as op
import time
from collections import OrderedDict
from util import load_json

import lxml.etree as et

from Profiler import Profiler


class Trepn(Profiler):
    DEVICE_PATH = '/sdcard/trepn/'

    def dependencies(self):
        return ['com.quicinc.trepn']

    def __init__(self, config, paths):
        super(Trepn, self).__init__(config, paths)
        self.output_dir = ''
        self.paths = paths
        self.pref_dir = None
        self.remote_pref_dir = op.join(Trepn.DEVICE_PATH, 'saved_preferences/')
        self.data_points = []
        self.build_preferences(config)

    def build_preferences(self, params):
        """Build the XML files to setup Trepn and the data points"""
        current_dir = op.dirname(op.realpath(__file__))
        # lxml is not the most secure parser, it is up to the user for valid configurations
        # https://docs.python.org/2/library/xml.html#xml-vulnerabilities
        self.pref_dir = op.join(self.paths['OUTPUT_DIR'], 'trepn.pref/')
        self.makedirs(self.pref_dir)

        preferences_file = et.parse(op.join(current_dir, 'trepn/preferences.xml'))
        if 'sample_interval' in params:
            for i in preferences_file.getroot().iter('int'):
                if i.get('name') == 'com.quicinc.preferences.general.profiling_interval':
                    i.set('value', str(params['sample_interval']))
        preferences_file.write(op.join(self.pref_dir, 'com.quicinc.trepn_preferences.xml'), encoding='utf-8',
                               xml_declaration=True, standalone=True)

        datapoints_file = et.parse(op.join(current_dir, 'trepn/data_points.xml'))
        dp_root = datapoints_file.getroot()
        data_points_dict = load_json(op.join(current_dir, 'trepn/data_points.json'))
        for dp in params['data_points']:
            dp = str(data_points_dict[dp])
            self.data_points.append(dp)
            dp_root.append(et.Element('int', {'name': dp, 'value': dp}))
        datapoints_file.write(op.join(self.pref_dir, 'com.quicinc.preferences.saved_data_points.xml'), encoding='utf-8',
                              xml_declaration=True, standalone=True)

    def load(self, device):
        device.push(self.pref_dir, self.remote_pref_dir)
        # There is no way to know if the following succeeded
        device.launch_package('com.quicinc.trepn')
        time.sleep(5)  # launch_package returns instantly
        # Trepn needs to be started for this to work
        device.shell('am broadcast -a com.quicinc.trepn.load_preferences '
                     '-e com.quicinc.trepn.load_preferences_file "%s"'
                     % op.join(self.remote_pref_dir, 'trepn.pref'))
        time.sleep(1)  # am broadcast returns instantly
        device.force_stop('com.quicinc.trepn')
        time.sleep(2)  # am force-stop returns instantly
        device.shell('am startservice com.quicinc.trepn/.TrepnService')

    def start_profiling(self, device, **kwargs):
        device.shell('am broadcast -a com.quicinc.trepn.start_profiling')

    def stop_profiling(self, device, **kwargs):
        device.shell('am broadcast -a com.quicinc.trepn.stop_profiling')

    def collect_results(self, device):
        # Gives the latest result
        db = device.shell(r'ls %s | grep "\.db$"' % Trepn.DEVICE_PATH).strip().splitlines()
        newest_db = db[len(db) - 1]
        csv_filename = '%s_%s.csv' % (device.id, op.splitext(newest_db)[0])
        if newest_db:
            device.shell('am broadcast -a com.quicinc.trepn.export_to_csv '
                         '-e com.quicinc.trepn.export_db_input_file "%s" '
                         '-e com.quicinc.trepn.export_csv_output_file "%s"' % (newest_db, csv_filename))
            time.sleep(1)  # adb returns instantly, while the command takes time
            device.pull(op.join(Trepn.DEVICE_PATH, csv_filename), self.output_dir)
            time.sleep(1)  # adb returns instantly, while the command takes time
            # Delete the originals
            device.shell('rm %s' % op.join(Trepn.DEVICE_PATH, newest_db))
            device.shell('rm %s' % op.join(Trepn.DEVICE_PATH, csv_filename))
        self.filter_results(op.join(self.output_dir, csv_filename))

    @staticmethod
    def read_csv(filename):
        result = []
        with open(filename, mode='r') as csv_file:
            csv_reader = csv.reader(csv_file)
            for row in csv_reader:
                result.append(row)
        return result

    def filter_results(self, filename):
        file_content = self.read_csv(filename)[3:]
        split_line = file_content.index(['System Statistics:'])
        data = file_content[:split_line - 2]
        system_statistics = file_content[split_line + 2:]
        system_statistics_dict = {str(statistic[0]): statistic[1] for statistic in system_statistics if
                                  not statistic == []}
        wanted_statistics = [system_statistics_dict[data_point] for data_point in self.data_points]
        filtered_data = self.filter_data(wanted_statistics, data)
        self.write_list_to_file(filename, filtered_data)

    @staticmethod
    def write_list_to_file(filename, rows):
        with open(filename, 'w') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

    def filter_data(self, wanted_statistics, data):
        wanted_columns = self.get_wanted_columns(wanted_statistics, data[0])
        filtered_data = self.filter_columns(wanted_columns, data)
        return filtered_data

    @staticmethod
    def filter_columns(wanted_columns, data):
        remaining_data = []
        for row in data:
            new_row = [row[column] for column in wanted_columns]
            remaining_data.append(new_row)
        return remaining_data

    @staticmethod
    def get_wanted_columns(statistics, header_row):
        wanted_columns = []
        last_time = None
        for statistic in statistics:
            last_time_added = False
            for i in range(len(header_row)):
                header_item = header_row[i].split('[')[0].strip()
                if header_item == 'Time':
                    last_time = i
                if header_item == statistic:
                    if not last_time_added:
                        wanted_columns.append(last_time)
                        last_time_added = True
                    wanted_columns.append(i)
        wanted_columns.sort()
        return wanted_columns

    def unload(self, device):
        device.shell('am stopservice com.quicinc.trepn/.TrepnService')
        device.shell('rm -r %s' % op.join(self.remote_pref_dir, 'trepn.pref'))

    def set_output(self, output_dir):
        self.output_dir = output_dir

    def aggregate_subject(self):
        filename = os.path.join(self.output_dir, 'Aggregated.csv')
        subject_rows = list()
        subject_rows.append(self.aggregate_trepn_subject(self.output_dir))
        self.write_to_file(filename, subject_rows)

    def aggregate_end(self, data_dir, output_file):
        rows = self.aggregate_final(data_dir)
        self.write_to_file(output_file, rows)

    @staticmethod
    def write_to_file(filename, rows):
        with open(filename, 'w') as f:
            writer = csv.DictWriter(f, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def aggregate_trepn_subject(self, logs_dir):
        def add_row(accum, new):
            row = {key: value + float(new[key]) for key, value in accum.items() if key not in ['Component', 'count']}
            count = accum['count'] + 1
            return dict(row, **{'count': count})

        runs = []
        for run_file in [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]:
            with open(os.path.join(logs_dir, run_file), 'rb') as run:
                run_dict = {}
                reader = csv.DictReader(run)
                column_readers = self.split_reader(reader)
                for k, v in column_readers.items():
                    init = dict({k: 0}, **{'count': 0})
                    run_total = reduce(add_row, v, init)
                    if not run_total['count'] == 0:
                        run_dict[k] = run_total[k] / run_total['count']
                runs.append(run_dict)
        init = dict({fn: 0 for fn in runs[0].keys()}, **{'count': 0})
        runs_total = reduce(add_row, runs, init)
        return OrderedDict(
            sorted({k: v / len(runs) for k, v in runs_total.items() if not k == 'count'}.items(), key=lambda x: x[0]))

    @staticmethod
    def split_reader(reader):
        column_dicts = {fn: [] for fn in reader.fieldnames if not fn.split('[')[0].strip() == 'Time'}
        for row in reader:
            for k, v in row.items():
                if not k.split('[')[0].strip() == 'Time' and not v == '':
                    column_dicts[k].append({k: v})
        return column_dicts

    def aggregate_final(self, data_dir):
        rows = []
        for device in self.list_subdir(data_dir):
            row = OrderedDict({'device': device})
            device_dir = os.path.join(data_dir, device)
            for subject in self.list_subdir(device_dir):
                row.update({'subject': subject})
                subject_dir = os.path.join(device_dir, subject)
                if os.path.isdir(os.path.join(subject_dir, 'trepn')):
                    row.update(self.aggregate_trepn_final(os.path.join(subject_dir, 'trepn')))
                    rows.append(row.copy())
                else:
                    for browser in self.list_subdir(subject_dir):
                        row.update({'browser': browser})
                        browser_dir = os.path.join(subject_dir, browser)
                        if os.path.isdir(os.path.join(browser_dir, 'trepn')):
                            row.update(self.aggregate_trepn_final(os.path.join(browser_dir, 'trepn')))
                            rows.append(row.copy())
        return rows

    @staticmethod
    def aggregate_trepn_final(logs_dir):
        for aggregated_file in [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]:
            if aggregated_file == "Aggregated.csv":
                with open(os.path.join(logs_dir, aggregated_file), 'rb') as aggregated:
                    reader = csv.DictReader(aggregated)
                    row_dict = OrderedDict()
                    for row in reader:
                        for f in reader.fieldnames:
                            row_dict.update({f: row[f]})
                    return OrderedDict(row_dict)

    @staticmethod
    def list_subdir(a_dir):
        """List immediate subdirectories of a_dir"""
        # https://stackoverflow.com/a/800201
        return [name for name in os.listdir(a_dir)
                if os.path.isdir(os.path.join(a_dir, name))]

    @staticmethod
    def makedirs(path):
        """Create a directory on path if it does not exist"""
        # https://stackoverflow.com/a/5032238
        try:
            os.makedirs(path)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

class FileNotFoundError(Exception):
    def __init__(self, filename):
        Exception.__init__(self, '[Errno %s] %s: \'%s\'' % (errno.ENOENT, os.strerror(errno.ENOENT), filename))


class FileFormatError(Exception):
    pass
