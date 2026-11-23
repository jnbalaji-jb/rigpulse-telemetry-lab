# Architecture and design decisions

## Goals and boundaries

Version 0.2.0 is a **local-first, interpretable monitoring prototype with multivariate ML anomaly detection (Milestone M1)**. The purpose is to demonstrate correct modular software practices, leak-free feature and model pipelines, and reproducible evaluation baselines. Data is synthetic by default.

### Components

1. `simulate.py`: time-ordered synthetic telemetry, rig variation, occasional spikes and mild degradation. Ground truth is written separately from observations.
2. `models.py` and `ingest.py`: strict schema with timezone-aware ISO 8601 input. Entire CSV validated before writing; invalid input cannot create a partially ingested batch.
3. `storage.py`: parameterized queries, enforced uniqueness and foreign keys; a reading uniquely identified by `(observed_at, rig_id, metric)`.
4. `features.py`: deterministic, UTC-bucketed causal time-series alignment and rolling past-only statistics (`align_series`, `rolling_features`, `export_features`).
5. `detection.py`: interpretable univariate detectors (`operating_limit` and `rolling_robust_z`) plus optional multivariate scoring via `ml.py` (`isolation_forest`).
6. `ml.py`: `Detector` protocol, leak-free training-only median imputation and z-score standardization, `IsolationForestDetector`, and versioned JSON artifact persistence.
7. `backtest.py`: embargoed chronological train/validation/test splits (`temporal_split_with_embargo`), Leave-One-Rig-Out (`LORO`) generalization validation, and baseline-vs-ML ablation reporting (`run_experiment_comparison`).
8. `drift.py`: univariate PSI; an exploratory comparison of first/last observed halves, not a fully specified online drift monitor.
9. `evaluation.py`: row-level precision/recall/F1 using explicitly separate injected synthetic labels. No target labels are read by any detector.
10. `api.py` and `web/`: read-only API and dependency-free dashboard. No auth in this development build.

## Key decisions

| Decision | Reason | Future tradeoff |
| --- | --- | --- |
| SQLite instead of PostgreSQL | Easy local setup and durable experiments | Limited multiwriter concurrency and retention controls |
| Batch detection and replacement of alerts | Simple, deterministic reruns | Inefficient for streams; incremental checkpoints needed |
| Historical-only baselines & training-only scalers | Prevents future leakage in features, scaling, and scoring | Needs handling of missing timestamps and delayed events |
| Rule + robust Z + Isolation Forest | Combines single-sensor explainability with cross-sensor context | Incremental online updates planned in M2 |
| Synthetic labels | Reproducible demonstrations without proprietary data | Cannot establish real-world performance |
| Browser UI with zero JS dependencies | Easy demos without npm/CDNs | Future accessibility and chart UX enhancements |

## Data integrity

- Timestamps are normalized to UTC seconds (subsecond precision is deliberately discarded; rejecting collisions rather than overwriting protects existing data).
- `INSERT OR IGNORE` makes ingest idempotent, but conflicting values at the same identity are **not** reconciled yet; adding explicit conflict auditing is a backlog item.
- Re-running `detect` removes old alerts and recomputes them with new parameters; it never changes raw readings.
- If the input is modified externally while detection runs, atomic read+write snapshot semantics are not guaranteed. Add transactional checkpoints before concurrent ingestion is implemented.

## Threat model and security limits

This is a single-user local development server, **not a hosted multi-tenant service**. The API exposes monitoring information without auth and must not be publicly deployed. A real production system requires authentication, per-rig authorization, encrypted transport, rate limits, secrets management, audit logs, and an explicit operations safety review.

## Evaluation design (Milestone M1)

`backtest.py` implements temporal train/validation/test windows with an embargo buffer around split boundaries, tests unseen rigs via Leave-One-Rig-Out (`LORO`) validation, and reports F1, PR-AUC, false alerts per rig-day, mean detection delay, and inference latency. Synthetic scoring is used strictly for engineering validation, not claims of real oilfield effectiveness.
