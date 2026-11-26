"""Milestone M2: Event-time streaming ingest, watermarks, backpressure, and checkpoints."""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Protocol

import numpy as np

from rigpulse.detection import FLOORS, LIMITS
from rigpulse.models import METRICS, RIG_ID_RE, Reading, normalize_timestamp
from rigpulse.storage import connect, initialize, insert_readings

STREAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_checkpoints (
    stream_id TEXT PRIMARY KEY,
    watermark TEXT NOT NULL,
    processed_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL,
    late_count INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(raw: str) -> datetime:
    norm = normalize_timestamp(raw)
    return datetime.fromisoformat(norm.replace("Z", "+00:00"))


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_stream_schema(db: str | Path) -> None:
    """Initialize core tables plus the stream_checkpoints table in SQLite."""
    initialize(db)
    with connect(db) as conn:
        conn.executescript(STREAM_SCHEMA)


@dataclass(frozen=True, slots=True)
class StreamEnvelope:
    """Typed streaming telemetry event with idempotency key and event/ingest timestamps."""

    event_id: str
    rig_id: str
    observed_at: str
    metric: str
    value: float
    ingested_at: str = field(default_factory=_utc_now_iso)
    source: str = "mqtt"

    def __post_init__(self) -> None:
        if not self.event_id or not str(self.event_id).strip():
            raise ValueError("event_id must be non-empty")
        if not RIG_ID_RE.fullmatch(self.rig_id):
            raise ValueError(f"invalid rig_id: {self.rig_id!r}")
        if self.metric not in METRICS:
            raise ValueError(f"unknown metric {self.metric!r}; expected one of {METRICS}")
        norm_obs = normalize_timestamp(self.observed_at)
        norm_ing = normalize_timestamp(self.ingested_at)
        object.__setattr__(self, "observed_at", norm_obs)
        object.__setattr__(self, "ingested_at", norm_ing)
        object.__setattr__(self, "value", float(self.value))

    def to_reading(self) -> Reading:
        return Reading.parse(
            {
                "observed_at": self.observed_at,
                "rig_id": self.rig_id,
                "metric": self.metric,
                "value": str(self.value),
            }
        )


class StreamConsumer(Protocol):
    """Pluggable consumer interface for MQTT, Kafka, or replay stream adapters."""

    source_name: str

    def poll(self, max_records: int = 100) -> list[StreamEnvelope]:
        """Fetch up to max_records envelopes from the underlying stream source."""
        ...


class BoundedEventQueue:
    """Thread-safe bounded event queue with explicit backpressure policies."""

    def __init__(self, maxsize: int = 1024, policy: str = "drop_oldest") -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if policy not in {"drop_oldest", "reject_newest"}:
            raise ValueError("policy must be 'drop_oldest' or 'reject_newest'")
        self.maxsize = maxsize
        self.policy = policy
        self._buffer: deque[StreamEnvelope] = deque()
        self._lock = threading.Lock()
        self.enqueued_count = 0
        self.dropped_count = 0

    def push(self, envelope: StreamEnvelope) -> bool:
        """Enqueue an event under backpressure; returns True if accepted."""
        with self._lock:
            if len(self._buffer) >= self.maxsize:
                self.dropped_count += 1
                if self.policy == "drop_oldest":
                    self._buffer.popleft()
                else:
                    return False
            self._buffer.append(envelope)
            self.enqueued_count += 1
            return True

    def pop(self) -> StreamEnvelope | None:
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer.popleft()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "depth": len(self._buffer),
                "maxsize": self.maxsize,
                "policy": self.policy,
                "enqueued_count": self.enqueued_count,
                "dropped_count": self.dropped_count,
            }


class IncrementalStreamDetector:
    """Stateful event-time detector with deduplication, watermarks, and SQLite checkpoints."""

    def __init__(
        self,
        stream_id: str = "default-stream",
        allowed_lateness_seconds: int = 3600,
        window: int = 24,
        min_history: int = 8,
        z_threshold: float = 4.5,
    ) -> None:
        if window < 4 or min_history < 2 or min_history > window:
            raise ValueError("require window >= 4, min_history >= 2, min_history <= window")
        self.stream_id = stream_id
        self.allowed_lateness = timedelta(seconds=allowed_lateness_seconds)
        self.window = window
        self.min_history = min_history
        self.z_threshold = z_threshold

        self.watermark: datetime | None = None
        self.seen_event_ids: deque[str] = deque(maxlen=10000)
        self._seen_set: set[str] = set()
        self.windows: dict[str, list[float]] = {}

        self.processed_count = 0
        self.duplicate_count = 0
        self.late_count = 0
        self.emitted_alerts: list[dict[str, Any]] = []

    def _record_event_id(self, event_id: str) -> bool:
        if event_id in self._seen_set:
            return False
        if len(self.seen_event_ids) == self.seen_event_ids.maxlen:
            evicted = self.seen_event_ids.popleft()
            self._seen_set.discard(evicted)
        self.seen_event_ids.append(event_id)
        self._seen_set.add(event_id)
        return True

    def process(self, envelope: StreamEnvelope, db: str | Path | None = None) -> dict[str, Any]:
        """Process one event with idempotency, watermark lateness tracking, and online scoring."""
        if not self._record_event_id(envelope.event_id):
            self.duplicate_count += 1
            return {"status": "duplicate", "event_id": envelope.event_id, "alerts": []}

        event_dt = _parse_utc(envelope.observed_at)
        is_late = False
        if self.watermark is not None and event_dt < (self.watermark - self.allowed_lateness):
            is_late = True
            self.late_count += 1

        if self.watermark is None or event_dt > self.watermark:
            self.watermark = event_dt

        reading = envelope.to_reading()
        key = f"{reading.rig_id}:{reading.metric}"
        history = self.windows.setdefault(key, [])

        alerts: list[dict[str, Any]] = []
        side, bound = LIMITS[reading.metric]
        breached = reading.value > bound if side == "upper" else reading.value < bound
        if breached:
            excess = reading.value - bound if side == "upper" else bound - reading.value
            severity = "critical" if excess > 0.12 * abs(bound) else "warning"
            alerts.append(
                {
                    "observed_at": reading.observed_at,
                    "rig_id": reading.rig_id,
                    "metric": reading.metric,
                    "value": reading.value,
                    "detector": "stream_operating_limit",
                    "severity": severity,
                    "score": round(excess, 5),
                    "explanation": (
                        f"{reading.metric} {reading.value:.2f} exceeded sample {side} "
                        f"limit {bound:.2f} (late={is_late})"
                    ),
                }
            )

        if len(history) >= self.min_history:
            past = history[-self.window :]
            centre = median(past)
            mad = median(abs(x - centre) for x in past)
            sigma = max(1.4826 * mad, FLOORS[reading.metric])
            z = abs(reading.value - centre) / sigma
            if z >= self.z_threshold:
                severity = "critical" if z >= 7.5 else "warning"
                alerts.append(
                    {
                        "observed_at": reading.observed_at,
                        "rig_id": reading.rig_id,
                        "metric": reading.metric,
                        "value": reading.value,
                        "detector": "stream_robust_z",
                        "severity": severity,
                        "score": round(z, 5),
                        "explanation": (
                            f"{reading.metric} deviated {z:.1f} robust SD from prior "
                            f"{len(past)} stream observations (late={is_late})"
                        ),
                    }
                )

        history.append(reading.value)
        if len(history) > self.window:
            del history[: len(history) - self.window]

        self.processed_count += 1
        self.emitted_alerts.extend(alerts)

        if db is not None:
            insert_readings(db, [reading])
            if alerts:
                self._persist_stream_alerts(db, alerts)

        return {
            "status": "late_processed" if is_late else "processed",
            "event_id": envelope.event_id,
            "watermark": _format_utc(self.watermark),
            "alerts": alerts,
        }

    @staticmethod
    def _persist_stream_alerts(db: str | Path, alerts: list[dict[str, Any]]) -> None:
        initialize(db)
        with connect(db) as conn:
            for a in alerts:
                row = conn.execute(
                    "SELECT id FROM readings WHERE observed_at=? AND rig_id=? AND metric=?",
                    (a["observed_at"], a["rig_id"], a["metric"]),
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO alerts("
                        "reading_id, detector, severity, score, explanation"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (
                            int(row["id"]),
                            a["detector"],
                            a["severity"],
                            float(a["score"]),
                            a["explanation"],
                        ),
                    )

    def save_checkpoint(self, db: str | Path) -> dict[str, Any]:
        """Persist detector state and event-time watermark atomically in SQLite."""
        ensure_stream_schema(db)
        wm_str = _format_utc(self.watermark) if self.watermark else "1970-01-01T00:00:00Z"
        state_payload = {
            "allowed_lateness_seconds": int(self.allowed_lateness.total_seconds()),
            "window": self.window,
            "min_history": self.min_history,
            "z_threshold": self.z_threshold,
            "seen_event_ids": list(self.seen_event_ids),
            "windows": self.windows,
        }
        with connect(db) as conn:
            conn.execute(
                """
                INSERT INTO stream_checkpoints (
                    stream_id, watermark, processed_count,
                    duplicate_count, late_count, state_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stream_id) DO UPDATE SET
                    watermark = excluded.watermark,
                    processed_count = excluded.processed_count,
                    duplicate_count = excluded.duplicate_count,
                    late_count = excluded.late_count,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    self.stream_id,
                    wm_str,
                    self.processed_count,
                    self.duplicate_count,
                    self.late_count,
                    json.dumps(state_payload, sort_keys=True),
                    _utc_now_iso(),
                ),
            )
        return self.snapshot()

    @classmethod
    def restore_checkpoint(
        cls,
        db: str | Path,
        stream_id: str = "default-stream",
    ) -> "IncrementalStreamDetector":
        """Restore detector state from SQLite checkpoint, or initialize a clean detector."""
        ensure_stream_schema(db)
        with connect(db) as conn:
            row = conn.execute(
                """
                SELECT watermark, processed_count, duplicate_count, late_count, state_json
                FROM stream_checkpoints
                WHERE stream_id = ?
                """,
                (stream_id,),
            ).fetchone()
        if row is None:
            return cls(stream_id=stream_id)

        state = json.loads(row["state_json"])
        detector = cls(
            stream_id=stream_id,
            allowed_lateness_seconds=int(state.get("allowed_lateness_seconds", 3600)),
            window=int(state.get("window", 24)),
            min_history=int(state.get("min_history", 8)),
            z_threshold=float(state.get("z_threshold", 4.5)),
        )
        if row["watermark"] != "1970-01-01T00:00:00Z":
            detector.watermark = _parse_utc(row["watermark"])
        detector.processed_count = int(row["processed_count"])
        detector.duplicate_count = int(row["duplicate_count"])
        detector.late_count = int(row["late_count"])
        for eid in state.get("seen_event_ids", []):
            detector._record_event_id(eid)
        detector.windows = {k: list(v) for k, v in state.get("windows", {}).items()}
        return detector

    def snapshot(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "watermark": _format_utc(self.watermark) if self.watermark else None,
            "processed_count": self.processed_count,
            "duplicate_count": self.duplicate_count,
            "late_count": self.late_count,
            "alerts_emitted": len(self.emitted_alerts),
        }


class _SimulatedBrokerConsumer:
    """Deterministic stream producer supporting burst, duplicate, and late-arrival injection."""

    def __init__(
        self,
        source_name: str,
        rigs: int = 2,
        messages: int = 120,
        duplicate_rate: float = 0.05,
        late_rate: float = 0.05,
        seed: int = 42,
    ) -> None:
        self.source_name = source_name
        self._buffer: deque[StreamEnvelope] = deque(
            self._build_stream(rigs, messages, duplicate_rate, late_rate, seed)
        )

    def _build_stream(
        self,
        rigs: int,
        messages: int,
        duplicate_rate: float,
        late_rate: float,
        seed: int,
    ) -> list[StreamEnvelope]:
        rng = np.random.default_rng(seed)
        base_dt = _parse_utc("2026-01-01T00:00:00Z")
        baselines = {
            "temperature_c": (68.0, 1.2, 34.0),
            "pressure_bar": (182.0, 2.5, -62.0),
            "vibration_mm_s": (2.3, 0.15, 5.4),
            "flow_m3_h": (310.0, 6.0, -105.0),
        }
        envelopes: list[StreamEnvelope] = []
        for idx in range(messages):
            rig_id = f"RIG-{(idx % max(1, rigs)) + 1:03d}"
            metric = METRICS[idx % len(METRICS)]
            event_dt = base_dt + timedelta(minutes=idx * 5)
            if idx > 12 and float(rng.random()) < late_rate:
                event_dt = event_dt - timedelta(hours=3)

            mean_v, std_v, shift_v = baselines[metric]
            is_fault = idx >= int(messages * 0.8) and idx % 3 == 0
            val = round(
                mean_v + float(rng.normal(0.0, std_v)) + (shift_v if is_fault else 0.0),
                4,
            )
            env = StreamEnvelope(
                event_id=f"{self.source_name}-evt-{idx:06d}",
                rig_id=rig_id,
                observed_at=_format_utc(event_dt),
                metric=metric,
                value=val,
                source=self.source_name,
            )
            envelopes.append(env)
            if float(rng.random()) < duplicate_rate:
                envelopes.append(env)
        return envelopes

    def poll(self, max_records: int = 100) -> list[StreamEnvelope]:
        batch: list[StreamEnvelope] = []
        while self._buffer and len(batch) < max_records:
            batch.append(self._buffer.popleft())
        return batch


class MockMQTTConnector(_SimulatedBrokerConsumer):
    """Simulated MQTT consumer implementing the StreamConsumer interface."""

    def __init__(
        self,
        rigs: int = 2,
        messages: int = 120,
        duplicate_rate: float = 0.05,
        late_rate: float = 0.05,
        seed: int = 42,
    ) -> None:
        super().__init__("mqtt", rigs, messages, duplicate_rate, late_rate, seed)


class MockKafkaConnector(_SimulatedBrokerConsumer):
    """Simulated Kafka partition consumer implementing the StreamConsumer interface."""

    def __init__(
        self,
        rigs: int = 2,
        messages: int = 120,
        duplicate_rate: float = 0.05,
        late_rate: float = 0.05,
        seed: int = 99,
    ) -> None:
        super().__init__("kafka", rigs, messages, duplicate_rate, late_rate, seed)


@dataclass(frozen=True, slots=True)
class _CheckpointView:
    stream_id: str
    watermark: str
    processed_count: int
    duplicate_count: int
    late_count: int
    updated_at: str


def fetch_stream_status(db: str | Path) -> list[dict[str, Any]]:
    """Return persisted stream checkpoints ordered by latest update."""
    ensure_stream_schema(db)
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT stream_id, watermark, processed_count, duplicate_count, late_count, updated_at
            FROM stream_checkpoints
            ORDER BY updated_at DESC, stream_id ASC
            """
        ).fetchall()
    return [asdict(_CheckpointView(**dict(r))) for r in rows]


def run_stream_benchmark(
    db: str | Path,
    rigs: int = 2,
    messages: int = 160,
    duplicate_rate: float = 0.05,
    late_rate: float = 0.05,
    stream_id: str = "mqtt-stream",
    broker: str = "mqtt",
    seed: int = 42,
) -> dict[str, Any]:
    """Run an end-to-end streaming ingest + checkpoint recovery benchmark."""
    consumer: StreamConsumer = (
        MockKafkaConnector(rigs, messages, duplicate_rate, late_rate, seed)
        if broker == "kafka"
        else MockMQTTConnector(rigs, messages, duplicate_rate, late_rate, seed)
    )
    envelopes = consumer.poll(max_records=messages * 3)
    queue = BoundedEventQueue(maxsize=max(64, len(envelopes) + 16), policy="drop_oldest")
    detector = IncrementalStreamDetector.restore_checkpoint(db, stream_id=stream_id)

    latencies_ms: list[float] = []
    t_start = time.perf_counter()
    for env in envelopes:
        queue.push(env)

    halfway = len(queue) // 2
    consumed = 0
    while len(queue) > 0:
        item = queue.pop()
        if item is None:
            break
        t_ev = time.perf_counter()
        detector.process(item, db=db)
        latencies_ms.append((time.perf_counter() - t_ev) * 1000.0)
        consumed += 1
        if consumed == halfway:
            detector.save_checkpoint(db)
            detector = IncrementalStreamDetector.restore_checkpoint(db, stream_id=stream_id)

    checkpoint = detector.save_checkpoint(db)
    elapsed = max(1e-6, time.perf_counter() - t_start)
    p50 = float(np.percentile(latencies_ms, 50)) if latencies_ms else 0.0
    p95 = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    max_ms = max(latencies_ms) if latencies_ms else 0.0

    return {
        "stream_id": stream_id,
        "broker": broker,
        "published_envelopes": len(envelopes),
        "throughput_msg_per_sec": round(len(envelopes) / elapsed, 2),
        "latency_ms": {
            "p50": round(p50, 3),
            "p95": round(p95, 3),
            "max": round(max_ms, 3),
        },
        "queue": queue.stats(),
        "checkpoint": checkpoint,
    }