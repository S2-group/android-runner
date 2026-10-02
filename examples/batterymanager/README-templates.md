# Per-device + per-(app, variant) experiment templates

This folder (`android-runner/examples/batterymanager/_templates/`) holds the
**parametric** Android Runner JSON templates used to generate every
`(app, variant, device)` experiment in the Master's-thesis matrix without
hand-copying a 1 KB JSON ~200+ times.

The convention is intentionally boring:

- **Device profiles** (`device_pixel3.json`, `device_pixel6.json`,
  `device_pixel9.json`) describe one physical device and capture all the
  per-device gotchas the agent has hit (ABI compatibility, BatteryManager
  status, required adb permission grants, sysfs fallback rule).
- **App-variant template** (`app_variant_2min.json`) is one canonical Android
  Runner experiment skeleton with **5 string placeholders** that get filled in
  per `(app, variant, device)`. The structural reference is
  [`monkey_espresso_mirror_2min_baseline.json`](../monkey_espresso_mirror_2min_baseline.json)
  — identical `profilers.batterymanager.*` block.

Locked experiment invariants in the template (do not vary across the matrix):

| Field                          | Value     | Meaning                                                                  |
| ------------------------------ | --------- | ------------------------------------------------------------------------ |
| `duration`                     | `120000`  | 2-minute fixed scenario window per run (cross-variant comparability).    |
| `repetitions`                  | `3`       | 3 measurement repetitions per `(app, variant, device)`.                  |
| `interaction_covers_duration`  | `true`    | The interaction script keeps the workload running for the full duration. |
| `time_between_run`             | `5000`    | 5 s settle between repetitions.                                          |
| `profilers.batterymanager.*`   | as-is     | Same shape as the canonical 2-min baseline config.                       |
| `scripts.*`                    | full set  | All lifecycle hooks present (uninstall / before / after / interaction).  |

## Placeholders

The template uses **double-curly-brace** placeholders so they're trivially
matched by `sed`/`grep`/`str.replace`:

| Placeholder           | Meaning                                                    | Example                                                  |
| --------------------- | ---------------------------------------------------------- | -------------------------------------------------------- |
| `{{APP_ID}}`          | Short app key used in filenames and the per-app uninstall hook script name. | `metronome`                                              |
| `{{APK_PATH}}`        | Absolute path to the variant APK to install.               | `/home/irena/Documents/Master Experiment/app_repositories_newest/final_dataset/Kr0oked_Metronome/baseline/app-debug.apk` |
| `{{APPLICATION_ID}}`  | Manifest package name (used by Android Runner as `application_id`). For packed APKs this is the **manifest** package, not the filename. | `com.bobek.metronome`                                    |
| `{{DEVICE_NAME}}`     | Android Runner `devices` block key — must match the device profile JSON. | `Pixel 3` / `Pixel 6` / `Pixel 9`                        |
| `{{INTERACTION_HOOK}}`| Relative path under `Scripts/` for the per-app Appium interaction script (Espresso-mirrored, black-box). | `Scripts/interaction_appium_metronome_espresso_mirror.py` |

The serial placeholders inside the device profiles
(`{{PIXEL3_SERIAL}}`, `{{PIXEL6_SERIAL}}`, `{{PIXEL9_SERIAL}}`) are **not**
substituted into the experiment JSON — Android Runner picks the connected
device via `adb` using the `devices` block key (e.g. `"Pixel 3": {}`). The
serial placeholders exist so adb commands documented in the device profile
(`adb -s <serial> shell pm grant ...`) can be replayed by the matrix runner
without ambiguity when more than one device is connected.

## How to materialize one concrete config

For a single `(app, variant, device)` cell of the matrix you have two equally
valid options.

### Option A: tiny Python helper (recommended for the E5 matrix sweep)

```python
import json
from pathlib import Path

TEMPLATE_DIR = Path("android-runner/examples/batterymanager/_templates")
OUT_DIR      = Path("android-runner/examples/batterymanager/generated")

def materialize(app_id: str,
                apk_path: str,
                application_id: str,
                device_name: str,
                interaction_hook: str,
                variant: str) -> Path:
    raw = (TEMPLATE_DIR / "app_variant_2min.json").read_text()
    filled = (raw
              .replace("{{APP_ID}}",         app_id)
              .replace("{{APK_PATH}}",       apk_path)
              .replace("{{APPLICATION_ID}}", application_id)
              .replace("{{DEVICE_NAME}}",    device_name)
              .replace("{{INTERACTION_HOOK}}", interaction_hook))
    config = json.loads(filled)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{app_id}_{variant}_{device_name.lower().replace(' ', '')}.json"
    out_path = OUT_DIR / out_name
    out_path.write_text(json.dumps(config, indent=2))
    return out_path
```

Example call for `(metronome, baseline, Pixel 3)`:

```python
materialize(
    app_id           = "metronome",
    apk_path         = "/home/irena/Documents/Master Experiment/app_repositories_newest/final_dataset/Kr0oked_Metronome/baseline/app-debug.apk",
    application_id   = "com.bobek.metronome",
    device_name      = "Pixel 3",
    interaction_hook = "Scripts/interaction_appium_metronome_espresso_mirror.py",
    variant          = "baseline",
)
```

That single call produces a fully valid Android Runner JSON identical in
structure to `monkey_espresso_mirror_2min_baseline.json`, ready to feed to
`python android_runner.py <path>`.

### Option B: one-liner `sed` for ad-hoc materialization

```bash
sed \
  -e 's|{{APP_ID}}|metronome|g' \
  -e 's|{{APK_PATH}}|/abs/path/to/app-debug.apk|g' \
  -e 's|{{APPLICATION_ID}}|com.bobek.metronome|g' \
  -e 's|{{DEVICE_NAME}}|Pixel 3|g' \
  -e 's|{{INTERACTION_HOOK}}|Scripts/interaction_appium_metronome_espresso_mirror.py|g' \
  android-runner/examples/batterymanager/_templates/app_variant_2min.json \
  > android-runner/examples/batterymanager/generated/metronome_baseline_pixel3.json
```

Then `python -m json.tool generated/metronome_baseline_pixel3.json` should
exit clean — that's the cheapest validation that all placeholders were
substituted.

## E5 (matrix sweep) convention

Epic E5 will programmatically generate **~200+ concrete experiment configs**
from this single template by sweeping the cross-product:

```
final_dataset apps  ×  {baseline, obfuscated, packed}  ×  {Pixel 3, Pixel 6, Pixel 9}
```

Concretely E5 will:

1. Walk `app_repositories_newest/final_dataset/` to enumerate apps.
2. For each app, look up its variant APK paths and its
   `interaction_appium_<app>_espresso_mirror.py` hook.
3. For each `(app, variant, device)` triple, call the `materialize()` helper
   above (or equivalent), writing the output to a `generated/` subfolder.
4. Per-device gotchas (BATTERY_STATS grant, sysfs fallback, ABI mismatch) are
   pulled from the corresponding `device_pixelN.json` profile and recorded
   in the run log — they do **not** mutate the experiment JSON itself. The
   experiment JSON stays a thin parametric shell; per-device behavior lives in
   the device profile + the run-time hooks.

Touching the locked invariants (`duration`, `repetitions`,
`interaction_covers_duration`, `time_between_run`,
`profilers.batterymanager.*`) requires updating this template so that **all
~200 generated configs change in lockstep** — never edit the generated
configs in place.

## Per-device gotchas (mirrored from device profiles)

These are summarized here so a reader of this README does not have to open the
JSON device profiles to understand the decision tree the matrix runner has to
walk for each device. Source of truth: the corresponding
`device_pixelN.json`.

### Pixel 3 (Android 12 / API 31)

- **ABI**: `ro.product.cpu.abilist == arm64-v8a,armeabi-v7a` — accepts both
  64-bit-only and 32-bit-only protected APKs. Most permissive of the three
  devices.
- **BatteryManager**: works directly. No `BATTERY_STATS` grant needed.
- **Energy decision rule**: always BatteryManager.
- **Serial reference**: `{{PIXEL3_SERIAL}}` (used in the device profile only).

### Pixel 6

- **ABI**: `ro.product.cpu.abilist == arm64-v8a` — 64-bit-only. Same constraint
  as Pixel 9: APKs whose only JNI libs live under `lib/armeabi-v7a/` will
  install-fail with `INSTALL_FAILED_NO_MATCHING_ABIS`.
- **BatteryManager**: works; if a `SecurityException: BATTERY_STATS permission`
  appears, run
  `adb -s {{PIXEL6_SERIAL}} shell pm grant <package_name> android.permission.BATTERY_STATS`
  where `<package_name>` is the BatteryManager companion APK
  (e.g. `com.example.batterymanager_utility`).
- **Energy decision rule**: BatteryManager → if grant denied, sysfs fallback.

### Pixel 9 (Android 15)

- **ABI (CRITICAL)**: `ro.product.cpu.abilist == arm64-v8a` only. Bangcle-style
  packers that emit only `lib/armeabi-v7a/` JNI libs will
  `INSTALL_FAILED_NO_MATCHING_ABIS`. This is an artifact-side mismatch, not a
  signing or adb bug — never weaken the protection to force install. Either
  re-pack with `arm64-v8a`/fat-ABI shells, or mark that
  `(app, packed-variant, Pixel 9)` cell as **not supported** in
  `final_dataset/`.
- **BatteryManager (CRITICAL)**: companion app raises
  `SecurityException: BATTERY_STATS permission` on Android 15 unless granted
  explicitly:
  `adb -s {{PIXEL9_SERIAL}} shell pm grant <package_name> android.permission.BATTERY_STATS`.
  If the grant is rejected by Android 15's privileged-permission policy,
  switch this device to **sysfs sampling** for that run and record it as
  BatteryManager-blocked in the run log. Never silently skip Pixel 9 from the
  matrix.
- **Energy decision rule**: BatteryManager (after grant) → sysfs fallback if
  the grant is denied.

## Files in this folder

| File                       | Purpose                                                 |
| -------------------------- | ------------------------------------------------------- |
| `device_pixel3.json`       | Pixel 3 (Android 12) device profile + ABI/energy notes. |
| `device_pixel6.json`       | Pixel 6 device profile + ABI/energy notes.              |
| `device_pixel9.json`       | Pixel 9 (Android 15) device profile + ABI/energy notes. |
| `app_variant_2min.json`    | Parametric 2-minute experiment template (5 placeholders). |
| `README-templates.md`      | This file.                                              |
