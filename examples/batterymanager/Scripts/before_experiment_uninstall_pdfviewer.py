# noinspection PyUnusedLocal
"""``before_experiment`` hook: chain device-state controls + BATTERY_STATS auto-grant + Pdf-Viewer uninstall.

Same pattern as the metronome / tipuous / repertoire / linkhub / diaguard / avnc uninstall hooks.
See ``before_experiment_uninstall_metronome.py`` for the canonical docstring.

PACKAGE = ``com.rajat.sample.pdfviewer`` — applicationId is unsuffixed across debug + release
(no ``applicationIdSuffix`` in ``app/build.gradle.kts``), so the same value works for every
variant including Bangcle.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PACKAGE = "com.rajat.sample.pdfviewer"


def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _device_state
        _device_state.apply_device_state(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_pdfviewer: device-state controls chain "
                "failed (continuing with grant + uninstall): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    try:
        import before_experiment_grant_battery_stats as _grant
        _grant.grant_battery_stats(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_pdfviewer: BATTERY_STATS auto-grant chain "
                "failed (continuing with uninstall): {}: {}\n".format(
                    type(exc).__name__, exc
                )
            )
        except Exception:
            pass

    try:
        import before_experiment_disable_radios_and_savers as _radios
        _radios.disable_radios_and_savers(device)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall: disable_radios_and_savers chain "
                "failed (continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        import before_experiment_sync_fixtures as _fixtures
        _fixtures.sync_fixtures(device)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall: sync_fixtures chain failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass

    try:
        if PACKAGE not in device.get_app_list():
            device.logger.info(
                "%s not installed; APK will be installed from experiment paths.",
                PACKAGE,
            )
            return
        device.logger.info(
            "Uninstalling %s so the device only receives the APK from this run's paths.",
            PACKAGE,
        )
        device.uninstall(PACKAGE)
    except Exception as exc:
        try:
            sys.stderr.write(
                "before_experiment_uninstall_pdfviewer: uninstall step failed "
                "(continuing): {}: {}\n".format(type(exc).__name__, exc)
            )
        except Exception:
            pass
