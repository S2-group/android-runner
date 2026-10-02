# noinspection PyUnusedLocal
"""Epic 1 (Measurement Validation Gate) — S2-group CpuFactorialTest workload.

Launches ``e.www.cpufactorialtest/.MainActivity``. The MainActivity sets up
a ``Handler.postDelayed`` loop that recomputes ``BigInteger.factorial(7858)``
every N ms, where N is baked into the APK (1000 for High, 2000 for Medium,
3000 for Low). See ``Apps/CpuFactorialTest/.../MainActivity.kt`` in
https://github.com/S2-group/android-apps-benchmark.

After ``am start``, this script just sleeps for the configured duration
(default 60 s) — all the CPU work happens inside the app's Handler.

Companion: ``interaction_s2_baseline.py``.

Compared to the prior ``interaction_validation_cpu.py``:
- Replaces "8 shell busy-loops on the Linux side" with the canonical S2-group
  CPU stress app — same methodology used in published benchmarks. This is
  app-driven workload (Dalvik runtime + BigInteger arithmetic), which
  produces a different per-cycle energy fingerprint than tight ASM busy-loops.
"""

from __future__ import annotations

import os
import time


PACKAGE = "e.www.cpufactorialtest"
ACTIVITY = ".MainActivity"


def _stay_on(device):
    try:
        device.shell("svc power stayon true")
    except Exception:
        pass


def _wake(device):
    try:
        device.shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.5)
        device.shell("wm dismiss-keyguard")
        time.sleep(0.5)
    except Exception:
        pass


def _launch(device):
    """Launch the CpuFactorialTest MainActivity via am start."""
    device.shell(f"am start -n {PACKAGE}/{PACKAGE}{ACTIVITY}")
    time.sleep(1.0)


def _stop(device):
    """Force-stop the app to make sure the Handler loop terminates cleanly
    even if onPause's removeCallbacks didn't run in time."""
    try:
        device.shell(f"am force-stop {PACKAGE}")
    except Exception:
        pass


def main(device, *args, **kwargs):
    duration_seconds = int(os.environ.get("E1_VALIDATION_DURATION_SECONDS", "60"))

    _stay_on(device)
    _wake(device)
    _launch(device)

    print(f"interaction_s2_cpu_factorial: launched {PACKAGE}; "
          f"factorial loop running for {duration_seconds}s",
          flush=True)

    try:
        time.sleep(duration_seconds)
    finally:
        _stop(device)

    print("interaction_s2_cpu_factorial: app force-stopped; done", flush=True)
