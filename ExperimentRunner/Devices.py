import os.path as op

# This is basically a singleton
# https://stackoverflow.com/a/10936915
import Adb
from Device import Device
from paths import ROOT_DIR
from util import ConfigError, load_json


class Devices:
    def __init__(self, devices, adb_path='adb', devices_spec=None):
        if devices_spec is None:
            devices_spec = op.join(ROOT_DIR, 'devices.json')

        Adb.setup(adb_path)
        mapping_file = load_json(devices_spec)
        self._device_map = {n: mapping_file.get(n, None) for n in devices}
        for name, device_id in self._device_map.items():
            if not device_id:
                raise ConfigError(name)
        self.devices = [Device(name, device_id, devices[name]) for name, device_id in self._device_map.items()]

    def __iter__(self):
        return iter(self.devices)

    def get_device(self, name):
        for device in self.devices:
            if device.name == name:
                return device

    def names(self):
        return self._device_map.keys()

    def ids(self):
        return self._device_map.values()

    def get_id(self, name):
        return self._device_map[name]

    def get_name(self, device_id):
        for k, v in self._device_map.items():
            if v == device_id:
                return k
