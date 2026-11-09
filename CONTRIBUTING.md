# Contributing

RigPulse is an evolving learning/engineering prototype. Please open an issue before making large changes. Development priorities live in `docs/ROADMAP.md` and `docs/BACKLOG.md`.

1. Use Python 3.11+ and install `pip install -e ".[dev]"`.
2. Run `python -m pytest -q` before proposing changes.
3. Keep detector math deterministic, explainable, and free of look-ahead leakage.
4. Introduce an isolated unit test for every edge case or bug fix.
5. Document public API/schema changes, especially ingestion and alert semantics.
6. Never include real customer telemetry, secrets, proprietary rig data, or unlicensed datasets.
7. Make small, descriptive commits and update the roadmap only when code is actually done.

This platform does not prescribe operational safety thresholds for physical equipment.
