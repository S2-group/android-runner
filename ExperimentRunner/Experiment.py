import logging
import os.path as op
import time
from os import remove, rmdir, walk
from threading import Thread

import Tests
import paths
from Devices import Devices
from Profilers import Profilers
from Scripts import Scripts
from util import ConfigError, makedirs, slugify_dir


# noinspection PyUnusedLocal
class Experiment(object):
    def __init__(self, config, progress, restart):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.progress = progress
        self.basedir = None
        self.random = config.get('randomization', False)
        if 'devices' not in config:
            raise ConfigError('"device" is required in the configuration')
        adb_path = config.get('adb_path', 'adb')
        self.devices = Devices(config['devices'], adb_path=adb_path, devices_spec=config.get('devices_spec'))
        self.replications = Tests.is_integer(config.get('replications', 1))
        self.paths = config.get('paths', [])
        self.profilers = Profilers(config.get('profilers', {}))
        monkeyrunner_path = config.get('monkeyrunner_path', 'monkeyrunner')
        self.scripts = Scripts(config.get('scripts', {}), monkeyrunner_path=monkeyrunner_path)
        self.time_between_run = Tests.is_integer(config.get('time_between_run', 0))
        Tests.check_dependencies(self.devices, self.profilers.dependencies())
        self.output_root = paths.OUTPUT_DIR
        self.result_file_structure = None
        if restart:
            for device in self.devices:
                self.prepare_device(device, restart=True)

    def prepare_device(self, device, restart=False):
        """Prepare the device for experiment"""
        self.logger.info('Device: %s' % device)
        self.profilers.load(device)
        device.unplug(restart)

    def cleanup(self, device):
        """Cleans up the changes on the devices"""
        device.plug()
        self.profilers.stop_profiling(device)
        self.profilers.unload(device)

    def get_progress_xml_file(self):
        return self.progress.progress_xml_file

    def update_progress(self):
        self.progress.write_progress_to_file()
        result_data_path = op.join(paths.BASE_OUTPUT_DIR, 'data')
        self.result_file_structure = self.walk_to_list(walk(result_data_path))

    def start(self):
        try:
            result_data_path = op.join(paths.BASE_OUTPUT_DIR, 'data')
            self.result_file_structure = self.walk_to_list(walk(result_data_path))
            while not self.progress.experiment_finished_check():
                current_run = self.get_experiment()
                self.run_experiment(current_run)
                self.save_progress()
        except Exception, e:
            import traceback
            print(traceback.format_exc())
            self.logger.error('%s: %s' % (e.__class__.__name__, e.message))
            self.finish_experiment(True, False)
            raise e
        except KeyboardInterrupt:
            self.finish_experiment(False, True)
            raise KeyboardInterrupt
        else:
            self.finish_experiment(False, False)

    def finish_experiment(self, error, interrupted):
        self.check_result_files(self.result_file_structure)
        for device in self.devices:
            try:
                self.cleanup(device)
            except Exception:
                continue
        if not error and not interrupted:
            self.aggregate_end()

    def run_experiment(self, current_run):
        self.prepare_run(current_run)
        self.run_run(current_run)
        self.finish_run(current_run)

    def prepare_run(self, current_run):
        self.prepare_output_dir(current_run)
        self.first_run_device(current_run)
        self.before_every_run_subject(current_run)

    def run_run(self, current_run):
        if 'browser' in current_run:
            self.run(self.devices.get_device(current_run['device']), current_run['path'],
                     int(current_run['runCount']), current_run['browser'])
        else:
            self.run(self.devices.get_device(current_run['device']), current_run['path'],
                     int(current_run['runCount']), None)

    def finish_run(self, current_run):
        self.progress.run_finished(current_run['runId'])
        self.last_run_subject(current_run)
        self.last_run_device(current_run)

    def save_progress(self):
        a = Thread(target=self.update_progress)
        a.start()
        a.join()

    def check_result_files(self, correct_file_list):
        result_data_path = op.join(paths.BASE_OUTPUT_DIR, 'data')
        current_file_structure = walk(result_data_path)
        current_file_list = self.walk_to_list(current_file_structure)
        for path in current_file_list:
            if path not in correct_file_list:
                if op.isfile(path):
                    remove(path)
                else:
                    rmdir(path)

    @staticmethod
    def walk_to_list(walk_result):
        walk_list = list()
        for (path, dirs, files) in walk_result:
            for dr in dirs:
                walk_list.append(op.join(path, dr))
            for fl in files:
                walk_list.append(op.join(path, fl))
        walk_list.reverse()
        return walk_list

    def get_experiment(self):
        if self.random:
            return self.progress.get_random_run()
        else:
            return self.progress.get_next_run()

    def first_run_device(self, current_run):
        device = self.devices.get_device(current_run['device'])
        if self.progress.device_first(current_run['device']):
            self.prepare_device(device)
            self.before_experiment(device)

    def before_every_run_subject(self, current_run):
        self.before_run_subject(self.devices.get_device(current_run['device']), current_run['path'])

    def last_run_device(self, current_run):
        if self.progress.device_finished(current_run['device']):
            self.after_experiment(self.devices.get_device(current_run['device']))

    def last_run_subject(self, current_run):
        if self.progress.subject_finished(current_run['device'], current_run['path']):
            self.after_last_run(self.devices.get_device(current_run['device']), current_run['path'])
            self.aggregate_subject()

    def prepare_output_dir(self, current_run):
        paths.OUTPUT_DIR = op.join(paths.BASE_OUTPUT_DIR, 'data/', current_run['device'],
                                   slugify_dir(current_run['path']))
        makedirs(paths.OUTPUT_DIR)

    def run(self, device, path, run, dummy):
        self.before_run(device, path, run)
        self.start_profiling(device, path, run)
        self.interaction(device, path, run)
        self.stop_profiling(device, path, run)
        self.after_run(device, path, run)

    def before_experiment(self, device, *args, **kwargs):
        """Hook executed before the first run of a device in current experiment"""
        self.scripts.run('before_experiment', device, *args, **kwargs)

    def before_run_subject(self, device, path, *args, **kwargs):
        """Hook executed before the first run for a subject"""
        pass

    def before_run(self, device, path, run, *args, **kwargs):
        """Hook executed before a run"""
        self.profilers.set_output()
        self.logger.info('Run %s/%s of subject "%s" on %s' % (run, self.replications, path, device.name))
        device.shell('logcat -c')
        self.logger.info('Logcat cleared')
        self.scripts.run('before_run', device, *args, **kwargs)

    def after_launch(self, device, path, run, *args, **kwargs):
        self.scripts.run('after_launch', device, device.id, device.current_activity())

    def start_profiling(self, device, path, run, *args, **kwargs):
        self.profilers.start_profiling(device)

    def interaction(self, device, path, run, *args, **kwargs):
        """Interactions on the device to be profiled"""
        self.scripts.run('interaction', device, *args, **kwargs)

    def stop_profiling(self, device, path, run, *args, **kwargs):
        self.profilers.stop_profiling(device)

    def before_close(self, device, path, run, *args, **kwargs):
        self.scripts.run('before_close', device, device.id, device.current_activity())

    def after_run(self, device, path, run, *args, **kwargs):
        """Hook executed after a run"""
        self.scripts.run('after_run', device, *args, **kwargs)
        self.profilers.collect_results(device)
        self.logger.debug('Sleeping for %s milliseconds' % self.time_between_run)
        time.sleep(self.time_between_run / 1000.0)

    def after_last_run(self, device, path, *args, **kwargs):
        """Hook executed after the last run of a subject"""
        pass

    def after_experiment(self, device, *args, **kwargs):
        """Hook executed after the last run for device of experiment"""
        self.logger.info('Experiment completed, start cleanup')
        self.scripts.run('after_experiment', device, *args, **kwargs)

    def aggregate_subject(self):
        self.profilers.aggregate_subject()

    def aggregate_end(self):
        self.profilers.aggregate_end(self.output_root)
