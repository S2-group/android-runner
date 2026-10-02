# noinspection PyUnusedLocal
"""``before_experiment`` hook: chain device-state controls + BATTERY_STATS auto-grant + Repertoire uninstall.

The original responsibility of this hook (uninstall the previous Repertoire
install so the next install is a clean one from the experiment's APK path) is
preserved unchanged. Mirrors the structure of
``before_experiment_uninstall_metronome.py``.

Chained hooks (in order):

  1. **E0.T8 — device-state controls** (``before_experiment_apply_device_state``):
     screen brightness lock, best-effort software charge-disable, discharge-validity
     check, per-device capability cache. May call ``sys.exit(1)`` if
     ``MASTEREXP_STRICT_DISCHARGE_CHECK=1`` and discharge cannot be verified —
     this is intentional and only happens in the strict mode used for the eventual
     thesis batch. Default lenient mode logs a warning and continues; the per-row
     ``notes`` column will carry ``energy_invalid_usb_supplying`` for runs whose
     discharge could not be verified.

  2. **E0.T10 — BATTERY_STATS auto-grant** (``before_experiment_grant_battery_stats``):
     idempotent ``pm grant`` for the BatteryManager companion. Required for the
     unprivileged-API USB-supply masking mitigation (noise sources § 1).

  3. Original Repertoire uninstall: removes any prior install so the experiment's
     declared APK path becomes the install of record.

Order rationale: physical-world state first (brightness, charge), then permission
state (BATTERY_STATS), then subject state (uninstall). Each step is wrapped so a
failure in one does NOT skip the others. Every failure path logs to stderr and
continues; the harness must never crash inside a hook (the only intentional exit
is the strict-mode discharge-check abort, which is a documented opt-in).

NOTE: this hook does NOT push the markdown song fixture to ``/sdcard/Download/``
— that is a one-time per-device setup step the user performs before the first
smoke run. See ``appium_android_tests/repertoire/CALIBRATION.md`` for the
``adb push`` recipe.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PACKAGE = "klalumiere.repertoire"


def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _device_state
        _device_state.apply_device_state(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_repertoire: device-state controls chain "
                "failed (continuing with grant + uninstall): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    try:
        import before_experiment_grant_battery_stats as _grant
        _grant.grant_battery_stats(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_repertoire: BATTERY_STATS auto-grant chain "
                "failed (continuing with uninstall): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
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
        if PACKAGE not in device.get_app_list():
            device.logger.info(
                "%s not installed; APK will be installed from experiment paths.",
                PACKAGE,
            )
            return
        device.logger.info(
            "Uninstalling %s so the device only receives the APK from this run's paths (fresh install).",
            PACKAGE,
        )
        device.uninstall(PACKAGE)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_repertoire: uninstall step failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass
