"""Thin AndroidRunner hook for Keep-Recipe."""
from __future__ import annotations
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.path.abspath(os.path.join(_HERE, *([os.pardir] * 4)))
if _WS not in sys.path: sys.path.insert(0, _WS)
os.environ.setdefault("APPIUM_APP", "keep_recipe")

def main(device, *args, **kwargs):
    from appium_android_tests import keep_recipe
    experiment = args[0] if len(args) >= 1 else None
    keep_recipe.run_workload(experiment=experiment, device=device)
