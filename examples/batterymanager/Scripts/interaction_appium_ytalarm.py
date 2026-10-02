# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for turtton YtAlarm."""
from __future__ import annotations
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

os.environ.setdefault("APPIUM_APP", "ytalarm")


def main(device, *args, **kwargs):
    from appium_android_tests import ytalarm
    # Pre-grant POST_NOTIFICATIONS to YtAlarm BEFORE the workload session
    # connects. AndroidRunner's monkey-launch step (which precedes this hook)
    # may inflate the runtime permission dialog that blocks the alarm-list UI
    # underneath. We grant via ``pm grant``, then force-stop + restart the app
    # so the (now-granted) permission state takes effect and the dialog
    # disappears on re-launch.
    try:
        import subprocess
        subprocess.run(
            ["adb", "-s", device.id, "shell", "pm", "grant",
             "net.turtton.ytalarm", "android.permission.POST_NOTIFICATIONS"],
            check=False, timeout=4,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Force-stop to clear any blocking dialog (the perm prompt remains
        # rendered until the requesting Activity is destroyed).
        subprocess.run(
            ["adb", "-s", device.id, "shell", "am", "force-stop",
             "net.turtton.ytalarm"],
            check=False, timeout=4,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Re-launch MainActivity cleanly.
        subprocess.run(
            ["adb", "-s", device.id, "shell", "am", "start", "-W", "-n",
             "net.turtton.ytalarm/net.turtton.ytalarm.activity.MainActivity"],
            check=False, timeout=8,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time as _t
        _t.sleep(1.5)
    except Exception:
        pass
    experiment = args[0] if len(args) >= 1 else None
    ytalarm.run_workload(experiment=experiment, device=device)
