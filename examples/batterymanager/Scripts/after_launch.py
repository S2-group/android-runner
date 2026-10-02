# noinspection PyUnusedLocal,PyUnusedLocal
"""``after_launch`` hook — last step before Profilers start.

Original responsibility (preserved): debug-print the args AndroidRunner passes.

Added 2026-05-15: **aggressive auto-dismiss for the Android 16 ``PageSizeMismatchDialog``**
("App isn't 16 KB compatible"). This dialog appears for:

1. The BatteryManager Utility companion (``com.example.batterymanager_utility``) — fires once
   per process launch when AndroidRunner starts the utility for energy sampling.

2. The subject-app's MainActivity — fires every time monkey starts a freshly-installed
   debug-built app whose native libs aren't 16-KB-aligned. **Every AndroidRunner run does a
   fresh install**, so the "Don't Show Again" persistent flag is reset each time — the
   dialog WILL re-fire on every run unless we dismiss it after each install.

The dismiss is **polling-based** (not one-shot) because:
- monkey returns before the activity is fully resumed, so the dialog can appear up to ~3s
  after monkey "returns success"
- The dialog can fire AGAIN if the system reloads the activity stack (Compose / CameraX
  cold-init can race with the warning system)

We poll for up to 10 seconds. Each iteration: uiautomator dump → look for "Don't Show
Again" → tap if found. Loop continues until either: (a) we've dismissed once AND the
dialog isn't visible for one consecutive iteration, or (b) the 10s budget expires.

Best-effort: failures are logged + swallowed (this hook must never crash the run). The
per-app Appium harness also dismisses inside its session as a defense-in-depth layer.

Full background: ``docs/BANGCLE_PIXEL9_ABI_COMPATIBILITY.md`` § "Adjacent finding —
Android 16 PageSizeMismatchDialog".
"""

import re
import sys
import threading
import time


_DSA_PATTERN = re.compile(
    r'<node[^>]*text="Don\'t Show Again"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
)

# Fallback: a generic "Android App Compatibility" dialog with only "OK" button.
# Observed 2026-06-29 on P6 (Android 16) for the calculator APK — same dialog
# class as PageSizeMismatch but a different platform build labels the dismiss
# button as just "OK" (text=) instead of "Don't Show Again" + "OK" both. We
# scope this match to dumps that ALSO contain the "Android App Compatibility"
# title to avoid clicking arbitrary OK buttons elsewhere in the UI.
_COMPAT_DIALOG_TITLE = "Android App Compatibility"
_OK_BUTTON_PATTERN = re.compile(
    r'<node[^>]*text="OK"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
)


def _dump_and_extract_dsa_bounds(device) -> tuple[int, int, int, int] | None:
    """Dump the UI and extract a dismiss-button's bounds if a compat dialog is visible.

    Returns (x1,y1,x2,y2) or None. Best-effort — swallows all exceptions.

    Selection priority (matches whichever the platform actually shows):
      1. "Don't Show Again" button if present (permanently silences the dialog
         for this install) — preferred because it short-circuits re-fires.
      2. "OK" button inside the "Android App Compatibility" dialog (some
         Android-16 platform builds, e.g. Pixel 6 dewdrop, show only OK).
    """
    dump_path = "/sdcard/after_launch_uidump.xml"
    xml = ""
    try:
        device.shell("uiautomator dump %s" % dump_path)
    except Exception as exc:
        # AndroidRunner's wrapper can raise AdbError on uiautomator's "UI hierchary dumped"
        # stderr noise — the file is still written. Stash exception body too in case it
        # contains the XML.
        xml = str(exc) or xml
    try:
        out = device.shell("cat %s" % dump_path) or ""
        xml = out if out else xml
    except Exception as exc:
        # Large XML may be wrapped into an AdbError on large stdout — extract from msg.
        xml = str(exc) or xml
    # Preferred: tap "Don't Show Again" if present (kills future re-fires)
    if "Don't Show Again" in xml:
        m = _DSA_PATTERN.search(xml)
        if m is not None:
            x1, y1, x2, y2 = map(int, m.groups())
            return x1, y1, x2, y2
    # Fallback: tap "OK" inside an "Android App Compatibility" dialog
    if _COMPAT_DIALOG_TITLE in xml:
        m = _OK_BUTTON_PATTERN.search(xml)
        if m is not None:
            x1, y1, x2, y2 = map(int, m.groups())
            return x1, y1, x2, y2
    return None


def _aggressive_dismiss_page_size_dialog(device, total_budget_s: float = 30.0) -> int:
    """Poll for the PageSize / AndroidAppCompatibility dialog for up to
    ``total_budget_s`` seconds, tapping the dismiss button whenever it appears.

    Returns the number of times the dialog was dismissed (typically 1 if
    encountered, 0 if not present).

    Budget rationale (2026-06-29 update): bumped 10s → 30s after observing on
    Pixel 6 (Android 16) that the calculator APK's compat dialog appeared
    >10 s after `launch_package` returned — we missed it and the cell ran
    blocked by the modal, contaminating the measurement. 30 s is well within
    the gap before AR's profilers actually start sampling.
    """
    dismissed = 0
    deadline = time.monotonic() + total_budget_s
    consecutive_misses = 0
    while time.monotonic() < deadline:
        bounds = None
        try:
            bounds = _dump_and_extract_dsa_bounds(device)
        except Exception:
            pass
        if bounds is None:
            consecutive_misses += 1
            # Exit early if we've successfully dismissed at least once AND the dialog has
            # been gone for 4 consecutive polls (= reliably dismissed; up from 2 to give
            # the platform time to re-fire if the dialog re-creates on Activity recreate).
            if dismissed >= 1 and consecutive_misses >= 4:
                break
            time.sleep(0.5)
            continue
        # Tap the center of whichever dismiss button _dump_and_extract_dsa_bounds picked
        # (Don't Show Again preferred, OK fallback for Android 16 compat dialog)
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        try:
            device.shell("input tap %d %d" % (cx, cy))
            dismissed += 1
            consecutive_misses = 0
            try:
                sys.stdout.write(
                    "after_launch: dismissed compat dialog "
                    "(#%d) via tap at (%d,%d)\n"
                    % (dismissed, cx, cy)
                )
            except Exception:
                pass
        except Exception as exc:
            try:
                sys.stderr.write(
                    "after_launch: input tap on compat-dialog button failed (continuing): "
                    "%s: %s\n" % (type(exc).__name__, exc)
                )
            except Exception:
                pass
        # Short settle before re-checking
        time.sleep(0.5)
    return dismissed


# Module-level guard so we never spawn more than one watcher per AR process,
# even if after_launch.main() somehow fires twice.
_WATCHER_STARTED = False


def _background_dismiss_watcher(device, duration_s: float = 600.0) -> None:
    """Daemon thread that keeps polling for the compat dialog through the
    entire cell and dismisses it whenever it re-fires.

    Why this exists (2026-06-29 night): on Pixel 6 + Pixel 9 (Android 16) the
    'Android App Compatibility' dialog can re-fire MID-WORKLOAD, not just at
    launch. Bangcle's runtime decryption triggers a delayed Activity recreate,
    which the platform answers with a fresh compat dialog after our
    after_launch foreground poll has already returned. Without this watcher,
    the dialog stays modal for the rest of the cell, blocks Appium's element
    finds, and the cell ends up CONTAM(float_mode_suspect) because no real
    workload runs.

    Implementation note: daemon=True means the thread dies automatically when
    the AR python process exits at end-of-cell — no explicit cleanup needed
    in before_close.py / after_run.py.

    Polling cadence: 3 s. Each poll runs `uiautomator dump` + `cat`, which
    coexists with Appium's UiAutomator2 server (different mechanism). 3 s is
    a balance between catching the dialog quickly and adb-overhead.
    """
    deadline = time.monotonic() + duration_s
    n_dismissed = 0
    while time.monotonic() < deadline:
        try:
            bounds = _dump_and_extract_dsa_bounds(device)
            if bounds is not None:
                x1, y1, x2, y2 = bounds
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                try:
                    device.shell("input tap %d %d" % (cx, cy))
                    n_dismissed += 1
                    try:
                        sys.stdout.write(
                            "after_launch BG watcher: dismissed compat dialog "
                            "(#%d) at (%d,%d)\n" % (n_dismissed, cx, cy)
                        )
                        sys.stdout.flush()
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            # Swallow everything — the watcher must never crash the AR run.
            pass
        time.sleep(3.0)


# noinspection PyUnusedLocal,PyUnusedLocal
def main(device, *args, **kwargs):
    print("AFTER_LAUNCH ARGS:", args)
    print("AFTER_LAUNCH KWARGS:", kwargs)

    # NOTE: We intentionally do NOT start Monkey from this hook.
    # On NativeExperiment runs, Android Runner doesn't pass the subject package here,
    # and device.current_activity() can point to the BatteryManager utility screen.
    # Monkey is started from Scripts/interaction.py where we can access experiment.package.

    # Aggressive polling dismiss for the Android 16 PageSizeMismatchDialog. Critical for
    # Bangcle-packed APKs because their libSecShell.so isn't 16-KB-aligned, and AndroidRunner's
    # per-run fresh install resets the "Don't Show Again" persistent flag every time. Without
    # this, the dialog blocks monkey from successfully foregrounding the subject app, and the
    # Android profiler errors with "No process found" when it tries to dump meminfo.
    #
    # Foreground budget cut 30 s -> 5 s (2026-06-29): the daemon watcher below
    # picks up anything that fires later (including mid-workload re-fires on
    # Bangcle's Activity recreate). Shorter foreground budget reclaims ~25 s
    # of bookkeeping per cell when the dialog is already up, and even more
    # when no dialog is shown at all.
    n_dismissed = _aggressive_dismiss_page_size_dialog(device, total_budget_s=5.0)

    # Spawn the daemon background watcher so the dialog stays handled for the
    # rest of the cell. daemon=True → dies automatically when AR process exits.
    global _WATCHER_STARTED
    if not _WATCHER_STARTED:
        try:
            t = threading.Thread(
                target=_background_dismiss_watcher,
                args=(device,),
                kwargs={"duration_s": 600.0},
                daemon=True,
                name="after_launch_dismiss_watcher",
            )
            t.start()
            _WATCHER_STARTED = True
            try:
                sys.stdout.write(
                    "after_launch: background dismiss watcher started "
                    "(polling every 3 s for 600 s)\n"
                )
            except Exception:
                pass
        except Exception as exc:
            try:
                sys.stderr.write(
                    "after_launch: failed to start BG watcher (continuing): "
                    "%s: %s\n" % (type(exc).__name__, exc)
                )
            except Exception:
                pass
    if n_dismissed > 0:
        try:
            sys.stdout.write(
                "after_launch: PageSizeMismatchDialog dismiss cycle complete "
                "(%d tap(s) fired)\n" % n_dismissed
            )
        except Exception:
            pass
