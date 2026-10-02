"""E1.5.T5 - Post-process Android Runner's built-in `android` profiler CSV.

Android Runner ships a CPU + memory profiler at `Plugins/android/Android.py`
(documented in the A-Mobile 2020 paper §3.4) that, when enabled in a config
via::

    "profilers": {
      "android": { "sample_interval": 1000, "data_points": ["cpu", "mem"] }
    }

writes a per-(device, run) CSV to::

    <run_id>/data/<device>/<subject>/android/<serial>_<ts>.csv

with columns ``datetime, cpu, mem`` (extra columns appear if more
data_points are configured). The plugin already produces an `Aggregated.csv`
(arithmetic mean per data point) at subject-aggregation time, but the mean
alone is not enough for the thesis: we also need p50, p95, and max so the
tracking matrix can answer "is the energy delta explainable by a CPU /
memory work delta?".

This module is a thin post-processor:

  - finds the most recent `android/<serial>_<ts>.csv` for the run
  - parses each numeric column robustly (tolerates the built-in's quirks
    like the ``TOTAL: 35`` prefix on the cpu column on some devices)
  - writes ``aux/aux_summary.json`` with avg/p50/p95/max for each column
  - exposes a CLI for back-fill against an existing run dir

The hook is wired into `after_run.py` AFTER `detect_crash_anr` so it never
runs in time-critical code paths. It is best-effort: any failure is logged
to stderr and the experiment continues.

Stdlib-only. Designed to coexist with pre-`android`-profiler runs (just
returns ``None`` and writes no summary).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import os.path as op
import re
import sys
from typing import Optional


_AUX_SUMMARY_FILENAME = "aux_summary.json"
# The built-in plugin's get_cpu_usage returns a string like "TOTAL: 35.0"
# on some devices (it does `shell_result.split('%')[0]` without trimming the
# leading "TOTAL: " token). We accept both shapes.
_TRAILING_NUMBER_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*$")


def _resolve_run_output_dir() -> Optional[str]:
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


def _find_android_csv(per_subject_dir: str) -> Optional[str]:
    """Return the newest ``android/<serial>_<ts>.csv`` under the per-subject dir.

    The built-in plugin writes ONE CSV per call to ``collect_results``, named
    ``<device.id>_<YYYY.MM.DD_HHMMSS>.csv`` (see
    ``Plugins/android/Android.py:collect_results``). On a re-run the dir may
    contain several; we pick the most recent by mtime.
    """
    android_dir = op.join(per_subject_dir, "android")
    if not op.isdir(android_dir):
        return None
    candidates = [
        op.join(android_dir, f)
        for f in os.listdir(android_dir)
        if f.endswith(".csv") and f.lower() != "aggregated.csv"
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: op.getmtime(p) if op.exists(p) else 0, reverse=True)
    return candidates[0]


def _coerce_float(raw: Optional[str]) -> Optional[float]:
    """Best-effort float coercion that tolerates the built-in plugin's quirks.

    The CPU column on some devices comes back as ``"TOTAL: 35.0"`` because
    ``Plugins/android/Android.py:get_cpu_usage`` does
    ``shell_result.split('%')[0]`` without trimming the leading "TOTAL: ".
    The memory column is always an integer KB string. We just pull the
    last numeric token from the cell and cast.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    m = _TRAILING_NUMBER_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _percentile(values: list, pct: float) -> Optional[float]:
    """Linear-interpolated percentile (0 <= pct <= 100). None on empty input."""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    sorted_vals = sorted(values)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _basic_stats(values: list) -> dict:
    if not values:
        return {"avg": None, "p50": None, "p95": None, "max": None, "min": None, "n": 0}
    fs = [float(v) for v in values if v is not None]
    if not fs:
        return {"avg": None, "p50": None, "p95": None, "max": None, "min": None, "n": 0}
    return {
        "avg": sum(fs) / len(fs),
        "p50": _percentile(fs, 50.0),
        "p95": _percentile(fs, 95.0),
        "max": max(fs),
        "min": min(fs),
        "n": len(fs),
    }


def aggregate_android_csv(csv_path: str) -> dict:
    """Compute avg/p50/p95/max for every NON-datetime column in ``csv_path``.

    Returns ``{"csv_path": ..., "samples": N, "columns": {col: stats, ...}}``.
    On any failure returns ``{"csv_path": csv_path, "samples": 0, "columns": {},
    "error": "<reason>"}``.
    """
    if not csv_path or not op.isfile(csv_path):
        return {"csv_path": csv_path, "samples": 0, "columns": {}, "error": "csv_missing"}

    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return {"csv_path": csv_path, "samples": 0, "columns": {}, "error": "empty_csv"}
            data_columns = [c for c in header if c.lower() != "datetime"]
            buckets: dict = {c: [] for c in data_columns}
            sample_count = 0
            for row in reader:
                if not row:
                    continue
                sample_count += 1
                row_map = dict(zip(header, row))
                for col in data_columns:
                    v = _coerce_float(row_map.get(col))
                    if v is not None:
                        buckets[col].append(v)
        return {
            "csv_path": csv_path,
            "samples": sample_count,
            "columns": {c: _basic_stats(buckets[c]) for c in data_columns},
        }
    except Exception as ex:
        return {
            "csv_path": csv_path,
            "samples": 0,
            "columns": {},
            "error": "%s: %s" % (type(ex).__name__, ex),
        }


def write_summary(per_subject_dir: str, payload: dict) -> Optional[str]:
    """Write ``payload`` as ``aux/aux_summary.json`` under the per-subject dir.

    Returns the absolute path on success, ``None`` on any failure.
    """
    try:
        aux_dir = op.join(per_subject_dir, "aux")
        os.makedirs(aux_dir, exist_ok=True)
        path = op.join(aux_dir, _AUX_SUMMARY_FILENAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
        return path
    except Exception as ex:
        try:
            sys.stderr.write(
                "aux_postprocess.write_summary(%s): %s: %s\n"
                % (per_subject_dir, type(ex).__name__, ex)
            )
        except Exception:
            pass
        return None


def process_per_subject_dir(per_subject_dir: str) -> dict:
    """Find the built-in plugin's CSV under ``per_subject_dir`` and aggregate it.

    Returns the same payload that gets written to ``aux/aux_summary.json``.
    When the `android` profiler block is not enabled in the experiment config
    (so the CSV does not exist), returns a payload with
    ``"error": "android_profiler_disabled"`` and writes nothing.
    """
    csv_path = _find_android_csv(per_subject_dir)
    if not csv_path:
        return {
            "csv_path": None,
            "samples": 0,
            "columns": {},
            "error": "android_profiler_disabled",
        }
    payload = aggregate_android_csv(csv_path)
    written_at = write_summary(per_subject_dir, payload)
    payload["written_to"] = written_at
    return payload


def process_run_output_dir(run_output_dir: str) -> list:
    """Walk ``<run_output_dir>/data/<device>/<subject>/`` and process each subject dir.

    This is the right entrypoint when called from ``after_experiment``, because
    AndroidRunner's profiler aggregation (which writes the per-(device, subject)
    `android/<serial>_<ts>.csv`) only completes after all per-run hooks have
    fired. By the time ``after_experiment`` runs, every subject dir has its
    profiler CSV on disk and we can write one ``aux/aux_summary.json`` per
    subject in a single pass.

    Returns a list of payloads (one per per-subject dir that contained an
    android profiler CSV). Returns an empty list when ``data/`` is missing or
    when no subject dir had a CSV.
    """
    results: list = []
    if not run_output_dir:
        return results
    data_dir = op.join(run_output_dir, "data")
    if not op.isdir(data_dir):
        return results
    try:
        device_dirs = sorted(
            op.join(data_dir, d) for d in os.listdir(data_dir)
            if op.isdir(op.join(data_dir, d))
        )
    except OSError as ex:
        try:
            sys.stderr.write(
                "aux_postprocess.process_run_output_dir: listdir(%s) failed: %s: %s\n"
                % (data_dir, type(ex).__name__, ex)
            )
        except Exception:
            pass
        return results
    for device_dir in device_dirs:
        try:
            subject_dirs = sorted(
                op.join(device_dir, s) for s in os.listdir(device_dir)
                if op.isdir(op.join(device_dir, s))
            )
        except OSError:
            continue
        for subject_dir in subject_dirs:
            payload = process_per_subject_dir(subject_dir)
            payload["per_subject_dir"] = subject_dir
            results.append(payload)
    return results


# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    """AndroidRunner-style hook. Best-effort - never raises into the experiment loop.

    Detects which lifecycle slot is calling us:

      - ``after_experiment`` (per-device, AFTER profiler aggregation): walk
        every per-subject dir under ``BASE_OUTPUT_DIR/data/<device>/<subject>/``
        and write one ``aux/aux_summary.json`` per subject. This is the
        production path — the per-run android CSV is only on disk after the
        profiler's ``aggregate_subject`` completes (see 2026-05-08T19:30 run
        for the empirical timing: after_run returned 12 s before the CSV
        landed, which is why we moved this hook out of after_run).

      - ``after_run`` (legacy / back-compat): still works if the CSV happens
        to be on disk by the time after_run fires (rare on most devices).
        Falls through to the per-subject path.
    """
    try:
        per_subject_dir = _resolve_run_output_dir()
        if not per_subject_dir:
            sys.stderr.write(
                "aux_postprocess: cannot resolve per-subject output dir; skipping\n"
            )
            return

        # Heuristic: if the resolved dir CONTAINS a `data/` subdir, we are in
        # the after_experiment slot (BASE_OUTPUT_DIR). Process all subjects.
        # If it IS a subject dir (has an `android/` sibling), process just it.
        run_root_candidate = per_subject_dir
        if op.isdir(op.join(run_root_candidate, "data")):
            results = process_run_output_dir(run_root_candidate)
            if not results:
                sys.stdout.write(
                    "aux_postprocess: no per-subject dirs found under %s/data — "
                    "android profiler may be disabled or experiment had no runs\n"
                    % run_root_candidate
                )
                sys.stdout.flush()
                return
            n_processed = sum(1 for r in results if not r.get("error"))
            n_disabled = sum(1 for r in results if r.get("error") == "android_profiler_disabled")
            sys.stdout.write(
                "aux_postprocess: walked %s subject dir(s) under %s "
                "(processed=%s, profiler_disabled=%s)\n"
                % (len(results), run_root_candidate, n_processed, n_disabled)
            )
            sys.stdout.flush()
            return

        payload = process_per_subject_dir(per_subject_dir)
        if payload.get("error") == "android_profiler_disabled":
            sys.stdout.write(
                "aux_postprocess: built-in `android` profiler not enabled for this run "
                "(%s); aux columns in tracking matrix will be empty\n" % per_subject_dir
            )
            sys.stdout.flush()
            return
        cols = payload.get("columns") or {}
        sys.stdout.write(
            "aux_postprocess: aggregated %s samples across %s data point(s) "
            "(written to %s)\n"
            % (payload.get("samples", 0), len(cols), payload.get("written_to") or "<not written>")
        )
        sys.stdout.flush()
    except Exception as ex:
        try:
            sys.stderr.write(
                "aux_postprocess.main: unexpected error (continuing): %s: %s\n"
                % (type(ex).__name__, ex)
            )
        except Exception:
            pass


def cli():
    """CLI for back-filling existing run dirs.

    Usage::

        python3 aux_postprocess.py --run-output-dir /path/to/output/2026.05.08_174322

    The post-processor walks the run dir for ``data/<device>/<subject>/android/`` 
    sub-trees and writes one ``aux/aux_summary.json`` per per-subject dir found.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Post-process Android Runner's built-in `android` profiler CSV "
            "into aux_summary.json (avg/p50/p95/max per column) for the "
            "tracking matrix."
        ),
    )
    parser.add_argument(
        "--run-output-dir",
        required=True,
        help=(
            "Either a per-subject dir (containing `android/`) or the "
            "top-level run dir (containing `data/<device>/<subject>/android/`). "
            "Both are accepted."
        ),
    )
    args = parser.parse_args()

    root = op.abspath(args.run_output_dir)
    if op.isdir(op.join(root, "android")):
        targets = [root]
    else:
        targets = sorted({
            op.dirname(p)
            for p in glob.glob(op.join(root, "data", "*", "*", "android"))
            if op.isdir(p)
        })
        if not targets:
            sys.stderr.write(
                "aux_postprocess.cli: no `android/` sub-tree under %s\n" % root
            )
            return

    for per_subject_dir in targets:
        payload = process_per_subject_dir(per_subject_dir)
        sys.stdout.write(
            "aux_postprocess.cli: %s -> %s (samples=%s, error=%s)\n"
            % (
                per_subject_dir,
                payload.get("written_to") or "<not written>",
                payload.get("samples", 0),
                payload.get("error") or "ok",
            )
        )


if __name__ == "__main__":
    cli()
