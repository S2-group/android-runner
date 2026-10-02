"""``before_experiment`` helper: apply device-state controls + discharge-validity check.

Sibling helper to ``before_experiment_grant_battery_stats.py``; invoked from
``before_experiment_uninstall_metronome.py`` (and the catch-all
``before_experiment.py``) via the chain-on-existing-hook pattern.

What this hook does on every experiment start:

  1. **Brightness lock** (noise source § 2): force adaptive brightness OFF
     and pin ``screen_brightness=128`` so day-vs-night ambient drift cannot
     leak ~200 mW of differential into the energy reading.

  2. **Best-effort software charge-disable** (noise source § 1, § 5):
     run the standard ``dumpsys battery unplug`` + ``set ac/usb/wireless 0``
     + ``set status 3`` sequence. On Pixel 3 / Pixel 9 this is known to be
     accepted by the Android API but ignored by the charging IC; we still
     run it so a per-device cache file documents "we tried" and so devices
     where the API *does* work get the right behaviour for free.

  3. **Discharge-validity ground-truth** (noise source § 1b): wait 5 s then
     read ``/sys/class/power_supply/battery/current_now``. Negative =
     verified discharge (battery is the source); non-negative = the charge
     IC is still supplying / dominating, the energy reading on this run
     will be USB-masked.

  4. **Per-device capability cache**: write a small JSON to
     ``Scripts/.device_state_capabilities/<serial>.json`` recording
     whether software charge-disable actually worked on this device. The
     matrix updater can later cite this as the explanation for why a row
     is tagged ``energy_invalid_usb_supplying``.

Strict-mode behaviour (``MASTEREXP_STRICT_DISCHARGE_CHECK=1``):
    Default lenient mode (env var unset) → log a warning and continue.
    Strict mode (env var = ``"1"``) → write a sentinel file and call
    ``sys.exit(1)``. Use strict mode for the eventual thesis batch where
    a USB-supplied row would silently corrupt the comparison.

Out of scope (per E0.T8):
    - Do NOT automate airplane mode.
    - Do NOT modify the BatteryManager plugin.
    - Do NOT install / configure ``hub-ctrl`` (that is Epic 1.6).
"""

from __future__ import annotations

import os
import os.path as op
import sys
from typing import Any, Dict, Optional

_HERE = op.dirname(op.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


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


def _resolve_experiment_root() -> Optional[str]:
    """Best-effort: where to drop the strict-mode sentinel file.

    ``paths.BASE_OUTPUT_DIR`` (top-level run dir) is the most useful target
    because the operator inspects it after a failed experiment. Falls back
    to ``OUTPUT_DIR`` (per-subject) and finally to the Scripts directory.
    """
    try:
        import paths as ar_paths  # type: ignore
    except Exception:
        return None
    base = getattr(ar_paths, "BASE_OUTPUT_DIR", None)
    if base and op.isdir(base):
        return base
    out = getattr(ar_paths, "OUTPUT_DIR", None)
    if out and op.isdir(out):
        return out
    return None


def _write_strict_failure_sentinel(snapshot: Dict[str, Any]) -> Optional[str]:
    """In strict mode, drop a JSON sentinel describing the discharge failure."""
    try:
        import json
        target_dir = _resolve_experiment_root() or _HERE
        path = op.join(target_dir, "device_state_strict_check_failed.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True, default=str)
            f.write("\n")
        os.replace(tmp, path)
        return path
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: failed to write strict sentinel: %s: %s"
            % (type(ex).__name__, ex)
        )
        return None


def apply_device_state(device) -> Dict[str, Any]:
    """Apply brightness + charge-disable + verify discharge; cache capabilities.

    Returns the assembled snapshot dict (same schema as ``device_state.json``)
    for callers that want to log or assert on it. The function is independent
    of the per-run hook (which builds its own fresh snapshot) — this one runs
    once per experiment and is the place where state-changing actions happen.

    Raises ``SystemExit(1)`` only when:
      - ``MASTEREXP_STRICT_DISCHARGE_CHECK=1`` is set in the environment, AND
      - the post-settle ``current_now`` reading does not classify as
        ``verified_discharge``.

    In every other case the function returns normally — failures are logged
    to stderr and surface as ``"unknown"`` / ``None`` fields in the snapshot.
    """
    import _lib_device_state as ds

    serial = ds.get_device_serial(device)
    _log_stdout(
        "before_experiment_apply_device_state: starting on %s "
        "(strict_mode=%s)" % (serial, "ON" if ds.strict_mode_enabled() else "OFF")
    )

    brightness_result: Optional[Dict[str, Any]] = None
    try:
        brightness_result = ds.apply_brightness_lock(device)
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: brightness lock raised on %s "
            "(continuing): %s: %s" % (serial, type(ex).__name__, ex)
        )

    # Disable Play Protect / Package Verifier so unsigned debug APKs (S2-group
    # Baseline + CpuFactorialTest, Bangcle-packed stubs, etc.) don't trigger
    # the "Unsafe app blocked" dialog or get silently uninstalled mid-experiment.
    # Discovered 2026-06-02 when first 3-phone parallel smoke FAILED on Pixel 3
    # + Pixel 6 (Android 12 / 13) because Play Protect uninstalled the S2
    # Baseline APK after AR's install step but before AR's am-start. Pixel 9
    # (Android 16) did not block. These four settings are user-mode (no root),
    # persistent across reboots, and revert when re-enabled via Play Store
    # Settings → Play Protect → "Scan apps with Play Protect" ON.
    try:
        for setting in (
            "package_verifier_user_consent -1",
            "verifier_verify_adb_installs 0",
            "package_verifier_enable 0",
            "upload_apk_enable 0",
        ):
            device.shell("settings put global %s" % setting)
        _log_stdout(
            "before_experiment_apply_device_state: Play Protect / package "
            "verifier disabled on %s (user-mode settings; persistent until "
            "user re-enables in Play Store settings)" % serial
        )
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: Play Protect disable raised "
            "on %s (continuing — risk of mid-experiment 'Unsafe app blocked' "
            "dialog): %s: %s" % (serial, type(ex).__name__, ex)
        )

    # Enable Appium's input-method editors. Without this, Android sometimes
    # foregrounds the `Settings → Languages & Input → Available virtual
    # keyboards` activity mid-cell when an Appium scenario does its first
    # `send_keys` (the platform shows "an app wants to enable an input
    # method" prompt). That activity blocks the workload for the rest of the
    # cell -> CONTAM. Observed on Pixel 9 / 6 / 3 during the 2026-06-29 pilot.
    #
    # `ime enable` is idempotent and persistent across reboots. Re-running
    # it every experiment is cheap. Three Appium IMEs ship with the Appium
    # Settings APK; enabling all three covers UnicodeIME (for special
    # characters), AppiumIME (basic typing) and EmptyIME (no-op fallback).
    try:
        for ime_id in (
            "io.appium.settings/.AppiumIME",
            "io.appium.settings/.UnicodeIME",
            "io.appium.settings/.EmptyIME",
        ):
            device.shell("ime enable %s" % ime_id)
        _log_stdout(
            "before_experiment_apply_device_state: Appium IMEs enabled on %s "
            "(prevents mid-cell 'Available virtual keyboards' Settings prompt)" % serial
        )
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: Appium IME enable raised on %s "
            "(continuing — risk of mid-cell Settings prompt blocking the workload): "
            "%s: %s" % (serial, type(ex).__name__, ex)
        )

    # 2026-07-29: also SET AppiumIME as the default. On Pixel 3 (Android 12)
    # the default was still GBoard (LatinIME), which pops the on-screen
    # keyboard whenever a text field gains focus — the poetskingdom title
    # dialog then shifts up and the "Confirm" button lands where our tap
    # coordinates cached from the pre-shift bounds no longer hit it, causing
    # a 100% S3→S4→S7 cascade fail. Setting AppiumIME as default matches
    # P6/P9 (already had it) — Appium's IME injects text silently without
    # showing an on-screen keyboard, so the dialog never shifts.
    try:
        device.shell("ime set io.appium.settings/.AppiumIME")
        _log_stdout(
            "before_experiment_apply_device_state: AppiumIME set as default on %s "
            "(silent-typing; prevents Compose dialogs shifting under a soft keyboard)" % serial
        )
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: `ime set AppiumIME` raised on %s "
            "(continuing — Pixel 3 dialogs may shift and Confirm tap may miss): "
            "%s: %s" % (serial, type(ex).__name__, ex)
        )

    # Reset battery-service mocking so the Java BatteryManager API keeps
    # receiving fresh PMIC updates during the run.
    #
    # AR's `device.unplug()` (called in `Experiment.prepare_device`) issues
    # `dumpsys battery set usb 0` to mock USB-unplugged state. On **Android 12**
    # (Pixel 3), this **freezes the battery service entirely** — dumpsys output
    # shows `(UPDATES STOPPED -- use 'reset' to restart)`. The companion APK
    # (`com.example.batterymanager_utility`) reads via
    # `BatteryManager.getIntProperty(BATTERY_PROPERTY_CURRENT_NOW)`, which hits
    # the frozen framework cache and returns pre-mock values for the whole run.
    # Net effect on Pixel 3: even when hub-ctrl successfully cuts USB power
    # (sysfs reports clean -270 mA discharge), the BM CSV samples show +200 mA
    # (cached charge value) → average power computation is wrong → matched-pair
    # invalid.
    #
    # Android 13 (Pixel 6) and 16 (Pixel 9) don't have this bug — the framework
    # keeps updating despite the mock. Calling `dumpsys battery reset` here
    # un-does the AR mock but is safe because:
    #   1. Our hub-ctrl (Epic 1.6) cuts USB power physically — no mock needed.
    #   2. When the hub cuts power, the framework receives the real
    #      "USB-unplugged" event from the PMIC and reports it correctly.
    #
    # Discovered 2026-06-02 during first 3-phone parallel S2-baseline smoke.
    try:
        device.shell("dumpsys battery reset")
        _log_stdout(
            "before_experiment_apply_device_state: battery service reset on %s "
            "(un-does AR's device.unplug() mock; hub-ctrl provides real power-cut)"
            % serial
        )
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: battery reset raised on %s "
            "(continuing — risk of frozen current_now on Android 12): %s: %s"
            % (serial, type(ex).__name__, ex)
        )

    disable_result: Optional[Dict[str, Any]] = None
    try:
        disable_result = ds.attempt_disable_charging(device)
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state: attempt_disable_charging raised on %s "
            "(continuing): %s: %s" % (serial, type(ex).__name__, ex)
        )

    snapshot = ds.assemble_device_state_snapshot(device)

    cache_payload = {
        "device_serial": serial,
        "android_release": snapshot.get("android_release"),
        "cpu_abilist": snapshot.get("cpu_abilist"),
        "last_attempt_disable_charging": disable_result,
        "last_brightness_lock": brightness_result,
        "last_discharge_verdict": snapshot.get("discharge_verdict"),
        "last_current_now_raw": snapshot.get("current_now_raw"),
        "last_observed_at": snapshot.get("captured_at"),
        "last_battery_level_pct": snapshot.get("battery_level_pct"),
    }
    written = ds.write_capability_cache(serial, cache_payload)
    if written:
        _log_stdout(
            "before_experiment_apply_device_state: cached capability record at %s" % written
        )

    verdict = snapshot.get("discharge_verdict") or "unknown"
    current_raw = snapshot.get("current_now_raw")

    # 2026-07-29: RETRY on suspected_supplying, with hub-recut between attempts.
    # The Plugable USB3-HUB7BC-EU exhibits intermittent BC 1.2 backfeed on a
    # seconds-timescale (root-cause audit in docs/PARALLEL_SWEEP_ISSUES.md).
    # If the hub port has been re-powered by a parallel-stream race or a
    # charging-IC re-arm, another `uhubctl -a 0` should re-cut it. Sequence:
    #   read → positive → re-cut hub → wait → read → positive → re-cut → …
    # If ANY read is negative, we accept the cell. If ALL N reads are positive
    # after N re-cuts, the leak is persistent → the workload will be
    # contaminated → abort fast (RuntimeError).
    if verdict == "suspected_supplying":
        import time as _time
        import os as _os
        import subprocess as _sp
        MAX_RETRIES = 3
        WAIT_S = 5.0
        # 2026-08-04: MASTEREXP_RECUT_CMD takes priority over MASTEREXP_HUB/PORT.
        # The hub/port pair is uhubctl-specific and uhubctl does NOT cut VBUS on
        # the YKUSH3 — it drives the board's internal USB2744 hub while the real
        # switch is an MCU-driven MOSFET reached only through ykushcmd. Issuing
        # the uhubctl re-cut on this hardware would report success, change
        # nothing, and let the retry loop conclude the leak was transient.
        # sweep_runner_parallel_ykush.sh sets MASTEREXP_RECUT_CMD instead and
        # leaves MASTEREXP_HUB/PORT unset; the uhubctl runner is unaffected.
        recut_cmd_env = _os.environ.get("MASTEREXP_RECUT_CMD", "").strip()
        hub_env = _os.environ.get("MASTEREXP_HUB", "")
        port_env = _os.environ.get("MASTEREXP_PORT", "")
        if recut_cmd_env:
            recut_argv = recut_cmd_env.split()
            recut_desc = recut_cmd_env
        elif hub_env and port_env:
            recut_argv = ["sudo", "-n", "/usr/sbin/uhubctl",
                          "-l", hub_env, "-p", port_env, "-a", "0"]
            recut_desc = "uhubctl hub %s port %s" % (hub_env, port_env)
        else:
            recut_argv = None
            recut_desc = ""
        can_recut = recut_argv is not None
        all_readings = [current_raw]
        for attempt in range(1, MAX_RETRIES):
            if can_recut:
                _log_stderr(
                    "before_experiment_apply_device_state: suspected_supplying "
                    "(current_now=%s) on %s — re-cutting via %s + "
                    "retry %d/%d after %.1fs"
                    % (all_readings[-1], serial, recut_desc,
                       attempt, MAX_RETRIES - 1, WAIT_S)
                )
                try:
                    _sp.run(
                        recut_argv,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=6,
                    )
                except Exception as _ex:
                    _log_stderr(
                        "  re-cut failed (continuing without): %s: %s"
                        % (type(_ex).__name__, _ex)
                    )
            else:
                _log_stderr(
                    "before_experiment_apply_device_state: suspected_supplying "
                    "(current_now=%s) on %s — neither MASTEREXP_RECUT_CMD nor "
                    "MASTEREXP_HUB/PORT set, cannot re-cut. Just retrying after %.1fs (%d/%d)"
                    % (all_readings[-1], serial, WAIT_S,
                       attempt, MAX_RETRIES - 1)
                )
            _time.sleep(WAIT_S)
            retry_snap = ds.verify_discharging(device, settle_s=0.0)
            all_readings.append(retry_snap.get("current_now_raw"))
            if retry_snap.get("verdict") == "verified_discharge":
                _log_stdout(
                    "before_experiment_apply_device_state: discharge verified on "
                    "%s on retry %d (current_now=%s; readings=%s) — proceeding"
                    % (serial, attempt, all_readings[-1], all_readings)
                )
                verdict = "verified_discharge"
                current_raw = all_readings[-1]
                snapshot["discharge_verdict"] = verdict
                snapshot["current_now_raw"] = current_raw
                snapshot["retry_all_readings"] = all_readings
                break
        else:
            snapshot["retry_all_readings"] = all_readings
            _log_stderr(
                "before_experiment_apply_device_state: %d/%d discharge attempts "
                "all suspected_supplying on %s (readings=%s) — hub leak confirmed"
                % (MAX_RETRIES, MAX_RETRIES, serial, all_readings)
            )

    if verdict == "verified_discharge":
        _log_stdout(
            "before_experiment_apply_device_state: discharge verified on %s "
            "(current_now=%s) — energy reading on this experiment is trustworthy"
            % (serial, current_raw)
        )
    elif verdict == "suspected_supplying":
        _log_stderr(
            "before_experiment_apply_device_state: discharge NOT verified on %s "
            "(current_now=%s ≥ 0). USB / charge-IC is still supplying. Per-row "
            "energy values WILL be USB-masked. Mitigation: hub-ctrl (Epic 1.6) "
            "or wireless ADB (RUNBOOK §5)." % (serial, current_raw)
        )
    else:
        _log_stderr(
            "before_experiment_apply_device_state: discharge UNKNOWN on %s "
            "(current_now unreadable). Treating as suspected_supplying for "
            "downstream tagging." % serial
        )

    if ds.strict_mode_enabled() and verdict != "verified_discharge":
        sentinel_path = _write_strict_failure_sentinel(snapshot)
        _log_stderr(
            "before_experiment_apply_device_state: STRICT MODE ACTIVE and "
            "discharge_verdict=%r on %s — aborting experiment. Sentinel: %s. "
            "To proceed anyway: unset %s in your shell."
            % (
                verdict,
                serial,
                sentinel_path or "<not written>",
                "MASTEREXP_STRICT_DISCHARGE_CHECK",
            )
        )
        # 2026-07-29: previously called sys.exit(1). AR's Script.mp_run wraps
        # execute_script in `try/except Exception:` — SystemExit inherits from
        # BaseException NOT Exception, so the child process died silently
        # without queue.put(...); the parent's queue.get() then blocked
        # forever until the outer sweep_runner watchdog fired at 480 s. Every
        # abort therefore cost ~9 min of wall time. Raising a plain Exception
        # is caught by AR, propagated as ScriptError, triggers cleanup +
        # non-zero rc — sweep_runner sees the fast exit and moves on within
        # seconds. See 2026-07-29 sweep-code audit for details.
        raise RuntimeError(
            "STRICT MODE ABORT on %s: discharge_verdict=%s current_now_raw=%s "
            "(sentinel=%s). Hub is leaking; cell skipped."
            % (
                serial,
                verdict,
                snapshot.get("current_now_raw"),
                sentinel_path or "<not written>",
            )
        )

    return snapshot


def main(device, *args, **kwargs):
    """Module entrypoint for direct AndroidRunner ``before_experiment`` wiring.

    The catch-all ``before_experiment.py`` and the per-app
    ``before_experiment_uninstall_metronome.py`` import this module and call
    ``apply_device_state`` directly (chain-on-existing-hook pattern). This
    ``main`` exists for symmetry / smoke-testing.
    """
    try:
        apply_device_state(device)
    except SystemExit:
        raise
    except Exception as ex:
        _log_stderr(
            "before_experiment_apply_device_state.main: unexpected exception "
            "(swallowed; experiment continues): %s: %s"
            % (type(ex).__name__, ex)
        )
