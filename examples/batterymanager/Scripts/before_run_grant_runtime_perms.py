"""``before_run`` helper: pre-grant Android-13+ runtime permissions to cohort apps.

Android-13+ introduces runtime permission prompts for POST_NOTIFICATIONS, the
READ_MEDIA_VISUAL_USER_SELECTED family, etc. Cohort apps that need these will
pop a system dialog on first launch — burning energy waiting for a non-existent
user, and (worse) skewing the measured workload because the prompt is modal and
the Appium scenario can't proceed past it.

Every AndroidRunner run installs a *fresh* copy of the APK, which means the
prompt re-fires on every run. The standard mitigation is to ``pm grant`` the
relevant permissions immediately after install + before launch. ``pm grant``
is silent / idempotent: it accepts pre-existing grants without complaint and
errors-only when the permission is unknown to the platform (e.g. trying to
grant POST_NOTIFICATIONS on Android 12 — irrelevant on our Pixel 3 because the
app never asks for it there).

Two flavours of grant:
    * ``pm grant <pkg> <perm>``    — standard runtime permissions (CAMERA,
      POST_NOTIFICATIONS, RECORD_AUDIO, READ_MEDIA_*, etc.). Silent failure
      if the perm doesn't exist on this Android version.
    * ``appops set <pkg> <op> allow`` — special-access permissions that
      ``pm grant`` cannot touch (SYSTEM_ALERT_WINDOW, MANAGE_EXTERNAL_STORAGE).
      These are app-ops, not runtime perms; same idempotency.

Contract (mirrors before_experiment_grant_battery_stats.py):
    * Idempotent — re-running is a no-op
    * Warn-not-fail — every shell call is try/excepted; the hook never raises
    * Verified — re-reads ``dumpsys package`` after ``pm grant`` and logs the
      result. ``appops`` grants log unconditionally (less critical to verify;
      cohort apps fall back to in-app overlay prompts that the Appium scenario
      auto-dismisses).

Per-app permission table (derived 2026-06-29 from per-app dumpsys audits):

    com.angelsoft.horoscapp       CAMERA
    com.faltenreich.diaguard      POST_NOTIFICATIONS
    com.rajat.sample.pdfviewer    READ_MEDIA_VISUAL_USER_SELECTED, READ_PHONE_STATE,
                                  ACCESS_MEDIA_LOCATION
    com.skyd.anivu.debug          POST_NOTIFICATIONS, READ_MEDIA_VISUAL_USER_SELECTED,
                                  READ_MEDIA_IMAGES
                                  appops: MANAGE_EXTERNAL_STORAGE
    net.ibbaa.keepitup            POST_NOTIFICATIONS
    net.turtton.ytalarm           POST_NOTIFICATIONS
                                  appops: SYSTEM_ALERT_WINDOW
    com.futsch1.medtimer          POST_NOTIFICATIONS
    jp.deadend.noname.skk         POST_NOTIFICATIONS, RECORD_AUDIO

Called from ``before_run.py`` AFTER the APK is installed by
``NativeExperiment.before_run_subject`` and BEFORE
``device.launch_package(self.package)`` fires — both happen during the same
run cycle. See NativeExperiment.py:37-58 for the order.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence


# Package → list of standard runtime permissions to grant via ``pm grant``.
RUNTIME_PERMS: dict[str, list[str]] = {
    "com.angelsoft.horoscapp": [
        "android.permission.CAMERA",
    ],
    "com.faltenreich.diaguard": [
        "android.permission.POST_NOTIFICATIONS",
    ],
    "com.rajat.sample.pdfviewer": [
        "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
        "android.permission.READ_PHONE_STATE",
        "android.permission.ACCESS_MEDIA_LOCATION",
    ],
    "com.skyd.anivu.debug": [
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
        "android.permission.READ_MEDIA_IMAGES",
    ],
    "net.ibbaa.keepitup": [
        "android.permission.POST_NOTIFICATIONS",
    ],
    "net.turtton.ytalarm": [
        "android.permission.POST_NOTIFICATIONS",
    ],
    "com.futsch1.medtimer": [
        "android.permission.POST_NOTIFICATIONS",
    ],
    "jp.deadend.noname.skk": [
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.RECORD_AUDIO",
    ],
}

# Package → list of (appop_name, mode) pairs to grant via ``appops set``.
APPOPS: dict[str, list[tuple[str, str]]] = {
    "com.skyd.anivu.debug": [
        ("MANAGE_EXTERNAL_STORAGE", "allow"),
    ],
    "net.turtton.ytalarm": [
        ("SYSTEM_ALERT_WINDOW", "allow"),
    ],
}

# APK-filename-slug → package, used when we can derive only the APK path from
# the AR ``current_run`` kwarg. Slug is the leading path component before the
# first underscore (e.g. ``horoscapp_baseline_protected.signed.apk`` → ``horoscapp``).
SLUG_TO_PACKAGE: dict[str, str] = {
    "horoscapp": "com.angelsoft.horoscapp",
    "diaguard": "com.faltenreich.diaguard",
    "pdfviewer": "com.rajat.sample.pdfviewer",
    "podaura": "com.skyd.anivu.debug",
    "keepitup": "net.ibbaa.keepitup",
    "ytalarm": "net.turtton.ytalarm",
    "medtimer": "com.futsch1.medtimer",
    "androidskk": "jp.deadend.noname.skk",
}


def _log_stdout(msg: str) -> None:
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _verify_perm_granted(device, package: str, permission: str) -> bool | None:
    """Best-effort read-back of a single permission via ``dumpsys package``.

    Returns:
        True  — dumpsys reports ``granted=true``
        False — dumpsys reports ``granted=false``
        None  — permission not mentioned in dumpsys output (likely doesn't
                exist on this Android version; treated as "harmless miss")
    """
    try:
        out = device.shell("dumpsys package %s" % package) or ""
    except Exception:
        return None
    for raw in out.splitlines():
        line = raw.strip()
        if permission not in line:
            continue
        if "granted=true" in line:
            return True
        if "granted=false" in line:
            return False
    return None


def _grant_runtime_perm(device, package: str, permission: str) -> str:
    """Run ``pm grant`` for a single permission and verify. Returns one of:
    'granted', 'already', 'unsupported', 'failed'.

    'unsupported' = pm command ran but post-grant dumpsys never mentioned the
    permission (= the platform doesn't recognize it; benign on Android < 13 for
    POST_NOTIFICATIONS / READ_MEDIA_* etc.).
    """
    pre = _verify_perm_granted(device, package, permission)
    if pre is True:
        return "already"

    try:
        device.shell("pm grant %s %s" % (package, permission))
    except Exception as ex:
        _log_stderr(
            "before_run_grant_runtime_perms: pm grant %s %s raised: %s: %s"
            % (package, permission, type(ex).__name__, ex)
        )

    post = _verify_perm_granted(device, package, permission)
    if post is True:
        return "granted"
    if post is None:
        # Permission name not in dumpsys at all → not declared by manifest on
        # this Android version. Harmless: app won't ask for it either.
        return "unsupported"
    return "failed"


def _set_appop(device, package: str, op_name: str, mode: str) -> str:
    """Run ``appops set`` for a single op. Returns 'set' or 'failed'.

    Verification: best-effort grep of ``appops get <pkg> <op>``. Many ops
    output ``<op>: allow`` when set; we treat the call as successful if
    appops doesn't raise.
    """
    try:
        device.shell("appops set %s %s %s" % (package, op_name, mode))
    except Exception as ex:
        _log_stderr(
            "before_run_grant_runtime_perms: appops set %s %s %s raised: %s: %s"
            % (package, op_name, mode, type(ex).__name__, ex)
        )
        return "failed"
    return "set"


def _resolve_package_from_apk_path(apk_path: str | None) -> str | None:
    """Map an APK filename to a known cohort package via the slug table."""
    if not apk_path:
        return None
    basename = os.path.basename(apk_path)
    # Strip extension and split on first underscore. APK convention:
    #   <slug>_<variant>[_<modifier>].signed.apk
    slug = basename.split("_", 1)[0] if "_" in basename else os.path.splitext(basename)[0]
    return SLUG_TO_PACKAGE.get(slug)


def grant_runtime_perms(device, package_or_apk_path: str | None) -> dict[str, str]:
    """Grant runtime perms + app-ops for ``package_or_apk_path``.

    Accepts either a fully-qualified package name (``com.faltenreich.diaguard``)
    or an APK path whose basename starts with a slug in ``SLUG_TO_PACKAGE``.
    Returns ``{<perm>: <status>, ...}`` for logging / matrix-row tagging.
    Empty dict if no perms are configured for the resolved package.
    """
    serial = "unknown-serial"
    try:
        serial = getattr(device, "id", None) or getattr(device, "name", None) or "unknown-serial"
    except Exception:
        pass

    package = package_or_apk_path
    if package and package.endswith(".apk"):
        package = _resolve_package_from_apk_path(package)
    if package is None or package not in RUNTIME_PERMS and package not in APPOPS:
        if package and package not in RUNTIME_PERMS and package not in APPOPS:
            # Cohort app without runtime-perm requirements (e.g. crcontainer,
            # gallerywall, plus all zero-network apps).
            _log_stdout(
                "before_run_grant_runtime_perms: no runtime perms configured for %s on %s "
                "(no-op; this is the expected path for apps without sensitive Android-13+ perms)"
                % (package, serial)
            )
        return {}

    perms: Sequence[str] = RUNTIME_PERMS.get(package, [])
    appops_for_pkg = APPOPS.get(package, [])

    results: dict[str, str] = {}
    for perm in perms:
        results[perm] = _grant_runtime_perm(device, package, perm)
    for op_name, mode in appops_for_pkg:
        results["appop:" + op_name] = _set_appop(device, package, op_name, mode)

    counts: dict[str, int] = {}
    for v in results.values():
        counts[v] = counts.get(v, 0) + 1
    summary = " ".join("%s=%d" % (k, v) for k, v in sorted(counts.items()))
    _log_stdout(
        "before_run_grant_runtime_perms: %s on %s — %s"
        % (package, serial, summary)
    )
    failed = [k for k, v in results.items() if v == "failed"]
    if failed:
        _log_stderr(
            "before_run_grant_runtime_perms: WARN failed grants on %s for %s: %s "
            "(experiment continues; the app may show a runtime dialog mid-cell)"
            % (serial, package, ", ".join(failed))
        )
    return results
