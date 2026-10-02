# noinspection PyUnusedLocal
"""Epic 1 (Measurement Validation Gate) — IDLE workload.

The "idle" half of the idle-vs-CPU measurement validation gate. Used to
establish a per-device idle-baseline energy reading so the CPU-stress
run has something to compare against.

This is NOT an Appium-driven scenario. The device is held in the
"installed Calculator + BatteryManager-utility foregrounded + screen on
+ no input" state for the full ``duration`` and the BatteryManager
profiler samples ``current_now`` / ``voltage_now`` every 100 ms.

Created 2026-05-19 evening after the hub adapter failed (Plugable hub
underpowered by a 12V/500mA router adapter that should have been 12V/4A).
With the hub down, Pixel 9 is already in WiFi-ADB-only / physical
USB-disconnected / battery-discharging state, which is exactly the state
Epic 1.6 was designed to *create* programmatically. Running the
idle-vs-CPU validation now produces credible absolute energy numbers
without the hub.

Companion: ``interaction_validation_cpu.py`` (the CPU-stress half).
"""

from __future__ import annotations

import os
import sys
import time


_HERE = os.path.dirname(os.path.abspath(__file__))


def _stay_on(device):
    """Keep the screen on for the duration of the run."""
    try:
        device.shell("svc power stayon true")
    except Exception:
        pass


def _wake(device):
    """Make sure the screen is unlocked + awake."""
    try:
        device.shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.5)
        device.shell("wm dismiss-keyguard")
        time.sleep(0.5)
    except Exception:
        pass


def main(device, *args, **kwargs):
    duration_seconds = int(os.environ.get("E1_VALIDATION_DURATION_SECONDS", "60"))

    _stay_on(device)
    _wake(device)

    print(f"interaction_validation_idle: idling for {duration_seconds}s "
          f"(no input, no compute; BatteryManager polling underneath)",
          flush=True)

    time.sleep(duration_seconds)

    print("interaction_validation_idle: done", flush=True)
