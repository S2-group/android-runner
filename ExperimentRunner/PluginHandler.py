import logging
import os
from shutil import copyfile

from pluginbase import PluginBase

import paths
from Python2 import Python2
from util import makedirs


class PluginHandler(object):
    def __init__(self, name, params, full_config={}):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.pluginParams = params
        self.name = name
        self.name_lower = name.lower()
        self.moduleName = self.name_lower.capitalize()
        self.subject_aggregated = False
        self.subject_aggregated_default = False
        self.paths = paths.paths_dict()

        self.plugin_base = PluginBase(package='ExperimentRunner.plugins')
        if self.name_lower == 'android' or self.name_lower == 'trepn' or self.name_lower == 'batterystats':
            plugin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Plugins')
            self.plugin_source = self.plugin_base.make_plugin_source(searchpath=[plugin_path])
            self.pluginModule = self.plugin_source.load_plugin(self.moduleName)
            self.currentProfiler = getattr(self.pluginModule, self.moduleName)(params, self.paths, full_config)
            self.name = self.name_lower
        else:
            plugin_path = os.path.join(paths.CONFIG_DIR, 'Plugins')
            if os.path.isdir(plugin_path):
                copyfile(os.path.join(paths.ROOT_DIR, 'ExperimentRunner', 'Plugins', 'Profiler.py'), os.path.join(
                    plugin_path, 'Profiler.py'))
                self.plugin_source = self.plugin_base.make_plugin_source(searchpath=[plugin_path])
                self.pluginModule = self.plugin_source.load_plugin(self.name)
                self.currentProfiler = getattr(self.pluginModule, self.name)(params, self.paths, full_config)
            else:
                raise ImportError
        self.logger.debug('%s: Initialized' % self.name)

    def dependencies(self):
        return self.currentProfiler.dependencies()

    def load(self, device):
        """Load (and start) the profiler process on the device"""
        self.logger.debug('%s: %s: Loading configuration' % (self.moduleName, device))
        self.currentProfiler.load(device)

    def start_profiling(self, device, **kwargs):
        """Start the profiling process"""
        self.logger.debug('%s: %s: Start profiling' % (self.moduleName, device))
        self.currentProfiler.start_profiling(device, **kwargs)

    def stop_profiling(self, device, **kwargs):
        """Stop the profiling process"""
        self.logger.debug('%s: %s: Stop profiling' % (self.moduleName, device))
        self.currentProfiler.stop_profiling(device, **kwargs)

    def collect_results(self, device):
        """Collect the data and clean up extra files on the device"""
        self.logger.debug('%s: %s: Collecting data' % (self.moduleName, device))
        self.currentProfiler.collect_results(device)

    def unload(self, device):
        """Stop the profiler, removing configuration files on device"""
        self.logger.debug('%s: %s: Cleanup' % (self.moduleName, device))
        self.currentProfiler.unload(device)

    def set_output(self):
        # TODO clean up!
        self.paths['OUTPUT_DIR'] = os.path.join(paths.OUTPUT_DIR, self.name)
        makedirs(self.paths['OUTPUT_DIR'])
        self.logger.debug('%s: Setting output: %s' % (self.moduleName, self.paths['OUTPUT_DIR']))
        self.currentProfiler.set_output(self.paths['OUTPUT_DIR'])

    def aggregate_subject(self):
        aggregate_subject_function = self.pluginParams.get('subject_aggregation', 'default')
        aggregate_subject_function_lower = aggregate_subject_function.lower()

        if aggregate_subject_function_lower == 'none':
            return
        elif aggregate_subject_function_lower == 'default':
            self.logger.debug('%s: aggregating subject results')
            self.currentProfiler.aggregate_subject()
            self.subject_aggregated = True
            self.subject_aggregated_default = True
        else:
            aggregate_subject_script = Python2(os.path.join(paths.CONFIG_DIR, aggregate_subject_function))
            self.logger.debug('%s: aggregating subject results')
            self.subject_aggregated = True
            self.subject_aggregated_default = False
            aggregate_subject_script.run(None, self.paths['OUTPUT_DIR'])

    def aggregate_data_end(self, output_dir):
        aggregate_function = self.pluginParams.get('experiment_aggregation', 'default')
        aggregate_function_lower = aggregate_function.lower()

        data_dir = os.path.join(output_dir, 'data')
        result_file = os.path.join(output_dir, 'Aggregated_Results_{}.csv'.format(self.moduleName))

        if aggregate_function_lower == 'none':
            return
        elif aggregate_function_lower == 'default':
            if self.subject_aggregated_default:
                self.logger.debug('%s: aggregating results')
                self.currentProfiler.aggregate_end(data_dir, result_file)
            elif not self.subject_aggregated:
                self.logger.debug('%s: aggregating results')
                self.aggregate_subjects_default(data_dir)
                self.currentProfiler.aggregate_end(data_dir, result_file)
            else:
                self.logger.info("{} profiler: User defined subject aggregation used,"
                                 " default experiment aggregation not possible.".format(self.moduleName))
                return
        else:
            aggregate_script = Python2(os.path.join(paths.CONFIG_DIR, aggregate_function))
            self.logger.debug('%s: aggregating results')
            aggregate_script.run(None, data_dir, result_file)

    def aggregate_subjects_default(self, data_dir):
        for device in self.list_subdir(data_dir):
            device_dir = os.path.join(data_dir, device)
            for subject in self.list_subdir(device_dir):
                subject_dir = os.path.join(device_dir, subject)
                if os.path.isdir(os.path.join(subject_dir, self.name)):
                    self.currentProfiler.set_output(os.path.join(subject_dir, self.name))
                    self.currentProfiler.aggregate_subject()
                else:
                    for browser in self.list_subdir(subject_dir):
                        browser_dir = os.path.join(subject_dir, browser)
                        if os.path.isdir(os.path.join(browser_dir, self.name)):
                            self.currentProfiler.set_output(os.path.join(browser_dir, self.name))
                            self.currentProfiler.aggregate_subject()

    @staticmethod
    def list_subdir(a_dir):
        """List immediate subdirectories of a_dir"""
        # https://stackoverflow.com/a/800201
        return [name for name in os.listdir(a_dir)
                if os.path.isdir(os.path.join(a_dir, name))]
