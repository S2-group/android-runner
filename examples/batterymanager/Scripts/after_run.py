import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# Original per-phone `stay_on_while_plugged_in`, read off the devices before any
# of this was touched (2026-08-07). Keyed by AR's device.id, which devices.json
# defines as the WiFi-ADB address.
_STAY_ON_ORIGINAL = {
    '10.15.10.204:5555': '7',    # pixel3
    '10.15.10.205:5555': '15',   # pixel6
    '10.15.10.93:5555':  '15',   # pixel9
}


def _restore_display_stay_on(device):
    """Put `stay_on_while_plugged_in` back to its pre-2026-08-07 value.

    HISTORY — a change that was tried, measured, and REVERTED.

    The idea was to charge with the display asleep: `stay_on_while_plugged_in`
    was 7/15, pinning the screen on for the whole time the port is powered, and
    a top-up is the dominant cost in the sweep's duty cycle. It looked like free
    throughput.

    It is not supported by the data. Measured 39% -> 70% top-ups:

        pixel3   screen ON : 5270 s, 2439 s     screen OFF : 2606 s
        pixel6   screen ON : 6310 s             screen OFF : 5311 s

    pixel3's two SCREEN-ON runs differ by 2.2x under identical conditions, which
    is far more spread than the effect being looked for — pixel6's apparent 16%
    gain is not distinguishable from that noise at n=1. The original 5270/6310/
    7539 s figures were also taken while the fuel gauges were still relearning
    after the packs ran flat, so they never were a valid baseline.

    Against an unproven benefit sits a real cost: the runner's 60 s SETTLE runs
    BEFORE `before_run` wakes the screen, so sleeping here means cells equilibrate
    dark and wake seconds before profiling, where 488 earlier cells equilibrated
    lit. Randomised order keeps that from biasing dE, but it is a step change in
    conditions mid-experiment for no measured gain.

    Doing it properly needs the sweep runner: sleep the display only for the
    duration of an actual top-up, and wake it BEFORE the settle window. That is
    queued in scratchpad/PENDING_AT_NEXT_RESTART.md.

    Restoring here rather than by hand because a phone mid-cell must not be poked
    over adb; this runs after `stop_profiling`, so it costs the measurement
    nothing and each phone self-heals on its next cell.
    """
    want = _STAY_ON_ORIGINAL.get(getattr(device, 'id', None))
    if want is None:
        return
    current = device.shell('settings get global stay_on_while_plugged_in')
    if current is not None and str(current).strip() == want:
        return  # already correct — don't spend an adb round-trip every cell
    device.shell('settings put global stay_on_while_plugged_in {}'.format(want))


# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    device.shell('am force-stop com.example.batterymanager_utility')

    try:
        import detect_crash_anr
        detect_crash_anr.main(device, *args, **kwargs)
    except Exception as exc:
        try:
            sys.stderr.write(
                "after_run: detect_crash_anr hook failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # Undo the 2026-08-07 screen-off-while-charging experiment. Wrapped because a
    # hook must never crash the experiment.
    try:
        _restore_display_stay_on(device)
    except Exception as exc:  # noqa: BLE001
        try:
            sys.stderr.write(
                "after_run: stay_on restore failed (continuing): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    # E1.5.T5 — `aux_postprocess` USED to be chained here, but the built-in
    # `android` profiler's CSV is only written during `aggregate_subject`,
    # which fires AFTER `after_run` returns (empirically ~12 s later in the
    # 2026-05-08T19:30 run). Calling the post-processor here read no CSV and
    # wrote no aux_summary.json. Moved to `after_experiment.py` where every
    # per-subject CSV is on disk by the time we walk the data tree.
