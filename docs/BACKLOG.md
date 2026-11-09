# Engineering backlog — GitHub issue-ready

Create these as GitHub issues after publishing the repository. Each item is distinct, testable, and suitable for meaningful incremental commits.

| Priority | Issue title | Why this matters | Estimated scope |
| --- | --- | --- | --- |
| P0 | `feat: build time-aligned multivariate signal windows` | Different sensors arrive at different times | Core library + tests |
| P0 | `feat: add Detector protocol and isolation-forest baseline` | Cross-sensor anomalies go undetected | Training module + tests |
| P0 | `test: add temporal and leave-one-rig-out backtesting` | Avoid overly optimistic random splits | Evaluation + CLI |
| P1 | `feat: experiment registry with immutable run configs` | Compare new AI-generated approaches reproducibly | Schema + files + CLI |
| P1 | `feat: incremental event-time streaming detector` | Current batch runs recompute all history | State machine + tests |
| P1 | `feat: group alert findings into incidents` | One failure generates multiple findings | DB migration + API + UI |
| P2 | `infra: add MQTT simulator and optional Kafka connector` | Test ingestion and failure recovery | Adapter + Compose |
| P2 | `security: introduce auth and per-rig RBAC` | Server currently runs locally and unauthenticated | Security + tests |
| P2 | `infra: PostgreSQL backend with migrations` | Improved concurrent writes and larger datasets | Persistence + benchmarks |
| P2 | `observability: metrics, logs, traces` | Instrument performance and reliability | OpenTelemetry tests |
| P2 | `docs: publish benchmark protocol and failure cases` | Transparent measurement and reproducibility | Docs + sample configs |
| P2 | `quality: property-based tests for out-of-order events` | Detect timestamp edge cases and duplicate conflicts | Test suite + fixes |

## Ready-to-start issue: time-aligned multivariate feature windows

**User story:** As an engineer, I want to identify the relationship between several equipment signals, so I can detect unusual combinations even when individual values appear normal.

**Task checklist:**
- [ ] Create `src/rigpulse/features.py` with `align_series(readings, bucket_minutes=60)` and `rolling_features(window=12)`.
- [ ] Support deterministic missing-value indicators, rather than silently dropping incomplete rows.
- [ ] Add median, gradient, trailing variability, pressure/flow ratio, and vibration-to-flow change features.
- [ ] Disallow the use of any future measurements while computing features for time `t`.
- [ ] Add tests for shuffled input, entirely missing sensors, timestamp boundaries, and a held-out rig.
- [ ] Add one runnable CLI command to export features and document its output schema.

**Definition of done:** the feature computation is deterministic, covered by unit tests, and demonstrably does not leak observations from time `t+1` into time `t`.

## Documentation discipline

Move issue labels from planned to implemented **only** after a passing test, updated architecture docs, and an actual commit. Small meaningful commits over time are preferable to artificially backdated or inflated activity.
