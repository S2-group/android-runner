# noinspection PyUnusedLocal
def main(device, *args, **kwargs):
    import csv
    import time
    import os.path as op

    import paths

    print("=INTERACTION=")
    print("DEVICE:", device.id)

    experiment = args[0] if len(args) >= 1 else None
    package = getattr(experiment, "package", None) if experiment is not None else None
    duration_s = getattr(experiment, "duration", 60)

    if not package:
        package = kwargs.get("app") or kwargs.get("package")

    if not package:
        print("INTERACTION: No package found; skipping.")
        return

    # Start Monkey in background but silence stdout/stderr so AndroidRunner.Adb.shell
    # doesn't treat Monkey output containing "error" as an adb error.
    monkey_cmd = (
        "monkey -p {pkg} --throttle 100 -s 1234 -v 1000 "
        "--ignore-crashes --ignore-timeouts --ignore-security-exceptions "
        "--pct-flip 0 --pct-trackball 0"
    ).format(pkg=package)
    wrapped = "sh -c '{}'".format(monkey_cmd.replace("'", "'\"'\"'") + " >/dev/null 2>&1 &")
    print("INTERACTION: starting Monkey for:", package)
    device.shell(wrapped)

    # Energy sampling via sysfs/dumpsys (works on unrooted Android 15; companion app needs BATTERY_STATS).
    # current_now: usually in µA (may be negative when discharging), voltage_now: usually in µV.
    out_dir = getattr(paths, "OUTPUT_DIR", ".")
    out_path = op.join(out_dir, "sysfs_power_{}_{}.csv".format(device.id, time.strftime("%Y.%m.%d_%H%M%S")))
    interval_s = 0.1
    end_t = time.time() + float(duration_s)

    def read_int(cmd):
        try:
            return int(device.shell(cmd).strip().splitlines()[-1])
        except Exception:
            return None

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch_ms", "current_now_ua", "voltage_now_uv"])
        while time.time() < end_t:
            now_ms = int(time.time() * 1000)
            cur = read_int("cat /sys/class/power_supply/battery/current_now")
            volt = read_int("cat /sys/class/power_supply/battery/voltage_now")
            w.writerow([now_ms, cur, volt])
            time.sleep(interval_s)

    print("INTERACTION: wrote power samples to", out_path)
