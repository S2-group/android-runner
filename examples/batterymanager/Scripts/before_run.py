"""``before_run`` hook for the BatteryManager experiments.

In addition to the original ``am start`` of the BatteryManager utility, this
hook now produces ``apk_meta.json`` for the active run so the
``after_experiment`` tracking-matrix updater can populate the
``apk_path`` / ``apk_sha256`` / ``apk_storage`` columns without needing
those values to be passed on the CLI.

Implementation note: AndroidRunner's ``scripts.before_run`` slot accepts a
single script path, so we extend this existing hook rather than wiring a
new one into every experiment JSON. The chain-on-existing-hook pattern is
the same one ``after_run.py`` already uses for ``detect_crash_anr``.
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _resolve_run_output_dir():
    """Best-effort resolution of the directory ``apk_meta.json`` should land in.

    Prefers ``paths.OUTPUT_DIR`` (per-(device, subject) data folder) because
    ``update_tracking_matrix.py:_find_apk_meta`` matches that location via
    its ``data/*/*/apk_meta.json`` glob. Falls back to ``BASE_OUTPUT_DIR``.
    """
    try:
        import paths as ar_paths  # type: ignore
    except Exception:
        return None
    out = getattr(ar_paths, "OUTPUT_DIR", None)
    if out and os.path.isdir(out):
        return out
    base = getattr(ar_paths, "BASE_OUTPUT_DIR", None)
    if base and os.path.isdir(base):
        return base
    return None


def _resolve_apk_path(args, kwargs):
    """Pull the active APK path out of whatever AndroidRunner forwarded.

    AndroidRunner calls ``scripts.run('before_run', device, *args, **kwargs)``
    where ``kwargs['current_run']`` is a dict containing ``path`` -- the APK
    path for this run (see ``AndroidRunner/Experiment.py``). Some callers
    may pass the path as a positional, so we accept both.
    """
    current_run = kwargs.get("current_run") if isinstance(kwargs, dict) else None
    if isinstance(current_run, dict):
        path = current_run.get("path")
        if path:
            return path
    for arg in args or ():
        if isinstance(arg, str) and arg.lower().endswith(".apk"):
            return arg
    return None


def _resolve_app_id(args, kwargs):
    """Best-effort resolution of the package name under test.

    AR does not pass application_id to before_run directly, so we read it from
    the run's config.json (written into the run output dir by AR at startup).
    Falls back to scanning kwargs. Returns None if it cannot be determined —
    callers must treat that as "skip", never as an error.
    """
    # 1. explicit kwarg, if a future AR version supplies one
    for key in ("application_id", "app_id", "package"):
        val = kwargs.get(key)
        if val:
            return str(val)
    # 2. config.json alongside the run output dir
    try:
        run_dir = _resolve_run_output_dir()
        if run_dir:
            import json
            # run output dir is <output>/<run_id>/data/<device>/<subject>;
            # config.json lives at <output>/<run_id>/config.json
            probe = run_dir
            for _ in range(5):
                cand = os.path.join(probe, "config.json")
                if os.path.isfile(cand):
                    with open(cand) as fh:
                        cfg = json.load(fh)
                    app_id = cfg.get("application_id")
                    if app_id:
                        return str(app_id)
                    break
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
    except Exception:
        pass
    return None


# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    # Issue: on P9 (Android 15), enabling the Appium IMEs triggers a
    # persistent "Available Virtual Keyboards" notification. When the
    # notification is auto-clicked (or the Activity is left resumed from a
    # prior cell), `com.android.settings/.Settings$AvailableVirtualKeyboardActivity`
    # steals focus from the app under test. Appium's `am start-activity -W`
    # then waits 10 s for the target app to reach top-focus, times out, and
    # the workload never starts (keepitup / diaguard / avnc CONTAMs on P9).
    # Pre-emptively force-stop com.android.settings + send KEYCODE_HOME to
    # clear any leftover Settings task before we launch the utility. Costs
    # ~500 ms per cell, no-op if Settings wasn't running.
    # WAKE FIRST. after_run.py puts the display to sleep so the phone charges
    # with the screen off (see that file for why). The display is therefore OFF
    # when a cell begins, and everything below — the Settings force-stop, the
    # utility launch, the whole Appium workload — assumes a live screen.
    #
    # KEYCODE_WAKEUP is idempotent: it turns the display on if it is off and
    # does nothing at all if it is already on. That matters because it makes
    # this safe on a cell that never slept, so no cell is treated differently
    # from any other. These phones carry no keyguard PIN (verified 2026-08-07:
    # mDreamingLockscreen=false on all three), so waking lands on the launcher
    # and the following KEYCODE_HOME settles it.
    #
    # This runs BEFORE profiling starts, so it costs the measurement nothing.
    try:
        device.shell('input keyevent KEYCODE_WAKEUP')
        device.shell('am force-stop com.android.settings')
        device.shell('input keyevent KEYCODE_HOME')
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        try:
            sys.stderr.write(
                "before_run: IME-picker suppression failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # ---- ORIENTATION PINNING (2026-08-01) --------------------------------
    # Force portrait and disable auto-rotate for every cell.
    #
    # WHY: nothing in the harness pinned rotation — verified by grepping the
    # whole Scripts/ tree and sweep_runner_parallel.sh for
    # orientation/accelerometer_rotation/user_rotation/freeze_rotation (zero
    # hits), and by reading the live setting on all three phones (unset).
    # The 2026-07-31 scenario-failure investigation attributed 23 failing
    # cells across SIX apps (another_notes, tipuous, linkhub, androidskk,
    # documenter, pdfviewer) to the device sitting in landscape: element
    # locators that resolve in portrait silently miss, and the scenario
    # scores a strict-fail that looks like flaky app behaviour.
    #
    # This is the single largest identified failure bucket and the cheapest
    # fix. It also removes a real confound from the ENERGY side — landscape
    # changes layout, redraw area and sometimes which views inflate at all,
    # so a variant that happened to run more landscape cells would carry a
    # systematic offset unrelated to its protection.
    #
    # accelerometer_rotation=0 disables auto-rotate; user_rotation=0 is
    # portrait. Both are plain Settings.System writes and cost ~2 adb calls.
    if os.environ.get("MASTEREXP_PIN_ORIENTATION", "1") == "1":
        try:
            device.shell('settings put system accelerometer_rotation 0')
            device.shell('settings put system user_rotation 0')
            got_accel = str(device.shell('settings get system accelerometer_rotation') or '').strip()
            got_rot = str(device.shell('settings get system user_rotation') or '').strip()
            sys.stderr.write(
                "before_run_pin_orientation: accelerometer_rotation={} user_rotation={} "
                "(want 0/0)\n".format(got_accel, got_rot)
            )
        except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
            try:
                sys.stderr.write(
                    "before_run_pin_orientation: failed (continuing): {}: {}\n".format(
                        type(exc).__name__, exc
                    )
                )
            except Exception:
                pass

    # ---- DEXOPT PINNING (2026-07-31) ------------------------------------
    # Force a deterministic AOT compilation filter on the app under test and
    # record the resulting dexopt state as a per-cell covariate.
    #
    # WHY, precisely (the weaker rationale is worth not overstating):
    #   AR installs the APK fresh at the start of every cell and uninstalls it
    #   at the end (verified in cell logs), so background dexopt CANNOT
    #   accumulate state across cells — the package does not exist between
    #   them. The "compilation state drifts mid-campaign" confound is
    #   therefore already largely neutralised by the install/uninstall cycle.
    #
    #   What pinning still buys us:
    #     1. A POSITIVE test instead of an absence test. Under an identical
    #        `-m speed` instruction, baseline gets a real .odex while Bangcle's
    #        assets/classes0.jar payload cannot be compiled — the package
    #        manager never sees it as DEX. The dexopt readout is then evidence
    #        of a real difference, not an inference from a shared absence.
    #        (Absence of dex2oat is the NULL STATE for every variant on modern
    #        Android — verified: our baseline cells show zero too. See
    #        docs/VARIANT_DETECTION_REFERENCE.md §2.1.)
    #     2. Removes install-time dexopt variance, which can still differ with
    #        thermal / storage / battery state even within a single cell.
    #
    # WHY IT IS SAFE HERE: AndroidRunner/Experiment.py:194-197 runs
    #   before_run()  ->  usb_handler.disable_usb()  ->  start_profiling()
    # so this hook executes while USB is still connected AND before the
    # energy profiler starts. The compilation CPU cost is wall-powered and
    # lands entirely outside the measured window.
    #
    # Opt-out: set MASTEREXP_PIN_DEXOPT=0 in the environment.
    if os.environ.get("MASTEREXP_PIN_DEXOPT", "1") == "1":
        try:
            pkg = os.environ.get("MASTEREXP_APP_ID") or _resolve_app_id(args, kwargs)
            if pkg:
                filt = os.environ.get("MASTEREXP_DEXOPT_FILTER", "speed")
                device.shell('cmd package compile -f -m {} {}'.format(filt, pkg))
                state = device.shell('dumpsys package dexopt | grep -A4 "\\[{}\\]"'.format(pkg))
                run_output_dir = _resolve_run_output_dir()
                if run_output_dir:
                    try:
                        with open(os.path.join(run_output_dir, "dexopt_state.txt"), "w") as fh:
                            fh.write("requested_filter={}\n".format(filt))
                            fh.write("package={}\n".format(pkg))
                            fh.write(str(state or ""))
                    except Exception:
                        pass
                sys.stderr.write(
                    "before_run_pin_dexopt: forced filter={} on {}\n".format(filt, pkg)
                )
            else:
                sys.stderr.write(
                    "before_run_pin_dexopt: could not resolve application_id; SKIPPED\n"
                )
        except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
            try:
                sys.stderr.write(
                    "before_run_pin_dexopt: failed (continuing): {}: {}\n".format(
                        type(exc).__name__, exc
                    )
                )
            except Exception:
                pass

    device.shell('am start -n "com.example.batterymanager_utility/com.example.batterymanager_utility.MainActivity" -a android.intent.action.MAIN -c android.intent.category.LAUNCHER')
    time.sleep(5)

    try:
        import _lib_apk_meta
        run_output_dir = _resolve_run_output_dir()
        apk_path = _resolve_apk_path(args, kwargs)
        _lib_apk_meta.write_apk_meta(run_output_dir, apk_path)
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        try:
            sys.stderr.write(
                "before_run: apk_meta hook failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # E0.T8 — per-run device_state.json snapshot. Captures battery /
    # discharge / brightness / airplane-mode / etc. so the matrix updater
    # can tag rows with `energy_invalid_usb_supplying` when the
    # charge controller flipped between runs (noise sources § 1b
    # "float-charge masking"). Never raises; failure logs to stderr and the
    # row simply loses its discharge tag.
    try:
        import before_run_record_device_state as _device_state_recorder
        _device_state_recorder.record_device_state(device, _resolve_run_output_dir())
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        try:
            sys.stderr.write(
                "before_run: device_state.json hook failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # 2026-06-29 — pre-grant Android-13+ runtime permissions to cohort apps
    # whose first-launch would otherwise pop a system permission dialog
    # (POST_NOTIFICATIONS / CAMERA / READ_MEDIA_* / RECORD_AUDIO / etc.). The
    # AR run installs a fresh APK on every cell so the dialog re-fires each
    # run; granting via ``pm grant`` (or ``appops set`` for special-access
    # ops) immediately after install + before launch removes the prompt
    # entirely. See ``before_run_grant_runtime_perms.py`` for the per-app
    # perm table and a discussion of the idempotency / failure modes.
    try:
        import before_run_grant_runtime_perms as _grant_perms
        apk_path = _resolve_apk_path(args, kwargs)
        _grant_perms.grant_runtime_perms(device, apk_path)
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        try:
            sys.stderr.write(
                "before_run: runtime-perm pre-grant hook failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # 2026-06-30 — Issue 27 fix: explicit `am start -n` direct-launch override
    # for packages where the activity resolver picks LeakCanary's LauncherActivity
    # instead of the app's real MainActivity. See before_run_direct_launch.py
    # for the per-package override table. Idempotent + no-op if package not in
    # the override table; safe to call after AR's standard launch chain.
    try:
        import before_run_direct_launch as _direct_launch
        _direct_launch.main(device, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        try:
            sys.stderr.write(
                "before_run: direct-launch override hook failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # E1.5.T1 + T2: CPU + memory sampling is performed by Android Runner's
    # built-in `android` profiler plugin (Plugins/android/Android.py); see the
    # `profilers.android` block in the experiment JSON. No before_run wiring
    # required — the profiler lifecycle is driven by AndroidRunner itself.
    # E1.5.T3: per-run logcat capture is provided by the BatteryManager
    # plugin's `adb_log` persistency_strategy (already enabled in every
    # active config); the resulting `logcat_<serial>_<ts>.txt` is consumed
    # automatically by `detect_crash_anr.read_logcat_from_run_dir`.
