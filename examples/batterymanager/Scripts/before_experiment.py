# noinspection PyUnusedLocal
"""Default ``before_experiment`` hook (catch-all for non-Metronome configs).

Chained hooks (in order; mirrors ``before_experiment_uninstall_metronome.py``):

  1. **E0.T8 — device-state controls** (``before_experiment_apply_device_state``):
     screen brightness lock, best-effort software charge-disable, discharge-validity
     check, per-device capability cache. May call ``sys.exit(1)`` only when
     ``MASTEREXP_STRICT_DISCHARGE_CHECK=1`` and discharge cannot be verified.

  2. **E0.T10 — BATTERY_STATS auto-grant** (``before_experiment_grant_battery_stats``):
     idempotent ``pm grant`` for the BatteryManager companion.

  3. **2026-06-30 — radios + savers off** (``before_experiment_disable_radios_and_savers``):
     Bluetooth off, Adaptive Charging off, Battery Saver off, Doze off,
     3rd-party-app warn-list. EDATA § 3.3 hardening. Idempotent.

  4. **2026-06-30 — fixture sync** (``before_experiment_sync_fixtures``):
     pushes per-app workload fixtures (happybirthday.md, sample.pdf, etc.)
     to every phone. Fixes Issue 26 (cross-phone fixture parity).
     Idempotent.

This file remains the catch-all default for legacy / non-Metronome experiment
configs that don't have a dedicated per-app uninstall hook. Wiring the chain
here means every experiment that goes through AndroidRunner — regardless of which
``before_experiment`` script is wired in its JSON — gets device-state controls +
USB-supply masking mitigation automatically.

Both this and ``before_experiment_uninstall_metronome.py`` call the same helpers.
The helpers are idempotent so chaining both would be safe; on a Metronome run,
the chain fires from the uninstall hook first and this module is never invoked.

Failure handling: any exception is caught and logged to stderr; AndroidRunner
continues. The harness must never crash inside a hook (the only intentional exit
is strict-mode discharge-check abort, which is a documented opt-in).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _device_state
        _device_state.apply_device_state(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment: device-state controls chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        import before_experiment_grant_battery_stats as _grant
        _grant.grant_battery_stats(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment: BATTERY_STATS auto-grant chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
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
                "before_experiment: disable_radios_and_savers chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        import before_experiment_sync_fixtures as _fixtures
        _fixtures.sync_fixtures(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment: sync_fixtures chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass
