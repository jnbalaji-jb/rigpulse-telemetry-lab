"""Tests for Milestone M2: Event-time streaming, watermarks, backpressure, and checkpoints."""
from __future__ import annotations

from rigpulse.storage import fetch_alerts, summary
from rigpulse.streaming import (
    BoundedEventQueue,
    IncrementalStreamDetector,
    MockKafkaConnector,
    MockMQTTConnector,
    StreamEnvelope,
    fetch_stream_status,
    run_stream_benchmark,
)


def test_bounded_queue_backpressure() -> None:
    q_drop = BoundedEventQueue(maxsize=2, policy="drop_oldest")
    for idx in range(3):
        accepted = q_drop.push(
            StreamEnvelope(
                event_id=f"e-{idx}",
                rig_id="RIG-001",
                observed_at=f"2026-01-01T00:0{idx}:00Z",
                metric="temperature_c",
                value=68.0 + idx,
            )
        )
        assert accepted is True
    assert len(q_drop) == 2
    assert q_drop.dropped_count == 1
    first = q_drop.pop()
    assert first is not None and first.event_id == "e-1"

    q_reject = BoundedEventQueue(maxsize=1, policy="reject_newest")
    assert (
        q_reject.push(
            StreamEnvelope("e-a", "RIG-001", "2026-01-01T00:00:00Z", "temperature_c", 68.0)
        )
        is True
    )
    assert (
        q_reject.push(
            StreamEnvelope("e-b", "RIG-001", "2026-01-01T00:01:00Z", "temperature_c", 69.0)
        )
        is False
    )


def test_incremental_detector_dedup_watermark_and_checkpoint(tmp_path) -> None:
    db = tmp_path / "stream.db"
    det = IncrementalStreamDetector(
        stream_id="s-1",
        allowed_lateness_seconds=120,
        window=12,
        min_history=6,
    )
    for idx in range(6):
        res = det.process(
            StreamEnvelope(
                event_id=f"evt-{idx}",
                rig_id="RIG-001",
                observed_at=f"2026-01-01T00:0{idx}:00Z",
                metric="temperature_c",
                value=68.0,
            ),
            db=db,
        )
        assert res["status"] == "processed"

    dup = det.process(
        StreamEnvelope(
            event_id="evt-5",
            rig_id="RIG-001",
            observed_at="2026-01-01T00:05:00Z",
            metric="temperature_c",
            value=68.0,
        ),
        db=db,
    )
    assert dup["status"] == "duplicate"
    assert det.duplicate_count == 1

    late = det.process(
        StreamEnvelope(
            event_id="evt-late",
            rig_id="RIG-001",
            observed_at="2026-01-01T00:01:00Z",
            metric="temperature_c",
            value=68.2,
        ),
        db=db,
    )
    assert late["status"] == "late_processed"
    assert det.late_count == 1

    det.save_checkpoint(db)

    restored = IncrementalStreamDetector.restore_checkpoint(db, stream_id="s-1")
    assert restored.processed_count == 7
    assert restored.duplicate_count == 1
    assert restored.late_count == 1
    assert restored.watermark == det.watermark

    spike = restored.process(
        StreamEnvelope(
            event_id="evt-spike",
            rig_id="RIG-001",
            observed_at="2026-01-01T00:06:00Z",
            metric="temperature_c",
            value=112.0,
        ),
        db=db,
    )
    assert len(spike["alerts"]) >= 1
    assert summary(db)["readings"] == 7
    assert len(fetch_alerts(db, rig_id="RIG-001")) >= 1


def test_connectors_and_stream_benchmark(tmp_path) -> None:
    mqtt = MockMQTTConnector(rigs=2, messages=20, duplicate_rate=0.0, late_rate=0.0, seed=5)
    kafka = MockKafkaConnector(rigs=2, messages=20, duplicate_rate=0.0, late_rate=0.0, seed=6)
    assert len(mqtt.poll(10)) == 10
    assert len(kafka.poll(10)) == 10

    db = tmp_path / "bench.db"
    res = run_stream_benchmark(db, rigs=2, messages=60, stream_id="mqtt-bench", broker="mqtt")
    assert res["checkpoint"]["processed_count"] == 60
    assert res["throughput_msg_per_sec"] > 0

    status_rows = fetch_stream_status(db)
    assert len(status_rows) == 1
    assert status_rows[0]["stream_id"] == "mqtt-bench"