"""Event-time streaming ingestion, bounded queues, broker adapters, and incremental checkpoints."""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from threading import Lock
from typing import Any, Iterable, Mapping, Protocol, Sequence

from rigpulse.detection import FLOORS, LIMITS
from rigpulse.models import METRICS, RIG_ID_RE, Alert, Reading, normalize_timestamp
from rigpulse.storage import (
    connect,
    initialize,
    insert_alerts,
    insert_single_reading,
    load_checkpoint,
    save_checkpoint,
)


class BackpressureError(RuntimeError):
    """Raised when a bounded stream queue reaches capacity under 'reject' policy."""


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(normalize_timestamp(ts).replace("Z", "+00:00"))


def derive_event_id(rig_id: str, metric: str, event_time: str) -> str:
    """Deterministic idempotency key for natural (rig, metric, event_time) tuples."""
    raw = f"{rig_id}|{metric}|{normalize_timestamp(event_time)}".encode("utf-8")
    return f"evt-{hashlib.sha256(raw).hexdigest()[:16]}"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """Typed streaming telemetry event with explicit event/ingest timestamps and idempotency key."""

    event_id: str
    rig_id: str
    metric: str
    value: float
    event_time: str
    ingested_at: str
    source: str = "stream"

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        default_source: str = "stream",
    ) -> "TelemetryEvent":
        rig_id = str(raw.get("rig_id", "")).strip()
        if not RIG_ID_RE.fullmatch(rig_id):
            raise ValueError(f"invalid rig_id: {rig_id!r}")

        metric = str(raw.get("metric", "")).strip()
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")

        try:
            value = float(raw["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("value must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError("value must be finite")

        raw_event_time = raw.get("event_time", raw.get("observed_at"))
        if raw_event_time is None or str(raw_event_time).strip() == "":
            raise ValueError("event_time (or observed_at) is required")
        event_time = normalize_timestamp(str(raw_event_time))

        raw_ingested = raw.get("ingested_at")
        ingested_at = (
            normalize_timestamp(str(raw_ingested))
            if raw_ingested is not None and str(raw_ingested).strip() != ""
            else _now_utc_iso()
        )

        raw_id = raw.get("event_id")
        if raw_id is None or str(raw_id).strip() == "":
            event_id = derive_event_id(rig_id, metric, event_time)
        else:
            event_id = str(raw_id).strip()
            if len(event_id) > 128:
                raise ValueError("event_id must be <= 128 characters")

        source = str(raw.get("source") or default_source).strip() or default_source
        return cls(
            event_id=event_id,
            rig_id=rig_id,
            metric=metric,
            value=value,
            event_time=event_time,
            ingested_at=ingested_at,
            source=source,
        )

    @classmethod
    def from_json(
        cls,
        payload: str | bytes,
        default_source: str = "stream",
    ) -> "TelemetryEvent":
        text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON event payload: {exc}") from exc
        if not isinstance(parsed, Mapping):
            raise ValueError("JSON event payload must be an object")
        return cls.from_dict(parsed, default_source=default_source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "rig_id": self.rig_id,
            "metric": self.metric,
            "value": self.value,
            "event_time": self.event_time,
            "ingested_at": self.ingested_at,
            "source": self.source,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_reading(self) -> Reading:
        return Reading(
            observed_at=self.event_time,
            rig_id=self.rig_id,
            metric=self.metric,
            value=self.value,
        )


class StreamConsumer(Protocol):
    """Pluggable consumer interface implemented by MQTT and Kafka connectors."""

    topic: str

    def poll(self, max_messages: int = 100) -> list[bytes | str | Mapping[str, Any]]:
        """Fetch up to `max_messages` raw messages from the stream source."""
        ...

    def commit(self) -> None:
        """Acknowledge consumed offsets / deliveries."""
        ...

    def close(self) -> None:
        """Release underlying client resources."""
        ...


class BoundedEventQueue:
    """Thread-safe bounded FIFO buffer with explicit backpressure policies."""

    def __init__(self, maxsize: int = 1024, policy: str = "reject") -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if policy not in ("reject", "drop_oldest"):
            raise ValueError("policy must be 'reject' or 'drop_oldest'")
        self.maxsize = maxsize
        self.policy = policy
        self._buf: deque[TelemetryEvent] = deque()
        self._lock = Lock()
        self.enqueued_count = 0
        self.dequeued_count = 0
        self.dropped_count = 0
        self.rejected_count = 0
        self.high_water_mark = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def push(self, event: TelemetryEvent) -> bool:
        with self._lock:
            if len(self._buf) >= self.maxsize:
                if self.policy == "reject":
                    self.rejected_count += 1
                    raise BackpressureError(
                        f"stream queue full ({len(self._buf)}/{self.maxsize})"
                    )
                self._buf.popleft()
                self.dropped_count += 1
            self._buf.append(event)
            self.enqueued_count += 1
            if len(self._buf) > self.high_water_mark:
                self.high_water_mark = len(self._buf)
            return True

    def push_many(self, events: Iterable[TelemetryEvent]) -> int:
        pushed = 0
        for ev in events:
            self.push(ev)
            pushed += 1
        return pushed

    def pop_batch(self, max_items: int = 100) -> list[TelemetryEvent]:
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        with self._lock:
            batch: list[TelemetryEvent] = []
            while self._buf and len(batch) < max_items:
                batch.append(self._buf.popleft())
            self.dequeued_count += len(batch)
            return batch

    def stats(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "maxsize": self.maxsize,
                "policy": self.policy,
                "depth": len(self._buf),
                "high_water_mark": self.high_water_mark,
                "enqueued": self.enqueued_count,
                "dequeued": self.dequeued_count,
                "dropped": self.dropped_count,
                "rejected": self.rejected_count,
            }


def _topic_matches(pattern: str, topic: str) -> bool:
    if pattern == "#" or pattern == topic:
        return True
    if pattern.endswith("/#"):
        prefix = pattern[:-2]
        return topic == prefix or topic.startswith(prefix + "/")
    return False


class MockMQTTConnector:
    """In-process deterministic MQTT broker/consumer simulator for streaming tests & demos."""

    def __init__(self, topic: str = "rigpulse/telemetry/#") -> None:
        self.topic = topic
        self._messages: deque[tuple[str, bytes]] = deque()
        self.published_count = 0
        self.committed_count = 0
        self.closed = False

    def publish(
        self,
        payload: TelemetryEvent | Mapping[str, Any] | str | bytes,
        topic: str | None = None,
    ) -> None:
        if self.closed:
            raise RuntimeError("MockMQTTConnector is closed")
        if isinstance(payload, TelemetryEvent):
            resolved_topic = topic or f"rigpulse/telemetry/{payload.rig_id}/{payload.metric}"
            raw = payload.to_json().encode("utf-8")
        elif isinstance(payload, Mapping):
            rig = str(payload.get("rig_id", "unknown"))
            metric = str(payload.get("metric", "unknown"))
            resolved_topic = topic or f"rigpulse/telemetry/{rig}/{metric}"
            raw = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
        elif isinstance(payload, str):
            resolved_topic = topic or "rigpulse/telemetry/raw"
            raw = payload.encode("utf-8")
        else:
            resolved_topic = topic or "rigpulse/telemetry/raw"
            raw = bytes(payload)

        self.published_count += 1
        if _topic_matches(self.topic, resolved_topic):
            self._messages.append((resolved_topic, raw))

    def poll(self, max_messages: int = 100) -> list[bytes]:
        if self.closed:
            raise RuntimeError("MockMQTTConnector is closed")
        out: list[bytes] = []
        while self._messages and len(out) < max_messages:
            _, raw = self._messages.popleft()
            out.append(raw)
        return out

    def commit(self) -> None:
        self.committed_count += 1

    def close(self) -> None:
        self.closed = True

    def pending_count(self) -> int:
        return len(self._messages)

    def seed_synthetic_stream(
        self,
        rigs: int = 4,
        events: int = 240,
        seed: int = 42,
        duplicate_rate: float = 0.1,
        late_rate: float = 0.1,
        late_offset_seconds: int = 900,
    ) -> dict[str, int]:
        """Publish deterministic synthetic MQTT messages with duplicates and late arrivals."""
        if events < 1 or rigs < 1:
            raise ValueError("rigs and events must be >= 1")
        if not 0.0 <= duplicate_rate <= 0.5 or not 0.0 <= late_rate <= 0.5:
            raise ValueError("duplicate_rate and late_rate must be in [0.0, 0.5]")

        rng = random.Random(seed)
        start = datetime(2026, 2, 1, tzinfo=timezone.utc)
        base = {
            "temperature_c": 67.0,
            "pressure_bar": 179.0,
            "vibration_mm_s": 2.1,
            "flow_m3_h": 335.0,
        }
        noise = {
            "temperature_c": 0.9,
            "pressure_bar": 2.5,
            "vibration_mm_s": 0.11,
            "flow_m3_h": 7.0,
        }
        offsets = {
            "temperature_c": 34.0,
            "pressure_bar": -74.0,
            "vibration_mm_s": 7.2,
            "flow_m3_h": -140.0,
        }

        primary_events: list[TelemetryEvent] = []
        for idx in range(events):
            rig = f"RIG-{(idx % rigs) + 1:03d}"
            metric = METRICS[(idx // rigs) % len(METRICS)]
            step = idx // (rigs * len(METRICS))
            event_dt = start + timedelta(minutes=step * 5)
            event_ts = event_dt.isoformat().replace("+00:00", "Z")
            ingest_ts = (event_dt + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")

            val = base[metric] + rng.gauss(0, noise[metric])
            # Inject deterministic anomalies after warm-up window
            if step >= 22 and step in (24, 38) and (idx % rigs) == 0:
                val += offsets[metric]

            ev = TelemetryEvent(
                event_id=f"mqtt-{seed}-{idx:06d}",
                rig_id=rig,
                metric=metric,
                value=round(val, 5),
                event_time=event_ts,
                ingested_at=ingest_ts,
                source="mqtt",
            )
            primary_events.append(ev)

        # Hold back selected events to arrive late after watermark advances
        delayed_buffer: list[tuple[int, TelemetryEvent]] = []
        stream_sequence: list[TelemetryEvent] = []
        late_injected = 0
        dup_injected = 0

        for idx, ev in enumerate(primary_events):
            if idx >= 8 and rng.random() < late_rate:
                release_at = min(len(primary_events) - 1, idx + max(4, late_offset_seconds // 60))
                delayed_buffer.append((release_at, ev))
                late_injected += 1
            else:
                stream_sequence.append(ev)
                if stream_sequence and rng.random() < duplicate_rate:
                    dup_source = stream_sequence[rng.randrange(len(stream_sequence))]
                    stream_sequence.append(dup_source)
                    dup_injected += 1

            ready = [item for rel, item in delayed_buffer if rel == idx]
            for late_ev in ready:
                stream_sequence.append(late_ev)

        for _, leftover in delayed_buffer:
            if leftover not in stream_sequence:
                stream_sequence.append(leftover)

        for ev in stream_sequence:
            self.publish(ev)

        return {
            "unique_events": len(primary_events),
            "published_messages": len(stream_sequence),
            "duplicates_injected": dup_injected,
            "late_events_injected": late_injected,
        }


class KafkaConsumerAdapter:
    """Adapter wrapping a Kafka-compatible consumer behind the StreamConsumer protocol."""

    def __init__(self, client: Any, topic: str = "rigpulse.telemetry", poll_timeout_ms: int = 100) -> None:
        self.client = client
        self.topic = topic
        self.poll_timeout_ms = poll_timeout_ms

    def poll(self, max_messages: int = 100) -> list[bytes | str | Mapping[str, Any]]:
        raw = self.client.poll(timeout_ms=self.poll_timeout_ms)
        if not raw:
            return []
        records: list[bytes | str | Mapping[str, Any]] = []
        if isinstance(raw, Mapping):
            for msgs in raw.values():
                for msg in msgs:
                    val = msg.value() if callable(getattr(msg, "value", None)) else getattr(msg, "value", msg)
                    records.append(val)
                    if len(records) >= max_messages:
                        return records
        else:
            for msg in raw:
                val = msg.value() if callable(getattr(msg, "value", None)) else getattr(msg, "value", msg)
                records.append(val)
                if len(records) >= max_messages:
                    break
        return records

    def commit(self) -> None:
        if hasattr(self.client, "commit"):
            self.client.commit()

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()


class IncrementalStreamDetector:
    """Stateful event-time stream processor with deduplication, watermark tracking, and checkpoints."""

    def __init__(
        self,
        consumer_id: str = "default-stream",
        window: int = 48,
        min_history: int = 16,
        z_threshold: float = 4.5,
        allowed_lateness_seconds: int = 300,
    ) -> None:
        if window < 8 or min_history < 5 or min_history > window:
            raise ValueError("require window>=8, min_history>=5, min_history<=window")
        if z_threshold <= 0:
            raise ValueError("z_threshold must be positive")
        if allowed_lateness_seconds < 0:
            raise ValueError("allowed_lateness_seconds must be >= 0")

        self.consumer_id = consumer_id
        self.window = window
        self.min_history = min_history
        self.z_threshold = z_threshold
        self.allowed_lateness_seconds = allowed_lateness_seconds

        self.history: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=self.window)
        )
        self.watermark_utc: str | None = None
        self.processed_events = 0
        self.duplicate_events = 0
        self.out_of_order_events = 0
        self.late_events = 0
        self.alerts_created = 0

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "min_history": self.min_history,
            "z_threshold": self.z_threshold,
            "allowed_lateness_seconds": self.allowed_lateness_seconds,
            "watermark_utc": self.watermark_utc,
            "processed_events": self.processed_events,
            "duplicate_events": self.duplicate_events,
            "out_of_order_events": self.out_of_order_events,
            "late_events": self.late_events,
            "alerts_created": self.alerts_created,
            "windows": {
                f"{rig}|{metric}": list(vals)
                for (rig, metric), vals in sorted(self.history.items())
            },
        }

    def save_checkpoint(self, db: str | Path) -> dict[str, Any]:
        updated = _now_utc_iso()
        state = self.snapshot_state()
        save_checkpoint(
            db=db,
            consumer_id=self.consumer_id,
            updated_at=updated,
            watermark_utc=self.watermark_utc,
            processed_events=self.processed_events,
            state=state,
        )
        return {
            "consumer_id": self.consumer_id,
            "updated_at": updated,
            "watermark_utc": self.watermark_utc,
            "processed_events": self.processed_events,
        }

    @classmethod
    def resume(
        cls,
        db: str | Path,
        consumer_id: str = "default-stream",
        window: int = 48,
        min_history: int = 16,
        z_threshold: float = 4.5,
        allowed_lateness_seconds: int = 300,
    ) -> "IncrementalStreamDetector":
        """Restore detector windows and watermark from SQLite checkpoint if present."""
        cp = load_checkpoint(db, consumer_id)
        if cp is None:
            return cls(
                consumer_id=consumer_id,
                window=window,
                min_history=min_history,
                z_threshold=z_threshold,
                allowed_lateness_seconds=allowed_lateness_seconds,
            )
        state = cp["state"]
        detector = cls(
            consumer_id=consumer_id,
            window=int(state.get("window", window)),
            min_history=int(state.get("min_history", min_history)),
            z_threshold=float(state.get("z_threshold", z_threshold)),
            allowed_lateness_seconds=int(
                state.get("allowed_lateness_seconds", allowed_lateness_seconds)
            ),
        )
        detector.watermark_utc = state.get("watermark_utc")
        detector.processed_events = int(state.get("processed_events", 0))
        detector.duplicate_events = int(state.get("duplicate_events", 0))
        detector.out_of_order_events = int(state.get("out_of_order_events", 0))
        detector.late_events = int(state.get("late_events", 0))
        detector.alerts_created = int(state.get("alerts_created", 0))
        for key, vals in state.get("windows", {}).items():
            rig, metric = key.split("|", 1)
            detector.history[(rig, metric)] = deque(
                (float(v) for v in vals), maxlen=detector.window
            )
        return detector

    def process_event(self, db: str | Path, event: TelemetryEvent) -> dict[str, Any]:
        """Ingest and score a single event incrementally with idempotency and watermark tracking."""
        t0 = time.perf_counter()
        initialize(db)

        is_late = False
        is_out_of_order = False
        if self.watermark_utc is None or event.event_time > self.watermark_utc:
            self.watermark_utc = event.event_time
        else:
            is_out_of_order = True
            lag = (_parse_utc(self.watermark_utc) - _parse_utc(event.event_time)).total_seconds()
            if lag > self.allowed_lateness_seconds:
                is_late = True

        with connect(db) as conn:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO stream_events("
                "event_id, observed_at, ingested_at, rig_id, metric, value, source, is_late"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.event_time,
                    event.ingested_at,
                    event.rig_id,
                    event.metric,
                    event.value,
                    event.source,
                    int(is_late),
                ),
            )
            is_new_event = (conn.total_changes - before) > 0

        if not is_new_event:
            self.duplicate_events += 1
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {
                "event_id": event.event_id,
                "status": "duplicate_skipped",
                "alerts_emitted": 0,
                "latency_ms": round(latency_ms, 4),
            }

        if is_late:
            self.late_events += 1
        elif is_out_of_order:
            self.out_of_order_events += 1

        reading_id, _ = insert_single_reading(db, event.to_reading())

        new_alerts: list[Alert] = []
        side, bound = LIMITS[event.metric]
        breached = event.value > bound if side == "upper" else event.value < bound
        if breached:
            excess = (event.value - bound) if side == "upper" else (bound - event.value)
            severity = "critical" if excess > 0.12 * abs(bound) else "warning"
            new_alerts.append(
                Alert(
                    reading_id=reading_id,
                    detector="operating_limit",
                    severity=severity,
                    score=round(excess, 5),
                    explanation=(
                        f"{event.metric} {event.value:.2f} exceeded sample {side} limit {bound:.2f}"
                    ),
                )
            )

        series_key = (event.rig_id, event.metric)
        past = self.history[series_key]
        if len(past) >= self.min_history:
            centre = median(past)
            mad = median(abs(x - centre) for x in past)
            sigma = max(1.4826 * mad, FLOORS[event.metric])
            z = abs(event.value - centre) / sigma
            if z >= self.z_threshold:
                severity = "critical" if z >= 7.5 else "warning"
                new_alerts.append(
                    Alert(
                        reading_id=reading_id,
                        detector="rolling_robust_z",
                        severity=severity,
                        score=round(z, 5),
                        explanation=(
                            f"{event.metric} deviated {z:.1f} robust SD from the prior "
                            f"{len(past)} observations"
                        ),
                    )
                )

        past.append(event.value)
        inserted_alerts = insert_alerts(db, new_alerts) if new_alerts else 0
        self.alerts_created += inserted_alerts
        self.processed_events += 1

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "event_id": event.event_id,
            "status": "late_processed" if is_late else ("out_of_order_processed" if is_out_of_order else "processed"),
            "alerts_emitted": inserted_alerts,
            "latency_ms": round(latency_ms, 4),
        }

    def consume_from(
        self,
        db: str | Path,
        consumer: StreamConsumer,
        queue: BoundedEventQueue | None = None,
        max_messages: int = 500,
        checkpoint_every: int = 50,
    ) -> dict[str, Any]:
        """Poll messages from a StreamConsumer through a BoundedEventQueue and score incrementally."""
        q = queue or BoundedEventQueue(maxsize=max(64, max_messages))
        raw_items = consumer.poll(max_messages=max_messages)
        for raw in raw_items:
            ev = (
                TelemetryEvent.from_dict(raw, default_source=consumer.topic)
                if isinstance(raw, Mapping)
                else TelemetryEvent.from_json(raw, default_source=consumer.topic)
            )
            q.push(ev)

        latencies: list[float] = []
        batch = q.pop_batch(max_items=max_messages)
        for idx, ev in enumerate(batch, start=1):
            res = self.process_event(db, ev)
            latencies.append(float(res["latency_ms"]))
            if checkpoint_every > 0 and idx % checkpoint_every == 0:
                self.save_checkpoint(db)

        self.save_checkpoint(db)
        consumer.commit()
        return {
            "polled": len(raw_items),
            "processed_total": self.processed_events,
            "duplicates_total": self.duplicate_events,
            "out_of_order_total": self.out_of_order_events,
            "late_total": self.late_events,
            "alerts_total": self.alerts_created,
            "watermark_utc": self.watermark_utc,
            "latencies_ms": latencies,
            "queue": q.stats(),
        }


def replay_stream_log(
    db: str | Path,
    since_utc: str | None = None,
    window: int = 48,
    min_history: int = 16,
    z_threshold: float = 4.5,
) -> dict[str, Any]:
    """Deterministically replay persisted stream_events in event-time order."""
    initialize(db)
    where = "WHERE observed_at >= ?" if since_utc else ""
    params = [normalize_timestamp(since_utc)] if since_utc else []
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT event_id, observed_at, ingested_at, rig_id, metric, value, source "
            f"FROM stream_events {where} ORDER BY observed_at ASC, event_id ASC",
            params,
        ).fetchall()

    rebuilt = IncrementalStreamDetector(
        consumer_id="replay-verifier",
        window=window,
        min_history=min_history,
        z_threshold=z_threshold,
    )
    alerts_replayed = 0
    for row in rows:
        ev = TelemetryEvent(
            event_id=str(row["event_id"]),
            rig_id=str(row["rig_id"]),
            metric=str(row["metric"]),
            value=float(row["value"]),
            event_time=str(row["observed_at"]),
            ingested_at=str(row["ingested_at"]),
            source=str(row["source"]),
        )
        side, bound = LIMITS[ev.metric]
        if (ev.value > bound if side == "upper" else ev.value < bound):
            alerts_replayed += 1
        past = rebuilt.history[(ev.rig_id, ev.metric)]
        if len(past) >= min_history:
            centre = median(past)
            mad = median(abs(x - centre) for x in past)
            sigma = max(1.4826 * mad, FLOORS[ev.metric])
            if abs(ev.value - centre) / sigma >= z_threshold:
                alerts_replayed += 1
        past.append(ev.value)
        rebuilt.watermark_utc = ev.event_time
        rebuilt.processed_events += 1

    return {
        "replayed_events": rebuilt.processed_events,
        "watermark_utc": rebuilt.watermark_utc,
        "alerts_recomputed": alerts_replayed,
    }


def _percentile_ms(samples: Sequence[float], q: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return round(ordered[idx], 4)


def run_stream_benchmark(
    db: str | Path,
    rigs: int = 4,
    events: int = 240,
    seed: int = 42,
    duplicate_rate: float = 0.1,
    late_rate: float = 0.1,
    checkpoint_every: int = 50,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run an end-to-end MQTT stream demo with simulated crash/restart checkpoint recovery."""
    broker = MockMQTTConnector(topic="rigpulse/telemetry/#")
    seeded = broker.seed_synthetic_stream(
        rigs=rigs,
        events=events,
        seed=seed,
        duplicate_rate=duplicate_rate,
        late_rate=late_rate,
    )

    queue = BoundedEventQueue(maxsize=max(256, seeded["published_messages"] + 16), policy="reject")
    consumer_id = "mqtt-primary-consumer"
    t0 = time.perf_counter()

    # Phase 1: consume first half and persist checkpoint
    half = max(1, seeded["published_messages"] // 2)
    worker_1 = IncrementalStreamDetector.resume(db, consumer_id=consumer_id)
    phase_1 = worker_1.consume_from(
        db,
        broker,
        queue=queue,
        max_messages=half,
        checkpoint_every=checkpoint_every,
    )

    # Simulate process restart: instantiate fresh worker from SQLite checkpoint
    worker_2 = IncrementalStreamDetector.resume(db, consumer_id=consumer_id)
    phase_2 = worker_2.consume_from(
        db,
        broker,
        queue=queue,
        max_messages=seeded["published_messages"],
        checkpoint_every=checkpoint_every,
    )
    elapsed_sec = max(time.perf_counter() - t0, 1e-6)
    broker.close()

    all_latencies = phase_1["latencies_ms"] + phase_2["latencies_ms"]
    report = {
        "benchmark": "M2_streaming_resilience",
        "config": {
            "rigs": rigs,
            "target_unique_events": events,
            "seed": seed,
            "duplicate_rate": duplicate_rate,
            "late_rate": late_rate,
            "checkpoint_every": checkpoint_every,
        },
        "broker_seed": seeded,
        "resilience": {
            "checkpoint_restarts_simulated": 1,
            "resumed_from_processed_count": phase_1["processed_total"],
            "final_unique_processed": worker_2.processed_events,
            "duplicates_skipped": worker_2.duplicate_events,
            "out_of_order_accepted": worker_2.out_of_order_events,
            "late_arrivals_flagged": worker_2.late_events,
            "watermark_utc": worker_2.watermark_utc,
            "alerts_created": worker_2.alerts_created,
        },
        "performance": {
            "total_messages_consumed": len(all_latencies),
            "elapsed_seconds": round(elapsed_sec, 4),
            "throughput_events_per_sec": round(len(all_latencies) / elapsed_sec, 2),
            "p50_latency_ms": _percentile_ms(all_latencies, 0.50),
            "p95_latency_ms": _percentile_ms(all_latencies, 0.95),
            "max_latency_ms": _percentile_ms(all_latencies, 1.00),
            "queue_high_water_mark": queue.high_water_mark,
        },
    }

    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "
", encoding="utf-8")

    return report
