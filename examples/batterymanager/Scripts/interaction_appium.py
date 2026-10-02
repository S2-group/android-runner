"""Generic Android Runner ``interaction`` hook for Appium black-box workloads.
Reads ``APPIUM_APP=<app_id>`` (preferred) or ``APPIUM_WORKLOAD`` (legacy single-token
fallback) and dispatches to ``appium_android_tests.<app_id>.run_workload(experiment,
device)``. Failures write ``appium_status.json`` with an explicit ``failure_reason`` and
return instead of raising. See ``README-appium-hooks.md`` for env vars + troubleshooting.
Stdlib + ``appium_android_tests._lib.status`` only — no top-level ``import appium``.
"""
from __future__ import annotations

import importlib
import json
import os
import os.path as op
import sys
import traceback

_HERE = op.dirname(op.abspath(__file__))
_FALLBACK_ROOT = "/home/irena/Documents/Master Experiment"
_RESERVED_WORKLOAD_NAMES = ("espresso_mirror", "generic")

def _find_workspace_root():
    cur = _HERE
    for _ in range(8):
        if op.isfile(op.join(cur, "appium_android_tests", "__init__.py")):
            return cur
        parent = op.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if op.isfile(op.join(_FALLBACK_ROOT, "appium_android_tests", "__init__.py")):
        return _FALLBACK_ROOT
    return None

_WORKSPACE_ROOT = _find_workspace_root()
if _WORKSPACE_ROOT and _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

def _artifact_dir():
    try:
        import paths as ar  # type: ignore
        od = getattr(ar, "OUTPUT_DIR", None)
        if od:
            return od
    except Exception:
        pass
    o = os.environ.get("APPIUM_UI_DUMP_DIR", "").strip()
    if o:
        base = o.rstrip(os.sep)
        return op.dirname(base) if base.endswith("appium_ui_dumps") else o
    return os.getcwd()

def _bail(reason):
    try:
        sys.stderr.write("INTERACTION_APPIUM: %s\n" % reason); sys.stderr.flush()
    except Exception:
        pass
    payload = {
        "session_created": False, "session_start_seconds": None,
        "workload_started": False, "steps_executed": 0,
        "session_connect_timeout_seconds": None,
        "workload_mode": os.environ.get("APPIUM_WORKLOAD", "").strip(),
        "failure_reason": reason, "session_timeout": False,
    }
    ad = _artifact_dir()
    try:
        from appium_android_tests._lib.status import write_status_raw
        write_status_raw(ad, payload); return
    except Exception:
        pass
    try:
        os.makedirs(ad, exist_ok=True)
        with open(op.join(ad, "appium_status.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True); f.write("\n")
    except Exception:
        pass

def main(device, *args, **kwargs):
    experiment = args[0] if len(args) >= 1 else None
    if _WORKSPACE_ROOT is None:
        return _bail("appium_android_tests package not on sys.path (workspace-root resolution failed from %s)" % _HERE)
    app_id = os.environ.get("APPIUM_APP", "").strip()
    if not app_id:
        legacy = os.environ.get("APPIUM_WORKLOAD", "").strip()
        if not legacy:
            return _bail("APPIUM_APP env var missing (set APPIUM_APP=<app_id> in the experiment JSON)")
        if "_" in legacy or legacy in _RESERVED_WORKLOAD_NAMES:
            return _bail("APPIUM_APP env var ambiguous: '%s' (looks like a workload-mode token, not an app id)" % legacy)
        app_id = legacy
    try:
        module = importlib.import_module("appium_android_tests." + app_id)
    except ModuleNotFoundError:
        return _bail("per-app module not found: appium_android_tests.%s" % app_id)
    run_workload = getattr(module, "run_workload", None)
    if run_workload is None:
        return _bail("per-app module has no run_workload: appium_android_tests.%s" % app_id)
    try:
        run_workload(experiment=experiment, device=device)
    except Exception as ex:
        traceback.print_exc()
        return _bail("per-app run_workload raised %s: %s" % (type(ex).__name__, ex))
