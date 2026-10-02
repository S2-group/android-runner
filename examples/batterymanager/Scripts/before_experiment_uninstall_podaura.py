# noinspection PyUnusedLocal
"""``before_experiment`` hook for PodAura: chain device-state controls +
BATTERY_STATS auto-grant + uninstall + pre-grant runtime perms.

Same pattern as the other apps' uninstall hooks, plus the extra perms PodAura
needs at runtime (POST_NOTIFICATIONS, READ_MEDIA_* and the special-permission
MANAGE_EXTERNAL_STORAGE via appops). Pre-granting avoids the in-app dialog
flow at S1.
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PACKAGE = "com.skyd.anivu.debug"


def _adb(device, *args):
    """Run an `adb` shell command via the device's serial."""
    serial = getattr(device, "id", None) or getattr(device, "serial", None)
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        subprocess.run(cmd, check=False, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _grant_runtime_perms(device):
    _adb(device, "shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
    _adb(device, "shell", "pm", "grant", PACKAGE, "android.permission.READ_MEDIA_IMAGES")
    _adb(device, "shell", "pm", "grant", PACKAGE, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED")
    _adb(device, "shell", "appops", "set", PACKAGE, "MANAGE_EXTERNAL_STORAGE", "allow")


def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _device_state
        _device_state.apply_device_state(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_podaura: device-state chain failed "
                "(continuing): %s: %s\n" % (type(exc).__name__, exc))
        except Exception:
            pass

    try:
        import before_experiment_grant_battery_stats as _grant
        _grant.grant_battery_stats(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_podaura: BATTERY_STATS grant chain "
                "failed (continuing): %s: %s\n" % (type(exc).__name__, exc))
        except Exception:
            pass

    try:
        import before_experiment_disable_radios_and_savers as _radios
        _radios.disable_radios_and_savers(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall: disable_radios_and_savers chain "
                "failed (continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        import before_experiment_sync_fixtures as _fixtures
        _fixtures.sync_fixtures(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall: sync_fixtures chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        if PACKAGE in device.get_app_list():
            device.logger.info("Uninstalling %s so the next run installs fresh.", PACKAGE)
            device.uninstall(PACKAGE)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_podaura: uninstall step failed "
                "(continuing): %s: %s\n" % (type(exc).__name__, exc))
        except Exception:
            pass
