import logging
from itertools import chain

from PluginHandler import PluginHandler


class Profilers(object):

    def __init__(self, config, full_config={}):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.profilers = []
        self.loaded_devices = []
        for name, params in config.items():
            try:
                self.profilers.append(PluginHandler(name, params, full_config))
            except ImportError:
                self.logger.error('Cannot import %s' % name)
                raise

    def dependencies(self):
        # https://stackoverflow.com/a/953097
        return list(chain.from_iterable([p.dependencies() for p in self.profilers]))

    def load(self, device):
        self.logger.info('Loading')
        if device.name not in self.loaded_devices:
            for p in self.profilers:
                p.load(device)
            self.loaded_devices.append(device.name)

    def start_profiling(self, device, **kwargs):
        self.logger.info('Start profiling')
        for p in self.profilers:
            p.start_profiling(device, **kwargs)

    def stop_profiling(self, device, **kwargs):
        self.logger.info('Stop profiling')
        for p in self.profilers:
            p.stop_profiling(device, **kwargs)

    def collect_results(self, device):
        self.logger.info('Collecting results')
        for p in self.profilers:
            p.collect_results(device)

    def unload(self, device):
        self.logger.info('Unloading')
        for p in self.profilers:
            p.unload(device)

    def set_output(self):
        self.logger.info('Setting output')
        for p in self.profilers:
            p.set_output()

    def aggregate_subject(self):
        self.logger.info('Start subject aggregation')
        for p in self.profilers:
            p.aggregate_subject()

    def aggregate_end(self, output_dir):
        self.logger.info('Start final aggregation')
        for p in self.profilers:
            p.aggregate_data_end(output_dir)
