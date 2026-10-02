# noinspection PyUnusedLocal
"""
Espresso-aligned Appium hook — same Android Runner lifecycle as
``interaction_appium_metronome.py``, but forces ``APPIUM_WORKLOAD=espresso_mirror`` so the shared
implementation in ``appium_android_tests.metronome`` runs the InstrumentedTest-inspired scenario
suite (``initialState``, edit/slider coupling, ``tempoMarkings`` walk, error touches).

Use this script in experiment JSON when you want to **classify** runs explicitly as
"Espresso-surface" vs the default baseline / exploratory ``interaction_appium_metronome.py`` hook.
Equivalent: keep the default hook and ``export APPIUM_WORKLOAD=espresso_mirror``.
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

# Tag this run as the metronome app so after_experiment.py's tracking-matrix
# updater records `app=metronome` reliably (independent of the legacy default).
# Uses setdefault so an explicit caller-set APPIUM_APP still wins.
os.environ.setdefault("APPIUM_APP", "metronome")


def main(device, *args, **kwargs):
    os.environ["APPIUM_WORKLOAD"] = "espresso_mirror"
    from appium_android_tests import metronome

    experiment = args[0] if len(args) >= 1 else None
    metronome.run_workload(experiment=experiment, device=device)
