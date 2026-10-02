# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for gujjwal00 AVNC.

Actual Appium / UiAutomator2 logic lives in
``appium_android_tests.avnc`` (per-app scenarios + selectors) on top of the shared library
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

# Tag this run as the avnc app for tracking-matrix purposes.
os.environ.setdefault("APPIUM_APP", "avnc")


def main(device, *args, **kwargs):
    # AVNC debug builds ship LeakCanary, whose LeakLauncherActivity is ALSO marked
    # LAUNCHER for pkg com.gaurav.avnc.debug. `monkey -p pkg 1` (AR's cold-start)
    # then resolves to Android's ResolverActivity ("Open with...") instead of
    # AVNC's StartupActivity, and every scenario strict-fails on missing UI.
    # Disable the LeakCanary launcher and explicitly foreground StartupActivity.
    for cmd in (
        "pm disable-user com.gaurav.avnc.debug/leakcanary.internal.activity.LeakLauncherActivity",
        "am start -W -n com.gaurav.avnc.debug/com.gaurav.avnc.StartupActivity "
        "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER "
        "--activity-clear-task",
    ):
        try:
            device.shell(cmd)
        except Exception as exc:
            try:
                sys.stderr.write(
                    "interaction_appium_avnc: pre-launch fixup failed (continuing): "
                    "{}: {}\n".format(type(exc).__name__, exc)
                )
            except Exception:
                pass

    from appium_android_tests import avnc

    experiment = args[0] if len(args) >= 1 else None
    avnc.run_workload(experiment=experiment, device=device)
