"""Per-app Appium hook template. Copy to ``Scripts/interaction_appium_<app_id>.py``,
replace ``<app_id>`` everywhere (folder under ``appium_android_tests/``), and reference
from the experiment JSON as
``"interaction": "examples/batterymanager/Scripts/interaction_appium_<app_id>.py"``.
See ``README-appium-hooks.md`` for the env-var contract.
"""
from __future__ import annotations
import os, os.path as op, sys

_HERE = op.dirname(op.abspath(__file__))
_WORKSPACE_ROOT = op.abspath(op.join(_HERE, *([os.pardir] * 4)))
for _p in (_WORKSPACE_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(device, *args, **kwargs):
    os.environ["APPIUM_APP"] = "<app_id>"
    import interaction_appium
    interaction_appium.main(device, *args, **kwargs)
