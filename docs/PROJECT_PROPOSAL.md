# RigPulse — Project Overview & Engineering Summary

**Project name:** RigPulse — Industrial Telemetry Reliability Lab

**Repository URL:** https://github.com/jnbalaji-jb/rigpulse-telemetry-lab

## Executive Summary

RigPulse is a local-first Python platform for industrial equipment telemetry reliability engineering. It ingests and validates multi-sensor time-series data (`temperature_c`, `pressure_bar`, `vibration_mm_s`, `flow_m3_h`), stores readings and checkpoints in SQLite, runs interpretable rule-based, statistical, and multivariate ML anomaly detectors, supports event-time streaming ingestion with crash recovery, evaluates detectors against isolated synthetic event labels, and exposes insights through FastAPI and an interactive web dashboard.

## Engineering Goals

Industrial telemetry monitoring requires balancing predictive detection with software correctness, explainability, and operational resilience. RigPulse is designed around:

- **Zero look-ahead leakage:** All rolling statistics, causal bucketed features, imputers, and standard scalers fit strictly on historical or designated training windows.
- **Deterministic reproducibility:** Seeded synthetic scenarios, deterministic feature alignment, and versioned JSON model artifacts ensure repeatable experiments.
- **Stream resilience:** Typed event schemas with idempotency keys, bounded queues with backpressure controls, event-time watermark tracking, late-arrival handling, and SQLite state checkpoints enable reliable recovery across restarts.
- **Explainable findings:** Every alert records the triggering detector, severity, numeric anomaly score, and human-readable explanation.

## Current Architecture & Implemented Capabilities (v0.3.0)

- **Core Pipeline (`v0.1.0`):** Seeded telemetry generator (`simulate.py`), strict UTC ISO-8601 validation (`models.py`, `ingest.py`), idempotent SQLite storage (`storage.py`), operating-limit and rolling robust-Z detectors (`detection.py`), PSI drift reporting (`drift.py`), offline evaluation (`evaluation.py`), FastAPI read API (`api.py`), and dependency-free SVG dashboard (`web/`).
- **Milestone M1 — Multivariate Anomaly Learning (`v0.2.0`):** Causal UTC-bucketed multivariate feature extraction (`features.py`), unified `Detector` protocol and leak-free `IsolationForestDetector` (`ml.py`), and embargoed temporal split plus Leave-One-Rig-Out (`LORO`) backtesting (`backtest.py`).
- **Milestone M2 — Real-Time Ingest & Resilience (`v0.3.0`):** Typed `TelemetryEvent` schema, `BoundedEventQueue` backpressure policies, `MockMQTTConnector` and `KafkaConsumerAdapter`, event-time watermark and late-arrival tracking, deterministic log replay, and `IncrementalStreamDetector` with SQLite checkpoint recovery (`streaming.py`).

## Roadmap Ahead

- **Milestone M3 — Alert Correlation & Incident Workflows:** Group multi-sensor and multi-detector alerts into tracked incidents with lifecycle states (`open`, `acknowledged`, `investigating`, `resolved`), deduplication windows, and hypothesis-labeled context explanations.
- **Milestone M4 — Production Infrastructure:** PostgreSQL repository adapter, authentication and per-rig RBAC, OpenTelemetry tracing/metrics, and multi-service container deployment profiles.
