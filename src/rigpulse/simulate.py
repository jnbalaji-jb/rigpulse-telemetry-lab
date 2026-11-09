"""Deterministic synthetic equipment signals with separately recorded injected events."""
from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rigpulse.models import METRICS


def generate(out_dir: str | Path, rigs: int = 4, hours: int = 240,
             seed: int = 42) -> dict:
    if not 1 <= rigs <= 50 or not 24 <= hours <= 10000:
        raise ValueError("rigs must be in [1,50], hours in [24,10000]")
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    telemetry = output / "telemetry.csv"
    truth = output / "synthetic_ground_truth.csv"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"temperature_c": 67.0, "pressure_bar": 179.0,
            "vibration_mm_s": 2.1, "flow_m3_h": 335.0}
    noise = {"temperature_c": 0.95, "pressure_bar": 2.7,
             "vibration_mm_s": 0.12, "flow_m3_h": 7.5}
    cycle = {"temperature_c": 3.8, "pressure_bar": 6.0,
             "vibration_mm_s": 0.2, "flow_m3_h": 8.0}
    offsets = {"temperature_c": 35.0, "pressure_bar": -76.0,
               "vibration_mm_s": 7.5, "flow_m3_h": -142.0}
    records = events = 0
    with telemetry.open("w", encoding="utf-8", newline="") as f, \
         truth.open("w", encoding="utf-8", newline="") as g:
        wr = csv.writer(f)
        gt = csv.writer(g)
        wr.writerow(["observed_at", "rig_id", "metric", "value"])
        gt.writerow(["observed_at", "rig_id", "metric", "is_anomaly"])
        for t in range(hours):
            ts = (start + timedelta(hours=t)).isoformat().replace("+00:00", "Z")
            for j in range(rigs):
                rig = f"RIG-{j+1:03d}"
                # Three true incident timestamps per rig when the horizon permits.
                event_times = {int(hours * fraction) + j * 2 for fraction in (0.24, 0.52, 0.8)}
                for metric in METRICS:
                    baseline = base[metric] + j * noise[metric] * 0.6
                    value = (baseline + cycle[metric] * math.sin(2 * math.pi * t / 24 + j / 3)
                             + rng.gauss(0, noise[metric]))
                    # A mild sustained deterioration in one rig: useful for future drift work.
                    if j == rigs - 1 and t >= int(hours * 0.7):
                        value += noise[metric] * (t - hours * 0.7) / max(1, hours * 0.3) * 2.0
                    is_event = int(t in event_times)
                    if is_event:
                        value += offsets[metric]
                        events += 1
                    wr.writerow([ts, rig, metric, f"{value:.5f}"])
                    gt.writerow([ts, rig, metric, is_event])
                    records += 1
    return {"telemetry_path": str(telemetry), "truth_path": str(truth),
            "readings": records, "injected_event_readings": events, "seed": seed,
            "data_source": "synthetic; not actual industrial equipment"}
