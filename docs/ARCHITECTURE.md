# Architecture and design decisions

## Goals and boundaries

Version 0.1 is a **local-first, interpretable monitoring prototype**. The purpose is to demonstrate correct modular software practices and establish a trustworthy baseline for more advanced anomaly models. Data is synthetic by default.

### Components

1. `simulate.py`: time-ordered synthetic telemetry, rig variation, occasional spikes and mild degradation. Ground truth is written separately from observations.
2. `models.py` and `ingest.py`: strict schema with timezone-aware ISO 8601 input. Entire CSV validated before writing; invalid input cannot create a partially ingested batch.
3. `storage.py`: parameterized queries, enforced uniqueness and foreign keys; a reading uniquely identified by `(observed_at, rig_id, metric)`.
4. `detection.py`: two simple explainable detectors: example operating-limit rules and an online-style *rolling robust z-score* computed only from earlier timestamps for each `(rig_id, metric)` series.
5. `drift.py`: univariate PSI; an exploratory comparison of first/last observed halves, not a fully specified online drift monitor.
6. `evaluation.py`: row-level precision/recall/F1 using explicitly separate injected synthetic labels. No target labels are read by the detector.
7. `api.py` and `web/`: read-only API and dependency-free dashboard. No auth in this development build.

## Key decisions

| Decision | Reason | Future tradeoff |
| --- | --- | --- |
| SQLite instead of PostgreSQL | Easy local setup and durable experiments | Limited multiwriter concurrency and retention controls |
| Batch detection and replacement of alerts | Simple, deterministic reruns | Inefficient for streams; incremental checkpoints needed |
| Historical-only baselines | Prevents future leakage in anomaly scoring | Needs handling of missing timestamps and delayed events |
| Rule + robust Z | Interpretable baseline for comparison | Cannot model cross-sensor interactions |
| Synthetic labels | Reproducible demonstrations without proprietary data | Cannot establish real-world performance |
| Browser UI with zero JS dependencies | Easy demos without npm/CDNs | Future accessibility and chart UX enhancements |

## Data integrity

- Timestamps are normalized to UTC seconds (subsecond precision is deliberately discarded in v0.1; rejecting collisions rather than overwriting protects existing data).
- `INSERT OR IGNORE` makes ingest idempotent, but conflicting values at the same identity are **not** reconciled in v0.1; adding explicit conflict auditing is a backlog item.
- Re-running `detect` removes old alerts and recomputes them with new parameters; it never changes raw readings.
- If the input is modified externally while detection runs, atomic read+write snapshot semantics are not guaranteed. Add transactional checkpoints before concurrent ingestion is implemented.

## Threat model and security limits

This is a single-user local development server, **not a hosted multi-tenant service**. The API exposes monitoring information without auth and must not be publicly deployed. A real production system requires authentication, per-rig authorization, encrypted transport, rate limits, secrets management, audit logs, and an explicit operations safety review.

## Future evaluation design

For M1, use temporal train/validation/test windows with an embargo around incident windows; test unseen rigs as an out-of-distribution split; report precision-recall area, false alerts per rig-day, recall at fixed false alert budget, time-to-detect, inference speed, and sensitivity to sensor dropouts. Never optimize on the held-out test set. Use synthetic scoring only for engineering validation, not claims of real oilfield effectiveness.
