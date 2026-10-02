"""Crash and ANR detection hook for AndroidRunner experiments.

Classifies the app's exit state by parsing logcat output and inspecting
the device's foreground package, then writes ``crash_anr_status.json``
next to the run outputs. The downstream tracking matrix consumes this
file to keep crash/ANR runs separate from clean energy aggregates
(see master-experiment.mdc constraint #4 - Crash/ANR isolation).

Black-box principle: this module is purely passive - it reads logs and
queries dumpsys, never modifies the app, never instruments protected
APKs, and never re-enables debug visibility.

Best-effort contract: every public function wraps I/O in try/except.
A failure here must NEVER crash the experiment.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import os.path as op
import re
import sys
import time


_FATAL_EXCEPTION_TOKEN = "FATAL EXCEPTION"
_TOMBSTONE_SIGNALS = ("SIGSEGV", "SIGABRT")
_TOMBSTONE_TOKEN = "tombstoned"

_ANR_RE = re.compile(r"\bANR in ([A-Za-z][\w\.\$]*)")

_SYSTEM_DIALOG_PATTERNS = (
    re.compile(r"Showing crash dialog"),
    re.compile(r"Application Not Responding dialog"),
    re.compile(r"\bam_anr\b"),
)

_FOREGROUND_PKG_RE = re.compile(r"([A-Za-z][\w\.]*)/[\w\.\$]+")


def _line_is_tombstone_signal(line):
    if _TOMBSTONE_TOKEN not in line:
        return False
    for sig in _TOMBSTONE_SIGNALS:
        if sig in line:
            return True
    return False


def _context_window(lines, idx, before=1, after=2):
    start = max(0, idx - before)
    end = min(len(lines), idx + 1 + after)
    return lines[start:idx], lines[idx + 1:end]


def classify(logcat_text, foreground_pkg, expected_pkg):
    """Classify the app's exit state.

    Pure given inputs. Returns a dict with keys ``status``, ``evidence``,
    ``expected_pkg``, ``foreground_pkg``. ``status`` is one of:
    ``none``, ``crash``, ``anr``, ``system_error_dialog``,
    ``lost_foreground``, ``unknown``.

    Detection priority (first matching rule wins for ``status``, but ALL
    matching evidence across rules is collected):

    1. ``crash``    - line contains ``FATAL EXCEPTION`` or
                      ``SIGSEGV``/``SIGABRT`` near ``tombstoned``.
    2. ``anr``      - line matches ``ANR in <pkg>``.
    3. ``system_error_dialog`` - ``Showing crash dialog``,
                      ``Application Not Responding dialog``, or
                      ``am_anr`` event.
    4. ``lost_foreground`` - expected_pkg is set, foreground_pkg differs,
                      and no crash/ANR/dialog signal was found.
    5. ``none``     - no signals AND foreground matches expected (or
                      expected is None).
    6. ``unknown``  - no signals AND expected_pkg is set but
                      foreground_pkg is missing.
    """
    result = {
        "status": "unknown",
        "evidence": [],
        "expected_pkg": expected_pkg,
        "foreground_pkg": foreground_pkg,
    }

    try:
        text = logcat_text or ""
        lines = text.splitlines()

        evidence = []
        crash_seen = False
        anr_seen = False
        sys_dialog_seen = False

        for i, line in enumerate(lines):
            if _FATAL_EXCEPTION_TOKEN in line:
                ctx_before, ctx_after = _context_window(lines, i, before=1, after=2)
                evidence.append({
                    "line": line,
                    "kind": "fatal_exception",
                    "context_before": ctx_before,
                    "context_after": ctx_after,
                })
                crash_seen = True

            if _line_is_tombstone_signal(line):
                ctx_before, ctx_after = _context_window(lines, i, before=1, after=2)
                evidence.append({
                    "line": line,
                    "kind": "tombstone_signal",
                    "context_before": ctx_before,
                    "context_after": ctx_after,
                })
                crash_seen = True

            if _ANR_RE.search(line):
                evidence.append({
                    "line": line,
                    "kind": "anr",
                })
                anr_seen = True

            for pat in _SYSTEM_DIALOG_PATTERNS:
                if pat.search(line):
                    evidence.append({
                        "line": line,
                        "kind": "system_dialog",
                    })
                    sys_dialog_seen = True
                    break

        result["evidence"] = evidence

        if crash_seen:
            result["status"] = "crash"
        elif anr_seen:
            result["status"] = "anr"
        elif sys_dialog_seen:
            result["status"] = "system_error_dialog"
        elif expected_pkg and foreground_pkg and foreground_pkg != expected_pkg:
            result["status"] = "lost_foreground"
        elif expected_pkg and not foreground_pkg:
            result["status"] = "unknown"
        else:
            result["status"] = "none"
    except Exception as exc:
        result["status"] = "unknown"
        result["error"] = "classify_failed: {}: {}".format(
            type(exc).__name__, exc
        )

    return result


def read_logcat_from_run_dir(run_output_dir):
    """Find and return the run's logcat text.

    Prefers raw ``logcat*.txt`` (BatteryManager plugin's adb_log strategy
    or any other text dump). Falls back to ``logcat*.csv`` so a CSV-only
    run does not silently lose all classification context. Returns the
    empty string when no candidate is found or any I/O step fails.
    """
    try:
        if not run_output_dir or not op.isdir(run_output_dir):
            return ""

        text_candidates = []
        for pattern in ("logcat*.txt", "logcat.txt"):
            text_candidates.extend(glob.glob(
                op.join(run_output_dir, "**", pattern), recursive=True))
            text_candidates.extend(glob.glob(
                op.join(run_output_dir, pattern)))

        csv_candidates = glob.glob(
            op.join(run_output_dir, "**", "logcat*.csv"), recursive=True)
        csv_candidates += glob.glob(op.join(run_output_dir, "logcat*.csv"))

        candidates = list(dict.fromkeys(text_candidates)) or list(dict.fromkeys(csv_candidates))
        if not candidates:
            return ""

        candidates.sort(
            key=lambda p: op.getmtime(p) if op.exists(p) else 0,
            reverse=True,
        )
        chosen = candidates[0]
        with open(chosen, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def get_foreground_pkg(device):
    """Best-effort foreground package query via ``adb shell dumpsys``.

    Returns ``None`` on any failure (including missing ``device`` arg,
    shell failures, or unparseable output). Never raises.
    """
    try:
        if device is None:
            return None

        commands = (
            "dumpsys activity activities | grep mResumedActivity",
            "dumpsys activity activities | grep mFocusedApp",
        )

        for cmd in commands:
            try:
                raw = device.shell(cmd)
            except Exception:
                continue
            if raw is None:
                continue
            text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            if not text or not text.strip():
                continue
            match = _FOREGROUND_PKG_RE.search(text)
            if match:
                return match.group(1)
        return None
    except Exception:
        return None


def write_status_json(run_output_dir, status):
    """Write ``crash_anr_status.json`` into ``run_output_dir``.

    Adds a ``timestamp`` field if the input dict does not have one.
    Returns the absolute path on success, or the empty string on failure.
    """
    try:
        if not run_output_dir:
            return ""
        try:
            os.makedirs(run_output_dir, exist_ok=True)
        except Exception:
            pass

        out_path = op.join(run_output_dir, "crash_anr_status.json")
        payload = dict(status) if isinstance(status, dict) else {"status": "unknown"}
        payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        return out_path
    except Exception:
        return ""


def cli():
    """Post-hoc CLI: read logcat from disk and write status JSON.

    Does NOT query a device (post-hoc by definition). Foreground package
    is reported as ``None`` so classification can only resolve to
    ``crash``/``anr``/``system_error_dialog``/``unknown``/``none`` based
    on the logcat alone.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Classify crash/ANR status from a finished run's logcat "
            "output and write crash_anr_status.json next to it."
        ),
    )
    parser.add_argument(
        "--run-output-dir",
        required=True,
        help="Path to the run output directory containing the logcat file.",
    )
    parser.add_argument(
        "--expected-pkg",
        default=None,
        help="Expected foreground package (used only for status semantics).",
    )
    args = parser.parse_args()

    try:
        logcat_text = read_logcat_from_run_dir(args.run_output_dir)
        result = classify(
            logcat_text,
            foreground_pkg=None,
            expected_pkg=args.expected_pkg,
        )
        out_path = write_status_json(args.run_output_dir, result)
        sys.stdout.write(
            "crash_anr_status: {} (evidence={}, wrote={})\n".format(
                result.get("status"),
                len(result.get("evidence", [])),
                out_path or "<failed>",
            )
        )
    except Exception as exc:
        try:
            sys.stderr.write("detect_crash_anr cli failed: {}\n".format(exc))
        except Exception:
            pass


def main(device, *args, **kwargs):
    """AndroidRunner-style hook. Best-effort - never raises.

    Resolves the per-run output dir from ``paths.OUTPUT_DIR``, queries
    the device's foreground package, reads the run's logcat, classifies,
    and writes ``crash_anr_status.json`` next to the run outputs.
    """
    run_output_dir = ""
    expected_pkg = None
    foreground_pkg = None
    logcat_text = ""

    try:
        try:
            import paths as ar_paths
            run_output_dir = getattr(ar_paths, "OUTPUT_DIR", "") or ""
        except Exception:
            run_output_dir = ""

        try:
            experiment = args[0] if args else None
            if experiment is not None:
                expected_pkg = getattr(experiment, "package", None)
            if not expected_pkg:
                expected_pkg = kwargs.get("app") or kwargs.get("package")
        except Exception:
            expected_pkg = None

        try:
            foreground_pkg = get_foreground_pkg(device)
        except Exception:
            foreground_pkg = None

        try:
            logcat_text = read_logcat_from_run_dir(run_output_dir)
        except Exception:
            logcat_text = ""

        result = classify(logcat_text, foreground_pkg, expected_pkg)
        out_path = write_status_json(run_output_dir, result)

        try:
            print(
                "CRASH_ANR_DETECT: status={} foreground={} expected={} -> {}".format(
                    result.get("status"),
                    foreground_pkg,
                    expected_pkg,
                    out_path or "<not-written>",
                )
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            sys.stderr.write("detect_crash_anr.main failed: {}\n".format(exc))
        except Exception:
            pass


if __name__ == "__main__":
    cli()
