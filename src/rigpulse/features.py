"""Deterministic, time-aligned, past-only telemetry features.

Rows are labeled by the END of their UTC bucket [start, end). Each row may
use observations in its own *completed* bucket and previous buckets, but
never observations after the indicated bucket end.

No model is trained here. This is the first building block for Issue #1.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Iterable, Mapping

from rigpulse.models import METRICS, normalize_timestamp
from rigpulse.storage import ordered_readings


def _utc_datetime(value: str) -> datetime:
    normalized = normalize_timestamp(value)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _bucket_start(timestamp: datetime, minutes: int) -> datetime:
    if minutes < 1 or minutes > 1440 or 1440 % minutes:
        raise ValueError("bucket_minutes must be a positive divisor of 1440")
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((timestamp - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=(elapsed_minutes // minutes) * minutes)


def _iso(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def align_series(
    readings: Iterable[Mapping[str, object]], bucket_minutes: int = 60,
) -> list[dict[str, object]]:
    """Align readings per rig into UTC buckets, preserving missing data.

    A row represents [bucket_end - bucket_minutes, bucket_end); the latest
    observation in that interval is chosen for each metric. No forward/back
    filling occurs. Entirely empty intervals between observed buckets are
    emitted with all four metrics missing. Rigs are processed independently.
    """
    if bucket_minutes < 1 or bucket_minutes > 1440 or 1440 % bucket_minutes:
        raise ValueError("bucket_minutes must be a positive divisor of 1440")

    latest: dict[tuple[str, datetime, str], tuple[datetime, float]] = {}
    bounds: dict[str, tuple[datetime, datetime]] = {}
    for row in readings:
        rig = str(row["rig_id"])
        metric = str(row["metric"])
        if metric not in METRICS:
            raise ValueError(f"unsupported metric: {metric!r}")
        ts = _utc_datetime(str(row["observed_at"]))
        value = float(row["value"])
        if not math.isfinite(value):
            raise ValueError("reading value must be finite")
        start = _bucket_start(ts, bucket_minutes)
        key = (rig, start, metric)
        prev = latest.get(key)
        if prev is None or (ts, value) > prev:
            latest[key] = (ts, value)
        if rig not in bounds:
            bounds[rig] = (start, start)
        else:
            lo, hi = bounds[rig]
            bounds[rig] = (min(start, lo), max(start, hi))

    rows: list[dict[str, object]] = []
    step = timedelta(minutes=bucket_minutes)
    for rig, (lo, hi) in sorted(bounds.items()):
        bucket = lo
        while bucket <= hi:
            output: dict[str, object] = {"rig_id": rig, "bucket_end_utc": _iso(bucket + step)}
            present = 0
            for metric in METRICS:
                picked = latest.get((rig, bucket, metric))
                output[metric] = picked[1] if picked is not None else None
                output[f"{metric}_missing"] = int(picked is None)
                present += int(picked is not None)
            output["present_metrics"] = present
            rows.append(output)
            bucket += step
    return rows


def rolling_features(aligned: Iterable[Mapping[str, object]], window: int = 12) -> list[dict[str, object]]:
    """Add previous-only trends and current-bucket cross-sensor features.

    A rolling median or population standard deviation at time t only uses
    samples from buckets strictly earlier than t. Missing buckets stay in the
    fixed-size history so there is no silent time-gap compression.
    """
    if window < 2:
        raise ValueError("window must be at least 2")
    by_rig: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in aligned:
        by_rig[str(row["rig_id"])].append(row)

    results: list[dict[str, object]] = []
    for rig, rows in sorted(by_rig.items()):
        rows = sorted(rows, key=lambda r: str(r["bucket_end_utc"]))
        histories = {metric: deque(maxlen=window) for metric in METRICS}
        previous_ratio: float | None = None
        seen: set[str] = set()
        for source in rows:
            at = str(source["bucket_end_utc"])
            if at in seen:
                raise ValueError(f"duplicate bucket end for rig {rig}: {at}")
            seen.add(at)
            row = dict(source)
            for metric in METRICS:
                current = source[metric]
                past = histories[metric]
                prior_values = [float(x) for x in past if x is not None]
                row[f"{metric}_history_count"] = len(prior_values)
                row[f"{metric}_past_median"] = median(prior_values) if prior_values else None
                row[f"{metric}_past_std"] = (
                    pstdev(prior_values) if len(prior_values) > 1 else None
                )
                immediate_previous = past[-1] if past else None
                row[f"{metric}_delta_1"] = (
                    float(current) - float(immediate_previous)
                    if current is not None and immediate_previous is not None else None
                )
            pressure = source["pressure_bar"]
            flow = source["flow_m3_h"]
            vibration = source["vibration_mm_s"]
            ratio = (
                float(pressure) / float(flow)
                if pressure is not None and flow is not None and abs(float(flow)) > 1e-12
                else None
            )
            vf_ratio = (
                float(vibration) / float(flow)
                if vibration is not None and flow is not None and abs(float(flow)) > 1e-12
                else None
            )
            row["pressure_flow_ratio"] = ratio
            row["vibration_flow_ratio"] = vf_ratio
            row["vibration_flow_ratio_delta_1"] = (
                vf_ratio - previous_ratio
                if vf_ratio is not None and previous_ratio is not None else None
            )
            for metric in METRICS:
                histories[metric].append(source[metric])
            previous_ratio = vf_ratio
            results.append(row)
    return results


def feature_columns() -> list[str]:
    """Stable CSV schema for downstream experiments and model training."""
    cols = ["rig_id", "bucket_end_utc", "present_metrics"]
    for metric in METRICS:
        cols.extend([
            metric,
            f"{metric}_missing",
            f"{metric}_history_count",
            f"{metric}_past_median",
            f"{metric}_past_std",
            f"{metric}_delta_1",
        ])
    return cols + ["pressure_flow_ratio", "vibration_flow_ratio", "vibration_flow_ratio_delta_1"]


def export_features(
    db: str | Path, out: str | Path, bucket_minutes: int = 60, window: int = 12,
) -> dict[str, object]:
    """Export reproducible features without reading labels or modifying alerts."""
    rows = rolling_features(align_series(ordered_readings(db), bucket_minutes), window)
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    cols = feature_columns()
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=cols, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {
        "output": str(output),
        "rows": len(rows),
        "rigs": len({row["rig_id"] for row in rows}),
        "features": len(cols) - 2,
        "bucket_minutes": bucket_minutes,
        "window": window,
        "warning": "Prototype feature export only; no trained ML detector or real-world validation.",
    }
