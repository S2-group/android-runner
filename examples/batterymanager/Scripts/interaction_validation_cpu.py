# noinspection PyUnusedLocal
"""Epic 1 (Measurement Validation Gate) — CPU-STRESS workload.

The "CPU" half of the idle-vs-CPU measurement validation gate. Spawns
N parallel busy-loop processes via ``adb shell`` for the full ``duration``,
then kills them. The BatteryManager profiler samples ``current_now`` /
``voltage_now`` every 100 ms throughout; expected behaviour is that
``current_now`` becomes substantially more negative (deeper discharge)
than during the idle run.

N defaults to ``nproc`` on the device (pegs all cores). Override via
``E1_VALIDATION_CPU_THREADS`` env var if you want a partial-load test.

Validation gate (post-run): ``cpu_total_mWh / idle_total_mWh >= 3.0``
(threshold per S2-group methodology; gives a safety margin against
short-window jitter).

See ``interaction_validation_idle.py`` for the idle half.
"""

from __future__ import annotations

import os
import sys
import time


_HERE = os.path.dirname(os.path.abspath(__file__))


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


def _detect_cpu_count(device):
    """Read ``nproc`` from the device; default to 8 if anything goes wrong."""
    try:
        out = device.shell("nproc").strip()
        n = int(out)
        if 1 <= n <= 32:
            return n
    except Exception:
        pass
    return 8


def _spawn_stress(device, n_threads):
    """Spawn ``n_threads`` busy-loop processes in the background via adb shell.

    Each process: ``sh -c "while true; do :; done"``. The ``:`` no-op runs
    as tight as possible; on aarch64 this saturates one core at ~100%.
    """
    cmd = (
        f"for i in $(seq 1 {n_threads}); do "
        "(nohup sh -c 'while true; do :; done' > /dev/null 2>&1 &) "
        "done"
    )
    device.shell(cmd)


def _kill_stress(device):
    """Kill all busy-loop processes spawned in ``_spawn_stress``."""
    try:
        device.shell("pkill -f 'while true; do :; done' || true")
        time.sleep(0.5)
    except Exception:
        pass


def main(device, *args, **kwargs):
    duration_seconds = int(os.environ.get("E1_VALIDATION_DURATION_SECONDS", "60"))
    n_threads = int(os.environ.get("E1_VALIDATION_CPU_THREADS", "0")) \
        or _detect_cpu_count(device)

    _stay_on(device)
    _wake(device)

    print(f"interaction_validation_cpu: spawning {n_threads} busy-loop "
          f"processes for {duration_seconds}s", flush=True)

    _spawn_stress(device, n_threads)
    try:
        time.sleep(duration_seconds)
    finally:
        _kill_stress(device)

    print("interaction_validation_cpu: stress threads killed; done", flush=True)
