"""``before_experiment`` for PoetsKingdom."""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path: sys.path.insert(0, _HERE)
PACKAGE = "com.wendorochena.poetskingdom"

def main(device, *args, **kwargs):
    try:
        import before_experiment_apply_device_state as _ds; _ds.apply_device_state(device)
    except SystemExit: raise
    except Exception as exc:
        try: sys.stderr.write("device_state chain failed: %s: %s\n" % (type(exc).__name__, exc))
        except Exception: pass
    try:
        import before_experiment_grant_battery_stats as _g; _g.grant_battery_stats(device)
    except Exception as exc:
        try: sys.stderr.write("battery_stats grant failed: %s: %s\n" % (type(exc).__name__, exc))
        except Exception: pass
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
            device.logger.info("%s not installed; will install from paths.", PACKAGE); return
        device.logger.info("Uninstalling %s.", PACKAGE); device.uninstall(PACKAGE)
    except Exception as exc:
        try: sys.stderr.write("uninstall step failed: %s: %s\n" % (type(exc).__name__, exc))
        except Exception: pass
