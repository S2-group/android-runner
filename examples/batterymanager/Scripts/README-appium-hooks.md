# Appium `interaction` hooks — quick reference

This doc covers the AndroidRunner `interaction` hooks for the **black-box Appium harness**
(`appium_android_tests/` package). It is intentionally short; deep documentation lives in:

- `appium_android_tests/CONVENTIONS.md` — folder layout, per-app `run_workload` contract,
  required artifacts, env-var ownership, black-box principle.
- `appium_android_tests/_lib/driver.py` — connect-time env vars (`APPIUM_SERVER_URL`,
  `APPIUM_SESSION_CONNECT_TIMEOUT_S`, `APPIUM_SKIP_U2_INSTALL`, `APPIUM_FORCE_U2_INSTALL`,
  `APPIUM_ATTACH_ONLY`, `APPIUM_APP_WAIT_DURATION_MS`, `APPIUM_WAIT_FOR_PACKAGE_S`,
  `APPIUM_WAIT_MAIN_UI_S`).
- `appium_android_tests/_lib/coverage.py` — `APPIUM_UI_DUMP_DIR`, `APPIUM_UI_DUMP_MAX`,
  `APPIUM_SAVE_UI_ON_MISS`.
- `appium_android_tests/<app>/scenarios.py` — per-app workload-mode env vars (e.g.
  `metronome/scenarios.py` documents `APPIUM_WORKLOAD`, `METRONOME_*_SUBSTR`,
  `ESPRESSO_MIRROR_*`, etc.).

## When to use which hook

There are three flavors of Appium `interaction` hook in this directory. Pick one per
experiment JSON; they are mutually exclusive on a given run.

### 1. Generic dispatcher — `interaction_appium.py` (preferred for new apps)

Use this hook directly and set `APPIUM_APP=<app_id>` in the experiment JSON's `pre_run`
hook (or in the launcher environment). The dispatcher imports
`appium_android_tests.<app_id>` and calls its `run_workload(experiment, device)`.

Example experiment JSON snippet:

```json
{
  "interaction": "examples/batterymanager/Scripts/interaction_appium.py",
  "interaction_covers_duration": true,
  "pre_run": "examples/batterymanager/Scripts/before_run_set_app_id.sh"
}
```

…where `before_run_set_app_id.sh` does `export APPIUM_APP=avnc` (or whatever app id).

### 2. Per-app wrapper — `interaction_appium_<app_id>.py`

Use this when the launcher cannot easily inject env vars (some AndroidRunner configs only
let you point at a script path) or for backwards compatibility with the existing
`interaction_appium_metronome.py` / `interaction_appium_metronome_espresso_mirror.py`
configs. Copy `interaction_appium_TEMPLATE.py` to `interaction_appium_<app_id>.py`,
replace `<app_id>` everywhere, and the wrapper hardcodes `APPIUM_APP` then delegates to
the generic dispatcher.

The Metronome wrappers (`interaction_appium_metronome*.py`) predate the generic
dispatcher and remain unchanged; they import `appium_android_tests.metronome` directly
and continue to be the canonical path for the existing Metronome configs.

### 3. Monkey-only — `interaction.py`, `interaction_monkey_only.py`

Not Appium; documented for completeness. These run `monkey` and read `sysfs` power
samples without UiAutomator2 / scoring artifacts. Use for smoke runs only — they do not
satisfy the strict-UI scoring contract used as the thesis-grade signal.

## Env-var contract (this hook only)

Two env vars are owned by the dispatcher itself; everything else is per-app or shared
(see the deep-doc references above and `appium_android_tests/CONVENTIONS.md` § "Env-var
ownership").

| Env var | Owner | Purpose |
|---|---|---|
| `APPIUM_APP` | dispatcher (this hook) | App id; required for the generic dispatcher. Ignored by per-app wrappers since they hardcode it. |
| `APPIUM_WORKLOAD` | per-app module | Selects scenario suite within a per-app module. `metronome/scenarios.py` honors `metronome` (default), `espresso_mirror`, `generic`. The dispatcher also accepts `APPIUM_WORKLOAD` as a **legacy** fallback for `APPIUM_APP` only when the value is a single non-reserved token (so `APPIUM_WORKLOAD=espresso_mirror` does NOT silently alias to an app id). |

The dispatcher must NOT shadow shared env vars; it touches only `APPIUM_APP` (read) and
`APPIUM_WORKLOAD` (read, for legacy detection only — never written).

## Troubleshooting — `appium_status.json` `failure_reason` strings

Every dispatcher failure path writes `appium_status.json` with one of the following
`failure_reason` strings. AndroidRunner will mark the run as
`crash_anr_status_missing` rather than as a Python traceback so the energy window stays
classifiable.

| `failure_reason` (prefix) | Most common cause | Fix |
|---|---|---|
| `APPIUM_APP env var missing` | Generic dispatcher referenced from experiment JSON, but no `APPIUM_APP` in the launcher env. | Set `export APPIUM_APP=<app_id>` in the launcher / `pre_run` hook, OR switch to the per-app wrapper which hardcodes it. |
| `APPIUM_APP env var ambiguous` | `APPIUM_APP` was empty and the legacy `APPIUM_WORKLOAD` fallback returned a reserved token (`espresso_mirror`, `generic`) or a value with `_`. | Set `APPIUM_APP` explicitly to the app id (e.g. `metronome`) and keep `APPIUM_WORKLOAD` for scenario-suite selection. |
| `appium_android_tests package not on sys.path` | Workspace-root walk-up from the script's location did not find `appium_android_tests/__init__.py`, and the hard-coded fallback also failed. | Confirm the script lives under `<workspace>/android-runner/examples/batterymanager/Scripts/` and the workspace root contains `appium_android_tests/`; otherwise patch `_FALLBACK_ROOT` in `interaction_appium.py`. |
| `per-app module not found: appium_android_tests.<app_id>` | `APPIUM_APP=<app_id>` is set but no `appium_android_tests/<app_id>/` folder exists. | Typo in the app id, or the per-app module hasn't been created yet. Add the folder per CONVENTIONS.md. |
| `per-app module has no run_workload: appium_android_tests.<app_id>` | The `<app_id>/__init__.py` doesn't re-export `run_workload`. | Add `from .scenarios import run_workload` to `<app_id>/__init__.py`. |
| `per-app run_workload raised <ExceptionType>: <msg>` | The per-app workload itself failed (Appium server unreachable, ImportError on the `appium` client, package not installed on device, etc.). | Check the AndroidRunner log for the full traceback, then triage by exception type. `ImportError: No module named 'appium'` → `pip install -r requirements-appium.txt`. `URLError: Connection refused` → start `appium` server. |

## Adding a new app — 5-step checklist

1. **Create the per-app module.** Add `appium_android_tests/<app>/scenarios.py`
   implementing `run_workload(experiment, device)` per `CONVENTIONS.md`. Reuse helpers
   from `appium_android_tests._lib` (`build_uiautomator2_options`,
   `connect_remote_webdriver`, `await_app_ready`, `record_numeric_edit_observed`,
   `start_workload_thread_and_block`, `write_appium_status`, etc.) — do NOT re-import
   `appium` directly outside the workload thread.
2. **Re-export `run_workload`.** In `appium_android_tests/<app>/__init__.py`, add
   `from .scenarios import run_workload`.
3. **Wire the hook.** Either (a) set `APPIUM_APP=<app>` in the experiment JSON's
   `pre_run` hook and reference `interaction_appium.py`, OR (b) copy
   `interaction_appium_TEMPLATE.py` to `interaction_appium_<app>.py`, replace `<app_id>`
   with `<app>`, and reference the wrapper.
4. **Set `interaction_covers_duration: true`** in the experiment JSON so
   `NativeExperiment` does not double-sleep over the workload window (the per-app
   `run_workload` handles its own duration via `start_workload_thread_and_block`).
5. **Smoke-test on Pixel 3** (Android 12) before scaling to Pixel 6 / Pixel 9. Verify
   that `appium_workload_coverage.jsonl`, `<workload>_scenario_report.{json,txt}`, and
   `appium_status.json` land under `paths.OUTPUT_DIR` and that
   `appium_status.json.failure_reason` is `null` on success.
