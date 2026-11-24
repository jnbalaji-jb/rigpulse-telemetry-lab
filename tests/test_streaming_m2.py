"""Tests for Milestone M2: Event-time streaming, backpressure, MQTT/Kafka adapters, and checkpoints."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from rigpulse.api import create_app
from rigpulse.cli import main
from rigpulse.storage import fetch_alerts, stream_status, summary
from rigpulse.streaming import (
    BackpressureError,
    BoundedEventQueue,
    IncrementalStreamDetector,
    KafkaConsumerAdapter,
    MockMQTTConnector,
    TelemetryEvent,
    replay_stream_log,
)


def test_telemetry_event_validation_and_deterministic_id():
    ev1 = TelemetryEvent.from_dict(
        {
            "rig_id": "RIG-001",
            "metric": "temperature_c",
            "value": 71.5,
            "event_time": "2026-02-01T05:30:00+05:30",
        }
    )
    ev2 = TelemetryEvent.from_dict(
        {
            "rig_id": "RIG-001",
            "metric": "temperature_c",
            "value": 71.5,
            "event_time": "2026-02-01T00:00:00Z",
        }
    )
    assert ev1.event_time == "2026-02-01T00:00:00Z"
    assert ev1.event_id == ev2.event_id
    assert ev1.event_id.startswith("evt-")

    with pytest.raises(ValueError, match="unknown metric"):
        TelemetryEvent.from_dict(
            {
                "rig_id": "RIG-001",
                "metric": "invalid_sensor",
                "value": 10.0,
                "event_time": "2026-02-01T00:00:00Z",
            }
        )


def test_bounded_event_queue_backpressure_policies():
    e1 = TelemetryEvent.from_dict(
        {"event_id": "e1", "rig_id": "RIG-001", "metric": "temperature_c", "value": 65.0, "event_time": "2026-02-01T00:00:00Z"}
    )
    e2 = TelemetryEvent.from_dict(
        {"event_id": "e2", "rig_id": "RIG-001", "metric": "temperature_c", "value": 66.0, "event_time": "2026-02-01T00:05:00Z"}
    )
    e3 = TelemetryEvent.from_dict(
        {"event_id": "e3", "rig_id": "RIG-001", "metric": "temperature_c", "value": 67.0, "event_time": "2026-02-01T00:10:00Z"}
    )

    reject_q = BoundedEventQueue(maxsize=2, policy="reject")
    reject_q.push(e1)
    reject_q.push(e2)
    with pytest.raises(BackpressureError, match="stream queue full"):
        reject_q.push(e3)
    assert reject_q.rejected_count == 1

    drop_q = BoundedEventQueue(maxsize=2, policy="drop_oldest")
    drop_q.push_many([e1, e2, e3])
    popped = drop_q.pop_batch(10)
    assert [e.event_id for e in popped] == ["e2", "e3"]
    assert drop_q.dropped_count == 1


def test_mqtt_and_kafka_adapters_with_dedup_and_late_events(tmp_path):
    db = tmp_path / "stream.db"
    mqtt = MockMQTTConnector(topic="rigpulse/telemetry/#")
    mqtt.publish(
        {
            "event_id": "msg-1",
            "rig_id": "RIG-001",
            "metric": "temperature_c",
            "value": 68.0,
            "event_time": "2026-02-01T01:00:00Z",
        }
    )
    # Unrelated topic ignored by subscription filter
    mqtt.publish({"rig_id": "RIG-001"}, topic="other/topic/ignored")
    # Duplicate message
    mqtt.publish(
        {
            "event_id": "msg-1",
            "rig_id": "RIG-001",
            "metric": "temperature_c",
            "value": 68.0,
            "event_time": "2026-02-01T01:00:00Z",
        }
    )
    # Late-arriving spike (> 300s behind watermark 01:00:00Z)
    mqtt.publish(
        {
            "event_id": "msg-late",
            "rig_id": "RIG-001",
            "metric": "temperature_c",
            "value": 108.0,
            "event_time": "2026-02-01T00:40:00Z",
        }
    )

    detector = IncrementalStreamDetector(consumer_id="test-mqtt", allowed_lateness_seconds=300)
    res = detector.consume_from(db, mqtt, max_messages=10)
    assert res["polled"] == 3
    assert res["processed_total"] == 2
    assert res["duplicates_total"] == 1
    assert res["late_total"] == 1
    assert res["alerts_total"] == 1

    # Verify KafkaConsumerAdapter wraps Kafka-like poll records
    @dataclass
    class FakeKafkaRecord:
        value: bytes

    class FakeKafkaClient:
        def __init__(self):
            self.committed = False
            self.closed = False

        def poll(self, timeout_ms: int = 100):
            return {
                "tp0": [
                    FakeKafkaRecord(
                        json.dumps(
                            {
                                "event_id": "kafka-1",
                                "rig_id": "RIG-002",
                                "metric": "pressure_bar",
                                "value": 180.0,
                                "event_time": "2026-02-01T01:05:00Z",
                            }
                        ).encode("utf-8")
                    )
                ]
            }

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    fake_client = FakeKafkaClient()
    kafka_adapter = KafkaConsumerAdapter(fake_client, topic="rigpulse.telemetry")
    kafka_res = detector.consume_from(db, kafka_adapter, max_messages=5)
    assert kafka_res["processed_total"] == 3
    assert fake_client.committed is True
    kafka_adapter.close()
    assert fake_client.closed is True


def test_checkpoint_crash_recovery_matches_uninterrupted_run(tmp_path):
    db_clean = tmp_path / "clean.db"
    db_crash = tmp_path / "crash.db"

    broker_a = MockMQTTConnector()
    broker_a.seed_synthetic_stream(rigs=2, events=96, seed=19, duplicate_rate=0.15, late_rate=0.15)
    clean_worker = IncrementalStreamDetector(consumer_id="w-clean")
    clean_worker.consume_from(db_clean, broker_a, max_messages=500, checkpoint_every=20)

    broker_b = MockMQTTConnector()
    seeded = broker_b.seed_synthetic_stream(rigs=2, events=96, seed=19, duplicate_rate=0.15, late_rate=0.15)
    half = seeded["published_messages"] // 2

    worker_part1 = IncrementalStreamDetector.resume(db_crash, consumer_id="w-crash")
    worker_part1.consume_from(db_crash, broker_b, max_messages=half, checkpoint_every=10)

    # Simulate crash & restart from SQLite checkpoint
    worker_part2 = IncrementalStreamDetector.resume(db_crash, consumer_id="w-crash")
    assert worker_part2.processed_events == worker_part1.processed_events
    worker_part2.consume_from(db_crash, broker_b, max_messages=500, checkpoint_every=10)

    assert worker_part2.processed_events == clean_worker.processed_events == 96
    assert worker_part2.duplicate_events == clean_worker.duplicate_events
    assert worker_part2.late_events == clean_worker.late_events
    assert worker_part2.watermark_utc == clean_worker.watermark_utc
    assert summary(db_crash)["readings"] == summary(db_clean)["readings"] == 96
    assert len(fetch_alerts(db_crash, limit=200)) == len(fetch_alerts(db_clean, limit=200))

    replayed = replay_stream_log(db_crash)
    assert replayed["replayed_events"] == 96


def test_cli_stream_demo_and_api_stream_status(tmp_path, capsys):
    db = tmp_path / "m2_demo.db"
    out = tmp_path / "reports" / "m2.json"
    rc = main(
        [
            "stream-demo",
            "--db",
            str(db),
            "--rigs",
            "3",
            "--events",
            "120",
            "--seed",
            "42",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["resilience"]["final_unique_processed"] == 120
    assert report["performance"]["throughput_events_per_sec"] > 0

    client = TestClient(create_app(db))
    st = client.get("/api/v1/stream/status").json()
    assert st["stream_events_total"] == 120
    assert len(st["checkpoints"]) == 1
    assert st["checkpoints"][0]["processed_events"] == 120
