"""Interpretable, leakage-free univariate detectors (rules + rolling robust z-score)."""
from __future__ import annotations

from collections import defaultdict, deque
from statistics import median

from rigpulse.models import Alert
from rigpulse.storage import ordered_readings, replace_alerts

# These are ILLUSTRATIVE sample-equipment limits, not industrial safety limits.
LIMITS = {
    "temperature_c": ("upper", 96.0),
    "pressure_bar": ("lower", 128.0),
    "vibration_mm_s": ("upper", 6.8),
    "flow_m3_h": ("lower", 220.0),
}
FLOORS = {
    "temperature_c": 1.5,
    "pressure_bar": 3.0,
    "vibration_mm_s": 0.15,
    "flow_m3_h": 9.0,
}


def detect(db: str, window: int = 48, min_history: int = 16, z_threshold: float = 4.5) -> dict:
    if window < 8 or min_history < 5 or min_history > window:
        raise ValueError("require window>=8, min_history>=5, min_history<=window")
    if z_threshold <= 0:
        raise ValueError("z_threshold must be positive")
    history = defaultdict(lambda: deque(maxlen=window))
    findings: list[Alert] = []
    for r in ordered_readings(db):
        series = (r["rig_id"], r["metric"])
        value, metric, rid = r["value"], r["metric"], r["id"]

        # Static operating constraints can flag early incidents before a warm-up period.
        side, bound = LIMITS[metric]
        breached = value > bound if side == "upper" else value < bound
        if breached:
            excess = (value - bound if side == "upper" else bound - value)
            severity = "critical" if excess > 0.12 * abs(bound) else "warning"
            findings.append(Alert(rid, "operating_limit", severity, round(excess, 5),
                                  f"{metric} {value:.2f} exceeded sample {side} limit {bound:.2f}"))

        # Use only earlier observations. The current reading is appended AFTER scoring.
        past = history[series]
        if len(past) >= min_history:
            centre = median(past)
            mad = median(abs(x - centre) for x in past)
            sigma = max(1.4826 * mad, FLOORS[metric])
            z = abs(value - centre) / sigma
            if z >= z_threshold:
                severity = "critical" if z >= 7.5 else "warning"
                findings.append(Alert(rid, "rolling_robust_z", severity, round(z, 5),
                                      f"{metric} deviated {z:.1f} robust SD from the prior {len(past)} observations"))
        past.append(value)
    replace_alerts(db, findings)
    return {
        "readings_scored": sum(1 for _ in ordered_readings(db)),
        "alerts_created": len(findings),
        "by_detector": {d: sum(a.detector == d for a in findings)
                        for d in ("operating_limit", "rolling_robust_z")},
        "parameters": {"window": window, "min_history": min_history, "z_threshold": z_threshold},
    }
