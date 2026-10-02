# noinspection PyUnusedLocal
"""Epic 1 (Measurement Validation Gate) — S2-group Baseline workload.

Launches ``e.www.baseline/.MainActivity`` and holds it foregrounded for the
configured duration. The Baseline app's MainActivity does ``setContentView``
and nothing else (see ``Apps/Baseline/.../MainActivity.kt`` in
https://github.com/S2-group/android-apps-benchmark) — it consumes only the
power the phone draws to keep its screen lit and the Activity resident.

Companion: ``interaction_s2_cpu_factorial.py`` (the CPU-stress half).

Compared to the prior ``interaction_validation_idle.py``:
- Replaces "Calculator placeholder + 60 s sleep" with the canonical S2-group
  baseline app — same methodology used in published benchmarks.
- The Baseline APK has no workload in onCreate, so the energy reading
  reflects "phone with screen on + a foreground Activity" alone.

S2-group recommended phone-side settings (apply manually):
- Disable WiFi → NOT done here (we use WiFi-ADB, so radio stays up).
  Document the WiFi-radio overhead separately if absolute idle is needed
  on this machine.
- Set screen brightness to minimum.
- Disable location services.
"""

from __future__ import annotations

import os
import time


PACKAGE = "e.www.baseline"
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
    """Launch the Baseline MainActivity via am start."""
    device.shell(f"am start -n {PACKAGE}/{PACKAGE}{ACTIVITY}")
    time.sleep(1.0)


def main(device, *args, **kwargs):
    duration_seconds = int(os.environ.get("E1_VALIDATION_DURATION_SECONDS", "60"))

    _stay_on(device)
    _wake(device)
    _launch(device)

    print(f"interaction_s2_baseline: launched {PACKAGE}; idling for {duration_seconds}s",
          flush=True)

    time.sleep(duration_seconds)

    print("interaction_s2_baseline: done", flush=True)
