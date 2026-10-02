# noinspection PyUnusedLocal
"""``before_experiment`` hook: chain device-state controls + BATTERY_STATS auto-grant + Documenter uninstall.

The original responsibility of this hook (uninstall the previous Documenter install
so the next install is a clean one from the experiment's APK path) is preserved
unchanged.

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

  3. Original Documenter uninstall: removes any prior install so the experiment's
     declared APK path becomes the install of record.

Order rationale: physical-world state first (brightness, charge), then permission
state (BATTERY_STATS), then subject state (uninstall). Each step is wrapped so a
failure in one does NOT skip the others. Every failure path logs to stderr and
continues; the harness must never crash inside a hook (the only intentional exit
is the strict-mode discharge-check abort, which is a documented opt-in).

PACKAGE = ``com.viliussutkus89.documenter.debug`` because the packed APK installed
on Pixel 9 is built from the debug build type which sets
``applicationIdSuffix = ".debug"`` (``app/build.gradle.kts:79-80``). The manifest
package on-device therefore ends in ``.debug`` — using the unsuffixed package
would silently no-op the uninstall and the next install would overlay the
previous one (cross-variant comparisons would be invalid; see
``CONVENTIONS.md`` § 10).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PACKAGE = "com.viliussutkus89.documenter.debug"


def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _device_state
        _device_state.apply_device_state(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_documenter: device-state controls chain "
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
                "before_experiment_uninstall_documenter: BATTERY_STATS auto-grant chain "
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
                "%s not installed; packed APK will be installed from experiment paths.",
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
                "before_experiment_uninstall_documenter: uninstall step failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass
