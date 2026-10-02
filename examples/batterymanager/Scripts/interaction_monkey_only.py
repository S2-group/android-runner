# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    import time

    # Same Monkey workload as interaction.py, but no sysfs loop.
    # Run length is the NativeExperiment `duration` (ms in JSON → seconds in code): after
    # this returns, NativeExperiment still calls time.sleep(self.duration), so the profiled
    # window matches a normal native run (Monkey started, then one sleep in the parent).

    print("=INTERACTION_MONKEY_ONLY=")
    print("DEVICE:", device.id)

    experiment = args[0] if len(args) >= 1 else None
    package = getattr(experiment, "package", None) if experiment is not None else None
    if not package:
        package = kwargs.get("app") or kwargs.get("package")

    if not package:
        print("INTERACTION_MONKEY_ONLY: No package found; skipping.")
        return

    monkey_cmd = (
        "monkey -p {pkg} --throttle 100 -s 1234 -v 1000 "
        "--ignore-crashes --ignore-timeouts --ignore-security-exceptions "
        "--pct-flip 0 --pct-trackball 0"
    ).format(pkg=package)
    wrapped = "sh -c '{}'".format(monkey_cmd.replace("'", "'\"'\"'") + " >/dev/null 2>&1 &")
    print("INTERACTION_MONKEY_ONLY: starting Monkey (background) for:", package)
    device.shell(wrapped)
    print("INTERACTION_MONKEY_ONLY: no sysfs; power data comes from BatteryManager profiler (if enabled).")
    return
