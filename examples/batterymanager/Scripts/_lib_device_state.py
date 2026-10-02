"""Device-state controls + discharge-validity primitives for E0.T8.

This module is the single source of truth for everything the BatteryManager
pipeline knows about the *physical state* of the phone at the moment a run
starts. Two callers consume it:

  - ``before_experiment_apply_device_state`` → applies the controllable
    state (screen brightness lock, best-effort software charge-disable),
    runs the discharge-validity check, caches per-device capabilities.
  - ``before_run_record_device_state``       → reads everything *back* into
    a per-run JSON snapshot (``device_state.json``) so the matrix updater
    can later tag rows whose energy reading is suspect.

Why the split. ``before_experiment`` runs ONCE per experiment and is the
right place to apply state changes. ``before_run`` runs PER RUN and is the
right place to capture a fresh snapshot of `current_now` (because the
charge-controller state can flip between runs — see
``docs/MEASUREMENT_NOISE_SOURCES.md`` § 1b).

Design contract (mirrors ``before_experiment_grant_battery_stats.py``):
  - **Stdlib only.** No third-party imports.
  - **Never raises.** Every public function wraps its body in try/except
    and returns either a value (with a ``status`` / ``error`` field on
    failure) or ``None``. The harness MUST NOT crash inside a hook.
  - **Idempotent.** Re-applying state on a re-run is a no-op (set the same
    brightness, re-attempt the same dumpsys sequence — both are safe).
  - **Best-effort.** ``attempt_disable_charging`` is a *best-effort*
    operation: on Pixel 3 / Pixel 9 we already know it does not actually
    disable charging at the hardware level (the dumpsys API is accepted
    but the charge IC ignores it — see § 1 verdicts table). The function
    still runs the sequence, then ``verify_discharging`` reads
    ``current_now`` to ground-truth what actually happened.

Out of scope (per E0.T8 task spec):
  - Do NOT automate airplane mode (operator-controlled per the user
    constraint clarified in the 2026-05-08 supervisor meeting).
  - Do NOT toggle wireless ADB (operator opt-in via RUNBOOK).
  - Do NOT modify the BatteryManager plugin itself.
"""

from __future__ import annotations

import json
import os
import os.path as op
import sys
import time
from typing import Any, Dict, Optional


SETTLE_SECONDS_DEFAULT = 5.0

CURRENT_NOW_PATH = "/sys/class/power_supply/battery/current_now"
VOLTAGE_NOW_PATH = "/sys/class/power_supply/battery/voltage_now"

BATTERYMANAGER_PACKAGE = "com.example.batterymanager_utility"
BATTERY_STATS_PERMISSION = "android.permission.BATTERY_STATS"


# ---------------------------------------------------------------------------
# Per-device sign-convention table for BATTERY_PROPERTY_CURRENT_NOW
# ---------------------------------------------------------------------------
#
# The Java `BatteryManager.getIntProperty(BATTERY_PROPERTY_CURRENT_NOW)` call
# returns values whose sign convention depends on (vendor PMIC + Health HAL
# version + Android version). The two empirically observed conventions in
# this cohort:
#
#   modern (+1):   positive = current entering battery (charging)
#                  negative = current leaving battery (discharging)
#                  Used by Pixel 6 (Android 13) + Pixel 9 (Android 16).
#
#   legacy (-1):   positive = current leaving battery (discharging)
#                  negative = current entering battery (charging)
#                  Used by Pixel 3 (Android 12). The Pixel 3 Health HAL reads
#                  from /sys/class/power_supply/maxfg/current_now (Maxim
#                  MAX17XXX fuel gauge), which exposes the legacy convention.
#
# IMPORTANT: this table is for the *Java API value only*. The kernel sysfs
# entry /sys/class/power_supply/battery/current_now uses the modern (+1)
# convention on ALL three devices, so verify_discharging() (which reads
# sysfs) does NOT need to apply this multiplier.
#
# Application:
#   - For raw display of signed current (plots, validation-gate output): apply.
#   - For energy / power magnitudes: NOT needed because every code path uses
#     abs() (audited 2026-06-02; see PIXEL3_CURRENT_NOW_SIGN_INVERSION.md).
#   - For matched-pair ratios: NOT needed because the same multiplier
#     applies to both runs in a pair and cancels.
#
# Keyed by `ro.product.device` (the Android product code). Defaults to +1 if
# the device isn't in the table — this keeps newly-added devices safe-by-
# default until empirically confirmed otherwise.
#
# Discovered 2026-06-02 during first 3-phone parallel S2-baseline smoke; full
# diagnosis in docs/PIXEL3_CURRENT_NOW_SIGN_INVERSION.md.
CURRENT_NOW_API_SIGN: Dict[str, int] = {
    "blueline": -1,   # Pixel 3, Android 12. Maxim MAX17XXX via Health HAL @2.0
    "oriole":   +1,   # Pixel 6, Android 13. Modern convention.
    "tokay":    +1,   # Pixel 9, Android 16. Modern convention.
}


def current_now_api_sign(device) -> int:
    """Return the sign multiplier for this device's BATTERY_PROPERTY_CURRENT_NOW.

    Returns +1 (modern convention) or -1 (legacy convention). Multiply the
    raw Java-API current_now value by this to convert to the modern (and
    canonical thesis) convention.

    Reads `ro.product.device` via `getprop`. Defaults to +1 (modern) if the
    device isn't in CURRENT_NOW_API_SIGN — newly-added devices are assumed
    modern until proven otherwise (the legacy convention is rare on
    post-2020 Android phones).
    """
    try:
        product_device = device.shell("getprop ro.product.device").strip()
    except Exception:
        return +1
    return CURRENT_NOW_API_SIGN.get(product_device, +1)


def normalize_current_now_api(device, raw_value):
    """Normalize a Java-API BATTERY_PROPERTY_CURRENT_NOW value to modern convention.

    Multiplies by the per-device sign multiplier (see CURRENT_NOW_API_SIGN
    docstring). Returns the same numeric type as input.

    For modern-convention devices (Pixel 6/9) this is a no-op (multiply by +1).
    For Pixel 3 (legacy convention) the sign flips.

    Use this in code paths that report signed current for display, plots, or
    validation-gate output. Do NOT use for energy/power magnitude computations
    — those should use abs() instead (the multiplier and abs() interact
    pathologically when called naively).
    """
    multiplier = current_now_api_sign(device)
    if multiplier == 1:
        return raw_value
    if raw_value is None:
        return None
    return -raw_value

CAPABILITY_CACHE_DIRNAME = ".device_state_capabilities"

DEFAULT_BRIGHTNESS_VALUE = 255  # 2026-07-27: bumped 128→255 (user override). Adds ~5-10% to per-cell energy but matched-pair ΔE preserved.
DEFAULT_BRIGHTNESS_MODE = 0  # 0 = manual, 1 = adaptive

STRICT_ENV_VAR = "MASTEREXP_STRICT_DISCHARGE_CHECK"


# ---------------------------------------------------------------------------
# Logging helpers (stderr/stdout only — never raise)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Low-level device probes (each returns Optional[T]; None on any failure)
# ---------------------------------------------------------------------------


def get_device_serial(device) -> str:
    """Best-effort serial extraction. Falls back to ``"unknown-serial"``."""
    try:
        return getattr(device, "id", None) or getattr(device, "name", None) or "unknown-serial"
    except Exception:
        return "unknown-serial"


def _device_shell(device, cmd: str) -> Optional[str]:
    """Wrap ``device.shell`` so callers never see exceptions.

    Returns the stripped stdout string on success, ``None`` on any failure.
    """
    try:
        out = device.shell(cmd)
    except Exception as ex:
        _log_stderr(
            "_lib_device_state._device_shell(%r) raised: %s: %s"
            % (cmd, type(ex).__name__, ex)
        )
        return None
    if out is None:
        return None
    try:
        return out.strip() if hasattr(out, "strip") else str(out).strip()
    except Exception:
        return None


def read_current_now_ua(device) -> Optional[int]:
    """Read ``/sys/class/power_supply/battery/current_now`` (Pixel = signed µA).

    Returns ``None`` if the file is unreadable or the value is non-numeric.
    Negative = discharging (battery → device); positive = charging
    (device → battery). Some non-Pixel drivers report mA — this helper
    does NOT normalise; the caller should treat values as device-specific
    raw and only check sign.
    """
    raw = _device_shell(device, "cat %s" % CURRENT_NOW_PATH)
    if raw is None:
        return None
    try:
        return int(raw.strip().splitlines()[0])
    except (ValueError, IndexError):
        try:
            return int(float(raw.strip().splitlines()[0]))
        except (ValueError, IndexError):
            _log_stderr(
                "_lib_device_state.read_current_now_ua: non-numeric value %r" % raw
            )
            return None


def read_voltage_now_uv(device) -> Optional[int]:
    """Read ``/sys/class/power_supply/battery/voltage_now`` (Pixel = µV)."""
    raw = _device_shell(device, "cat %s" % VOLTAGE_NOW_PATH)
    if raw is None:
        return None
    try:
        return int(raw.strip().splitlines()[0])
    except (ValueError, IndexError):
        try:
            return int(float(raw.strip().splitlines()[0]))
        except (ValueError, IndexError):
            return None


def read_battery_level_pct(device) -> Optional[int]:
    """Parse ``level: NN`` from ``dumpsys battery``."""
    out = _device_shell(device, "dumpsys battery") or ""
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("level:"):
            try:
                return int(s.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                return None
    return None


def read_screen_brightness(device) -> Dict[str, Any]:
    """Return ``{"value": int|None, "mode": int|None, "mode_label": str|None}``.

    Mode values: 0 = manual, 1 = adaptive (Android system definition).
    """
    val_raw = _device_shell(device, "settings get system screen_brightness")
    mode_raw = _device_shell(device, "settings get system screen_brightness_mode")
    value: Optional[int] = None
    mode: Optional[int] = None
    if val_raw and val_raw.lower() != "null":
        try:
            value = int(val_raw)
        except ValueError:
            value = None
    if mode_raw and mode_raw.lower() != "null":
        try:
            mode = int(mode_raw)
        except ValueError:
            mode = None
    label: Optional[str]
    if mode is None:
        label = None
    elif mode == 0:
        label = "manual"
    elif mode == 1:
        label = "adaptive"
    else:
        label = "unknown_mode_%s" % mode
    return {"value": value, "mode": mode, "mode_label": label}


def read_airplane_mode(device) -> Optional[bool]:
    """Read-only check of ``settings get global airplane_mode_on``.

    Returns ``True`` / ``False`` / ``None`` (when the setting is absent or
    unparseable). The harness does NOT toggle airplane mode — the operator
    controls it manually per the 2026-05-08 supervisor-meeting decision.
    """
    raw = _device_shell(device, "settings get global airplane_mode_on")
    if raw is None or raw.lower() in ("null", ""):
        return None
    try:
        return int(raw) != 0
    except ValueError:
        return None


def read_third_party_pkg_count(device) -> Optional[int]:
    """Count of user-installed (third-party) packages — ``pm list packages -3``.

    Audit aid for "noise source #4 — other foreground/background apps":
    a clean test device should have a small, stable count.
    """
    out = _device_shell(device, "pm list packages -3") or ""
    if not out:
        return 0 if out == "" else None
    try:
        return sum(1 for line in out.splitlines() if line.strip().startswith("package:"))
    except Exception:
        return None


def read_battery_stats_grant(device) -> Optional[bool]:
    """Mirror of ``before_experiment_grant_battery_stats._verify_grant``.

    Lives here too so the per-run snapshot doesn't have to import the
    grant module (avoids cross-hook coupling in case the grant module is
    ever moved or renamed).
    """
    out = _device_shell(device, "dumpsys package %s" % BATTERYMANAGER_PACKAGE)
    if out is None:
        return None
    for line in out.splitlines():
        s = line.strip()
        if BATTERY_STATS_PERMISSION not in s:
            continue
        if "granted=true" in s:
            return True
        if "granted=false" in s:
            return False
    return None


def read_cpu_abilist(device) -> Optional[str]:
    """``getprop ro.product.cpu.abilist`` — relevant for Bangcle ABI checks."""
    return _device_shell(device, "getprop ro.product.cpu.abilist")


def read_android_release(device) -> Optional[str]:
    """``getprop ro.build.version.release`` (e.g. ``"15"``)."""
    return _device_shell(device, "getprop ro.build.version.release")


# ---------------------------------------------------------------------------
# Charging-disable: best-effort sequence + ground-truth verification
# ---------------------------------------------------------------------------


def attempt_disable_charging(device) -> Dict[str, Any]:
    """Run the standard ``dumpsys battery`` sequence to *try* to stop charging.

    Returns a dict capturing what was attempted::

        {
          "attempted": True,
          "steps": [
            {"cmd": "dumpsys battery unplug", "ok": True / False},
            {"cmd": "dumpsys battery set ac 0", "ok": ...},
            {"cmd": "dumpsys battery set usb 0", "ok": ...},
            {"cmd": "dumpsys battery set wireless 0", "ok": ...},
            {"cmd": "dumpsys battery set status 3", "ok": ...},  # 3 = Discharging
          ]
        }

    "ok" here only means the command did not raise — it does NOT mean the
    charging IC actually obeyed. ``verify_discharging`` is the
    ground-truth check that follows.

    Per the § 1 verdicts table, on Pixel 3 + Pixel 9 these commands are
    accepted by the API but ignored by the charge controller. We still
    run them so we have a documented "we tried" record per device.
    """
    sequence = [
        "dumpsys battery unplug",
        "dumpsys battery set ac 0",
        "dumpsys battery set usb 0",
        "dumpsys battery set wireless 0",
        "dumpsys battery set status 3",
    ]
    steps = []
    for cmd in sequence:
        try:
            device.shell(cmd)
            steps.append({"cmd": cmd, "ok": True})
        except Exception as ex:
            steps.append({"cmd": cmd, "ok": False, "error": "%s: %s" % (type(ex).__name__, ex)})
    return {"attempted": True, "steps": steps}


def verify_discharging(device, settle_s: float = SETTLE_SECONDS_DEFAULT) -> Dict[str, Any]:
    """Ground-truth: after ``settle_s`` seconds, read ``current_now`` and classify.

    Reads from ``/sys/class/power_supply/battery/current_now`` directly (NOT the
    Java BatteryManager API). The sysfs path uses the modern sign convention on
    ALL three cohort devices (Pixel 3 / 6 / 9) — verified empirically 2026-06-02.
    Therefore this function is **unaffected** by the Pixel 3 Java-API sign
    inversion documented in CURRENT_NOW_API_SIGN above. No per-device sign
    multiplier is needed here.


    Verdict semantics::

        verified_discharge   → current_now < 0    (battery is the source)
        suspected_supplying  → current_now >= 0   (USB / charge IC contributing)
        unknown              → current_now unreadable

    The dict also carries the raw value so the per-run snapshot can record
    the actual µA / mA reading for forensic purposes.
    """
    try:
        time.sleep(max(0.0, float(settle_s)))
    except Exception:
        pass
    current_ua = read_current_now_ua(device)
    if current_ua is None:
        return {
            "verdict": "unknown",
            "current_now_raw": None,
            "settle_seconds": settle_s,
            "reason": "current_now_unreadable",
        }
    verdict = "verified_discharge" if current_ua < 0 else "suspected_supplying"
    return {
        "verdict": verdict,
        "current_now_raw": current_ua,
        "settle_seconds": settle_s,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# Brightness lock
# ---------------------------------------------------------------------------


def apply_brightness_lock(
    device,
    *,
    value: int = DEFAULT_BRIGHTNESS_VALUE,
    mode: int = DEFAULT_BRIGHTNESS_MODE,
) -> Dict[str, Any]:
    """Set ``screen_brightness_mode`` (0=manual) + ``screen_brightness`` (0..255).

    Returns a dict with what was attempted and what was read back::

        {
          "applied": True,
          "set_value": 128,
          "set_mode": 0,
          "readback": {"value": 128, "mode": 0, "mode_label": "manual"},
        }

    Failures inside the helper are non-fatal — the readback dict will simply
    show whatever the device currently reports.
    """
    cmds = [
        "settings put system screen_brightness_mode %d" % int(mode),
        "settings put system screen_brightness %d" % int(value),
    ]
    for cmd in cmds:
        _device_shell(device, cmd)
    return {
        "applied": True,
        "set_value": int(value),
        "set_mode": int(mode),
        "readback": read_screen_brightness(device),
    }


# ---------------------------------------------------------------------------
# Capability cache (per-device JSON, written into Scripts/.device_state_capabilities/)
# ---------------------------------------------------------------------------


def capability_cache_dir() -> str:
    """Return the directory the capability cache lives in (Scripts/.device_state_capabilities/).

    Created on first use. Living next to the hook scripts keeps the cache
    co-located with the code that owns it and survives across runs without
    needing the AndroidRunner output dir to be stable.
    """
    here = op.dirname(op.abspath(__file__))
    cache_dir = op.join(here, CAPABILITY_CACHE_DIRNAME)
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as ex:
        _log_stderr(
            "_lib_device_state.capability_cache_dir: makedirs failed (continuing): %s: %s"
            % (type(ex).__name__, ex)
        )
    return cache_dir


def capability_cache_path(serial: str) -> str:
    """Per-device cache file path. Sanitise the serial for filesystem safety."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in (serial or "unknown"))
    return op.join(capability_cache_dir(), "%s.json" % safe)


def read_capability_cache(serial: str) -> Optional[Dict[str, Any]]:
    """Read the cached per-device capability record, or ``None`` on any failure."""
    path = capability_cache_path(serial)
    if not op.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as ex:
        _log_stderr(
            "_lib_device_state.read_capability_cache(%s): %s: %s"
            % (path, type(ex).__name__, ex)
        )
        return None


def write_capability_cache(serial: str, payload: Dict[str, Any]) -> Optional[str]:
    """Write the per-device capability record atomically; return the path."""
    path = capability_cache_path(serial)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
            f.write("\n")
        os.replace(tmp, path)
        return path
    except Exception as ex:
        _log_stderr(
            "_lib_device_state.write_capability_cache(%s): %s: %s"
            % (path, type(ex).__name__, ex)
        )
        return None


# ---------------------------------------------------------------------------
# Snapshot assembly (per-run device_state.json schema)
# ---------------------------------------------------------------------------


def assemble_device_state_snapshot(
    device,
    *,
    capability_record: Optional[Dict[str, Any]] = None,
    settle_s: float = SETTLE_SECONDS_DEFAULT,
) -> Dict[str, Any]:
    """Build the dict that ``before_run_record_device_state`` writes per run.

    Schema (every key always present; value is ``None`` / ``"unknown"`` /
    explanatory string when the underlying probe failed)::

        {
          "schema_version":           "e0t8.v1",
          "captured_at":              "2026-05-09T12:34:56Z",
          "device_serial":            "<serial>",
          "android_release":          "12" | "15" | None,
          "cpu_abilist":              "arm64-v8a,armeabi-v7a" | None,
          "battery_level_pct":        87 | None,
          "current_now_raw":          -488 | None,
          "voltage_now_uv":           4391000 | None,
          "discharge_verdict":        "verified_discharge" | "suspected_supplying" | "unknown",
          "discharge_settle_seconds": 5.0,
          "screen_brightness":        {"value":..., "mode":..., "mode_label":...},
          "airplane_mode_on":         True | False | None,
          "third_party_pkg_count":    7 | None,
          "battery_stats_granted":    True | False | None,
          "capability_record_serial": "<serial>" | None,   # cache freshness witness
        }

    The dict is JSON-serialisable (only Optional[primitive] / dict / list).
    """
    serial = get_device_serial(device)
    discharge = verify_discharging(device, settle_s=settle_s)
    snapshot: Dict[str, Any] = {
        "schema_version": "e0t8.v1",
        "captured_at": _iso_utc_now(),
        "device_serial": serial,
        "android_release": read_android_release(device),
        "cpu_abilist": read_cpu_abilist(device),
        "battery_level_pct": read_battery_level_pct(device),
        "current_now_raw": discharge.get("current_now_raw"),
        "voltage_now_uv": read_voltage_now_uv(device),
        "discharge_verdict": discharge.get("verdict"),
        "discharge_settle_seconds": discharge.get("settle_seconds"),
        "screen_brightness": read_screen_brightness(device),
        "airplane_mode_on": read_airplane_mode(device),
        "third_party_pkg_count": read_third_party_pkg_count(device),
        "battery_stats_granted": read_battery_stats_grant(device),
        "capability_record_serial": (capability_record or {}).get("device_serial"),
    }
    return snapshot


def _iso_utc_now() -> str:
    """ISO-8601 UTC string with second precision; safe on all stdlibs."""
    try:
        from datetime import datetime, timezone
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Strict-mode helper (env-var lookup)
# ---------------------------------------------------------------------------


def strict_mode_enabled() -> bool:
    """Return True iff ``MASTEREXP_STRICT_DISCHARGE_CHECK=1`` is set in the env.

    Strict mode is opt-in for the eventual thesis batch — it tells
    ``before_experiment_apply_device_state`` to abort the experiment when
    discharge cannot be verified, so accidentally-USB-supplied rows never
    enter the matrix. Default (env var unset / 0) is lenient: tag the row
    in ``notes`` and let dev iteration proceed.
    """
    try:
        return os.environ.get(STRICT_ENV_VAR, "").strip() == "1"
    except Exception:
        return False
