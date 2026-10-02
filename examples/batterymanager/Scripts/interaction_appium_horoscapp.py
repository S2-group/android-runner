# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for angelsoft071 HoroscApp.

Before delegating to the per-app workload, this hook **grants
``android.permission.CAMERA``** via ``pm grant`` so the palmistry-fragment CameraX preview
opens cleanly (no system permission dialog interrupts the workload). The app is guaranteed
to be installed at this point — AndroidRunner installs the APK between ``before_run`` and
``after_launch``, and ``interaction`` runs after that.

Actual Appium / UiAutomator2 logic lives in ``appium_android_tests.horoscapp``.

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

PACKAGE = "com.angelsoft.horoscapp"

# Tag this run as the horoscapp app for tracking-matrix purposes.
os.environ.setdefault("APPIUM_APP", "horoscapp")


def _grant_camera(device) -> None:
    """Pre-grant CAMERA permission. Idempotent — pm grant is a no-op if already granted."""
    try:
        out = device.shell("pm grant %s android.permission.CAMERA" % PACKAGE) or ""
        try:
            sys.stdout.write(
                "interaction_appium_horoscapp: granted CAMERA permission to %s "
                "(stdout=%r)\n" % (PACKAGE, out[:160])
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            sys.stderr.write(
                "interaction_appium_horoscapp: CAMERA grant failed (continuing — palmistry "
                "scenario may see a permission dialog): %s: %s\n" % (type(exc).__name__, exc)
            )
        except Exception:
            pass


def main(device, *args, **kwargs):
    _grant_camera(device)
    from appium_android_tests import horoscapp

    experiment = args[0] if len(args) >= 1 else None
    horoscapp.run_workload(experiment=experiment, device=device)
