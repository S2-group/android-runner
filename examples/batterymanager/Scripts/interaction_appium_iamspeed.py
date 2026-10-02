# noinspection PyUnusedLocal
"""Thin AndroidRunner ``interaction`` hook for ViliusSutkus89 IamSpeed.

Pre-grants location + notification runtime permissions so the workload starts in a known
state without system permission dialogs interrupting scenarios.

**Critical:** GPS must NOT be enabled before launch. On Android 14+ the app's
``SpeedListenerService`` requires the ``FOREGROUND_SERVICE_LOCATION`` permission to start
a location-typed foreground service — but ``IamSpeed v1.2.5``'s manifest doesn't declare
it. When permission + location + GPS are all on, ``IamSpeedFragment.serviceCanBeStartedOnStartup``
auto-launches the service which then crashes with
``SecurityException: Starting FGS with type location ... requires permissions
FOREGROUND_SERVICE_LOCATION`` (kills the app process, system shows "Close"
crash dialog). By keeping GPS off at launch, the fragment shows ``button_enable_gps``
and the service auto-start never fires. Scenarios still exercise the bytecode-heavy
paths (Navigation Component fragment transitions, action-bar menu items, AppCompat
preference inflation, system-settings round-trip via ``button_enable_location``).

Actual Appium logic lives in :mod:`appium_android_tests.iamspeed`.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

PACKAGE = "com.viliussutkus89.iamspeed.debug"

# Tag this run as the iamspeed app for tracking-matrix purposes.
os.environ.setdefault("APPIUM_APP", "iamspeed")


def _grant_runtime_perms(device) -> None:
    """Pre-grant location + notification permissions. Idempotent."""
    perms = [
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.POST_NOTIFICATIONS",
    ]
    for p in perms:
        try:
            out = device.shell("pm grant %s %s" % (PACKAGE, p)) or ""
            try:
                sys.stdout.write(
                    "interaction_appium_iamspeed: granted %s to %s (stdout=%r)\n"
                    % (p, PACKAGE, out[:120])
                )
            except Exception:
                pass
        except Exception as exc:
            try:
                sys.stderr.write(
                    "interaction_appium_iamspeed: grant %s failed (continuing): "
                    "%s: %s\n" % (p, type(exc).__name__, exc)
                )
            except Exception:
                pass


def _disable_gps(device) -> None:
    """Force GPS off so IamSpeed's auto-start of the location-typed foreground service never
    fires (the v1.2.5 manifest is missing FOREGROUND_SERVICE_LOCATION → fatal SecurityException
    on Android 14+). Non-fatal on failure; the scenarios still pass action-only if GPS is on.
    """
    try:
        device.shell("settings put secure location_mode 0")  # 0 = OFF
    except Exception:
        pass


def main(device, *args, **kwargs):
    _grant_runtime_perms(device)
    _disable_gps(device)
    from appium_android_tests import iamspeed
    experiment = args[0] if len(args) >= 1 else None
    iamspeed.run_workload(experiment=experiment, device=device)
