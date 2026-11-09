# GitHub project submission briefing (editable, honest draft)

**Project name:** RigPulse — Industrial Telemetry Reliability Lab

**Repository URL (after you publish it):** https://github.com/jnbalaji-jb/rigpulse-telemetry-lab

## Short project summary

RigPulse is a local-first Python platform for exploring industrial equipment telemetry reliability. It ingests validated time-series data, stores it in SQLite, computes interpretable anomaly alerts, evaluates detectors against isolated synthetic event labels, and exposes insights through FastAPI and an interactive web dashboard. The current project is a working **v0.1 prototype** using reproducible synthetic data—not a production monitoring or industrial safety system.

## Why this matters to me (personalize before using)

I am interested in reliability engineering and applying ML to operational telemetry. I want to build a system where software correctness, transparent anomaly explanations, data-quality controls, and real-world engineering trade-offs are as important as predictive performance. This repository is my environment for progressively implementing and evaluating those capabilities.

## Current state (accurate as of generated starter)

The initial project includes a Python package, an installable CLI, a synthetic data generator, strict CSV ingestion, idempotent SQLite storage, an explainable rules + robust-z detector, a basic PSI drift report, offline evaluation, FastAPI endpoints, a dashboard, pytest coverage, and CI configuration.

## Work I plan to continue next

My immediate priority is a **multivariate, temporally valid anomaly-detection pipeline**. I plan to align sensor signals by event time, design window features, compare rule-based baselines with Isolation Forest, introduce reproducible backtesting across time and unseen rigs, and measure false alarms per rig-day. After that, I want to implement streaming ingestion and incident correlation, followed by production-focused security and observability work.

## Why AI-assisted coding is useful to this project

I expect to use an AI coding assistant for bounded implementation tasks such as feature engineering, test generation, refactoring, and API design. I'll assess each proposal for test correctness, future-data leakage, reliability under noisy/late sensor events, clarity, and maintainability. I will run the code and make the final engineering decisions myself.

## Important honesty checklist before an application

- [ ] I have reviewed and can explain the current code and tests.
- [ ] I have run the demo successfully on my own machine.
- [ ] I have made at least one genuine engineering improvement and can describe it.
- [ ] I created a GitHub issue for my first planned milestone.
- [ ] I have not claimed that planned work is implemented.
- [ ] I can confirm that I have rights to all code and data in the repository.
- [ ] I will disclose AI assistance accurately if the program asks how the code was developed.

> **Do not submit this draft as a claim that you personally implemented all generated starter code.** Adapt it to your actual involvement, review the repository, and do real work on the next milestone. Project acceptance is up to the program's reviewers.
