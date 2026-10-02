# Monkey + sysfs power measurement (black-box)

This folder contains a **black-box experiment pipeline** that:

- installs/launches a target Android app (by package name)
- generates interaction/workload using **Android Monkey**
- **samples battery current + voltage via sysfs** during the workload
- computes **power (W)** and **energy (J)** from the samples

It was built to work even when developer tests (Espresso) are unreliable and when the Android Runner BatteryManager
companion app cannot run on newer Android versions due to privileged-permission restrictions.

---

## Why sysfs (and not the BatteryManager plugin)?

Android Runner’s BatteryManager profiler requires a companion app (`com.example.batterymanager_utility`).
On Android 15 (Pixel 9 in this setup), the companion app may crash with:

- `java.lang.SecurityException: ... does not have android.permission.BATTERY_STATS`

`BATTERY_STATS` is a privileged/signature permission on many builds, so a normal user-installed app cannot obtain it.

To keep the pipeline **unprivileged** and **black-box**, we read current/voltage directly from:

- `/sys/class/power_supply/battery/current_now`
- `/sys/class/power_supply/battery/voltage_now`

These are kernel-exposed power-supply readings (sysfs).

---

## What you run

### 1) Install the app under test

Example for Metronome:

```bash
adb install -r "/home/irena/Documents/Master Experiment/app_repositories_newest/app_repositories/Kr0oked_Metronome/app/build/outputs/apk/debug/app-debug.apk"
```

**Bangcle-protected, manually signed Metronome (same package id):** install on **Pixel 3** (serial `89WX0HWVF` in `devices.json` as `"Pixel 3"`). The signed APK used here:

`/home/irena/Documents/Master Thesis/APKs/app-protected-signed.apk`

```bash
adb -s 89WX0HWVF install -r "/home/irena/Documents/Master Thesis/APKs/app-protected-signed.apk"
```

Then run `examples/batterymanager/monkey_experiment_pixel3.json` (still `com.bobek.metronome` in `"apps"`).

### 2) Run the experiment

```bash
cd "/home/irena/Documents/Master Experiment/android-runner"
source .venv/bin/activate
python3 __main__.py examples/batterymanager/monkey_experiment.json
```

For **Pixel 3** (e.g. Bangcle build above): `python3 __main__.py examples/batterymanager/monkey_experiment_pixel3.json`

**Pixel 3 + Monkey + BatteryManager plugin only (no sysfs):** use `monkey_experiment_pixel3_batterymanager.json`, which sets `profilers.batterymanager` and `interaction` to `Scripts/interaction_monkey_only.py` (Monkey in the background; power comes from the plugin, not from `sysfs_power_*.csv`).

```bash
python3 __main__.py examples/batterymanager/monkey_experiment_pixel3_batterymanager.json
```

Install the [BatteryManager companion](https://github.com/S2-group/batterymanager-companion/releases) on the device (`com.example.batterymanager_utility`) and grant its permissions. On some OS levels (e.g. Android 15) the plugin may still fail; **Android 12 on Pixel 3** is a reasonable place to try `adb_log` mode.

| Config | Device | Energy data |
|--------|--------|-------------|
| `monkey_experiment.json` | Pixel 9 (in file) | sysfs + `compute_energy_from_sysfs.py` |
| `monkey_experiment_pixel3.json` | Pixel 3 | sysfs + `compute_energy_from_sysfs.py` |
| `monkey_experiment_pixel3_batterymanager.json` | Pixel 3 | BatteryManager plugin (logcat → per-run CSV; `Aggregated_Results_Batterymanager.csv` at end of run) |
| `monkey_experiment_pixel3_appium_metronome.json` | Pixel 3 | **Appium** workload (`Scripts/interaction_appium_metronome.py`) instead of Monkey; install `requirements-appium.txt`, run `appium` on the host |
| `monkey_experiment_pixel3_appium_metronome_batterymanager.json` | Pixel 3 | Same Appium workload + **BatteryManager** profiler (no sysfs CSV) |

**BatteryManager: what we observed in this project**

- On **Pixel 9 / Android 15**, the companion app can fail (e.g. `BATTERY_STATS` / privileged permissions), so the **sysfs** path is the reliable default there.
- On **Pixel 3 / Android 12 (API 31)**, a full run with `monkey_experiment_pixel3_batterymanager.json` **succeeded**: the companion started the data-collection service, the plugin produced per-run `logcat_<serial>_*.csv` under `data/.../batterymanager/`, and after aggregation the run folder contained `Aggregated_Results_Batterymanager.csv` (example output folder: `output/2026.04.24_131804/`).
- The stock Android Runner plugin needed small **compatibility fixes** in this environment (NumPy 2.x: `trapz` → `trapezoid`; pandas: `drop` without `axis` together with `columns`). Those changes live in `AndroidRunner/Plugins/batterymanager/Batterymanager.py` in this tree.

Notes:
- `examples/batterymanager/monkey_experiment.json` pins `adb_path` so the run does not depend on your shell `PATH`.
- The device name in the config must exist in `android-runner/devices.json`.

---

## What the experiment does

The experiment is configured as a **native** Android Runner experiment (`type: "native"`):

- **Subject**: the app package name in `"apps"` (e.g. `com.bobek.metronome`)
- **Repetitions**: `"repetitions": 2` → two independent runs
- **Duration**: `"duration": 60000` ms → ~60 seconds per run

Workload is usually **Monkey**, unless you point `"interaction"` at another script; where energy numbers come from depends on the `interaction` script and `profilers`:

- **Default** (`Scripts/interaction.py`): same Monkey command, then **sysfs** sampling to `sysfs_power_*.csv` (see [Output layout](#output-layout)). Use when you want the host-side script `compute_energy_from_sysfs.py` and no dependency on the companion app.
- **Monkey only** (`Scripts/interaction_monkey_only.py`): starts Monkey only. Use with `"profilers": { "batterymanager": ... }` so the Android Runner **BatteryManager** plugin collects `BATTERY_PROPERTY_CURRENT_NOW` / `EXTRA_VOLTAGE` (via the companion and `adb_log` persistency by default in that config). No `sysfs_power_*.csv` is written.
- **Appium (Metronome)** (`Scripts/interaction_appium_metronome.py`): **UiAutomator2** workload on the device via an Appium server on the host (`APPIUM_SERVER_URL`, default `http://127.0.0.1:4723`). Uses `device.id` as `udid`. Runs in a **background thread** so the native experiment’s duration sleep still defines the measured window (same pattern as background Monkey). Install deps: `pip install -r requirements-appium.txt`. Details and step-by-step: `appium_android_tests/README.md`.

---

## Methodology note: foreground app (documented; script update pending)

**Issue.** If `before_run.py` starts the BatteryManager **companion** (`com.example.batterymanager_utility`), the **screen can stay on that utility** for much of the run while Monkey still targets the **subject** package (e.g. `com.bobek.metronome`). Whole-device sysfs energy then mixes the wrong foreground UI with the workload you intend to compare. That **pollutes** comparisons such as **debug vs R8 vs Allatori**—the confound is not the obfuscator, it is “utility on screen vs app on screen.”

**Intended pipeline** (to apply in the Android Runner scripts when you have time; companion code can stay in the tree for a future BatteryManager-profiler attempt):

1. **Before run:** `am force-stop` the **target** package, then **launch** the target app (e.g. `adb shell monkey -p <package> -c android.intent.category.LAUNCHER 1`, or resolve the launcher activity with `cmd package resolve-activity`).
2. **Interaction:** unchanged—start Monkey for the target package and sample sysfs (see `Scripts/interaction.py`).
3. **After run:** `am force-stop` the **target** package only. Do not start or force-stop the BatteryManager utility as part of the default sysfs path.

**Logging** (when implemented): log the package launched, when Monkey starts, and the path of the sysfs CSV so experiment logs are easy to audit.

**BatteryManager profiler:** sysfs measurement does **not** require the companion. Keep any BatteryManager-related setup in docs or optional configs for when you try the **plugin** again on a build where the companion is usable.

---

## Output layout

Android Runner writes outputs under:

`examples/batterymanager/output/<timestamp>/`

Inside the run folder, the sysfs sample CSVs are under:

`data/<device name>/<subject slug>/sysfs_power_<deviceId>_<timestamp>.csv`

Example:

`output/2026.04.24_111912/data/Pixel 9/com-bobek-metronome/sysfs_power_56040DLAQ0027U_2026.04.24_112134.csv`

Each file corresponds to **one repetition**.

If you use the **BatteryManager** profiler, Android Runner also writes plugin outputs under the same run (e.g. logcat-derived CSV and plugin aggregation) under paths like `data/<device>/<subject>/batterymanager/` (see the plugin’s behaviour in `AndroidRunner/Plugins/batterymanager/`).

---

## What is measured (units)

Each `sysfs_power_*.csv` contains:

- `epoch_ms`: wall-clock timestamp (milliseconds since epoch)
- `current_now_ua`: battery current (microamps, µA)
  - can be **negative** while discharging (device-dependent convention)
- `voltage_now_uv`: battery voltage (microvolts, µV)

---

## How power and energy are calculated

We treat each sample as an approximate instantaneous battery-side measurement:

### Convert units

- \(I\,[A] = \text{current\_now\_ua} \times 10^{-6}\)
- \(V\,[V] = \text{voltage\_now\_uv} \times 10^{-6}\)
- \(t\,[s] = \text{epoch\_ms} / 1000\)

### Instantaneous power (Watts)

\[
P(t)\,[W] \approx |I(t)| \cdot V(t)
\]

We use \(|I|\) so discharging current yields positive power draw.

### Energy over the run (Joules)

Energy is the integral of power over time:

\[
E\,[J] = \int P(t)\,dt
\]

With discrete samples, we compute \(E\) using the **trapezoidal rule**:

\[
E \approx \sum_i \frac{P_i + P_{i+1}}{2} \cdot (t_{i+1} - t_i)
\]

### Average power (Watts)

\[
\bar{P} = \frac{E}{T}
\]

where \(T\) is the run duration in seconds.

### BatteryManager plugin path (Android Runner aggregation)

This applies when you use `profilers.batterymanager` and the same data points as in `monkey_experiment_pixel3_batterymanager.json` (`BATTERY_PROPERTY_CURRENT_NOW`, `EXTRA_VOLTAGE`, persistency `adb_log`). The companion streams samples; the plugin parses logcat into a CSV, then **aggregates** in `Batterymanager.aggregate_batterymanager_runs` (see `AndroidRunner/Plugins/batterymanager/Batterymanager.py`).

**1) Time axis**

- The `Timestamp` column is shifted so the first sample is at **0**; the plugin then divides by **1000** (Android Runner’s convention there is “raw → axis used for integration”; treat the result as the time coordinate in seconds for integration together with the power series).

**2) Per-sample power (W)**

The plugin uses the absolute value of instantaneous current and voltage from the BatteryManager API, converted to **amperes** and **volts**:

- `BATTERY_PROPERTY_CURRENT_NOW` is in **microamperes** (µA) in the API; the code divides by \(10^6\) to get **A**.
- `EXTRA_VOLTAGE` is taken as **millivolts** (mV) relative to the formula in code; the code divides by **1000** to get **V** (check your Android version and companion logs if you need exact SI traceability in the write-up).

\[
P(t)\,[W] = \lvert I(t) \rvert\,[A] \cdot V(t)\,[V]
\]

Implemented as:

\[
P = \frac{\lvert \text{BATTERY\_PROPERTY\_CURRENT\_NOW} \rvert}{10^6} \cdot \frac{\text{EXTRA\_VOLTAGE}}{10^3}
\]

(Exact division layout matches the Java/Android Runner `calculate_power` in `Batterymanager.py`.)

**3) `Avg power (W)`**

- Arithmetic mean of the per-sample `power` column for that run: \(\bar{P} = \text{mean}(P_i)\).

**4) `Energy simple (J)`**

- **Not** the trapezoidal integral. It is the **rectangle rule** using the **average** power and the **last** (relative) timestamp as run length:

\[
E_{\text{simple}} = \bar{P} \cdot t_{\text{end}}
\]

where \(t_{\text{end}} = \max_i(\text{Timestamp}_i)\) after preprocessing.

This equals \(\int P\,dt\) only if power were constant; otherwise it can differ from `Energy trapz (J)`.

**5) `Energy trapz (J)`**

- Trapezoidal integration of **power vs time** for that run (NumPy `trapezoid` with fallback to `trapz` in our patched code):

\[
E_{\text{trapz}} \approx \int P(t)\,dt \quad \text{on the discrete } (t_i, P_i) \text{ series.}
\]

This is the same **idea** as the sysfs `compute_energy_from_sysfs.py` trapezoidal energy, but using the companion’s sampling and column layout instead of `sysfs_power_*.csv`.

**6) `Aggregated_Results_Batterymanager.csv`**

- One row per run (per device/app/repetition), with columns such as `Avg power (W)`, `Energy simple (J)`, `Energy trapz (J)`, and meaned raw fields, written when the experiment finishes and **final aggregation** runs. If that step errors, re-run with `--progress` pointing at the run’s `progress.xml` after fixing the code (as done for the Pixel 3 Bangcle run).

---

## Computing the summary CSV

Script:

- `compute_energy_from_sysfs.py`

Run it on a specific Android Runner output folder:

```bash
python3 examples/batterymanager/compute_energy_from_sysfs.py \
  "examples/batterymanager/output/2026.04.24_111912"
```

It writes:

- `sysfs_energy_summary.csv` (one row per `sysfs_power_*.csv`)

Optional:

```bash
python3 examples/batterymanager/compute_energy_from_sysfs.py \
  "examples/batterymanager/output/2026.04.24_111912" \
  --write-power-csv
```

This also writes per-sample CSVs containing `power_w`.

---

## Limitations / caveats to mention in a write-up

- **Battery-side approximation**: current/voltage are battery-level readings; they approximate device power draw, but are not as accurate as external hardware (Monsoon).
- **Sampling granularity**: sysfs sampling at 100 ms is “best effort”; actual timing can drift with device load.
- **Kernel/vendor differences**: some devices expose different units or paths; Pixel 9 works with the paths above.
- **Monkey reproducibility**: fixed seed (`-s 1234`) improves reproducibility, but UI timing/network conditions still introduce variance.

