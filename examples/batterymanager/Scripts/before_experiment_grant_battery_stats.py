"""``before_experiment`` helper: grant ``BATTERY_STATS`` to the BatteryManager companion APK.

Validated 2026-05-08 cross-permission control on Pixel 3 (Android 12) and Pixel 9
(Android 15): granting ``com.example.batterymanager_utility`` the
``android.permission.BATTERY_STATS`` runtime permission unmasks the real fuel-gauge
reading on USB-charged devices. Without the grant, the BatteryManager
``BATTERY_PROPERTY_CURRENT_NOW`` API returns the USB-masked value (charging IC supply
minus device draw); with the grant it returns the underlying battery-side current
measurement. Pixel 3 baseline went from 0.011 W (USB-masked) to 0.82 W (real) → 75×
discharge unmasking. See ``docs/MEASUREMENT_NOISE_SOURCES.md`` § 1 for the full
cross-permission table.

This module is a sibling helper to ``before_experiment_uninstall_metronome.py`` and
is invoked from there via the chain-on-existing-hook pattern (same pattern E0.T4b
introduced in ``before_run.py`` for ``_lib_apk_meta``). Call ``grant_battery_stats(device)``
before the per-app uninstall step.

Contract:
    - **Idempotent.** ``pm grant`` is a no-op when the permission is already granted.
    - **Warn-not-fail.** Every code path is wrapped in try/except; any failure logs
      to stderr and returns ``False`` instead of raising into AndroidRunner.
    - **Verified.** Reads back ``dumpsys package`` after the grant to confirm
      ``granted=true``. The verification result is logged so the
      ``after_experiment`` matrix updater can later tag rows where verification
      failed (handled separately by E0.T8 ``device_state.json``).

Out of scope (per E0.T10 task spec):
    - Do NOT install the BatteryManager companion APK from this hook.
    - Do NOT auto-revoke BATTERY_STATS at the end of the experiment.
    - Do NOT touch sysfs.
"""

from __future__ import annotations

import sys

BATTERYMANAGER_PACKAGE = "com.example.batterymanager_utility"
PERMISSION = "android.permission.BATTERY_STATS"


def _log_stdout(msg: str) -> None:
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _verify_grant(device) -> bool:
    """Return True iff ``dumpsys package`` reports ``BATTERY_STATS: granted=true``.

    Best-effort: any exception or missing output returns False. The caller treats
    False as "warn but continue" — the experiment runs anyway and the resulting
    rows will simply have a USB-masked energy reading (which the existing noise-
    sources doc § 1 catalogues as `RESOLVED-software` only when granted).
    """
    try:
        out = device.shell("dumpsys package %s" % BATTERYMANAGER_PACKAGE) or ""
    except Exception as ex:
        _log_stderr(
            "before_experiment_grant_battery_stats: verify failed reading dumpsys: %s: %s"
            % (type(ex).__name__, ex)
        )
        return False
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if PERMISSION not in line:
            continue
        if "granted=true" in line:
            return True
        if "granted=false" in line:
            return False
    return False


def grant_battery_stats(device) -> bool:
    """Grant ``BATTERY_STATS`` to the BatteryManager companion APK on ``device``.

    Returns ``True`` if the post-grant verification reports ``granted=true``,
    ``False`` otherwise. Never raises.

    The function logs:
        - Success → stdout: ``before_experiment_grant_battery_stats: granted ... on <serial>``
        - Already-granted → stdout: ``before_experiment_grant_battery_stats: already granted on <serial>``
        - Verification failure → stderr: ``before_experiment_grant_battery_stats: grant failed for <serial>: <reason>``
        - Package missing → stderr: ``before_experiment_grant_battery_stats: package <pkg> not installed on <serial>``
    """
    serial = "unknown-serial"
    try:
        serial = getattr(device, "id", None) or getattr(device, "name", None) or "unknown-serial"
    except Exception:
        pass

    try:
        installed = False
        try:
            apps = device.get_app_list() or []
            installed = BATTERYMANAGER_PACKAGE in apps
        except Exception as ex:
            _log_stderr(
                "before_experiment_grant_battery_stats: get_app_list failed on %s "
                "(continuing with grant attempt anyway): %s: %s"
                % (serial, type(ex).__name__, ex)
            )
            installed = True
        if not installed:
            _log_stderr(
                "before_experiment_grant_battery_stats: package %s not installed on %s "
                "(install the BatteryManager companion APK during device setup); "
                "skipping grant — energy reading will be USB-masked"
                % (BATTERYMANAGER_PACKAGE, serial)
            )
            return False

        already = _verify_grant(device)
        if already:
            _log_stdout(
                "before_experiment_grant_battery_stats: already granted on %s "
                "(no-op; this is the expected path on a re-run)" % serial
            )
            return True

        try:
            device.shell("pm grant %s %s" % (BATTERYMANAGER_PACKAGE, PERMISSION))
        except Exception as ex:
            _log_stderr(
                "before_experiment_grant_battery_stats: pm grant raised on %s: %s: %s"
                % (serial, type(ex).__name__, ex)
            )

        if _verify_grant(device):
            _log_stdout(
                "before_experiment_grant_battery_stats: granted %s to %s on %s "
                "(USB-supply masking now mitigated for this session)"
                % (PERMISSION, BATTERYMANAGER_PACKAGE, serial)
            )
            return True

        _log_stderr(
            "before_experiment_grant_battery_stats: grant failed for %s: "
            "post-grant dumpsys did NOT report granted=true. Energy reading on "
            "this run will be USB-masked. Manual fallback: "
            "adb -s %s shell pm grant %s %s"
            % (serial, serial, BATTERYMANAGER_PACKAGE, PERMISSION)
        )
        return False
    except Exception as ex:
        _log_stderr(
            "before_experiment_grant_battery_stats: unexpected exception on %s "
            "(swallowed; experiment continues): %s: %s"
            % (serial, type(ex).__name__, ex)
        )
        return False
