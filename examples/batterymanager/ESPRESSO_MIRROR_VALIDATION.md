# Espresso vs Appium “espresso_mirror” workload — validation matrix

This document supports **thesis / committee validation**: it states how closely the Android Runner
Appium workload (`APPIUM_WORKLOAD=espresso_mirror`, hook
`Scripts/interaction_appium_metronome_espresso_mirror.py`) relates to the **upstream** Metronome
**Espresso** suite.

**Source of truth (Espresso)**  
- `app/src/androidTest/java/com/bobek/metronome/InstrumentedTest.kt`  
- `app/src/androidTest/java/com/bobek/metronome/AbstractAndroidTest.kt` (shared `R.id` helpers, `applyTempo`, `verifyTempoMarking`)

**Appium implementation**  
- `Scripts/interaction_appium_metronome.py` — function `run_espresso_mirror_workload` (and helpers such as `_type_numeric_edit_by_id`, `_ESPRESSO_TEMPO_MARKING_WALK`, UI pulse fillers)

---

## 1. Methodology difference (all scenarios)

| Aspect | Espresso (`androidTest`) | Appium (black-box) |
|--------|-------------------------|-------------------|
| **APK** | Typically debug + **test runner** on device/emulator | Same **installable** app id (`com.bobek.metronome`) as user experiments (debug / R8 / …) |
| **Selectors** | `withId(R.id.*)`, `SliderUtils.setValue`, Hamcrest | `find_element(ID, "package:id/…")`, `UiAutomator` text/description for pulse |
| **Tempo / beats / subdivisions** | Often **slider** `setValue` then **assert** edit + slider | We **type into `*_edit` fields** (equivalent end-state if UI sync matches Espresso’s coupling tests; **not** identical gestures for energy) |
| **Assertions** | Strict (`matches(withText(…))`, `displaysError()`, slider `withValue`) | **Soft / logging**: booleans in `appium_workload_coverage.jsonl`, optional substring checks on `tempo_marking_text` |
| **Error tests** | Assert `TextInputLayout` shows error | **“Touch only”**: enter invalid text then restore — **does not** assert error drawable/state |

**Validation claim:** The Appium suite is **scenario-aligned** (same screens and IDs, same numeric journeys where possible), **not** a byte-for-byte replay of Espresso gestures or assertions.

---

## 2. Scenario-by-scenario comparison

### Legend

- **Close** — Same logical steps and views; gesture path may differ (edit vs slider).
- **Partial** — Subset of Espresso steps, or substring check instead of exact `withText(R.string.…)`.
- **Touch-only** — Same inputs as Espresso’s “bad value” phase; **no** Espresso-style error UI assertion.
- **Not implemented** — No dedicated Appium scenario (may overlap indirectly).

| Espresso `@Test` (InstrumentedTest) | Appium `scenario_name` | Relationship | Notes |
|------------------------------------|-------------------------|--------------|--------|
| `contentVisible` | *(none)* | **Not implemented** | Espresso checks `loading_indicator` gone + `content` visible. Appium uses `await_app_ready` (FAB / strings), not the same view IDs. |
| `initialState` | `initialState` | **Partial / Close** | Espresso: sliders → 4, 1, `applyTempo(80)` + **many** `check()` on sliders/edits/markings. Appium: types `4`, `1`, `80` into edits + checks **Andante** substring on `tempo_marking_text`. Does **not** assert slider `withValue`. |
| `beatsSliderAndEditReflectEachOther` | `beatsReflect` | **Close** | Espresso: slider 1 → edit shows `1` → `replaceText("2")` → slider `withValue(2)`. Appium: `beats_edit` `1` → `2` only (skips explicit slider drag). |
| `subdivisionsSliderAndEditReflectEachOther` | `subdivisionsReflect` | **Close** | Same pattern as beats; edit-only path. |
| `tempoSliderAndEditReflectEachOther` | `tempoReflect` | **Close** | Espresso: slider 30 → edit `30` → `replaceText("40")`. Appium: `tempo_edit` `30` → `40`. |
| `tempoMarkings` | `tempoMarkingsWalk` | **Partial** | Espresso: **18** `applyTempo` / `verifyTempoMarking` pairs (exact string resource match). Appium: **8** tempo checkpoints with **English** substring defaults (`_ESPRESSO_TEMPO_MARKING_WALK`). Missing intermediate tempos (e.g. 59, 65, 75, 107, 119, 167, 169, 252). Env vars `METRONOME_TEMPO_MARKING_*_SUBSTR` for locale. |
| `beatsErrorWhenValueTooBig` | `invalidBeatsReset` | **Touch-only** | Espresso: slider 1, type `9`, **assert** `beats_edit_layout` error + slider unchanged. Appium: `1` → `9` → restore `4`; **no** layout error assertion. |
| `beatsErrorWhenValueNotANumber` | `beatsErrorNonNumericTouch` | **Touch-only** | Espresso: type `.`, assert error. Appium: `1` → `.` → restore `4`. |
| `subdivisionsErrorWhenValueTooBig` | `subdivisionsErrorTooBigTouch` | **Touch-only** | Espresso: subdivisions `5` invalid from base 1. Appium: `1` → `5` → restore `2` (same restore shape as our suite, not necessarily Espresso’s implied “2”). |
| `subdivisionsErrorWhenValueNotANumber` | *(none)* | **Not implemented** | Could add mirror of `.` + restore if needed. |
| `tempoErrorWhenValueTooBig` | `tempoErrorTooBigTouch` | **Touch-only** | Espresso: tempo 30, `253`, assert error on layout. Appium: `30` → `253` → restore `80`. |
| `tempoErrorWhenValueNotANumber` | *(none)* | **Not implemented** | Same gap as subdivisions non-number for tempo field. |

---

## 3. What Appium adds that Espresso does not define as `@Test`

| Mechanism | Purpose |
|-----------|---------|
| **`_espresso_mirror_ui_pulse_until_near_deadline`** | Keeps **continuous UI** (FAB, tempo ±, tick viz, tap tempo, swipes) between suite rounds until ~8s before workload deadline — fills JSON `duration` with interaction. |
| **`_espresso_mirror_ui_pulse_final_gap`** | Uses last ~second(s) before deadline. |
| **Baseline workload** (`APPIUM_WORKLOAD=metronome`) | Separate longer structured tap loop — **not** Espresso-mapped; documented elsewhere. |

These are **energy / workload saturations**, not claims of Espresso parity.

---

## 4. Summary counts (for validation slides)

| Category | Count |
|----------|-------|
| Espresso `@Test` methods in `InstrumentedTest` | **13** |
| Named Appium espresso_mirror scenarios per suite round | **9** |
| Espresso tests with a **dedicated** Appium analogue (full / partial / touch-only) | **9** of **13** (`contentVisible`, `subdivisionsErrorWhenValueNotANumber`, `tempoErrorWhenValueNotANumber` have **no** dedicated scenario) |
| `tempoMarkings` | Espresso **18** checkpoints → Appium **8** tempo stops in `_ESPRESSO_TEMPO_MARKING_WALK` (subset) |

---

## 5. Suggested wording for a thesis / defence

> We automated the **same Metronome UI surfaces** addressed in `InstrumentedTest`, using **resource IDs**
> and **edit fields** on the **same APK** used in energy experiments. Where Espresso asserts internal
> slider positions and `TextInputLayout` errors, our black-box driver records **success booleans** and
> **optional** marking substrings. **Gesture paths** differ (typing edits vs `SliderUtils.setValue`)
> but align with the app’s **slider–edit coupling** tests as behavioural intent. **Continuous tap/swipe**
> fillers occupy remaining experiment time without claiming equivalence to a specific Espresso `@Test`.

---

## 6. References (paths in this repo workspace)

- Espresso:  
  `app_repositories_newest/app_repositories/Kr0oked_Metronome/app/src/androidTest/java/com/bobek/metronome/InstrumentedTest.kt`
- Appium mirror:  
  `android-runner/examples/batterymanager/Scripts/interaction_appium_metronome.py` (`run_espresso_mirror_workload`)
- Thin hook:  
  `android-runner/examples/batterymanager/Scripts/interaction_appium_metronome_espresso_mirror.py`

*Generated for validation; update this file if scenarios or `_ESPRESSO_TEMPO_MARKING_WALK` change.*
