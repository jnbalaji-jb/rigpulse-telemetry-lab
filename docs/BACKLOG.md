# Engineering backlog

Track work as focused GitHub issues. Each item is distinct, testable, and suitable for incremental pull requests.

| Priority | Status | Issue title | Why this matters | Estimated scope |
| --- | --- | --- | --- | --- |
| P0 | ✅ Done (M1) | `feat: build time-aligned multivariate signal windows` | Different sensors arrive at different times | Core library + tests |
| P0 | ✅ Done (M1) | `feat: add Detector protocol and isolation-forest baseline` | Cross-sensor anomalies go undetected | Training module + tests |
| P0 | ✅ Done (M1) | `test: add temporal and leave-one-rig-out backtesting` | Avoid overly optimistic random splits | Evaluation + CLI |
| P1 | ✅ Done (M2) | `feat: incremental event-time streaming detector` | Batch-only runs recompute all history | State machine + tests |
| P1 | ✅ Done (M2) | `infra: add MQTT simulator and optional Kafka connector` | Test ingestion and failure recovery | Adapter + Compose |
| P1 | ✅ Done (M2) | `quality: tests for out-of-order events and checkpoints` | Verify timestamp edge cases and duplicate conflicts | Test suite + fixes |
| P1 | 🟡 Next (M3) | `feat: group alert findings into incidents` | One failure generates multiple findings | DB migration + API + UI |
| P2 | 🟡 Planned (M3) | `feat: experiment registry with immutable run configs` | Compare modeling experiments reproducibly | Schema + files + CLI |
| P2 | 🟡 Planned (M4) | `security: introduce auth and per-rig RBAC` | Server currently runs locally and unauthenticated | Security + tests |
| P2 | 🟡 Planned (M4) | `infra: PostgreSQL backend with migrations` | Improved concurrent writes and larger datasets | Persistence + benchmarks |
| P2 | 🟡 Planned (M4) | `observability: metrics, logs, traces` | Instrument performance and reliability | OpenTelemetry tests |
| P2 | 🟡 Planned (M4) | `docs: publish benchmark protocol and failure cases` | Transparent measurement and reproducibility | Docs + sample configs |

## Active milestone issue: alert correlation and incident workflows (Issue #3 / M3)

**User story:** As a reliability engineer, I want correlated anomalies across sensors and detectors grouped into a single tracked incident, so I can triage root causes without duplicate alert fatigue.

**Task checklist:**
- [ ] Add an `incidents` table and migration in `src/rigpulse/storage.py` linking correlated `alerts` within a configurable rig/time window.
- [ ] Support incident lifecycle states (`open`, `acknowledged`, `investigating`, `resolved`) with append-only analyst feedback notes.
- [ ] Generate structured context explanations summarizing recent signal history and correlated anomalies labeled as *hypotheses*.
- [ ] Expose `GET /api/v1/incidents` and `PATCH /api/v1/incidents/{id}` endpoints in `src/rigpulse/api.py` and an incident triage view in `src/rigpulse/web/`.
- [ ] Add unit and integration tests covering multi-detector grouping, suppression windows, and audit history.

**Definition of done:** a multi-sensor anomaly event produces one grouped incident with deterministic deduplication, full state transition auditability, and passing CI tests.

## Documentation discipline

Move issue labels from planned to implemented **only** after a passing test suite, updated architecture docs, and a merged pull request.
