"""APK provenance writer for the (E0.T4) tracking matrix pipeline.

Produces ``apk_meta.json`` next to a run's output artifacts so the
``after_experiment`` hook (``update_tracking_matrix.py``) can populate
``apk_path`` / ``apk_sha256`` / ``apk_storage`` without needing to be told
the APK path again on the CLI.

This module is intentionally minimal:

* standard library only (``hashlib`` / ``json`` / ``os`` / ``sys``);
* never raises -- a failure here MUST NOT abort the experiment lifecycle;
* schema is the subset of fields ``update_tracking_matrix.py`` accepts
  (see ``_resolve_apk_provenance`` and ``_find_apk_meta`` there).

The helper drops the file under the per-(device, subject) data directory
that AndroidRunner exposes as ``paths.OUTPUT_DIR`` at ``before_run`` time.
``_find_apk_meta`` already searches that location via its
``data/*/*/apk_meta.json`` glob, so a single drop is enough; we fall back
to the run-level directory if the per-run directory cannot be derived.
"""

from __future__ import annotations

import hashlib
import json
import os
import os.path as op
import sys


_CHUNK_BYTES = 64 * 1024  # streaming SHA-256 chunk size

_FALLBACK_NOTE = "before_run_write_apk_meta"


def _log_stderr(msg):
    try:
        sys.stderr.write("%s: %s\n" % (_FALLBACK_NOTE, msg))
    except Exception:
        pass


def _log_stdout(msg):
    try:
        sys.stdout.write("%s: %s\n" % (_FALLBACK_NOTE, msg))
    except Exception:
        pass


def _sha256_file(path):
    """Streaming SHA-256 of ``path``. Returns ``None`` on any read error."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_CHUNK_BYTES), b""):
                h.update(chunk)
    except OSError as exc:
        _log_stderr("sha256 read failed for %s: %s" % (path, exc))
        return None
    return h.hexdigest()


def _ensure_dir(path):
    try:
        os.makedirs(path)
    except OSError:
        pass


def write_apk_meta(run_output_dir, apk_path):
    """Drop ``apk_meta.json`` in ``run_output_dir`` for this APK.

    Parameters
    ----------
    run_output_dir : str
        Directory where the matrix updater can find the file. The most
        useful value is ``paths.OUTPUT_DIR`` (per-(device, subject) data
        folder) because ``_find_apk_meta`` matches it via its
        ``data/*/*/apk_meta.json`` glob. The run-level timestamp directory
        also works.
    apk_path : str
        Path to the APK that was/will be installed for this run.

    Behaviour
    ---------
    * Computes SHA-256 by streaming the file in 64 KB chunks; if the file
      is unreadable, ``apk_sha256`` is written as an empty string and a
      ``note`` field records why -- the row will still be populated with
      ``apk_path`` and ``apk_storage`` so the gap is visible.
    * ``apk_storage`` is fixed to ``"local"`` here -- the migration to
      ``gh-release:`` / ``zenodo:`` URIs happens at thesis-submission time
      via the post-processing snippet documented in
      ``specs/TRACKING_MATRIX.md``.
    * Never raises. Any failure is logged to stderr; the row will still
      be appended (it just falls back to the existing
      ``apk_meta_missing`` notes tag, which is the documented behaviour).
    """
    try:
        if not run_output_dir:
            _log_stderr("no run_output_dir; skipping")
            return
        if not apk_path:
            _log_stderr("no apk_path; skipping")
            return

        abs_apk = op.abspath(apk_path)

        meta = {
            "apk_path": abs_apk,
            "apk_sha256": "",
            "apk_storage": "local",
        }

        if op.isfile(abs_apk):
            digest = _sha256_file(abs_apk)
            if digest:
                meta["apk_sha256"] = digest
            else:
                meta["note"] = "apk_sha256_compute_failed"
        else:
            meta["note"] = "apk_file_not_found_at_hook_time"

        _ensure_dir(run_output_dir)
        target = op.join(run_output_dir, "apk_meta.json")
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as exc:
            _log_stderr("write failed for %s: %s" % (target, exc))
            return

        sha_prefix = (meta["apk_sha256"] or "")[:8] or "<unknown>"
        _log_stdout(
            "wrote apk_meta.json (apk_path=%s, sha256=%s...)"
            % (abs_apk, sha_prefix)
        )
    except Exception as exc:  # noqa: BLE001 - hook must never crash the experiment
        _log_stderr("unexpected failure: %s: %s" % (type(exc).__name__, exc))
