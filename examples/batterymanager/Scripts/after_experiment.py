# noinspection PyUnusedLocal
"""``after_experiment`` hook: append one row to ``specs/tracking_matrix.csv``.

Android Runner calls this once per device after every run on that device has
finished. We hand off to ``update_tracking_matrix.py`` (stdlib-only, never
raises) via subprocess so a bug in the helper or a missing artifact cannot
crash the experiment lifecycle.

Identity for the row comes from environment variables set by the experiment
launcher::

    APPIUM_APP          → app key, e.g. ``metronome`` (default if unset)
    APPIUM_BUILD_LABEL  → variant label (``baseline``, ``r8``, ``allatori``,
                          ``bangcle`` — heuristic mapping from common synonyms
                          like ``obfuscated`` / ``packed`` is performed when
                          the value is not one of the canonical names)

Device key is normalized from ``device.name`` / ``device.id`` (e.g. ``Pixel 3``
→ ``pixel3``) so the row matches the canonical ``{pixel3, pixel6, pixel9}``
enum used elsewhere in the project.

The Android Runner ``BASE_OUTPUT_DIR`` (i.e. the per-experiment timestamp
folder such as ``output/2026.05.05_235701``) is the canonical run-output
directory. Its basename becomes ``run_id`` — the dedup key for the matrix.
"""

from __future__ import annotations

import logging
import os
import os.path as op
import re
import subprocess
import sys


_LOG = logging.getLogger("after_experiment.tracking_matrix")


_VARIANT_ALIASES = {
    "baseline": "baseline",
    "unprotected": "baseline",
    "stock": "baseline",
    "original": "baseline",
    "r8": "r8",
    "minified": "r8",
    "allatori": "allatori",
    "obfuscated": "allatori",
    "obfuscapk": "allatori",
    "bangcle": "bangcle",
    "packed": "bangcle",
    "shielded": "bangcle",
    "protected": "bangcle",
}


def _normalize_variant(label):
    if not label:
        return None
    key = re.sub(r"[^a-z0-9]+", "", str(label).lower())
    if key in _VARIANT_ALIASES:
        return _VARIANT_ALIASES[key]
    for token, mapped in _VARIANT_ALIASES.items():
        if token in key:
            return mapped
    return str(label).lower() or None


def _infer_variant_from_run_output(run_output_dir):
    """Infer variant from the per-(device, subject) folder name under data/.

    AndroidRunner names that folder by slugifying the APK path, so an APK
    called ``app-protected-signed.apk`` ends up as a subdir whose name
    contains ``protected``. We scan that slug for any of the known variant
    aliases (``protected`` / ``packed`` / ``bangcle`` / ``r8`` / ``allatori``
    / ``baseline`` / ...). Returns ``None`` if the slug is unhelpful.
    """
    if not run_output_dir:
        return None
    data_dir = op.join(run_output_dir, "data")
    if not op.isdir(data_dir):
        return None
    try:
        for device_name in os.listdir(data_dir):
            device_path = op.join(data_dir, device_name)
            if not op.isdir(device_path):
                continue
            for subject_slug in os.listdir(device_path):
                if not op.isdir(op.join(device_path, subject_slug)):
                    continue
                slug = re.sub(r"[^a-z0-9]+", "", subject_slug.lower())
                # Order matters: check more-specific aliases (bangcle,
                # allatori) before more-general ones (packed, obfuscated)
                # so e.g. "obfuscated-with-allatori" resolves to allatori.
                priority = [
                    "bangcle", "allatori", "obfuscapk", "r8",
                    "shielded", "protected", "packed", "obfuscated", "minified",
                    "baseline", "unprotected", "stock", "original",
                ]
                for token in priority:
                    if token in slug:
                        return _VARIANT_ALIASES.get(token, token)
    except OSError:
        return None
    return None


def _normalize_device(name_or_id):
    if not name_or_id:
        return "unknown"
    key = re.sub(r"[^a-z0-9]+", "", str(name_or_id).lower())
    for canonical in ("pixel3", "pixel6", "pixel9"):
        if canonical in key:
            return canonical
    return key or "unknown"


# Known per-app slugs registered under appium_android_tests/<slug>/ (as of 2026-05-12).
# When new per-app modules land, add their slugs here so app-inference from the APK
# basename works out of the box. Order doesn't matter (no aliasing — slugs are exact).
_KNOWN_APP_SLUGS = (
    "metronome", "tipuous", "documenter", "linkhub", "repertoire",
    "calculator", "iamspeed", "poetskingdom", "keeprecipe", "anothernotes",
    "itsok", "dayswithoutbadhabits", "pdfviewer",
)


def _infer_app_from_run_output(run_output_dir):
    """Infer app slug from the per-(device, subject) folder name under data/.

    AndroidRunner names that folder by slugifying the APK path. Examples:
      - .../tipuous_protected.signed.apk → ...apks-tipuous_protected-signed-apk
      - .../com.bobek.metronome_26_protected-v2.1.1.signed.apk →
        ...apks-com-bobek-metronome_26_protected-v2-1-1-signed-apk
      - .../documenter-universal-debug_protected.signed.apk →
        ...apks-documenter-universal-debug_protected-signed-apk
      - .../Linkhub_protected.signed.apk → ...apks-linkhub_protected-signed-apk
    We scan the slug for any token in `_KNOWN_APP_SLUGS`. Returns None if the
    slug is unhelpful — caller should fall back to APPIUM_APP env or "unknown"
    (NOT to "metronome", which is just the pilot app, not a sensible default).

    Companion to `_infer_variant_from_run_output` and uses the same walk shape.
    """
    if not run_output_dir:
        return None
    data_dir = op.join(run_output_dir, "data")
    if not op.isdir(data_dir):
        return None
    try:
        for device_name in os.listdir(data_dir):
            device_path = op.join(data_dir, device_name)
            if not op.isdir(device_path):
                continue
            for subject_slug in os.listdir(device_path):
                if not op.isdir(op.join(device_path, subject_slug)):
                    continue
                slug = re.sub(r"[^a-z0-9]+", "", subject_slug.lower())
                # Order matters slightly: longer slugs first so e.g.
                # "linkhub" wins over a hypothetical "link" subset, and
                # "metronome" matches the v2.1.1-suffixed APK basename
                # `com-bobek-metronome_26_protected-v2-1-1-signed-apk`.
                for app_slug in sorted(_KNOWN_APP_SLUGS, key=len, reverse=True):
                    if app_slug in slug:
                        return app_slug
    except OSError:
        return None
    return None


def _resolve_run_output_dir():
    """Prefer ``paths.BASE_OUTPUT_DIR`` (the timestamp folder); fall back to ``OUTPUT_DIR``."""
    try:
        import paths as ar_paths  # type: ignore
    except Exception:
        return None
    base = getattr(ar_paths, "BASE_OUTPUT_DIR", None)
    if base and op.isdir(base):
        return base
    out = getattr(ar_paths, "OUTPUT_DIR", None)
    if out and op.isdir(out):
        # OUTPUT_DIR is per-(device,subject); walk up to the timestamp dir.
        cur = op.abspath(out)
        for _ in range(6):
            if op.isdir(op.join(cur, "data")):
                return cur
            parent = op.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return out
    return None


def _resolve_helper_path():
    return op.join(op.dirname(op.abspath(__file__)), "update_tracking_matrix.py")


# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    try:
        run_output_dir = _resolve_run_output_dir()
        if not run_output_dir:
            _LOG.warning("tracking_matrix: could not resolve run output dir; skipping")
            return

        # E1.5.T5 — Post-process the built-in `android` profiler's per-(device,
        # subject) CSV NOW (vs in after_run, where the CSV was not yet on disk
        # — see the 2026-05-08T19:30 run, where after_run returned 12 s before
        # `Profilers:Start final aggregation`). One walk of `data/<device>/<subject>/`
        # writes `aux/aux_summary.json` per subject; the matrix updater (called
        # by the subprocess below) then reads those summaries and populates
        # cpu_avg_pct / cpu_p95_pct / mem_pss_avg_mb / mem_pss_max_mb.
        try:
            import aux_postprocess
            results = aux_postprocess.process_run_output_dir(run_output_dir)
            n_processed = sum(1 for r in results if not r.get("error"))
            n_disabled = sum(1 for r in results if r.get("error") == "android_profiler_disabled")
            if results:
                _LOG.info(
                    "aux_postprocess: walked %d subject dir(s) (processed=%d, profiler_disabled=%d)",
                    len(results), n_processed, n_disabled,
                )
        except Exception as exc:
            _LOG.warning(
                "aux_postprocess: pre-tracking-matrix step failed (continuing): %s: %s",
                type(exc).__name__, exc,
            )

        # App resolution chain (post-2026-05-12 fix), most-trusted first:
        #   1. APPIUM_APP env var — set by the per-app interaction wrappers
        #      (e.g. interaction_appium_tipuous.py does
        #      `os.environ.setdefault("APPIUM_APP", "tipuous")`) or by the
        #      generic dispatcher's launcher.
        #   2. APK-basename slug — derived from the per-subject output folder
        #      name (which AndroidRunner slugifies from the APK path).
        #   3. "unknown" — explicit. Previously this fell back to "metronome",
        #      which silently misclassified every non-Metronome run as
        #      Metronome (10 rows in tracking_matrix.csv had to be backfilled
        #      after this bug was discovered 2026-05-12). NEVER default to a
        #      specific app slug — make the misclassification visible instead.
        app = (
            os.environ.get("APPIUM_APP")
            or _infer_app_from_run_output(run_output_dir)
            or "unknown"
        )
        # Variant resolution, in order of trust:
        #   1. APPIUM_BUILD_LABEL env var (explicit, set by the experiment launcher)
        #   2. Scan the per-subject output folder name (slug of the APK path)
        #   3. Fallback: "baseline"
        # This avoids the silent-misclassification bug where a packed APK
        # was being recorded as variant=baseline because nothing set the env.
        variant = (
            _normalize_variant(os.environ.get("APPIUM_BUILD_LABEL"))
            or _infer_variant_from_run_output(run_output_dir)
            or "baseline"
        )
        device_key = _normalize_device(getattr(device, "name", None) or getattr(device, "id", None))

        helper = _resolve_helper_path()
        if not op.isfile(helper):
            _LOG.warning("tracking_matrix: helper not found at %s; skipping", helper)
            return

        cmd = [
            sys.executable or "python3",
            helper,
            "--app", app,
            "--variant", variant,
            "--device", device_key,
            "--run-output-dir", run_output_dir,
        ]
        _LOG.info("tracking_matrix: invoking %s", " ".join(cmd))
        # Best-effort: the helper itself never raises, but guard the subprocess
        # call too in case Python is missing or PATH is broken.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            _LOG.warning(
                "tracking_matrix: helper exited %s\nstdout=%s\nstderr=%s",
                result.returncode, result.stdout, result.stderr,
            )
        elif result.stderr:
            _LOG.info("tracking_matrix: helper stderr: %s", result.stderr.strip())
    except Exception as ex:  # noqa: BLE001 - hook must never crash the experiment
        _LOG.warning("tracking_matrix: update failed (%s: %s)", type(ex).__name__, ex)
