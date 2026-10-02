# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for exponentialGroth Calculator.

Actual Appium / UiAutomator2 logic lives in :mod:`appium_android_tests.calculator`
(per-app scenarios + selectors) on top of the shared library
:mod:`appium_android_tests._lib`.

Set ``"interaction_covers_duration": true`` in the experiment JSON so ``NativeExperiment``
does not double-sleep this run.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

# Tag this run as the calculator app for tracking-matrix purposes.
os.environ.setdefault("APPIUM_APP", "calculator")


def main(device, *args, **kwargs):
    from appium_android_tests import calculator

    experiment = args[0] if len(args) >= 1 else None
    calculator.run_workload(experiment=experiment, device=device)
