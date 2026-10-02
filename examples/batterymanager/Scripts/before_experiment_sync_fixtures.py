"""``before_experiment`` helper: sync per-app workload fixtures to phone.

Issue 26 fix. Until now, fixtures (happybirthday.md for repertoire, sample.pdf
for documenter, etc.) lived only on whatever phone the scenario author last
pushed them to. P9 had them; P3 / P6 didn't. Cells passed quietly with empty
file lists or "no document found" UI states that the scenario chained-clicked
past, never producing the real "open a document" workload.

This hook fixes that by mirroring a canonical fixtures/ directory to every
phone at experiment start. Idempotent and re-runnable. Each fixture has:
  - source: local path relative to repo root
  - target: target path on phone (typically /sdcard/Download/...)

The catalog below is editable. Add entries for any new fixture-dependent
scenarios.

Pattern follows before_experiment_apply_device_state.py.

Failure handling: per-fixture failures are logged but never raise. The hook
proceeds to push every other fixture. Missing fixtures (source file doesn't
exist on the laptop) are an operator error and are reported but do not abort.
"""

from __future__ import annotations

import os
import os.path as op
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = op.dirname(op.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Resolve the master-experiment repo root from this script's location:
#   $REPO_ROOT/android-runner/examples/batterymanager/Scripts/<this file>
# So repo root is 4 levels up from _HERE.
_REPO_ROOT = op.abspath(op.join(_HERE, "..", "..", "..", ".."))


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


# (source_relpath, target_path_on_phone, description, owner_scenario)
# source_relpath is relative to _REPO_ROOT.
# Add entries here when new fixture-dependent scenarios land.
FIXTURE_CATALOG: List[Tuple[str, str, str, str]] = [
    (
        "_external/fixtures/happybirthday.md",
        "/sdcard/Download/happybirthday.md",
        "Markdown note used by Repertoire scenarios.",
        "repertoire",
    ),
    (
        "app_repositories_newest/final_dataset/ViliusSutkus89_Documenter/app/"
        "src/androidTest/assets/testFiles/geneve_1564.pdf",
        "/sdcard/Download/sample.pdf",
        "PDF used by Documenter open-document scenario.",
        "documenter",
    ),
]


def _run_adb_push(serial: str, source: str, target: str) -> Dict[str, Any]:
    """`adb -s <serial> push <source> <target>`. Subprocess.run bypasses pyand
    which would shlex.split the path and mangle spaces (see GC plugin bug 4).
    """
    result: Dict[str, Any] = {"source": source, "target": target}
    if not op.isfile(source):
        result["status"] = "source_missing"
        return result
    try:
        proc = subprocess.run(
            ["adb", "-s", serial, "push", source, target],
            capture_output=True, text=True, timeout=30,
        )
        result["status"] = "ok" if proc.returncode == 0 else "push_failed"
        result["returncode"] = proc.returncode
        if proc.stderr.strip():
            result["stderr"] = proc.stderr.strip()[:200]
        # 2026-07-26: touch the file to now so SAF's "Recent" / "This week"
        # categories show it. Otherwise apps like documenter can't find the
        # fixture through the SAF picker's default view when the source file's
        # mtime is old (e.g. sample.pdf is from 2025-08-08 in the repo).
        # ALSO trigger MediaStore scan: files pushed via `adb push` are on the
        # filesystem but NOT indexed in MediaStore, which SAF picker relies on
        # to enumerate documents. Without this, sample.pdf never appears in
        # documenter's SAF picker on Android 10+.
        if result["status"] == "ok":
            subprocess.run(
                ["adb", "-s", serial, "shell", "touch", target],
                capture_output=True, text=True, timeout=10,
            )
            # MediaStore rescan for scoped-storage visibility (Android 10+).
            subprocess.run(
                ["adb", "-s", serial, "shell", "am", "broadcast",
                 "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                 "-d", "file://" + target],
                capture_output=True, text=True, timeout=15,
            )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
    except Exception as ex:
        result["status"] = "exception"
        result["error"] = "%s: %s" % (type(ex).__name__, ex)
    return result


def _resolve_serial(device) -> Optional[str]:
    """Pull the wifi-ADB / USB-ADB id from the pyand Device object."""
    for attr in ("id", "device_id", "serial"):
        s = getattr(device, attr, None)
        if isinstance(s, str) and s:
            return s
    # Fallback: ask the device.
    try:
        s = device.shell("getprop ro.serialno")
        return (s or "").strip() or None
    except Exception:
        return None


def sync_fixtures(device) -> Dict[str, Any]:
    """Push every entry in FIXTURE_CATALOG to the phone. Returns a snapshot
    suitable for inclusion in the per-experiment device_state.json.
    """
    serial = _resolve_serial(device)
    _log_stdout("before_experiment_sync_fixtures: starting on %s" % serial)

    if not serial:
        _log_stderr("  cannot resolve device serial; skipping fixture sync")
        return {"status": "no_serial", "results": []}

    results: List[Dict[str, Any]] = []
    for source_relpath, target, desc, owner in FIXTURE_CATALOG:
        source = op.join(_REPO_ROOT, source_relpath)
        push_result = _run_adb_push(serial, source, target)
        push_result["owner"] = owner
        push_result["description"] = desc
        results.append(push_result)
        status = push_result.get("status", "?")
        _log_stdout("  %s (%s) -> %s : %s" % (op.basename(source), owner,
                                              target, status))

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    missing = [r for r in results if r.get("status") == "source_missing"]
    if missing:
        _log_stderr("  WARNING: %d fixture source files missing on laptop: %s"
                    % (len(missing), ", ".join(r["source"] for r in missing)))

    snapshot = {
        "serial": serial,
        "results": results,
        "ok_count": ok_count,
        "total": len(results),
    }
    _log_stdout("  pushed %d/%d fixtures" % (ok_count, len(results)))
    return snapshot


def main(device, *args, **kwargs):
    """Module entrypoint for direct AndroidRunner ``before_experiment`` wiring,
    or for chaining from before_experiment.py / per-app uninstall hooks.
    """
    try:
        sync_fixtures(device)
    except Exception as ex:
        _log_stderr("before_experiment_sync_fixtures.main: unexpected exception "
                    "(swallowed; experiment continues): %s: %s"
                    % (type(ex).__name__, ex))
