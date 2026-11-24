# Product roadmap

Completed milestones are marked explicitly; subsequent milestones represent planned engineering work.

## Milestone M1 — Multivariate anomaly learning (✅ Completed in v0.2.0)

**Implemented deliverables:**
1. `src/rigpulse/features.py`: Timestamp-aligned UTC buckets across all four metrics per rig with explicit missing-data indicators, lag features, and leak-free boundaries.
2. `src/rigpulse/ml.py`: Unified `Detector` protocol and `IsolationForestDetector` with training-only median imputation and standardization, JSON model artifact serialization, and top-feature deviation explanations.
3. `src/rigpulse/backtest.py`: Chronological train/validation/test splits with an embargo window (`temporal_split_with_embargo`), Leave-One-Rig-Out (`LORO`) out-of-distribution generalization tests, and ablation comparisons across `operating_limit`, `rolling_robust_z`, `isolation_forest`, and `ensemble_rz_or_if`.
4. `src/rigpulse/cli.py` & `src/rigpulse/detection.py`: Added `rigpulse train`, `rigpulse compare`, and `rigpulse detect --model`, reporting F1, PR-AUC, false alerts per rig-day, mean detection delay, and inference latency.
5. `tests/test_ml_m1.py`: Automated tests covering artifact round-trip, multivariate scoring, temporal embargo disjointness, and CLI experiment execution.

---

## Milestone M2 — Real-time ingest and resilience (✅ Completed in v0.3.0)

**Implemented deliverables:**
1. `src/rigpulse/streaming.py`: Typed `TelemetryEvent` schema with `rig_id`, `event_time`, `ingested_at`, and deterministic or explicit `event_id` idempotency keys.
2. `src/rigpulse/streaming.py` & `compose.yaml`: Pluggable `StreamConsumer` protocol with `MockMQTTConnector` (wildcard topic filtering and synthetic stream generator) and `KafkaConsumerAdapter`, plus an optional Mosquitto broker profile in `compose.yaml`.
3. `src/rigpulse/streaming.py`: `BoundedEventQueue` with `"reject"` (`BackpressureError`) and `"drop_oldest"` policies, event-time watermark tracking, out-of-order and late-arrival classification, idempotent deduplication, and `replay_stream_log`.
4. `src/rigpulse/storage.py` & `src/rigpulse/streaming.py`: `IncrementalStreamDetector` with persisted rolling window state and watermark checkpoints (`detector_checkpoints` and `stream_events` tables) supporting crash-recovery restarts via `IncrementalStreamDetector.resume()`.
5. `src/rigpulse/cli.py`, `src/rigpulse/api.py`, & `tests/test_streaming_m2.py`: Added `rigpulse stream-demo` throughput/latency benchmark CLI, `GET /api/v1/stream/status` endpoint, live dashboard streaming connector status, and automated resilience tests.

---

## Milestone M3 — Alert correlation and incident workflows (🟡 Next)

**Problem:** Multiple detectors can flag the same event and a single equipment incident can span multiple sensors.

**Implementation tasks:**
1. Add incident grouping across detectors and configurable suppression/deduplication windows.
2. Introduce incident states (open, acknowledged, investigating, resolved) with append-only feedback and analyst notes.
3. Generate context explanations showing recent signal history, associated sensor anomalies, and suspected causes labeled as *hypotheses*.
4. Add notifications (webhook first), delivery retry policies, and an incident detail UI.
5. Track analyst feedback to measure false alerts and audit threshold changes.

**Acceptance criteria:** one physical event can result in one tracked incident, with reproducible grouping and an auditable state transition history.

## Milestone M4 — Production-grade infrastructure (🟡 Planned)

**Problem:** SQLite and unauthenticated HTTP are suitable only for an isolated development demo.

**Implementation tasks:**
1. Add PostgreSQL repository adapter, migrations, and query-performance tests.
2. Implement authentication, per-rig authorization, audit trails, and secret handling.
3. Instrument OpenTelemetry traces, structured logs, and Prometheus-compatible metrics.
4. Add container health checks, Docker Compose with broker + database, automated security scans, and CI load tests.
5. Publish deployment profiles and explicit operational safety constraints.

**Acceptance criteria:** multi-user access-control integration tests, reproducible deployment, bounded request latencies, and documented failure recovery procedures.
