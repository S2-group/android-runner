# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for Faltenreich Diaguard.

Actual Appium / UiAutomator2 logic lives in
``appium_android_tests.diaguard`` (per-app scenarios + selectors) on top of the shared library
``appium_android_tests._lib`` (driver lifecycle, scoring, coverage, timing, status).

Set ``"interaction_covers_duration": true`` in the experiment JSON so ``NativeExperiment`` does
not double-sleep this run.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

# Tag this run as the diaguard app for tracking-matrix purposes.
os.environ.setdefault("APPIUM_APP", "diaguard")


def main(device, *args, **kwargs):
    from appium_android_tests import diaguard

    experiment = args[0] if len(args) >= 1 else None
    diaguard.run_workload(experiment=experiment, device=device)
