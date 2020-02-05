import logging
import multiprocessing as mp
import os.path as op
import signal

import Tests
from util import FileNotFoundError


class ScriptError(Exception):
    pass


class Script(object):
    def __init__(self, path, timeout=0, logcat_regex=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.path = path
        if not op.isfile(path):
            raise FileNotFoundError(path)
        self.filename = op.basename(path)
        self.timeout = float(Tests.is_integer(timeout)) / 1000
        self.logcat_event = logcat_regex
        if logcat_regex is not None:
            self.logcat_event = Tests.is_string(logcat_regex)

    def execute_script(self, device, *args, **kwargs):
        """The method that is extended to execute the script"""
        self.logger.info(self.filename)

    def mp_run(self, queue, device, *args, **kwargs):
        """The multiprocessing wrapper of execute_script()"""
        try:
            output = self.execute_script(device, *args, **kwargs)
            self.logger.debug('%s returned %s' % (self.filename, output))
        except Exception, e:
            import traceback
            queue.put((e, traceback.format_exc()))
        queue.put('script')

    @staticmethod
    def mp_logcat_regex(queue, device, regex):
        """The multiprocessing wrapper of Device.logcat_regex()"""
        # https://stackoverflow.com/a/21936682
        # pyadb uses subprocess.communicate(), therefore it blocks
        device.logcat_regex(regex)
        queue.put('logcat')

    def run(self, device, *args, **kwargs):
        """Execute the script with respect to the termination conditions"""
        # https://stackoverflow.com/a/6286343
        with script_timeout(seconds=self.timeout):
            processes = []
            try:
                queue = mp.Queue()
                processes.append(mp.Process(target=self.mp_run, args=(queue, device,) + args, kwargs=kwargs))
                if self.logcat_event is not None and device is not None:
                    processes.append(mp.Process(target=self.mp_logcat_regex, args=(queue, device, self.logcat_event)))
                for p in processes:
                    p.start()
                for p in processes:
                    p.join(self.timeout)

                result = queue.get()
                if isinstance(result, tuple):
                    name = result[0].__class__.__name__
                    message = str(result[0])
                    trace = result[1]
                    log_message = '%s in %s: %s\n%s' % (name, self.filename, message, trace)
                    # self.logger.error(log_message)
                    raise ScriptError(log_message)
            except TimeoutError:
                self.logger.debug('Interaction function timeout (%sms)' % self.timeout)
                result = 'timeout'
            finally:
                for p in processes:
                    p.terminate()
            return result


class TimeoutError(Exception):
    pass


# https://stackoverflow.com/a/22348885
class script_timeout:
    def __init__(self, seconds):
        self.seconds = float(seconds)

    def handle_timeout(self, signum, frame):
        raise TimeoutError()

    def __enter__(self):
        if self.seconds != 0:
            signal.signal(signal.SIGALRM, self.handle_timeout)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def __exit__(self, type, value, traceback):
        if self.seconds != 0:
            signal.alarm(0)
