import os.path as op
import time

from . import Tests
from .Experiment import Experiment
from .util import ConfigError


class NativeExperiment(Experiment):
    def __init__(self, config, progress, restart):
        self.package = None
        self.duration = Tests.is_integer(config.get('duration', 0)) / 1000
        self.autostart_subject = config.get('autostart_subject', True)
        self.experiment_args = config.get('experiment_args', [0]) # Just a single argument, if none are specified
        super(NativeExperiment, self).__init__(config, progress, restart)
        # If True, the interaction script blocks for the full ``duration`` window itself (e.g. sysfs loop,
        # or Appium subprocess hold — see interaction_appium_metronome.py). Skip the extra sleep below so
        # the profiled window is not doubled.
        self.interaction_covers_duration = bool(config.get('interaction_covers_duration', False))
        self.pre_installed_apps = config.get('apps', [])
        # When installing from ``paths``, the runner otherwise derives the package name from the APK
        # filename (splitext basename). Obfuscated or packed builds often use unrelated filenames;
        # set ``application_id`` to the manifest packageName (e.g. com.bobek.metronome).
        self.application_id = config.get('application_id')
        for apk in config.get('paths', []):
            if not op.isfile(apk):
                raise ConfigError('File %s not found' % apk)

    def cleanup(self, device):
        super(NativeExperiment, self).cleanup(device)
        if self.package in device.get_app_list() and self.package not in self.pre_installed_apps:
            device.uninstall(self.package)

    def before_experiment(self, device, *args, **kwargs):
        super(NativeExperiment, self).before_experiment(device)

    def before_run_subject(self, device, path, *args, **kwargs):
        super(NativeExperiment, self).before_run_subject(device, path)
        if path in self.pre_installed_apps:
            self.package = path
        else:
            filename = op.basename(path)
            self.logger.info('APK: %s' % filename)
            pkg = self.application_id or op.splitext(filename)[0]
            if pkg not in device.get_app_list():
                device.install(path)
            self.package = pkg

    def get_run_count(self):
        return self.repetitions * len(self.experiment_args)
    
    def before_run(self, device, path, run, *args, **kwargs):
        super(NativeExperiment, self).before_run(device, path, run, *args, **kwargs)
        if self.autostart_subject:
            device.configure_settings_device(self.package, enable=True)
            device.launch_package(self.package)
        time.sleep(1)
        self.after_launch(device, path, run)

    def start_profiling(self, device, path, run, *args, **kwargs):
        self.profilers.start_profiling(device, app=self.package)

    def interaction(self, device, path, run, *args, **kwargs):
        super(NativeExperiment, self).interaction(device, path, run, *args, **kwargs)
        if not self.interaction_covers_duration:
            time.sleep(self.duration)

    def after_run(self, device, path, run, *args, **kwargs):
        self.before_close(device, path, run)
        device.force_stop(self.package)
        if self.clear_cache == True:
            device.clear_app_data(self.package)
        device.configure_settings_device(self.package, enable=False)
        time.sleep(3)
        super(NativeExperiment, self).after_run(device, path, run)

    def after_last_run(self, device, path, *args, **kwargs):
        super(NativeExperiment, self).after_last_run(device, path)
        if self.package in device.get_app_list() and self.package not in self.pre_installed_apps:
            device.uninstall(self.package)
        self.package = None

    def after_experiment(self, device, *args, **kwargs):
        super(NativeExperiment, self).after_experiment(device)
