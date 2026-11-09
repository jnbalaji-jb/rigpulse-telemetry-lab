"""Population Stability Index for the earliest and latest time windows per metric."""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict

from rigpulse.storage import ordered_readings


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        raise ValueError("empty distribution")
    k = (len(sorted_values) - 1) * p
    a = math.floor(k)
    b = math.ceil(k)
    return sorted_values[a] + (sorted_values[b] - sorted_values[a]) * (k - a)


def psi(reference: list[float], current: list[float], buckets: int = 8) -> float:
    if not reference or not current:
        raise ValueError("reference and current must be nonempty")
    if buckets < 2:
        raise ValueError("buckets must be >=2")
    ref_sorted = sorted(reference)
    # Deduplicate edges for constant-valued reference distributions.
    edges = sorted(set(percentile(ref_sorted, k / buckets) for k in range(1, buckets)))
    bins = len(edges) + 1
    a, b = [0] * bins, [0] * bins
    for x in reference:
        a[bisect_right(edges, x)] += 1
    for x in current:
        b[bisect_right(edges, x)] += 1
    smoothing = 1e-4
    return sum(
        ((y / len(current) + smoothing) - (x / len(reference) + smoothing))
        * math.log((y / len(current) + smoothing) / (x / len(reference) + smoothing))
        for x, y in zip(a, b)
    )


def drift_report(db: str, min_samples: int = 24) -> dict:
    groups = defaultdict(list)
    for r in ordered_readings(db):
        groups[r["metric"]].append(r["value"])
    metrics = []
    for metric, points in sorted(groups.items()):
        n = len(points) // 2
        if n < min_samples:
            metrics.append({"metric": metric, "status": "insufficient_data", "samples_per_window": n})
            continue
        score = psi(points[:n], points[-n:])
        status = "high_drift" if score >= 0.25 else "watch" if score >= 0.1 else "stable"
        metrics.append({"metric": metric, "psi": round(score, 5),
                        "status": status, "samples_per_window": n})
    return {"comparison": "first half vs last half of observations by metric",
            "method": "quantile-binned population stability index (PSI)",
            "metrics": metrics,
            "caution": "PSI flags a distribution change; it does not prove a fault. Per-rig stratification is a future milestone."}
