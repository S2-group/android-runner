#!/usr/bin/env python3
"""
Compute power/energy from sysfs battery samples produced by Scripts/interaction.py.

Inputs (per run): sysfs_power_*.csv with columns:
  - epoch_ms
  - current_now_ua   (microamps, can be negative while discharging)
  - voltage_now_uv   (microvolts)

Outputs:
  - sysfs_energy_summary.csv (one row per input file)
  - sysfs_energy_<input>.csv (optional per-sample power; enable with --write-power-csv)
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional


@dataclass(frozen=True)
class Sample:
    t_s: float
    current_a: float
    voltage_v: float

    @property
    def power_w(self) -> float:
        # Use magnitude so discharging (-I) yields positive power draw.
        return abs(self.current_a) * self.voltage_v


def iter_sysfs_csv_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("sysfs_power_*.csv"):
        if p.is_file():
            yield p


def parse_samples(path: Path) -> list[Sample]:
    samples: list[Sample] = []
    with path.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            epoch_ms = int(row["epoch_ms"])
            current_ua = row.get("current_now_ua")
            voltage_uv = row.get("voltage_now_uv")
            if current_ua is None or voltage_uv is None:
                continue
            if current_ua == "" or voltage_uv == "":
                continue

            current_a = float(current_ua) * 1e-6
            voltage_v = float(voltage_uv) * 1e-6
            t_s = epoch_ms / 1000.0
            samples.append(Sample(t_s=t_s, current_a=current_a, voltage_v=voltage_v))
    return samples


def trapz_energy_j(samples: list[Sample]) -> float:
    if len(samples) < 2:
        return float("nan")
    e = 0.0
    for a, b in zip(samples, samples[1:]):
        dt = b.t_s - a.t_s
        if dt <= 0:
            continue
        e += 0.5 * (a.power_w + b.power_w) * dt
    return e


def duration_s(samples: list[Sample]) -> float:
    if len(samples) < 2:
        return float("nan")
    return max(0.0, samples[-1].t_s - samples[0].t_s)


def safe_mean(values: Iterable[float]) -> float:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def write_power_csv(input_path: Path, samples: list[Sample]) -> Path:
    out_path = input_path.with_name(input_path.stem.replace("sysfs_power_", "sysfs_energy_") + ".csv")
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "current_a", "voltage_v", "power_w"])
        for s in samples:
            w.writerow([s.t_s, s.current_a, s.voltage_v, s.power_w])
    return out_path


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "root",
        nargs="?",
        default=str(Path(__file__).resolve().parent / "output"),
        help="Root directory containing Android Runner output/ (default: examples/batterymanager/output)",
    )
    ap.add_argument("--write-power-csv", action="store_true", help="Write per-sample power CSV next to each input file.")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    files = sorted(iter_sysfs_csv_files(root))
    if not files:
        raise SystemExit(f"No sysfs_power_*.csv found under {root}")

    # Put summary at the root of the selected output folder (or provided root).
    summary_path = root / "sysfs_energy_summary.csv"
    with summary_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "input_file",
                "n_samples",
                "duration_s",
                "avg_current_a_abs",
                "avg_voltage_v",
                "avg_power_w",
                "energy_trapz_j",
            ]
        )
        for p in files:
            samples = parse_samples(p)
            dur = duration_s(samples)
            e_j = trapz_energy_j(samples)
            avg_i = safe_mean(abs(s.current_a) for s in samples)
            avg_v = safe_mean(s.voltage_v for s in samples)
            avg_p = safe_mean(s.power_w for s in samples)

            if args.write_power_csv:
                write_power_csv(p, samples)

            w.writerow([str(p.relative_to(root)), len(samples), dur, avg_i, avg_v, avg_p, e_j])

    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

