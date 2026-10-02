# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for SkyD666 PodAura.

Actual Appium / UiAutomator2 logic lives in ``appium_android_tests.podaura``.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

os.environ.setdefault("APPIUM_APP", "podaura")


def main(device, *args, **kwargs):
    from appium_android_tests import podaura

    experiment = args[0] if len(args) >= 1 else None
    podaura.run_workload(experiment=experiment, device=device)
