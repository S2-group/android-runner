"""``before_run`` helper: explicit `am start -n` to bypass LeakCanary launcher confusion.

Issue 27 fix. On debug builds that pull in LeakCanary as a dependency
(another_notes, keep_recipe, repertoire, ...), LeakCanary registers its own
launcher activity (`leakcanary.internal.activity.LeakLauncherActivity`) with
intent-filter MAIN+LAUNCHER and `android:enabled="false"`. Multiple build
systems (and pyand's `device.launch_package`) don't always filter out
disabled launchers, so the resolver may pick LeakCanary's activity instead
of the app's real main one — cell foreground becomes the LeakCanary UI
instead of the app under test, scenarios run against the wrong process,
the CRASH_ANR_DETECT line shows ``foreground=com.google.android.apps.nexuslauncher``
because LeakCanary closes itself and falls back to the Pixel launcher.

The fix: instead of trusting the resolver, explicitly `am start -n
<pkg>/<activity>` for the app's known main activity. This hook maintains
a table of (package -> main_activity) overrides and force-starts the app
that way at the beginning of every run that has a registered override.

For apps not in the table, the hook is a no-op — the standard
`device.launch_package` chain in NativeExperiment runs unchanged.

Contract (mirrors before_run_grant_runtime_perms.py):
    * Idempotent — re-running is safe
    * Warn-not-fail — every adb call is try/excepted
    * The standard launch chain in NativeExperiment still runs after
      this hook; calling `am start` twice is harmless (foreground intent)

AR call shape (verified 2026-06-30 from sweep logs):
    AR invokes ``scripts.run('before_run', device, *args, **kwargs)`` with
    NO positional args and a single kwarg ``current_run`` — a dict shaped
    ``{'runId': str, 'device': str, 'path': str, 'runCount': int}``.
    The Android package name is NOT in current_run; only the APK path is.
    We therefore resolve the package by mapping the APK filename slug to
    a known LeakCanary-affected package, with an aapt fallback.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Optional


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


# Package -> explicit main activity to launch with `am start -n`.
# Only apps where LeakCanary (or another resolver-confounder) causes
# AR's `device.launch_package` to pick the wrong launcher.
#
# Audit method: on a phone with the debug APK installed,
#     adb shell cmd package resolve-activity --brief <package>
# returns the resolver's pick. If it contains "leakcanary" / "Leak",
# add an override here pointing to the app's real MainActivity (find
# via `aapt dump badging <apk> | grep launchable-activity`).
DIRECT_LAUNCH_OVERRIDES: Dict[str, str] = {
    # Activity names verified 2026-06-30 via `aapt dump badging` of each
    # APK's manifest (launchable-activity line).
    "com.maltaisn.notes.debug":      "com.maltaisn.notes.ui.main.MainActivity",
    "com.keeprecipes.android":       "com.keeprecipes.android.MainActivity",
    "com.klalumiere.repertoire":     "klalumiere.repertoire.MainActivity",
    # AVNC debug build ships LeakCanary; resolve-activity returns Android's
    # ResolverActivity because both StartupActivity (activity-alias for
    # HomeActivity) and LeakLauncherActivity are LAUNCHER (added 2026-07-24).
    "com.gaurav.avnc.debug":         "com.gaurav.avnc.StartupActivity",
}


# APK-filename-slug → package for the LeakCanary-affected cohort apps.
# Tries progressively longer underscore-joined prefixes of the filename
# stem, so ``another_notes_baseline.signed.apk`` matches ``another_notes``
# before falling back to ``another``. Mirrors the slug-table pattern in
# ``before_run_grant_runtime_perms.py``.
SLUG_TO_PACKAGE: Dict[str, str] = {
    # maltaisn/another-notes-app
    "another_notes":    "com.maltaisn.notes.debug",
    "another":          "com.maltaisn.notes.debug",
    "notes":            "com.maltaisn.notes.debug",
    "maltaisn":         "com.maltaisn.notes.debug",
    # keeprecipes/keep-recipe
    "keep_recipe":      "com.keeprecipes.android",
    "keeprecipe":       "com.keeprecipes.android",
    "keeprecipes":      "com.keeprecipes.android",
    "keep":             "com.keeprecipes.android",
    # klalumiere/repertoire
    "repertoire":       "com.klalumiere.repertoire",
    "klalumiere":       "com.klalumiere.repertoire",
    # gujjwal00/avnc (debug build has LeakCanary launcher confusion)
    "avnc":             "com.gaurav.avnc.debug",
    "gaurav":           "com.gaurav.avnc.debug",
}


def _resolve_apk_path(args, kwargs) -> Optional[str]:
    """Pull the active APK path out of whatever AndroidRunner forwarded.

    Mirrors ``before_run.py:_resolve_apk_path``. AR calls
    ``scripts.run('before_run', device, *args, **kwargs)`` where
    ``kwargs['current_run']`` is a dict containing ``path`` (the APK path
    for this run). Some callers may pass the path as a positional, so we
    accept both.
    """
    current_run = kwargs.get("current_run") if isinstance(kwargs, dict) else None
    if isinstance(current_run, dict):
        path = current_run.get("path")
        if path:
            return path
    for arg in args or ():
        if isinstance(arg, str) and arg.lower().endswith(".apk"):
            return arg
    return None


def _resolve_package_from_slug(apk_path: Optional[str]) -> Optional[str]:
    """Map an APK filename to a known LeakCanary-affected package via slug table.

    Tries the longest underscore-joined prefix first (``a_b_c``) and walks
    back to the shortest (``a``), so multi-token slugs like ``another_notes``
    win over single-token ``another`` when both are in the table.
    """
    if not apk_path:
        return None
    basename = os.path.basename(apk_path)
    # Strip common APK suffixes
    stem = basename
    for suffix in (".signed.apk", ".nscpatched.apk", ".apk"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    tokens = stem.split("_") if "_" in stem else [stem]
    # a_b_c → a_b_c, a_b, a
    for n in range(len(tokens), 0, -1):
        candidate = "_".join(tokens[:n]).lower()
        if candidate in SLUG_TO_PACKAGE:
            return SLUG_TO_PACKAGE[candidate]
    return None


def _resolve_package_via_aapt(apk_path: Optional[str]) -> Optional[str]:
    """Fallback: extract package name by running ``aapt dump badging`` on the APK.

    Used when the APK basename doesn't match any slug in SLUG_TO_PACKAGE
    (e.g. plain ``app-debug.apk`` from a gradle build). Tries ``aapt`` then
    ``aapt2``; returns None if neither is on PATH or the APK isn't parseable.
    """
    if not apk_path or not os.path.isfile(apk_path):
        return None
    # Hard-code the build-tools path since `aapt` is not on PATH for the
    # python invocation environment.
    candidate_aapts = [
        shutil.which("aapt"),
        shutil.which("aapt2"),
        "/home/irena/Android/Sdk/build-tools/34.0.0/aapt",
        "/home/irena/Android/Sdk/build-tools/30.0.2/aapt",
    ]
    for binpath in candidate_aapts:
        if not binpath or not os.path.isfile(binpath):
            continue
        try:
            proc = subprocess.run(
                [binpath, "dump", "badging", apk_path],
                capture_output=True, text=True, timeout=15,
            )
            out = proc.stdout or ""
            # `package: name='com.foo.bar' versionCode=... versionName=...`
            m = re.search(r"package:\s*name='([^']+)'", out)
            if m:
                return m.group(1)
        except Exception as ex:
            _log_stderr("  aapt dump badging on %s raised: %s: %s"
                        % (apk_path, type(ex).__name__, ex))
            continue
    return None


def _resolve_serial(device) -> Optional[str]:
    for attr in ("id", "device_id", "serial"):
        s = getattr(device, attr, None)
        if isinstance(s, str) and s:
            return s
    try:
        s = device.shell("getprop ro.serialno")
        return (s or "").strip() or None
    except Exception:
        return None


def _adb_shell(serial: str, cmd: str, timeout: float = 10.0) -> Optional[str]:
    """Run `adb -s <serial> shell <cmd>` via subprocess. Bypasses pyand
    so embedded spaces / shell-special chars survive unmangled.
    """
    try:
        proc = subprocess.run(
            ["adb", "-s", serial, "shell", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.stdout
    except Exception as ex:
        _log_stderr("  adb shell %r raised: %s: %s" % (cmd, type(ex).__name__, ex))
        return None


def direct_launch_if_overridden(device, package_name: str) -> Dict[str, object]:
    """If `package_name` has a registered override, force-start its real
    main activity via `am start -n`. No-op for packages without an override.
    """
    result: Dict[str, object] = {"package": package_name, "overridden": False}
    activity = DIRECT_LAUNCH_OVERRIDES.get(package_name)
    if not activity:
        return result

    serial = _resolve_serial(device)
    if not serial:
        result["status"] = "no_serial"
        return result

    component = "%s/%s" % (package_name, activity)
    out = _adb_shell(serial, "am start -n %s" % component)
    result["overridden"] = True
    result["component"] = component
    result["am_start_stdout"] = (out or "").strip()[:200]
    if out is None:
        result["status"] = "am_start_exception"
    elif "Error" in out or "does not exist" in out:
        result["status"] = "am_start_error"
        _log_stderr("  am start -n %s reported error: %s" % (component, out.strip()))
    else:
        result["status"] = "ok"
        _log_stdout("  forced launch: am start -n %s" % component)

    return result


def verify_overrides(device) -> Dict[str, object]:
    """Operator-shell helper: for every override in DIRECT_LAUNCH_OVERRIDES,
    confirm the activity actually exists on this phone. Use to audit before
    enabling overrides.
    """
    serial = _resolve_serial(device)
    if not serial:
        return {"status": "no_serial"}
    results = {}
    for pkg, activity in DIRECT_LAUNCH_OVERRIDES.items():
        out = _adb_shell(serial, "dumpsys package %s | grep -i %s | head -3"
                         % (pkg, activity.rsplit(".", 1)[-1]))
        results[pkg] = {"activity": activity, "dumpsys_hit": bool(out and out.strip())}
    return {"status": "ok", "serial": serial, "results": results}


def main(device, *args, **kwargs):
    """Module entrypoint. AR `before_run` slot.

    AR invokes ``scripts.run('before_run', device, *args, **kwargs)`` with
    NO positional args and a single kwarg ``current_run`` shaped
    ``{'runId', 'device', 'path', 'runCount'}``. The package name is not
    present — we recover it from the APK path via slug table, with an
    ``aapt dump badging`` fallback for non-conformant APK filenames.
    """
    try:
        package: Optional[str] = None

        # Legacy call shapes — accept a pre-resolved package if any caller
        # ever passes one directly (chain-on-hook callers, manual invocation).
        if "package" in kwargs and isinstance(kwargs["package"], str):
            package = kwargs["package"]
        elif "name" in kwargs and isinstance(kwargs["name"], str) \
                and not kwargs["name"].lower().endswith(".apk"):
            package = kwargs["name"]
        elif args and isinstance(args[-1], dict) and "name" in args[-1]:
            package = args[-1]["name"]
        elif args and isinstance(args[0], dict) and "name" in args[0]:
            package = args[0]["name"]

        # Primary path: AR forwards current_run={'path': '<apk>', ...}.
        # Resolve the APK path, then derive the package.
        apk_path: Optional[str] = None
        if not package:
            apk_path = _resolve_apk_path(args, kwargs)
            # Release-buildType APKs do NOT include LeakCanary (it's
            # debugImplementation-only in Kotlin cohort) — the whole direct-
            # launch override mechanism is redundant AND the slug table has
            # debug package names that don't match release applicationIds
            # (avnc/documenter/tipuous/podaura/another_notes have `.debug`
            # suffix on debug, different name on release). Skip slug lookup
            # for these APKs; standard AR launch chain (via application_id
            # in the AR config) handles them correctly.
            fn = os.path.basename(apk_path).lower() if apk_path else ""
            if "_release" in fn:
                _log_stdout(
                    "before_run_direct_launch: %s is a release variant; "
                    "skipping direct-launch override (no LeakCanary in release)."
                    % os.path.basename(apk_path)
                )
                return
            package = _resolve_package_from_slug(apk_path)

        # Fallback: parse the APK manifest directly via aapt.
        if not package and apk_path:
            package = _resolve_package_via_aapt(apk_path)
            if package:
                _log_stdout(
                    "before_run_direct_launch: resolved package %r from %s via aapt fallback"
                    % (package, os.path.basename(apk_path))
                )

        # LeakCanary-cooked launchers prepend `com.squareup.leakcanary.` to
        # the package name (per 2026-06-04 log evidence). Strip it for lookup.
        if package and package.startswith("com.squareup.leakcanary."):
            stripped = package[len("com.squareup.leakcanary."):]
            _log_stdout("before_run_direct_launch: detected LeakCanary-prefixed "
                        "package %r; treating as %r for override lookup"
                        % (package, stripped))
            package = stripped

        if package:
            if package in DIRECT_LAUNCH_OVERRIDES:
                direct_launch_if_overridden(device, package)
            else:
                # Package resolved but not in override table — expected no-op
                # for the vast majority of cohort apps. Log at stdout for
                # post-hoc auditing without alarming the operator.
                _log_stdout(
                    "before_run_direct_launch: %s has no override; "
                    "standard launch chain will run" % package
                )
        else:
            _log_stderr("before_run_direct_launch: could not resolve package "
                        "from args/kwargs (apk_path=%r); skipping. args=%r kwargs=%r"
                        % (apk_path, args, kwargs))
    except Exception as ex:
        _log_stderr("before_run_direct_launch.main: unexpected exception "
                    "(swallowed; run continues): %s: %s"
                    % (type(ex).__name__, ex))
