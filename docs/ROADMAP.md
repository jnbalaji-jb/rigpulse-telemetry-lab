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

## Milestone M2 — Real-time ingest and resilience (🟡 Next)

**Problem:** Batch-only import and full recompute will not work for sustained telemetry feeds.

**Implementation tasks:**
1. Define a typed event schema with device ID, event timestamp, ingestion timestamp, and an idempotency key.
2. Implement a mock MQTT connector and Kafka adapter behind a pluggable consumer interface; provide optional broker-backed integration tests.
3. Add event-time windows, handling of late/out-of-order measurements, deduplication, replay, and bounded queues/backpressure.
4. Introduce incremental detector state and crash-recovery checkpoints.
5. Benchmark throughput and end-to-end delay under bursts and stream interruptions.

**Acceptance criteria:** a local docker-compose integration test simulates restarts, duplicate messages, and late-arrival events with stable results and bounded memory.

## Milestone M3 — Alert correlation and incident workflows (🟡 Planned)

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
