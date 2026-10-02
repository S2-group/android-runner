"""``before_experiment`` helper: disable radios + power-saving features for clean energy floor.

EDATA-style methodological hardening (Blokland et al. MOBILESoft 2025 § 3.3).
Reduces variance sources that can mask the protection-overhead signal we want
to measure. Each control is best-effort + idempotent + reset semantics-aware.

What this hook does on every experiment start:

  1. **Bluetooth OFF** — `svc bluetooth disable` is the canonical command;
     works without root on userdebug builds. Cuts radio scanning that
     otherwise adds ~5-30 mW base load when paired devices are nearby.

  2. **Adaptive Charging OFF** — Pixel-specific. Tries the
     `settings put global adaptive_battery_management_enabled 0` key.
     **Known limitation:** the OS may re-enable this asynchronously
     (per Google Pixel community thread); not load-bearing for our
     methodology since we run on hub-cut DC anyway, but worth setting
     for consistency. NOTE: Adaptive *Battery* and Adaptive *Charging*
     are distinct features in Pixel UX — the settings keys are not
     publicly documented for Adaptive Charging specifically. We attempt
     both candidate keys; verification log will reveal which (if any)
     stuck.

  3. **Battery Saver OFF** — three keys:
       - `low_power 0`              (current state — already off should be fine)
       - `automatic_power_save_mode 0`  (no auto-trigger at low SoC)
       - `dynamic_power_savings_enabled 0`  (no dynamic kicks)
     Plus `cmd power set-mode 0` as a belt-and-suspenders force.

  4. **Doze (deviceidle) DISABLE** — `dumpsys deviceidle disable` —
     RESETS ON REBOOT, so we run it every experiment. Doze enters deep
     sleep after ~5 min of inactivity; for a 120 s workload this isn't
     usually a problem, but it can race with cell teardown and skew the
     "idle baseline" portion of the energy curve. Disabling for the
     duration of the experiment is the cleanest path.

  5. **3rd-party-app inventory** — log every `pm list packages -3`
     entry not in our known cohort. The operator decides whether to
     uninstall (manual; we never auto-uninstall, that's user data).

Strict-mode (`MASTEREXP_STRICT_DEVICE_HARDEN=1`):
    Default lenient mode (env var unset) → log a warning per failed
    setting, continue. Strict mode → abort the experiment if any of
    the radio/saver disables fails to take effect on read-back.
    For the eventual thesis batch where any forgotten Bluetooth scan
    could add μJ of variance to ΔE_steady.
"""

from __future__ import annotations

import os
import os.path as op
import sys
from typing import Any, Dict, List, Optional

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


# Cohort packages we expect to see on the phones. Anything not in this set
# is logged as a 3rd-party warn-list entry; not auto-uninstalled.
KNOWN_COHORT_PACKAGES = {
    "com.exponentialgroth.calculator",
    "com.maltaisn.notes.debug",
    "com.keeprecipes.android",
    "com.qubacy.itsok",
    "com.angelsoft.horoscapp",
    "com.faltenreich.diaguard",
    "com.rajat.sample.pdfviewer",
    "com.skyd.anivu.debug",
    "net.ibbaa.keepitup",
    "net.turtton.ytalarm",
    "com.futsch1.medtimer",
    "jp.deadend.noname.skk",
    "com.klalumiere.repertoire",
    "com.viliussutkus89.documenter",
    "com.kxsv.linkhub",
    "com.tipuous",
    "com.curiouslearning.crcontainer",
    "com.lhasahz.poetskingdom",
    # AR / Appium infra
    "com.example.batterymanager_utility",
    "io.appium.settings",
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
}


def _strict_mode() -> bool:
    return os.environ.get("MASTEREXP_STRICT_DEVICE_HARDEN") == "1"


def _exec(device, cmd: str) -> Optional[str]:
    """Run `cmd` via pyand `device.shell`, return stdout or None on exception."""
    try:
        out = device.shell(cmd)
        return out if out is not None else ""
    except Exception as ex:
        _log_stderr("  device.shell(%r) raised: %s: %s" % (cmd, type(ex).__name__, ex))
        return None


def _try_set(device, label: str, set_cmd: str, get_cmd: Optional[str] = None,
             expected: Optional[str] = None) -> Dict[str, Any]:
    """Try to set + verify a single setting. Returns a small result dict.

    `get_cmd` returns the post-set value; `expected` is the substring we
    expect to find (e.g. "0"). If `get_cmd` is None we only report set status.
    """
    set_out = _exec(device, set_cmd)
    result: Dict[str, Any] = {"label": label, "set_ok": set_out is not None, "set_cmd": set_cmd}
    if get_cmd is None:
        return result
    got = _exec(device, get_cmd)
    result["readback"] = (got or "").strip()
    if expected is not None and got is not None:
        result["verified"] = expected in got
    return result


def disable_radios_and_savers(device) -> Dict[str, Any]:
    """Apply Bluetooth-off + Adaptive Charging-off + Battery Saver-off + Doze-off
    + log 3rd-party packages. Returns the snapshot for inclusion in
    device_state.json by the per-run hook.
    """
    serial = _exec(device, "getprop ro.serialno") or "?"
    serial = serial.strip()

    _log_stdout("before_experiment_disable_radios_and_savers: starting on %s "
                "(strict=%s)" % (serial, "ON" if _strict_mode() else "OFF"))

    results: List[Dict[str, Any]] = []

    # --- 1. Bluetooth OFF ---
    # `svc bluetooth disable` is the canonical approach (works without root
    # on userdebug). `settings put global bluetooth_on 0` is a softer
    # fallback that the system may overwrite. We try both.
    results.append(_try_set(device, "svc_bluetooth_disable",
                            "svc bluetooth disable",
                            "settings get global bluetooth_on", "0"))
    results.append(_try_set(device, "settings_bluetooth_on_0",
                            "settings put global bluetooth_on 0",
                            "settings get global bluetooth_on", "0"))

    # --- 2. Adaptive Charging OFF (Pixel) ---
    # Public name is undocumented. Two candidate keys:
    #   adaptive_battery_management_enabled  (search results say this exists)
    #   adaptive_charging_enabled            (educated guess via naming convention)
    # Set both; system will silently ignore the wrong one.
    results.append(_try_set(device, "adaptive_battery_management_off",
                            "settings put global adaptive_battery_management_enabled 0",
                            "settings get global adaptive_battery_management_enabled", "0"))
    results.append(_try_set(device, "adaptive_charging_off",
                            "settings put secure adaptive_charging_enabled 0",
                            "settings get secure adaptive_charging_enabled", "0"))

    # --- 3. Battery Saver OFF ---
    results.append(_try_set(device, "low_power_off",
                            "settings put global low_power 0",
                            "settings get global low_power", "0"))
    results.append(_try_set(device, "automatic_power_save_mode_off",
                            "settings put global automatic_power_save_mode 0",
                            "settings get global automatic_power_save_mode", "0"))
    results.append(_try_set(device, "dynamic_power_savings_off",
                            "settings put global dynamic_power_savings_enabled 0",
                            "settings get global dynamic_power_savings_enabled", "0"))
    # Belt-and-suspenders: force normal power mode.
    results.append(_try_set(device, "power_set_mode_0",
                            "cmd power set-mode 0",
                            None, None))

    # --- 4. Doze (deviceidle) DISABLE ---
    # Resets on reboot. We run it every experiment.
    results.append(_try_set(device, "deviceidle_disable",
                            "dumpsys deviceidle disable",
                            "dumpsys deviceidle | head -2", None))

    # --- 5. 3rd-party app inventory ---
    third_party_raw = _exec(device, "pm list packages -3") or ""
    pkgs = [line.strip().split(":", 1)[-1] for line in third_party_raw.splitlines()
            if line.strip().startswith("package:")]
    unexpected = sorted(p for p in pkgs if p not in KNOWN_COHORT_PACKAGES)

    snapshot: Dict[str, Any] = {
        "serial": serial,
        "results": results,
        "third_party_packages_unexpected": unexpected,
        "third_party_packages_count_total": len(pkgs),
    }

    # Log summary
    n_ok = sum(1 for r in results if r.get("verified", r.get("set_ok", False)))
    _log_stdout("  applied %d/%d controls successfully; %d unexpected 3rd-party packages"
                % (n_ok, len(results), len(unexpected)))
    if unexpected:
        _log_stdout("  unexpected packages: %s" % ", ".join(unexpected[:10])
                    + (" ..." if len(unexpected) > 10 else ""))

    # Strict mode: abort if any radio/saver disable failed
    if _strict_mode():
        failed = [r["label"] for r in results
                  if "verified" in r and r["verified"] is False]
        if failed:
            _log_stderr("before_experiment_disable_radios_and_savers: STRICT MODE — "
                        "the following controls failed verification: %s. "
                        "To proceed anyway: unset MASTEREXP_STRICT_DEVICE_HARDEN."
                        % ", ".join(failed))
            sys.exit(1)

    return snapshot


def main(device, *args, **kwargs):
    """Module entrypoint for direct AndroidRunner ``before_experiment`` wiring,
    or for chaining from ``before_experiment.py`` / per-app uninstall hooks.
    """
    try:
        disable_radios_and_savers(device)
    except SystemExit:
        raise
    except Exception as ex:
        _log_stderr("before_experiment_disable_radios_and_savers.main: unexpected "
                    "exception (swallowed; experiment continues): %s: %s"
                    % (type(ex).__name__, ex))
