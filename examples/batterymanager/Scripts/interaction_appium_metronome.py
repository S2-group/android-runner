# noinspection PyUnusedLocal
"""
Thin Android Runner ``interaction`` hook for Kr0oked Metronome.

The actual Appium / UiAutomator2 logic lives in the workspace package
``appium_android_tests.metronome`` (per-app scenarios + selectors) on top of the shared library
``appium_android_tests._lib`` (driver lifecycle, scoring, coverage, timing, status). See
``appium_android_tests/CONVENTIONS.md`` for the folder layout and the contract expected of each
per-app module.

Workload selection, env-var contract, and produced artifacts (``appium_workload_coverage.jsonl``,
``espresso_mirror_scenario_report.{json,txt}``, ``appium_status.json``) are documented in
``appium_android_tests/metronome/scenarios.py``. Set ``"interaction_covers_duration": true`` in the
experiment JSON so ``NativeExperiment`` does not double-sleep this run.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

# Tag this run as the metronome app so after_experiment.py's tracking-matrix
# updater records `app=metronome` reliably (instead of relying on it being
# the legacy default — which was the source of the 2026-05-12 misclassification
# bug for non-Metronome runs). Uses setdefault so an explicit caller-set
# APPIUM_APP still wins.
os.environ.setdefault("APPIUM_APP", "metronome")


def main(device, *args, **kwargs):
    from appium_android_tests import metronome

    experiment = args[0] if len(args) >= 1 else None
    metronome.run_workload(experiment=experiment, device=device)
