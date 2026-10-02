"""``before_run`` helper: snapshot device state into ``device_state.json``.

Per-run companion to ``before_experiment_apply_device_state.py``. The
``before_experiment`` hook applied state once at experiment start and
verified discharge once; this hook captures a *fresh* snapshot every run
because the charge-controller state can flip between runs even with the
``BATTERY_STATS`` grant carried over (see noise sources § 1b "float-charge
masking").

The resulting ``device_state.json`` lands in ``paths.OUTPUT_DIR`` (i.e.
``<run_dir>/data/<device>/<subject>/device_state.json``) so the
``after_experiment`` matrix updater can read it via the same
``data/*/*/device_state.json`` glob it already uses for ``apk_meta.json``.

Schema (all 9 fields from the E0.T8 spec)::

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
      "capability_record_serial": "<serial>" | None
    }

Contract:
    - **Stdlib only.** No third-party imports.
    - **Never raises.** Hook must not crash the experiment.
    - **No state mutation.** Read-only probes; charging-disable + brightness
      are applied by the experiment-level hook, NOT here.
"""

from __future__ import annotations

import json
import os
import os.path as op
import sys
from typing import Any, Dict, Optional

_HERE = op.dirname(op.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


_FILENAME = "device_state.json"


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


def _resolve_run_output_dir() -> Optional[str]:
    """Mirror of ``before_run._resolve_run_output_dir``.

    Prefers ``paths.OUTPUT_DIR`` (per-(device, subject) data folder) so the
    matrix updater finds the file via its ``data/*/*/device_state.json`` glob.
    """
    try:
        import paths as ar_paths  # type: ignore
    except Exception:
        return None
    out = getattr(ar_paths, "OUTPUT_DIR", None)
    if out and op.isdir(out):
        return out
    base = getattr(ar_paths, "BASE_OUTPUT_DIR", None)
    if base and op.isdir(base):
        return base
    return None


def record_device_state(device, run_output_dir: Optional[str] = None) -> Optional[str]:
    """Build the snapshot dict and write ``device_state.json`` atomically.

    Returns the absolute path written, or ``None`` on any failure (logged to
    stderr). The caller treats ``None`` as a soft warning — the run still
    proceeds; the matrix updater simply leaves the discharge-verdict tag off
    that row.
    """
    try:
        import _lib_device_state as ds
    except Exception as ex:
        _log_stderr(
            "before_run_record_device_state: _lib_device_state import failed: %s: %s"
            % (type(ex).__name__, ex)
        )
        return None

    serial = ds.get_device_serial(device)
    target_dir = run_output_dir or _resolve_run_output_dir()
    if not target_dir:
        _log_stderr(
            "before_run_record_device_state: cannot resolve per-run output dir on %s; "
            "device_state.json NOT written (matrix row will lose its discharge tag)" % serial
        )
        return None

    capability_record = ds.read_capability_cache(serial)
    snapshot: Dict[str, Any] = ds.assemble_device_state_snapshot(
        device, capability_record=capability_record
    )

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as ex:
        _log_stderr(
            "before_run_record_device_state: mkdir %s failed (continuing with write attempt): %s: %s"
            % (target_dir, type(ex).__name__, ex)
        )

    target = op.join(target_dir, _FILENAME)
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True, default=str)
            f.write("\n")
        os.replace(tmp, target)
    except Exception as ex:
        _log_stderr(
            "before_run_record_device_state: write %s failed: %s: %s"
            % (target, type(ex).__name__, ex)
        )
        return None

    verdict = snapshot.get("discharge_verdict") or "unknown"
    current_raw = snapshot.get("current_now_raw")
    _log_stdout(
        "before_run_record_device_state: wrote %s "
        "(serial=%s discharge=%s current_now=%s)"
        % (target, serial, verdict, current_raw)
    )
    return target


def main(device, *args, **kwargs):
    """Module entrypoint for direct AndroidRunner wiring (or chain-on-hook callers)."""
    try:
        record_device_state(device)
    except Exception as ex:
        _log_stderr(
            "before_run_record_device_state.main: unexpected exception "
            "(swallowed; run continues): %s: %s"
            % (type(ex).__name__, ex)
        )
