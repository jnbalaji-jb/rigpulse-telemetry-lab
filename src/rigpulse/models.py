"""Domain models and trustworthy input validation."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

RIG_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
METRICS = ("temperature_c", "pressure_bar", "vibration_mm_s", "flow_m3_h")


def normalize_timestamp(raw: str) -> str:
    """Convert ISO-8601 timestamps into stable UTC seconds for lexicographic ordering."""
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {raw!r}") from exc
    if dt.tzinfo is None:
        raise ValueError("timestamp must include a timezone (e.g. +00:00 or Z)")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Reading:
    observed_at: str
    rig_id: str
    metric: str
    value: float

    @classmethod
    def parse(cls, source: dict[str, str]) -> "Reading":
        rig_id = str(source.get("rig_id", "")).strip()
        metric = str(source.get("metric", "")).strip()
        if not RIG_ID_RE.fullmatch(rig_id):
            raise ValueError(f"invalid rig_id: {rig_id!r}")
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")
        try:
            value = float(source["value"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("value must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return cls(normalize_timestamp(source.get("observed_at", "")), rig_id, metric, value)


@dataclass(frozen=True, slots=True)
class Alert:
    reading_id: int
    detector: str
    severity: str
    score: float
    explanation: str
