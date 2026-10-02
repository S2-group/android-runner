#!/usr/bin/env python3
"""Append (or replace) one row in ``specs/tracking_matrix.csv`` for an Android Runner run.

Stdlib-only. Best-effort: this script never raises out to the caller. On any
internal error it writes a row with ``notes="update_failed: <error>"`` and exits 0
so the experiment lifecycle is never disturbed.

Usage::

    python3 update_tracking_matrix.py \
        --app metronome --variant baseline --device pixel3 \
        --run-output-dir /path/to/android-runner/examples/batterymanager/output/2026.05.05_235701

The matrix file is located by walking up from this script until a directory
named ``specs/`` is found that contains ``tracking_matrix.csv`` (override with
``--matrix-path``). See ``specs/TRACKING_MATRIX.md`` for the schema.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import hashlib
import io
import json
import os
import os.path as op
import sys
import traceback
from typing import Iterable, Optional


COLUMNS = [
    "app",
    "variant",
    "device",
    "run_id",
    "timestamp",
    "installed",
    "appium_session",
    "workload_started",
    "scenario_action_pass",
    "scenario_strict_pass",
    "energy_source",
    "aggregated_energy_mwh",
    "crash_anr_status",
    "apk_path",
    "apk_sha256",
    "apk_storage",
    # E0.T7: Three-window energy split (post-hoc analysis of the same single
    # measurement; the runtime profiler boundaries are unchanged).
    "energy_pre_workload_mwh",
    "energy_workload_only_mwh",
    "energy_whole_window_mwh",
    "duration_pre_workload_s",
    "duration_workload_s",
    "duration_whole_window_s",
    # E1.5.T5: Aux samplers (CPU + memory). Empty when the run pre-dates
    # E1.5.T1/T2 or when the sampler init failed — never raises.
    "cpu_avg_pct",
    "cpu_p95_pct",
    "mem_pss_avg_mb",
    "mem_pss_max_mb",
    "notes",
]

# Columns added by E0.T7 (kept here so the back-fill / migration scripts can
# enumerate them without re-listing every name).
ENERGY_WINDOW_COLUMNS = (
    "energy_pre_workload_mwh",
    "energy_workload_only_mwh",
    "energy_whole_window_mwh",
    "duration_pre_workload_s",
    "duration_workload_s",
    "duration_whole_window_s",
)

# Columns added by E1.5.T5. Same convention as ENERGY_WINDOW_COLUMNS.
AUX_SAMPLER_COLUMNS = (
    "cpu_avg_pct",
    "cpu_p95_pct",
    "mem_pss_avg_mb",
    "mem_pss_max_mb",
)

VALID_VARIANTS = ("baseline", "r8", "allatori", "bangcle")
VALID_DEVICES = ("pixel3", "pixel6", "pixel9")
VALID_CRASH_STATUSES = (
    "none",
    "crash",
    "anr",
    "system_error_dialog",
    "lost_foreground",
    "unknown",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_str(value: Optional[bool]) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _now_iso_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _walk_up_for_matrix(start: str) -> Optional[str]:
    """Walk up from ``start`` looking for ``specs/tracking_matrix.csv``."""
    cur = op.abspath(start)
    last = None
    while cur and cur != last:
        candidate = op.join(cur, "specs", "tracking_matrix.csv")
        if op.isfile(candidate):
            return candidate
        last, cur = cur, op.dirname(cur)
    return None


def _resolve_matrix_path(override: Optional[str]) -> str:
    """Locate ``specs/tracking_matrix.csv`` (script location → workspace root)."""
    if override:
        return op.abspath(override)
    here = op.dirname(op.abspath(__file__))
    found = _walk_up_for_matrix(here)
    if found:
        return found
    cwd = _walk_up_for_matrix(os.getcwd())
    if cwd:
        return cwd
    # Fallback: assume default repo layout (script in
    # android-runner/examples/batterymanager/Scripts → workspace 4 levels up).
    repo_root = op.abspath(op.join(here, op.pardir, op.pardir, op.pardir, op.pardir))
    return op.join(repo_root, "specs", "tracking_matrix.csv")


# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------


def _find_first(paths: Iterable[str]) -> Optional[str]:
    for p in paths:
        if p and op.isfile(p):
            return p
    return None


def _find_appium_status(run_output_dir: str) -> Optional[str]:
    """``appium_status.json`` lives under ``data/<device>/<package_dir>/`` after a run.

    Tolerate it being directly under the run dir (older layouts / smoke runs).
    """
    direct = op.join(run_output_dir, "appium_status.json")
    if op.isfile(direct):
        return direct
    matches = sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "appium_status.json")))
    if matches:
        return matches[0]
    matches = sorted(glob.glob(op.join(run_output_dir, "data", "**", "appium_status.json"), recursive=True))
    return matches[0] if matches else None


def _find_scenario_report(run_output_dir: str) -> Optional[str]:
    direct = op.join(run_output_dir, "espresso_mirror_scenario_report.json")
    if op.isfile(direct):
        return direct
    matches = sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "espresso_mirror_scenario_report.json")))
    if matches:
        return matches[0]
    matches = sorted(glob.glob(
        op.join(run_output_dir, "data", "**", "espresso_mirror_scenario_report.json"),
        recursive=True,
    ))
    return matches[0] if matches else None


def _find_crash_anr_status(run_output_dir: str) -> Optional[str]:
    """``crash_anr_status.json`` is produced by the (future) E0.T5 hook."""
    return _find_first([
        op.join(run_output_dir, "crash_anr_status.json"),
        *sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "crash_anr_status.json"))),
        *sorted(glob.glob(op.join(run_output_dir, "data", "**", "crash_anr_status.json"), recursive=True)),
    ])


def _find_aux_summary(run_output_dir: str) -> Optional[str]:
    """Locate ``aux/aux_summary.json`` written by E1.5.T5's `aux_postprocess.py`.

    The post-processor reads Android Runner's built-in `android` profiler CSV
    (under ``paths.OUTPUT_DIR/android/``) and writes its aggregates to
    ``paths.OUTPUT_DIR/aux/aux_summary.json``. From the top-level run dir
    that's ``data/<device>/<subject>/aux/aux_summary.json``. We also accept a
    top-level ``aux/aux_summary.json`` for back-fill / smoke tests.
    """
    filename = "aux_summary.json"
    return _find_first([
        op.join(run_output_dir, "aux", filename),
        *sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "aux", filename))),
        *sorted(glob.glob(
            op.join(run_output_dir, "data", "**", "aux", filename),
            recursive=True,
        )),
    ])


def _read_aux_summary(run_output_dir: str) -> Optional[dict]:
    """Read ``aux/aux_summary.json``. Returns ``None`` when missing OR malformed.

    Best-effort: pre-aux runs and runs where the `android` profiler block was
    not enabled simply return ``None`` here, the caller leaves the cells empty,
    and ``notes`` gets no annotation (absent aux summary is a valid state).
    """
    path = _find_aux_summary(run_output_dir)
    if not path:
        return None
    return _read_json(path)


def _aux_stat(payload: Optional[dict], column: str, stat_key: str) -> Optional[float]:
    """Pull ``payload["columns"][column][stat_key]`` if all three exist and are numeric.

    The new schema (post 2026-05-08T19:30Z) is::

        {
          "csv_path": "...",
          "samples": N,
          "columns": {
            "cpu": {"avg":..., "p50":..., "p95":..., "max":..., "min":..., "n":...},
            "mem": {"avg":..., ...}
          }
        }
    """
    if not isinstance(payload, dict):
        return None
    columns = payload.get("columns")
    if not isinstance(columns, dict):
        return None
    block = columns.get(column)
    if not isinstance(block, dict):
        return None
    val = block.get(stat_key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _find_apk_meta(run_output_dir: str) -> Optional[str]:
    """``apk_meta.json`` is dropped by the experiment template / pre_run hook.

    Schema (all keys optional)::

        {
          "apk_path":    "app_repositories_newest/_built_apks/metronome/bangcle.apk",
          "apk_sha256":  "<hex>",            // optional; will be (re)computed if file is readable
          "apk_storage": "local"             // or "gh-release:<tag>/<asset>"
                                             // or "zenodo:<doi>/<asset>"
        }
    """
    return _find_first([
        op.join(run_output_dir, "apk_meta.json"),
        *sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "apk_meta.json"))),
        *sorted(glob.glob(op.join(run_output_dir, "data", "**", "apk_meta.json"), recursive=True)),
    ])


def _find_device_state(run_output_dir: str) -> Optional[str]:
    """Locate ``device_state.json`` written by E0.T8's per-run hook.

    ``before_run_record_device_state.record_device_state`` writes it into
    ``paths.OUTPUT_DIR``, which from the top-level run dir is
    ``data/<device>/<subject>/device_state.json``. We also accept a
    top-level ``device_state.json`` for back-fill / smoke tests.

    Schema (all 9 fields from the E0.T8 spec, see ``_lib_device_state.assemble_device_state_snapshot``).
    Pre-E0.T8 runs simply lack the file → ``None`` → caller leaves the
    matrix row's discharge tag empty.
    """
    return _find_first([
        op.join(run_output_dir, "device_state.json"),
        *sorted(glob.glob(op.join(run_output_dir, "data", "*", "*", "device_state.json"))),
        *sorted(glob.glob(
            op.join(run_output_dir, "data", "**", "device_state.json"),
            recursive=True,
        )),
    ])


def _read_device_state(run_output_dir: str) -> Optional[dict]:
    """Read ``device_state.json``. Returns ``None`` when missing OR malformed."""
    path = _find_device_state(run_output_dir)
    if not path:
        return None
    return _read_json(path)


def _device_state_notes(payload: Optional[dict]) -> list[str]:
    """Convert ``device_state.json`` → list of ``notes`` strings for the matrix row.

    The contract is: only emit annotations when something is *abnormal*. A
    fully-clean run (verified discharge, brightness locked at 128/manual,
    BATTERY_STATS granted) produces an empty list — the ``notes`` column
    stays uncluttered.

    Annotations (worst-case takes precedence in the operator's eye):

      - ``energy_invalid_usb_supplying`` — ``discharge_verdict ==
        "suspected_supplying"`` (charge IC dominating; energy reading
        cannot be compared cross-variant).
      - ``energy_validity_unknown`` — ``discharge_verdict == "unknown"``
        (current_now unreadable; treat as USB-supplied for safety).
      - ``companion_apk_legacy_no_battery_stats_declared`` — ``battery_stats_granted
        == False`` AFTER the 2026-05-08 evening rebuild. **Retracted
        semantics:** this used to be ``battery_stats_not_granted`` and was
        believed to flag bad readings. The 2026-05-08 evening A/B test on
        Pixel 3 + the AOSP API surface (``current.txt``) both prove the
        grant does NOT change ``BATTERY_PROPERTY_CURRENT_NOW``. The tag
        now means: *the upstream S2-group companion APK is installed
        instead of the rebuilt fork from
        ``_external/batterymanager-companion-fork/``*. That's a regression
        to detect — install the rebuilt APK and re-grant — but the
        readings themselves are NOT invalidated by this annotation alone.
        See `docs/MEASUREMENT_NOISE_SOURCES.md` § 1 for the full
        retraction.
      - ``brightness_not_locked`` — adaptive mode still on or value drift
        > ±32 from the locked default of 128.
    """
    if not isinstance(payload, dict):
        return []
    notes: list[str] = []
    verdict = payload.get("discharge_verdict")
    current_raw = payload.get("current_now_raw")
    if verdict == "suspected_supplying":
        notes.append("energy_invalid_usb_supplying=current_now_raw=%s" % current_raw)
    elif verdict == "unknown":
        notes.append("energy_validity_unknown=current_now_unreadable")
    granted = payload.get("battery_stats_granted")
    if granted is False:
        notes.append("companion_apk_legacy_no_battery_stats_declared")
    brightness = payload.get("screen_brightness") or {}
    if isinstance(brightness, dict):
        mode = brightness.get("mode")
        value = brightness.get("value")
        if mode == 1:
            notes.append("brightness_adaptive_not_locked")
        if value is not None:
            try:
                if abs(int(value) - 128) > 32:
                    notes.append("brightness_drift=%s" % value)
            except (TypeError, ValueError):
                pass
    return notes


def _charge_dominant_notes(run_output_dir: str) -> list[str]:
    """Detect § 1b float-charge masking from the raw BatteryManager CSV.

    The signature of float-charge masking (see
    `docs/MEASUREMENT_NOISE_SOURCES.md` § 1b) is that the charge controller
    is in active-charge mode during the workload, so the per-sample
    ``BATTERY_PROPERTY_CURRENT_NOW`` reads positive (charging) more than
    half the time. The energy integrator multiplies ``|I| × V``, so when
    the workload's discharge contribution is dominated by charger ramp,
    the resulting ``Energy (J)`` cannot be compared cross-variant.

    Threshold: > 50 % positive samples ⇒ tag the row with
    ``energy_invalid_charge_dominant=<pct>%_positive_samples`` so the
    cross-variant aggregator can drop the row from comparisons until
    Epic 1.6 hub-ctrl lands. The < 50 % case stays untagged (clean
    discharge dominates).

    Best-effort: never raises, returns ``[]`` on any I/O / parse failure
    (the absence of the tag means "we couldn't tell", not "clean").
    """
    try:
        candidates = sorted(glob.glob(op.join(
            run_output_dir, "data", "*", "*", "batterymanager", "logcat_*.csv",
        )))
        if not candidates:
            candidates = sorted(glob.glob(op.join(
                run_output_dir, "data", "**", "batterymanager", "logcat_*.csv",
            ), recursive=True))
        if not candidates:
            return []
        # Pick the largest CSV (most samples) if there are multiple — same
        # tie-break the energy-window slicer uses.
        path = max(candidates, key=lambda p: op.getsize(p))
        cur_keys = (
            "BATTERY_PROPERTY_CURRENT_NOW", "current_now_ua",
            "current_uA", "current_ua",
        )
        n = 0
        n_pos = 0
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = _first_present(row, cur_keys)
                if raw is None:
                    continue
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    continue
                n += 1
                if v > 0:
                    n_pos += 1
        if n < 30:
            # Too few samples to draw a verdict — typical for smoke runs.
            return []
        pct = 100.0 * n_pos / n
        if pct > 50.0:
            return ["energy_invalid_charge_dominant=%.0f%%_positive_samples" % pct]
        return []
    except Exception:  # noqa: BLE001 - never raise
        return []


# ---------------------------------------------------------------------------
# APK identity (sha256 + provenance string)
# ---------------------------------------------------------------------------


def _resolve_workspace_root(matrix_path: str) -> str:
    """Workspace root is the directory that contains ``specs/tracking_matrix.csv``."""
    return op.dirname(op.dirname(op.abspath(matrix_path)))


def _sha256_file(path: str) -> Optional[str]:
    """Streaming SHA-256 of ``path``. Returns ``None`` if the file is unreadable."""
    if not path or not op.isfile(path):
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _normalize_apk_path(apk_path: Optional[str], workspace_root: str) -> tuple[str, Optional[str]]:
    """Return ``(stored_apk_path, abs_apk_path_or_None)``.

    ``stored_apk_path`` is what we write into the CSV: the path relative to
    the workspace root if the APK lives inside the workspace, otherwise the
    absolute path. Empty string if no APK was supplied.
    """
    if not apk_path:
        return "", None
    abs_path = op.abspath(apk_path)
    if op.isfile(abs_path):
        try:
            common = op.commonpath([abs_path, workspace_root])
        except ValueError:
            common = ""
        stored = op.relpath(abs_path, workspace_root) if common == workspace_root else abs_path
        return stored, abs_path
    # File not found yet — still record the requested path for trace; no hash.
    return apk_path, None


def _resolve_apk_identity(
    *,
    cli_apk_path: Optional[str],
    cli_apk_sha256: Optional[str],
    cli_apk_storage: Optional[str],
    apk_meta: Optional[dict],
    workspace_root: str,
) -> tuple[str, str, str, list[str]]:
    """Combine CLI args + ``apk_meta.json`` into ``(apk_path, apk_sha256, apk_storage, notes)``.

    Precedence (highest first): CLI flag > ``apk_meta.json`` > empty.
    SHA-256 is recomputed from the file whenever it is readable, even if a
    value was provided, so a mismatch surfaces as a ``notes`` warning rather
    than a silent lie.
    """
    notes: list[str] = []
    meta = apk_meta if isinstance(apk_meta, dict) else {}

    apk_path_raw = cli_apk_path or meta.get("apk_path") or ""
    apk_storage = cli_apk_storage or meta.get("apk_storage") or ("local" if apk_path_raw else "")
    declared_sha = cli_apk_sha256 or meta.get("apk_sha256") or ""

    apk_path_stored, abs_path = _normalize_apk_path(apk_path_raw, workspace_root)

    computed_sha = _sha256_file(abs_path) if abs_path else None
    if computed_sha:
        if declared_sha and declared_sha.lower() != computed_sha.lower():
            notes.append(
                "apk_sha256_mismatch=declared:%s,computed:%s"
                % (declared_sha[:12], computed_sha[:12])
            )
        apk_sha256 = computed_sha
    else:
        if apk_path_raw and not abs_path:
            notes.append("apk_file_missing=%s" % apk_path_raw)
        apk_sha256 = declared_sha  # may still be empty

    return apk_path_stored, apk_sha256, apk_storage, notes


# ---------------------------------------------------------------------------
# Energy aggregation
# ---------------------------------------------------------------------------


def _joules_to_mwh(joules: float) -> float:
    """1 mWh = 3.6 J → mWh = J / 3.6."""
    return float(joules) / 3.6


def _read_batterymanager_aggregate(run_output_dir: str) -> Optional[float]:
    """Return aggregated energy in mWh from ``Aggregated_Results_Batterymanager.csv``."""
    path = op.join(run_output_dir, "Aggregated_Results_Batterymanager.csv")
    if not op.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            joules: list[float] = []
            for row in reader:
                # Prefer the trapezoidal integral; fall back to the simple sum.
                value = row.get("Energy trapz (J)") or row.get("Energy simple (J)")
                if value is None or value == "":
                    continue
                try:
                    joules.append(float(value))
                except ValueError:
                    continue
        if not joules:
            return None
        # AndroidRunner aggregates per-(device, subject, run); the experiment we
        # care about typically yields one row, but if there are several
        # repetitions, sum them so the matrix entry covers the whole run dir.
        return _joules_to_mwh(sum(joules))
    except OSError:
        return None


def _read_sysfs_energy_summary(run_output_dir: str) -> Optional[float]:
    """Return aggregated energy in mWh from a pre-computed ``sysfs_energy_summary.csv``."""
    candidates = sorted(glob.glob(op.join(run_output_dir, "**", "sysfs_energy_summary.csv"), recursive=True))
    if not candidates:
        return None
    try:
        with open(candidates[0], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for key in ("energy_mwh", "Energy mWh", "energy_total_mwh"):
                    if row.get(key):
                        try:
                            return float(row[key])
                        except ValueError:
                            continue
                for key in ("energy_j", "Energy (J)", "Energy J", "energy_trapz_j"):
                    if row.get(key):
                        try:
                            return _joules_to_mwh(float(row[key]))
                        except ValueError:
                            continue
    except OSError:
        return None
    return None


def _integrate_sysfs_power(run_output_dir: str) -> Optional[float]:
    """Trapezoidal integral of ``sysfs_power_*.csv`` samples → mWh.

    Schema (see ``Scripts/interaction.py``)::

        epoch_ms,current_now_ua,voltage_now_uv

    ``current_now`` may be negative (discharging convention); we take the
    magnitude because the matrix records *energy*, not signed flow.
    """
    candidates = sorted(glob.glob(op.join(run_output_dir, "**", "sysfs_power_*.csv"), recursive=True))
    if not candidates:
        return None
    total_joules = 0.0
    any_samples = False
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                prev_t: Optional[float] = None
                prev_p: Optional[float] = None
                for row in reader:
                    try:
                        t = int(row["epoch_ms"]) / 1000.0
                        cur_ua = float(row["current_now_ua"])
                        volt_uv = float(row["voltage_now_uv"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    power_w = abs(cur_ua * 1e-6) * (volt_uv * 1e-6)
                    if prev_t is not None and prev_p is not None:
                        dt = t - prev_t
                        if 0 < dt < 60:  # skip pathological gaps
                            total_joules += 0.5 * (power_w + prev_p) * dt
                            any_samples = True
                    prev_t, prev_p = t, power_w
        except OSError:
            continue
    if not any_samples or total_joules <= 0:
        return None
    return _joules_to_mwh(total_joules)


def _aggregate_energy(run_output_dir: str) -> tuple[str, Optional[float]]:
    """Return ``(energy_source, aggregated_energy_mwh)``."""
    bm = _read_batterymanager_aggregate(run_output_dir)
    if bm is not None:
        return "batterymanager", bm
    sysfs_summary = _read_sysfs_energy_summary(run_output_dir)
    if sysfs_summary is not None:
        return "sysfs", sysfs_summary
    sysfs_integ = _integrate_sysfs_power(run_output_dir)
    if sysfs_integ is not None:
        return "sysfs", sysfs_integ
    return "none", None


# ---------------------------------------------------------------------------
# E0.T7 - Three-window energy split (post-hoc analysis)
# ---------------------------------------------------------------------------
#
# The Android Runner BatteryManager plugin records *one* per-sample CSV per
# run (``data/<device>/<subject>/batterymanager/logcat_<serial>_*.csv``). The
# whole-run energy is already covered by the legacy ``aggregated_energy_mwh``
# column. E0.T7 slices that **same** stream into three windows and integrates
# each one independently, so a thesis query can separate (1) protection
# startup cost, (2) steady-state UI cost, (3) total user-facing cost from a
# single measurement. No extra wall-clock runtime, no extra device wear, and
# the runtime profiler boundaries are deliberately *not* moved (reverting
# this section restores the pre-T7 behaviour).
#
# Window definitions:
#   pre_workload : profiler_start          -> first appium scenario ts
#   workload_only: first appium scenario ts -> last appium scenario ts
#   whole_window : profiler_start          -> profiler_stop  (== legacy aggregated_energy_mwh)


def _parse_log_timestamp_to_host_s(line: str) -> Optional[float]:
    """Parse the leading ``YYYY-MM-DD HH:MM:SS,mmm`` from a Python-logging line.

    The log timestamp is naive local time of the host that ran the experiment.
    Convert to UTC epoch seconds via the system tz so it can be compared to
    the (already-UTC) Appium scenario ``ts`` values and to BatteryManager
    sample timestamps after offset alignment.
    """
    if not line or len(line) < 23:
        return None
    head = line[:23]  # "2026-05-08 10:57:04,403"
    try:
        naive = _dt.datetime.strptime(head, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None
    try:
        local_aware = naive.astimezone()  # interprets naive as local time (Py 3.6+)
        return local_aware.timestamp()
    except (ValueError, OSError):
        return None


def _parse_iso_utc_to_s(value: object) -> Optional[float]:
    """Parse an ISO-8601 UTC string (``...Z`` or ``+00:00``) into epoch seconds."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    try:
        return d.timestamp()
    except (ValueError, OSError):
        return None


def _parse_profiler_boundaries(run_output_dir: str) -> tuple[Optional[float], Optional[float]]:
    """Return ``(start_host_s, stop_host_s)`` from ``experiment.log``.

    AndroidRunner emits multiple ``Profilers:Stop profiling`` lines (the real
    end-of-measurement plus a later one during subject aggregation). We take
    the **first** Start and the **first Stop after that Start** so the window
    matches the actual battery-sampling phase.
    """
    log_path = op.join(run_output_dir, "experiment.log")
    if not op.isfile(log_path):
        return None, None
    start_host_s: Optional[float] = None
    stop_host_s: Optional[float] = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if start_host_s is None:
                    if "Profilers:Start profiling" in line or "Profilers: Start profiling" in line:
                        start_host_s = _parse_log_timestamp_to_host_s(line)
                else:
                    if "Profilers:Stop profiling" in line or "Profilers: Stop profiling" in line:
                        stop_host_s = _parse_log_timestamp_to_host_s(line)
                        if stop_host_s is not None:
                            break
    except OSError:
        return None, None
    return start_host_s, stop_host_s


def _parse_appium_workload_boundaries(run_output_dir: str) -> tuple[Optional[float], Optional[float]]:
    """Return ``(first_ts_s, last_ts_s)`` from ``appium_workload_coverage.jsonl``."""
    candidates = sorted(glob.glob(op.join(
        run_output_dir, "data", "*", "*", "appium_workload_coverage.jsonl",
    )))
    if not candidates:
        candidates = sorted(glob.glob(op.join(
            run_output_dir, "data", "**", "appium_workload_coverage.jsonl",
        ), recursive=True))
    if not candidates:
        return None, None
    path = candidates[0]
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None, None
    if not lines:
        return None, None
    try:
        first = json.loads(lines[0])
    except ValueError:
        first = {}
    try:
        last = json.loads(lines[-1])
    except ValueError:
        last = {}
    return _parse_iso_utc_to_s(first.get("ts")), _parse_iso_utc_to_s(last.get("ts"))


def _voltage_raw_to_volts(volt_raw: float) -> float:
    """Robustly convert a BatteryManager voltage reading to volts.

    The BatteryManager Java API ``EXTRA_VOLTAGE`` is documented in **mV**
    (typical Li-ion values 3000-4400). The sysfs ``voltage_now`` node is in
    **uV** (3000000-4400000). A unit-less ``volts`` reading would be < 10.
    Ranges below are widened to cover degraded cells and weird devices.
    """
    if volt_raw <= 0:
        return 0.0
    if volt_raw < 100:
        return float(volt_raw)              # already volts
    if volt_raw < 100_000:
        return float(volt_raw) / 1000.0     # mV -> V
    return float(volt_raw) / 1_000_000.0    # uV -> V


def _parse_batterymanager_logcat(csv_path: str) -> list[tuple[float, float, float]]:
    """Parse a raw per-sample CSV into ``[(device_ts_ms, current_uA, voltage_V), ...]``.

    Tolerates schema variations across BatteryManager plugin versions. Returns
    an empty list on any unrecoverable error.
    """
    ts_keys = ("Timestamp", "timestamp", "epoch_ms", "ts")
    cur_keys = ("BATTERY_PROPERTY_CURRENT_NOW", "current_now_ua", "current_uA", "current_ua")
    volt_keys = ("EXTRA_VOLTAGE", "voltage_now_uv", "voltage_uV", "voltage_uv", "voltage")
    samples: list[tuple[float, float, float]] = []
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_raw = _first_present(row, ts_keys)
                cur_raw = _first_present(row, cur_keys)
                volt_raw = _first_present(row, volt_keys)
                if ts_raw is None or cur_raw is None or volt_raw is None:
                    continue
                try:
                    ts_v = float(ts_raw)
                    cur_v = float(cur_raw)
                    volt_v_raw = float(volt_raw)
                except (TypeError, ValueError):
                    continue
                # Normalise timestamp to ms (10 digits = seconds, 13 = ms).
                if ts_v < 1e12:
                    ts_v *= 1000.0
                samples.append((ts_v, cur_v, _voltage_raw_to_volts(volt_v_raw)))
    except OSError:
        return []
    samples.sort(key=lambda t: t[0])
    return samples


def _first_present(row: dict, keys: Iterable[str]):
    for k in keys:
        if k in row and row[k] not in ("", None):
            return row[k]
    return None


def _trapezoidal_energy_mwh(
    host_samples: list[tuple[float, float]],
    t_a: Optional[float],
    t_b: Optional[float],
) -> Optional[float]:
    """Trapezoidal integration of ``(host_s, power_W)`` samples over ``[t_a, t_b]``.

    Includes only samples that fall inside the window; boundary fractions are
    truncated. With ~10 Hz BatteryManager sampling and >=60 s windows that is
    sub-1% loss — well within the +/-10% sanity bound the matrix uses.
    """
    if t_a is None or t_b is None or not (t_b > t_a):
        return None
    in_window = [(t, p) for t, p in host_samples if t_a <= t <= t_b]
    if len(in_window) < 2:
        return None
    total_joules = 0.0
    prev_t, prev_p = in_window[0]
    for t, p in in_window[1:]:
        dt = t - prev_t
        if 0 < dt < 60:        # skip pathological gaps (matches sysfs integrator)
            total_joules += 0.5 * (p + prev_p) * dt
        prev_t, prev_p = t, p
    if total_joules <= 0:
        return None
    return _joules_to_mwh(total_joules)


def _empty_energy_windows(*notes: str) -> dict:
    return {
        "energy_pre_workload_mwh": None,
        "energy_workload_only_mwh": None,
        "energy_whole_window_mwh": None,
        "duration_pre_workload_s": None,
        "duration_workload_s": None,
        "duration_whole_window_s": None,
        "notes_addendum": list(notes),
    }


def _compute_energy_windows(run_output_dir: str) -> dict:
    """Compute pre_workload / workload_only / whole_window energy + duration.

    Best-effort: never raises. On any failure the six numeric values are
    ``None`` and ``notes_addendum`` carries an ``energy_window_split_unresolved=<reason>``
    annotation that the caller appends to the row's ``notes`` column.
    """
    try:
        notes: list[str] = []

        # 1) Locate the raw per-sample CSV.
        csv_candidates = sorted(glob.glob(op.join(
            run_output_dir, "data", "*", "*", "batterymanager", "logcat_*.csv",
        )))
        if not csv_candidates:
            csv_candidates = sorted(glob.glob(op.join(
                run_output_dir, "data", "**", "batterymanager", "logcat_*.csv",
            ), recursive=True))
        if not csv_candidates:
            return _empty_energy_windows("energy_window_split_unresolved=raw_csv_missing")
        if len(csv_candidates) > 1:
            # Take the largest file (most samples) and document the choice.
            csv_path = max(csv_candidates, key=lambda p: op.getsize(p))
            notes.append("multi_logcat_csvs_picked_largest")
        else:
            csv_path = csv_candidates[0]

        # 2) Parse raw samples.
        samples = _parse_batterymanager_logcat(csv_path)
        if len(samples) < 2:
            notes.append("energy_window_split_unresolved=raw_csv_unparseable_or_empty")
            return _empty_energy_windows(*notes)
        first_sample_device_ms = samples[0][0]
        last_sample_device_ms = samples[-1][0]

        # 3) Parse the profiler boundaries from experiment.log; fall back to
        #    sample boundaries if the log is unavailable / unparseable. We
        #    treat the host clock and the device clock as potentially offset
        #    (BatteryManager timestamps are device-local epoch ms; on Pixels
        #    that are still on factory time the offset can be years).
        profiler_start_host_s, profiler_stop_host_s = _parse_profiler_boundaries(run_output_dir)
        if profiler_start_host_s is None:
            profiler_start_host_s = first_sample_device_ms / 1000.0
            notes.append("profiler_start_fallback_to_first_sample")
        if profiler_stop_host_s is None:
            profiler_stop_host_s = last_sample_device_ms / 1000.0
            notes.append("profiler_stop_fallback_to_last_sample")

        # 4) Compute the device-clock -> host-clock offset using the start
        #    anchor (first sample is recorded ~immediately after Start
        #    profiling, so this anchor is tight to within one sample period).
        device_to_host_offset_s = profiler_start_host_s - (first_sample_device_ms / 1000.0)
        host_samples: list[tuple[float, float]] = []
        for ts_ms, cur_ua, volt_v in samples:
            host_t = ts_ms / 1000.0 + device_to_host_offset_s
            power_w = abs(cur_ua * 1e-6) * volt_v   # |I| (A) * V (V) = W
            host_samples.append((host_t, power_w))

        # 5) Whole window.
        energy_whole = _trapezoidal_energy_mwh(
            host_samples, profiler_start_host_s, profiler_stop_host_s,
        )
        duration_whole = (
            profiler_stop_host_s - profiler_start_host_s
            if energy_whole is not None else None
        )

        # 6) Workload sub-window + pre-workload remainder.
        workload_start_host_s, workload_end_host_s = _parse_appium_workload_boundaries(run_output_dir)
        if workload_start_host_s is None or workload_end_host_s is None:
            notes.append("appium_workload_boundaries_missing")
            return {
                "energy_pre_workload_mwh": None,
                "energy_workload_only_mwh": None,
                "energy_whole_window_mwh": energy_whole,
                "duration_pre_workload_s": None,
                "duration_workload_s": None,
                "duration_whole_window_s": duration_whole,
                "notes_addendum": notes,
            }

        # Clamp workload boundaries inside the whole window so the slices stay
        # non-overlapping if the JSONL ts drifts past Stop profiling.
        if workload_start_host_s < profiler_start_host_s:
            workload_start_host_s = profiler_start_host_s
            notes.append("workload_start_clamped_to_profiler_start")
        if workload_end_host_s > profiler_stop_host_s:
            workload_end_host_s = profiler_stop_host_s
            notes.append("workload_end_clamped_to_profiler_stop")

        energy_workload = _trapezoidal_energy_mwh(
            host_samples, workload_start_host_s, workload_end_host_s,
        )
        duration_workload = (
            workload_end_host_s - workload_start_host_s
            if energy_workload is not None else None
        )
        energy_pre = _trapezoidal_energy_mwh(
            host_samples, profiler_start_host_s, workload_start_host_s,
        )
        duration_pre = (
            workload_start_host_s - profiler_start_host_s
            if energy_pre is not None else None
        )

        return {
            "energy_pre_workload_mwh": energy_pre,
            "energy_workload_only_mwh": energy_workload,
            "energy_whole_window_mwh": energy_whole,
            "duration_pre_workload_s": duration_pre,
            "duration_workload_s": duration_workload,
            "duration_whole_window_s": duration_whole,
            "notes_addendum": notes,
        }
    except Exception as ex:  # noqa: BLE001 - best-effort, never raise
        return _empty_energy_windows(
            "energy_window_split_unresolved=%s:%s" % (type(ex).__name__, ex)
        )


# ---------------------------------------------------------------------------
# Row composition
# ---------------------------------------------------------------------------


def _scenario_pass(report: Optional[dict]) -> tuple[Optional[bool], Optional[bool]]:
    """Return ``(action_pass, strict_pass)`` from the espresso-mirror report."""
    if not isinstance(report, dict):
        return None, None
    summary = report.get("summary") or {}
    try:
        action = int(summary.get("total_action_only_pass", 0)) > 0
    except (TypeError, ValueError):
        action = None
    try:
        strict = int(summary.get("total_strict_ui_pass", 0)) > 0
    except (TypeError, ValueError):
        strict = None
    return action, strict


def _appium_flags(status: Optional[dict]) -> tuple[Optional[bool], Optional[bool], Optional[bool]]:
    """Return ``(installed, appium_session, workload_started)``.

    ``installed`` is inferred — Appium can only create a session for an
    installed package, so ``session_created=true`` ⇒ installed. Otherwise we
    cannot tell from this artifact alone, so leave it ``unknown``.
    """
    if not isinstance(status, dict):
        return None, None, None
    session = status.get("session_created")
    workload = status.get("workload_started")
    session_b = bool(session) if isinstance(session, bool) else None
    workload_b = bool(workload) if isinstance(workload, bool) else None
    installed_b: Optional[bool] = True if session_b else None
    return installed_b, session_b, workload_b


def _crash_status(payload: Optional[dict]) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    raw = payload.get("status") or payload.get("crash_anr_status")
    if isinstance(raw, str) and raw in VALID_CRASH_STATUSES:
        return raw
    return "unknown"


def _compose_row(
    *,
    app: str,
    variant: str,
    device: str,
    run_id: str,
    run_output_dir: str,
    workspace_root: str,
    cli_apk_path: Optional[str],
    cli_apk_sha256: Optional[str],
    cli_apk_storage: Optional[str],
) -> dict:
    appium_status = _read_json(_find_appium_status(run_output_dir) or "")
    scenario_report = _read_json(_find_scenario_report(run_output_dir) or "")
    crash_payload = _read_json(_find_crash_anr_status(run_output_dir) or "")
    apk_meta = _read_json(_find_apk_meta(run_output_dir) or "")
    # E1.5.T5 — aux summary written by `aux_postprocess.py` from the built-in
    # `android` profiler's CSV. May be absent on pre-aux runs or when the
    # `android` profiler block is not enabled in the experiment config; in
    # both cases the cells stay empty and `notes` is unaffected.
    aux_summary = _read_aux_summary(run_output_dir)
    # E0.T8 — per-run device-state snapshot. Pre-E0.T8 runs simply lack the
    # file → device_notes is empty and the notes column gets no annotation.
    device_state = _read_device_state(run_output_dir)

    installed, session, workload = _appium_flags(appium_status)
    action_pass, strict_pass = _scenario_pass(scenario_report)

    # E0.T7 - compute the three windows from the raw per-sample CSV. Always
    # safe to call: never raises, returns Nones + notes_addendum on failure.
    windows = _compute_energy_windows(run_output_dir)
    if windows.get("energy_whole_window_mwh") is not None:
        # New code path: the per-sample integrator owns the whole-window
        # number. Source is implicitly batterymanager (raw CSV).
        energy_source = "batterymanager"
        energy_mwh: Optional[float] = windows["energy_whole_window_mwh"]
    else:
        # Fall back to the legacy aggregator (Aggregated_Results_Batterymanager.csv
        # or the sysfs paths) so the column never silently regresses.
        energy_source, energy_mwh = _aggregate_energy(run_output_dir)
    apk_path, apk_sha256, apk_storage, apk_notes = _resolve_apk_identity(
        cli_apk_path=cli_apk_path,
        cli_apk_sha256=cli_apk_sha256,
        cli_apk_storage=cli_apk_storage,
        apk_meta=apk_meta,
        workspace_root=workspace_root,
    )

    notes_parts: list[str] = []
    if appium_status is None:
        notes_parts.append("appium_status_missing")
    if scenario_report is None:
        notes_parts.append("scenario_report_missing")
    if crash_payload is None:
        notes_parts.append("crash_anr_status_missing")
    if not apk_path and not apk_meta:
        notes_parts.append("apk_meta_missing")
    failure_reason = (appium_status or {}).get("failure_reason") if isinstance(appium_status, dict) else None
    if failure_reason:
        notes_parts.append("appium_failure_reason=%s" % failure_reason)
    notes_parts.extend(apk_notes)
    notes_parts.extend(windows.get("notes_addendum") or [])
    notes_parts.extend(_device_state_notes(device_state))
    notes_parts.extend(_charge_dominant_notes(run_output_dir))

    return {
        "app": app,
        "variant": variant,
        "device": device,
        "run_id": run_id,
        "timestamp": _now_iso_utc(),
        "installed": _bool_str(installed),
        "appium_session": _bool_str(session),
        "workload_started": _bool_str(workload),
        "scenario_action_pass": _bool_str(action_pass),
        "scenario_strict_pass": _bool_str(strict_pass),
        "energy_source": energy_source,
        "aggregated_energy_mwh": "" if energy_mwh is None else ("%.6f" % energy_mwh),
        "crash_anr_status": _crash_status(crash_payload),
        "apk_path": apk_path,
        "apk_sha256": apk_sha256,
        "apk_storage": apk_storage,
        "energy_pre_workload_mwh": _fmt_window(windows.get("energy_pre_workload_mwh")),
        "energy_workload_only_mwh": _fmt_window(windows.get("energy_workload_only_mwh")),
        "energy_whole_window_mwh": _fmt_window(windows.get("energy_whole_window_mwh")),
        "duration_pre_workload_s": _fmt_window(windows.get("duration_pre_workload_s"), precision=3),
        "duration_workload_s": _fmt_window(windows.get("duration_workload_s"), precision=3),
        "duration_whole_window_s": _fmt_window(windows.get("duration_whole_window_s"), precision=3),
        # E1.5.T5 — aux columns sourced from the built-in `android` profiler's
        # CSV via `aux_postprocess.py`. Built-in column semantics:
        #   - "cpu" = system-wide CPU% (TOTAL line of `dumpsys cpuinfo`)
        #   - "mem" = subject-app PSS in KB (TOTAL row of `dumpsys meminfo <pkg>`)
        # The built-in does NOT split app-vs-system CPU or break PSS into
        # java/native heap; if those are needed later, extend `aux_postprocess`.
        "cpu_avg_pct": _fmt_window(_aux_stat(aux_summary, "cpu", "avg"), precision=2),
        "cpu_p95_pct": _fmt_window(_aux_stat(aux_summary, "cpu", "p95"), precision=2),
        "mem_pss_avg_mb": _fmt_window(_kb_to_mb(_aux_stat(aux_summary, "mem", "avg")), precision=2),
        "mem_pss_max_mb": _fmt_window(_kb_to_mb(_aux_stat(aux_summary, "mem", "max")), precision=2),
        "notes": "; ".join(notes_parts),
    }


def _kb_to_mb(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value) / 1024.0
    except (TypeError, ValueError):
        return None


def _fmt_window(value: Optional[float], *, precision: int = 6) -> str:
    """Format an energy/duration window value for the CSV (empty string if None)."""
    if value is None:
        return ""
    fmt = "%%.%df" % precision
    return fmt % float(value)


def _empty_row(
    *,
    app: str,
    variant: str,
    device: str,
    run_id: str,
    note: str,
) -> dict:
    row = {col: "" for col in COLUMNS}
    row.update({
        "app": app or "unknown",
        "variant": variant or "unknown",
        "device": device or "unknown",
        "run_id": run_id or "unknown",
        "timestamp": _now_iso_utc(),
        "installed": "unknown",
        "appium_session": "unknown",
        "workload_started": "unknown",
        "scenario_action_pass": "unknown",
        "scenario_strict_pass": "unknown",
        "energy_source": "none",
        "aggregated_energy_mwh": "",
        "crash_anr_status": "unknown",
        "apk_path": "",
        "apk_sha256": "",
        "apk_storage": "",
        # E0.T7 - leave the six energy-window cells empty in the failure row.
        "energy_pre_workload_mwh": "",
        "energy_workload_only_mwh": "",
        "energy_whole_window_mwh": "",
        "duration_pre_workload_s": "",
        "duration_workload_s": "",
        "duration_whole_window_s": "",
        # E1.5.T5 - aux sampler cells stay empty in the failure row.
        "cpu_avg_pct": "",
        "cpu_p95_pct": "",
        "mem_pss_avg_mb": "",
        "mem_pss_max_mb": "",
        "notes": note,
    })
    return row


# ---------------------------------------------------------------------------
# CSV I/O (idempotent)
# ---------------------------------------------------------------------------


def _read_existing_rows(matrix_path: str) -> list[dict]:
    if not op.isfile(matrix_path):
        return []
    try:
        with open(matrix_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return [dict(r) for r in reader]
    except OSError:
        return []


def _write_rows(matrix_path: str, rows: list[dict]) -> None:
    os.makedirs(op.dirname(matrix_path), exist_ok=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {col: row.get(col, "") for col in COLUMNS}
        writer.writerow(clean)
    with open(matrix_path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def _upsert_row(matrix_path: str, new_row: dict) -> None:
    rows = _read_existing_rows(matrix_path)
    rows = [r for r in rows if r.get("run_id") != new_row["run_id"]]
    rows.append(new_row)
    _write_rows(matrix_path, rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append/replace one row in specs/tracking_matrix.csv from an AndroidRunner run output dir.",
    )
    parser.add_argument("--app", required=True, help="short app key, e.g. metronome")
    parser.add_argument(
        "--variant",
        required=True,
        help="one of: %s" % ", ".join(VALID_VARIANTS),
    )
    parser.add_argument(
        "--device",
        required=True,
        help="one of: %s" % ", ".join(VALID_DEVICES),
    )
    parser.add_argument(
        "--run-output-dir",
        required=True,
        help="path to the AndroidRunner run output dir (basename = run_id)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="override the run_id (default: basename of --run-output-dir)",
    )
    parser.add_argument(
        "--matrix-path",
        default=None,
        help="override the path to specs/tracking_matrix.csv",
    )
    parser.add_argument(
        "--apk-path",
        default=None,
        help=(
            "path to the APK that produced this run; SHA-256 is computed from "
            "the file. Overrides apk_meta.json. Stored relative to the "
            "workspace root if it lives inside the workspace."
        ),
    )
    parser.add_argument(
        "--apk-sha256",
        default=None,
        help=(
            "optional pre-computed SHA-256 hex; used for cross-checking only "
            "when --apk-path points to an existing file (mismatch is logged "
            "to the notes column)."
        ),
    )
    parser.add_argument(
        "--apk-storage",
        default=None,
        help=(
            "where the APK currently lives, e.g. 'local', "
            "'gh-release:<tag>/<asset>', 'zenodo:<doi>/<asset>'. "
            "Defaults to 'local' when --apk-path is given."
        ),
    )
    return parser


def _main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    matrix_path = _resolve_matrix_path(args.matrix_path)
    workspace_root = _resolve_workspace_root(matrix_path)
    run_output_dir = op.abspath(args.run_output_dir)
    run_id = args.run_id or op.basename(run_output_dir.rstrip(os.sep)) or "unknown"

    try:
        if not op.isdir(run_output_dir):
            row = _empty_row(
                app=args.app,
                variant=args.variant,
                device=args.device,
                run_id=run_id,
                note="update_failed: run_output_dir not found: %s" % run_output_dir,
            )
        else:
            row = _compose_row(
                app=args.app,
                variant=args.variant,
                device=args.device,
                run_id=run_id,
                run_output_dir=run_output_dir,
                workspace_root=workspace_root,
                cli_apk_path=args.apk_path,
                cli_apk_sha256=args.apk_sha256,
                cli_apk_storage=args.apk_storage,
            )
        _upsert_row(matrix_path, row)
    except Exception as ex:  # noqa: BLE001 - best-effort by design
        try:
            row = _empty_row(
                app=args.app,
                variant=args.variant,
                device=args.device,
                run_id=run_id,
                note="update_failed: %s: %s" % (type(ex).__name__, ex),
            )
            _upsert_row(matrix_path, row)
        except Exception:  # noqa: BLE001 - swallow to honor the never-raise contract
            sys.stderr.write("update_tracking_matrix: failed to record failure row\n")
            sys.stderr.write(traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
