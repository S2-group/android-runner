import os.path as op
import time
import multiprocessing as mp
from . import Tests
import paths
from .BrowserFactory import BrowserFactory
from .Experiment import Experiment
from .util import makedirs, slugify_dir
from AndroidRunner.PrematureStoppableRun import PrematureStoppableRun 


class WebExperiment(Experiment):
    def __init__(self, config, progress, restart):
        super(WebExperiment, self).__init__(config, progress, restart)
        self.browsers = [BrowserFactory.get_browser(b)() for b in config.get('browsers', ['chrome'])]
        Tests.check_dependencies(self.devices, [b.package_name for b in self.browsers])
        self.duration = Tests.is_integer(config.get('duration', 0)) / 1000
        self.config = config
    
    # Browsers have version specific formatting, allows re-creation if needed
    def regenerate_browsers(self, device):
        # Regenerate browsers based on device version
        self.browsers = [BrowserFactory.get_browser(b)(device) for b in self.config.get('browsers', ['chrome'])]

    def run(self, device, path, run, browser_name):
        browser = None
        for browserItem in self.browsers:
            if browser_name in browserItem.to_string():
                browser = browserItem
        kwargs = {
            'browser': browser,
            'app': browser.package_name
        }
        self.before_run(device, path, run, **kwargs)
        self.after_launch(device, path, run, **kwargs)

        self.usb_handler.disable_usb()
        self.start_profiling(device, path, run, **kwargs)

        if self.run_stopping_condition_config:
            self.queue = mp.Queue()
            premature_stoppable_run = PrematureStoppableRun(self.run_stopping_condition_config, self.queue, self.interaction, device, path, run, **kwargs)
            premature_stoppable_run.run()
        else:
            self.interaction(device, path, run, **kwargs)

        self.stop_profiling(device, path, run, **kwargs)
        self.usb_handler.enable_usb()

        self.before_close(device, path, run, **kwargs)
        self.after_run(device, path, run, **kwargs)

    def last_run_subject(self, current_run):
        if self.progress.subject_finished(current_run['device'], current_run['path'], current_run['browser']):
            self.after_last_run(self.devices.get_device(current_run['device']), current_run['path'])
            self.aggregate_subject()

    def prepare_output_dir(self, current_run):
        paths.OUTPUT_DIR = op.join(paths.BASE_OUTPUT_DIR, 'data/', current_run['device'],
                                   slugify_dir(current_run['path']),
                                   current_run['browser'])
        makedirs(paths.OUTPUT_DIR)

    def before_run_subject(self, device, path, *args, **kwargs):
        super(WebExperiment, self).before_run_subject(device, path, *args, **kwargs)
        self.logger.info('URL: %s' % path)

    def before_run(self, device, path, run, *args, **kwargs):
        super(WebExperiment, self).before_run(device, path, run, *args, **kwargs)
        device.shell('logcat -c')
        kwargs['browser'].start(device)
        time.sleep(5)

    def before_experiment(self, device, *args, **kwargs):
        super().before_experiment(self, device, *args, **kwargs)
        self.regenerate_browsers(device)

    def interaction(self, device, path, run, *args, **kwargs):
        kwargs['browser'].load_url(device, path)
        time.sleep(5)
        super(WebExperiment, self).interaction(device, path, run, *args, **kwargs)
        # TODO: Fix web experiments running longer than self.duration
        time.sleep(self.duration)

    def after_run(self, device, path, run, *args, **kwargs):
        kwargs['browser'].stop(device, self.clear_cache)
        time.sleep(3)
        super(WebExperiment, self).after_run(device, path, run, *args, **kwargs)

    def after_last_run(self, device, path, *args, **kwargs):
        super(WebExperiment, self).after_last_run(device, path, *args, **kwargs)

    def cleanup(self, device):
        super(WebExperiment, self).cleanup(device)
        for browser in self.browsers:
            browser.stop(device, clear_data=True)
