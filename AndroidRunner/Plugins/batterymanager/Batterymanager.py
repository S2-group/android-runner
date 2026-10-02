import csv
import numpy as np
import os.path as op
import os
import pandas as pd
import time
import re

from AndroidRunner.Plugins.Profiler import Profiler


class Batterymanager(Profiler):

    ANDROID_VERSION_11_API_LEVEL_30 = 30
    BATTERYMANAGER_DEVICE_OUTPUT_FILE = '/storage/emulated/0/Documents/BatteryManager.csv'
    AVAILABLE_DATA_POINTS = ['ACTION_CHARGING', 'ACTION_DISCHARGING',
                             'BATTERY_HEALTH_COLD', 'BATTERY_HEALTH_DEAD', 'BATTERY_HEALTH_GOOD',
                             'BATTERY_HEALTH_OVERHEAT',
                             'BATTERY_HEALTH_OVER_VOLTAGE', 'BATTERY_HEALTH_UNKNOWN',
                             'BATTERY_HEALTH_UNSPECIFIED_FAILURE',
                             'BATTERY_PLUGGED_AC', 'BATTERY_PLUGGED_DOCK', 'BATTERY_PLUGGED_USB',
                             'BATTERY_PLUGGED_WIRELESS',
                             'BATTERY_PROPERTY_CAPACITY', 'BATTERY_PROPERTY_CHARGE_COUNTER',
                             'BATTERY_PROPERTY_CURRENT_AVERAGE',
                             'BATTERY_PROPERTY_CURRENT_NOW', 'BATTERY_PROPERTY_ENERGY_COUNTER',
                             'BATTERY_PROPERTY_STATUS',
                             'BATTERY_STATUS_CHARGING', 'BATTERY_STATUS_DISCHARGING', 'BATTERY_STATUS_FULL',
                             'BATTERY_STATUS_NOT_CHARGING', 'BATTERY_STATUS_UNKNOWN',
                             'EXTRA_BATTERY_LOW', 'EXTRA_HEALTH', 'EXTRA_ICON_SMALL', 'EXTRA_LEVEL', 'EXTRA_PLUGGED',
                             'EXTRA_PRESENT',
                             'EXTRA_SCALE', 'EXTRA_STATUS', 'EXTRA_TECHNOLOGY', 'EXTRA_TEMPERATURE', 'EXTRA_VOLTAGE']

    AVAILABLE_PERSISTENCY_STRATEGIES = ['csv', 'adb_log']

    def __init__(self, config, paths):
        super(Batterymanager, self).__init__(config, paths)
        self.output_dir = ''
        self.paths = paths
        self.profile = False

        self.sampling_rate = config.get('sample_interval', 1000)  # default: every second

        self.data_points = self.validate_config('data_points',
                                                config['data_points'],
                                                Batterymanager.AVAILABLE_DATA_POINTS)

        self.persistency_strategy = self.validate_config('persistency_strategy',
                                                         config['persistency_strategy'],
                                                         Batterymanager.AVAILABLE_PERSISTENCY_STRATEGIES)

    def validate_config(self, field, raw_data_points, available_data_points):
        invalid_data_points = [
            dp for dp in raw_data_points if dp not in set(available_data_points)]
        if invalid_data_points:
            self.logger.warning(
                'Invalid {} in config: {}'.format(field, invalid_data_points))
        return [dp for dp in raw_data_points
                if dp in available_data_points]

    # Check if the selected data points are valid
    def start_profiling(self, device, **kwargs):
        device.shell(self.build_intent(True))

    def stop_profiling(self, device, **kwargs):
        device.shell(self.build_intent(False))

    def build_intent(self, is_start):
        if is_start:
            intent_data_fields = ','.join(self.data_points)
            intent_to_csv = 'true' if 'csv' in self.persistency_strategy else 'false'
            intent = f'am start-foreground-service -n "com.example.batterymanager_utility/com.example' \
                     f'.batterymanager_utility.DataCollectionService" --ei sampleRate {self.sampling_rate} --es ' \
                     f'"dataFields" "{intent_data_fields}" --ez toCSV {intent_to_csv}'
        else:
            intent = f'am stopservice com.example.batterymanager_utility/com.example.batterymanager_utility' \
                     f'.DataCollectionService'

        return intent

    def collect_results(self, device):
        # sleep for 5 seconds to make sure the service has stopped
        time.sleep(5)
        if 'csv' in self.persistency_strategy:
            batterymanager_csv_file = op.join(self.output_dir,
                                              '{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S')))
            device.pull('{}'.format(self.BATTERYMANAGER_DEVICE_OUTPUT_FILE), batterymanager_csv_file)
            device.shell('rm -f {}'.format(self.BATTERYMANAGER_DEVICE_OUTPUT_FILE))

        if 'adb_log' in self.persistency_strategy:
            logcat_file = op.join(self.output_dir,
                                  'logcat_{}_{}.txt'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S')))
            self.pull_logcat(device, logcat_file)

            header, rows = self.get_logcat(device)
            self.write_logcat_csv(device, header, rows)

    @staticmethod
    def pull_logcat(device, logcat_file):
        """
        From Android 11 (API level 30) the path /mnt/sdcard cannot be accessed via ADB
        as you don't have permissions to access this path. However, we can access /sdcard.
        """
        device_api_version = int(device.shell("getprop ro.build.version.sdk"))

        if device_api_version >= Batterymanager.ANDROID_VERSION_11_API_LEVEL_30:
            logcat_output_file_device_dir_path = "/sdcard"
        else:
            logcat_output_file_device_dir_path = "/mnt/sdcard"

        device.shell(f"logcat -f {logcat_output_file_device_dir_path}/logcat.txt -d")
        device.pull(f"{logcat_output_file_device_dir_path}/logcat.txt", logcat_file)
        device.shell(f"rm -f {logcat_output_file_device_dir_path}/logcat.txt")

    @staticmethod
    def get_logcat(device):
        header_pattern = 'BatteryMgr:DataCollectionService: onStartCommand: rawFields => '
        data_pattern = 'BatteryMgr:DataCollectionService: stats => '

        raw_header = device.logcat_regex(header_pattern)
        raw_rows = device.logcat_regex(data_pattern)

        return raw_header, raw_rows

    @staticmethod
    def preprocess_logcat(header, rows):
        header = header.split('=> ')[1]
        header = header.split('\n')[0]
        header = header.split(',')

        # FOR OLDER DEVICES remove all non-letter characters except _
        header = [re.sub(r'[^a-zA-Z_]', '', h) for h in header]

        rows = rows.split('\n')
        # FOR OLDER DEVICES remove rows containing "
        rows = [row for row in rows if '"' not in row]
        rows = [row.split('=> ')[1].split(',') for row in rows]

        rows.sort(key=lambda x: x[0])
        return header, rows

    def write_logcat_csv(self, device, header, rows):
        header, rows = Batterymanager.preprocess_logcat(header, rows)
        logcat_csv_file = op.join(self.output_dir,
                                  'logcat_{}_{}.csv'.format(device.id, time.strftime('%Y.%m.%d_%H%M%S')))

        with open(logcat_csv_file, 'w') as lc_csv_file:
            csv_writer = csv.writer(lc_csv_file)
            csv_writer.writerow(header)
            for row in rows:
                csv_writer.writerow(row)

    def unload(self, device):
        return

    def dependencies(self):
        return ['com.example.batterymanager_utility']

    def load(self, device):
        return

    def set_output(self, output_dir):
        self.output_dir = output_dir

    def aggregate_subject(self):
        return

    @staticmethod
    def list_subdir(a_dir):
        """List immediate subdirectories of a_dir"""
        # https://stackoverflow.com/a/800201
        return [name for name in os.listdir(a_dir)
                if os.path.isdir(os.path.join(a_dir, name))]

    @staticmethod
    def preprocess_values(df):
        df['Timestamp'] = df['Timestamp'] - df['Timestamp'][0]
        # conversion from microseconds to seconds
        df['Timestamp'] = df['Timestamp'] / 1000
        return df

    @staticmethod
    def calculate_power(df):
        df['power'] = (abs(df['BATTERY_PROPERTY_CURRENT_NOW']) / 1000 / 1000) * (df['EXTRA_VOLTAGE'] / 1000)
        return df

    @staticmethod
    def trapezoid_method(df):
        # NumPy 2.0+ renamed trapz to trapezoid; keep both for venvs on 1.x and 2.x
        y, x = df['power'].values, df['Timestamp'].values
        trapezoid = getattr(np, "trapezoid", None) or getattr(np, "trapz", None)
        if trapezoid is None:
            raise RuntimeError("NumPy trapezoidal integration API not found")
        return trapezoid(y, x)

    @staticmethod
    def aggregate_batterymanager_runs(logs_dir):
        runs = pd.DataFrame()
        run_number = 0
        for run_file in [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]:
            f_name = os.path.join(logs_dir, run_file)
            if not f_name.endswith(".csv"):
                continue
            run_df = pd.read_csv(f_name)

            stats = {}
            if 'BATTERY_PROPERTY_CURRENT_NOW' in run_df.columns and 'EXTRA_VOLTAGE' in run_df.columns:
                run_df = Batterymanager.preprocess_values(run_df)
                run_df = Batterymanager.calculate_power(run_df)
                avg_power = run_df['power'].mean()
                stats.update({'Avg power (W)': avg_power})
                stats.update({'Energy simple (J)': avg_power * run_df['Timestamp'].max()})
                stats.update({'Energy trapz (J)': Batterymanager.trapezoid_method(run_df)})
            stats.update(run_df.mean().to_dict())
            stats.update({'run': run_number})
            run_number += 1

            runs = pd.concat([runs, pd.DataFrame(stats, index=[0])], ignore_index=True)

        runs = runs.drop(columns=['Timestamp', 'power'], errors='ignore')
        return runs

    @staticmethod
    def aggregate(data_dir):
        df = pd.DataFrame()
        for device in Batterymanager.list_subdir(data_dir):
            device_dir = os.path.join(data_dir, device)
            for subject in Batterymanager.list_subdir(device_dir):
                subject_dir = os.path.join(device_dir, subject)
                if os.path.isdir(os.path.join(subject_dir, 'batterymanager')):
                    runs_df = Batterymanager.aggregate_batterymanager_runs(os.path.join(subject_dir, 'batterymanager'))
                    runs_df['subject'] = subject
                    runs_df['device'] = device
                    df = pd.concat([df, runs_df], ignore_index=True)
        return df[df.columns[::-1]]

    def aggregate_end(self, data_dir, output_file):
        print(('Output file: {}'.format(output_file)))
        rows = self.aggregate(data_dir)
        rows.to_csv(output_file, index=False)
