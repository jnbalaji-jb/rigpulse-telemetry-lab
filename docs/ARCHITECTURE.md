# Architecture and design decisions

## Goals and boundaries

Version 0.3.0 is a **local-first, interpretable monitoring prototype with multivariate ML anomaly detection (Milestone M1) and event-time streaming resilience (Milestone M2)**. The purpose is to demonstrate correct modular software practices, leak-free feature and model pipelines, stateful stream processing with checkpoint recovery, and reproducible evaluation baselines. Data is synthetic by default.

### Components

1. `simulate.py`: time-ordered synthetic telemetry, rig variation, occasional spikes and mild degradation. Ground truth is written separately from observations.
2. `models.py` and `ingest.py`: strict schema with timezone-aware ISO 8601 input. Entire CSV validated before writing; invalid input cannot create a partially ingested batch.
3. `storage.py`: parameterized queries, enforced uniqueness and foreign keys; a reading uniquely identified by `(observed_at, rig_id, metric)`, plus `stream_events` and `detector_checkpoints` tables.
4. `features.py`: deterministic, UTC-bucketed causal time-series alignment and rolling past-only statistics (`align_series`, `rolling_features`, `export_features`).
5. `detection.py`: interpretable univariate detectors (`operating_limit` and `rolling_robust_z`) plus optional multivariate scoring via `ml.py` (`isolation_forest`).
6. `ml.py`: `Detector` protocol, leak-free training-only median imputation and z-score standardization, `IsolationForestDetector`, and versioned JSON artifact persistence.
7. `backtest.py`: embargoed chronological train/validation/test splits (`temporal_split_with_embargo`), Leave-One-Rig-Out (`LORO`) generalization validation, and baseline-vs-ML ablation reporting (`run_experiment_comparison`).
8. `streaming.py`: typed `TelemetryEvent` schema, `BoundedEventQueue` backpressure policies, `MockMQTTConnector`, `KafkaConsumerAdapter`, event-time watermark and late-arrival tracking, deterministic log replay, and `IncrementalStreamDetector` with SQLite checkpoint recovery.
9. `drift.py`: univariate PSI; an exploratory comparison of first/last observed halves, not a fully specified online drift monitor.
10. `evaluation.py`: row-level precision/recall/F1 using explicitly separate injected synthetic labels. No target labels are read by any detector.
11. `api.py` and `web/`: read-only API (including `/api/v1/stream/status`) and dependency-free dashboard. No auth in this development build.

## Key decisions

| Decision | Reason | Future tradeoff |
| --- | --- | --- |
| SQLite instead of PostgreSQL | Easy local setup and durable experiments | Limited multiwriter concurrency and retention controls |
| Batch + incremental checkpointed stream modes | Supports both full reproducible experiments and crash-recoverable streaming | Distributed broker offsets needed for multi-worker deployments |
| Historical-only baselines & training-only scalers | Prevents future leakage in features, scaling, and scoring | Needs multi-horizon late-event retraction policies |
| Rule + robust Z + Isolation Forest | Combines single-sensor explainability with cross-sensor context | Incident correlation & alert grouping planned in M3 |
| Synthetic labels | Reproducible demonstrations without proprietary data | Cannot establish real-world performance |
| Browser UI with zero JS dependencies | Easy demos without npm/CDNs | Future accessibility and chart UX enhancements |

## Data integrity

- Timestamps are normalized to UTC seconds (subsecond precision is deliberately discarded; rejecting collisions rather than overwriting protects existing data).
- `INSERT OR IGNORE` makes batch CSV ingest idempotent, while `stream_events` enforces `event_id` idempotency keys across duplicate stream deliveries and crash restarts.
- Re-running batch `detect` replaces old alerts and recomputes them with new parameters; incremental stream scoring appends alerts idempotently via `INSERT OR IGNORE`.

## Threat model and security limits

This is a single-user local development server, **not a hosted multi-tenant service**. The API exposes monitoring information without auth and must not be publicly deployed. A real production system requires authentication, per-rig authorization, encrypted transport, rate limits, secrets management, audit logs, and an explicit operations safety review.

## Evaluation and resilience design (M1 & M2)

`backtest.py` implements temporal train/validation/test windows with an embargo buffer around split boundaries and Leave-One-Rig-Out (`LORO`) validation. `streaming.py` benchmarks event-time throughput, p50/p95 latency, duplicate suppression, late-arrival handling, and checkpoint crash recovery (`rigpulse stream-demo`). Synthetic scoring is used strictly for engineering validation, not claims of real oilfield effectiveness.
