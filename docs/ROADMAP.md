# Product roadmap — genuinely unfinished work

The following is a **development plan, not a claim that these features already exist**. Each milestone contains code, testing, and design work suitable for continued AI-assisted coding sessions and human engineering review.

## Milestone M1 — Multivariate anomaly learning

**Problem:** Current detectors treat each sensor separately; they cannot detect joint anomalies like pressure falling while vibration rises even if neither individual reading crosses its own threshold.

**Implementation tasks:**
1. Timestamp-align all four metrics per rig; build sliding windows with explicit missing-data masks, sensor-lag features, and leak-free train/inference boundaries.
2. Add `Detector` interface and isolation-forest baseline; include an optional autoencoder benchmark later, with all training performed within the experiment, no bundled trained weights.
3. Add temporal train/validation/test splits; for generalization checks, withhold entire rigs and incident windows.
4. Add reproducible experiment configurations, a JSON metrics registry, and a simple ablation runner comparing rules, robust Z, IsolationForest, and ensembles.
5. Report precision/recall/F1, PR-AUC, false alerts per rig-day, inference latency, and detection delay, with regression tests.

**Acceptance criteria:** new `rigpulse train` / `rigpulse compare` commands; documented protocol; artifacts reproducible with a fixed seed; backtest doesn't expose test labels to the detector; CI covers schema, train/test isolation, and API output contract.

## Milestone M2 — Real-time ingest and resilience

**Problem:** Batch-only import and full recompute will not work for sustained telemetry feeds.

**Implementation tasks:**
1. Define a typed event schema with device ID, event timestamp, ingestion timestamp, and an idempotency key.
2. Implement a mock MQTT connector and Kafka adapter behind a pluggable consumer interface; provide optional broker-backed integration tests.
3. Add event-time windows, handling of late/out-of-order measurements, deduplication, replay, and bounded queues/backpressure.
4. Introduce incremental detector state and crash-recovery checkpoints.
5. Benchmark throughput and end-to-end delay under bursts and stream interruptions.

**Acceptance criteria:** a local docker-compose integration test simulates restarts, duplicate messages, and late-arrival events with stable results and bounded memory.

## Milestone M3 — Alert correlation and incident workflows

**Problem:** Two detectors can flag the same event and a single equipment incident can span multiple sensors.

**Implementation tasks:**
1. Add incident grouping across detectors and configurable suppression/deduplication windows.
2. Introduce incident states (open, acknowledged, investigating, resolved) with append-only feedback and analyst notes.
3. Generate context explanations showing recent signal history, associated sensor anomalies, and suspected causes labeled as *hypotheses*.
4. Add notifications (webhook first), delivery retry policies, and an incident detail UI.
5. Track analyst feedback to measure false alerts and audit threshold changes.

**Acceptance criteria:** one physical event can result in one tracked incident, with reproducible grouping and an auditable state transition history.

## Milestone M4 — Production-grade infrastructure

**Problem:** SQLite and unauthenticated HTTP are suitable only for an isolated development demo.

**Implementation tasks:**
1. Add PostgreSQL repository adapter, migrations, and query-performance tests.
2. Implement authentication, per-rig authorization, audit trails, and secret handling.
3. Instrument OpenTelemetry traces, structured logs, and Prometheus-compatible metrics.
4. Add container health checks, Docker Compose with broker + database, automated security scans, and CI load tests.
5. Publish deployment profiles and explicit operational safety constraints.

**Acceptance criteria:** multi-user access-control integration tests, reproducible deployment, bounded request latencies, and documented failure recovery procedures.

## Suggested first four working sessions

| Session | Deliverable | Definition of done |
| --- | --- | --- |
| 1 | Alignment layer | Per-rig time-window feature matrix + missing-sensor tests |
| 2 | Isolation Forest detector | New detector interface, testable fit/predict path, no leakage |
| 3 | Time-aware backtesting | Frozen temporal test window + PR metrics and false-alert rate |
| 4 | Engineering comparison report | Baseline vs ML experiments, honest failure cases, README updates |

The roadmap is intentionally bounded: do **not** claim completed features in a project application until the code, tests, and docs exist.
