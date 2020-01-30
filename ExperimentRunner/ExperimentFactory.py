import logging
import os.path as op
import shutil

import paths
import util
from Experiment import Experiment
from NativeExperiment import NativeExperiment
from Progress import Progress
from WebExperiment import WebExperiment
from tests.PluginTests import PluginTests

logger = logging.getLogger('ExperimentFactory')


class ExperimentFactory(object):
    def __init__(self):
        pass

    @staticmethod
    def from_json(path, progress, args={}):
        """Returns an Experiment object from a JSON configuration"""
        logger.info(path)
        shutil.copy(path, op.join(paths.OUTPUT_DIR, 'config.json'))
        config = util.load_json(path)

        if "test" in args and args["test"]:
            config['duration'] = 1000
            config['time_between_run'] = 1000
            config['replications'] = 1
            print("Test mode enabled, duration and time between run changed to one second and replications to 1.")

        experiment_type = config['type']
        if experiment_type == 'plugintest':
            return PluginTests(config)
        if progress is None:
            progress = Progress(config_file=path, config=config, load_progress=False)
            restart = False
        else:
            restart = True
        if experiment_type == 'native':
            return NativeExperiment(config, progress, restart)
        elif experiment_type == 'web':
            return WebExperiment(config, progress, restart)
        else:
            return Experiment(config, progress, restart)
