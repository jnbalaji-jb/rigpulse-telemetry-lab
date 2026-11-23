"""Temporal & leave-one-rig-out backtesting and baseline-vs-ML ablation runner."""
from __future__ import annotations

import csv
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from sklearn.metrics import average_precision_score

from rigpulse.detection import FLOORS, LIMITS
from rigpulse.features import _bucket_start, _iso, _utc_datetime, align_series, rolling_features
from rigpulse.ml import IsolationForestDetector, ModelConfig
from rigpulse.models import METRICS, normalize_timestamp
from rigpulse.storage import ordered_readings


def load_bucket_ground_truth(
    truth_path: str | Path, bucket_minutes: int = 60,
) -> dict[tuple[str, str], bool]:
    """Aggregate row-level synthetic labels into bucket-level truth (never exposed to detectors)."""
    step = timedelta(minutes=bucket_minutes)
    bucket_truth: dict[tuple[str, str], bool] = {}
    with open(truth_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"observed_at", "rig_id", "metric", "is_anomaly"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("truth CSV must contain observed_at, rig_id, metric, is_anomaly")
        for row in reader:
            if row["is_anomaly"] not in ("0", "1"):
                raise ValueError("is_anomaly must be 0 or 1")
            ts = _utc_datetime(normalize_timestamp(row["observed_at"]))
            b_end = _iso(_bucket_start(ts, bucket_minutes) + step)
            key = (str(row["rig_id"]), b_end)
            bucket_truth[key] = bucket_truth.get(key, False) or (row["is_anomaly"] == "1")
    return bucket_truth


def temporal_split_with_embargo(
    feature_rows: Sequence[Mapping[str, Any]],
    train_ratio: float = 0.5,
    val_ratio: float = 0.2,
    embargo_buckets: int = 2,
) -> dict[str, list[Mapping[str, Any]]]:
    """Chronologically partition buckets with an embargo buffer to prevent boundary leakage."""
    timestamps = sorted({str(r["bucket_end_utc"]) for r in feature_rows})
    n = len(timestamps)
    if n < 12:
        raise ValueError("need at least 12 distinct time buckets for temporal split")

    train_end = int(n * train_ratio)
    val_start = min(n - 2, train_end + embargo_buckets)
    val_end = max(val_start + 1, int(n * (train_ratio + val_ratio)))
    test_start = min(n - 1, val_end + embargo_buckets)

    train_ts = set(timestamps[:train_end])
    val_ts = set(timestamps[val_start:val_end])
    test_ts = set(timestamps[test_start:])

    return {
        "train": [r for r in feature_rows if str(r["bucket_end_utc"]) in train_ts],
        "validation": [r for r in feature_rows if str(r["bucket_end_utc"]) in val_ts],
        "test": [r for r in feature_rows if str(r["bucket_end_utc"]) in test_ts],
    }


def _score_univariate_baselines(
    feature_rows: Sequence[Mapping[str, Any]],
    window: int = 48,
    min_history: int = 16,
    z_threshold: float = 4.5,
) -> dict[str, list[dict[str, Any]]]:
    """Score aligned buckets using past-only operating limits and rolling robust Z."""
    by_rig: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in feature_rows:
        by_rig[str(r["rig_id"])].append(r)

    rule_preds: list[dict[str, Any]] = []
    robust_z_preds: list[dict[str, Any]] = []

    for rig, rows in sorted(by_rig.items()):
        rows = sorted(rows, key=lambda x: str(x["bucket_end_utc"]))
        histories = {m: deque(maxlen=window) for m in METRICS}
        for row in rows:
            max_excess = 0.0
            max_z = 0.0
            for m in METRICS:
                val = row.get(m)
                if val is None:
                    continue
                v = float(val)
                side, bound = LIMITS[m]
                excess = (v - bound) if side == "upper" else (bound - v)
                if excess > max_excess:
                    max_excess = excess

                past = histories[m]
                if len(past) >= min_history:
                    c = median(past)
                    mad = median(abs(x - c) for x in past)
                    sigma = max(1.4826 * mad, FLOORS[m])
                    z = abs(v - c) / sigma
                    if z > max_z:
                        max_z = z
                past.append(v)

            key_meta = {"rig_id": rig, "bucket_end_utc": row["bucket_end_utc"]}
            rule_preds.append({**key_meta, "score": max_excess, "is_anomaly": max_excess > 0.0})
            robust_z_preds.append({**key_meta, "score": max_z, "is_anomaly": max_z >= z_threshold})

    return {"operating_limit": rule_preds, "rolling_robust_z": robust_z_preds}


def compute_metrics(
    predictions: Sequence[Mapping[str, Any]],
    truth: Mapping[tuple[str, str], bool],
    bucket_minutes: int = 60,
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    """Compute F1, PR-AUC, false alerts per rig-day, and incident detection delay."""
    y_true: list[int] = []
    y_score: list[float] = []
    tp = fp = tn = fn = 0

    by_rig: dict[str, list[tuple[str, bool, bool]]] = defaultdict(list)

    for p in predictions:
        key = (str(p["rig_id"]), str(p["bucket_end_utc"]))
        if key not in truth:
            continue
        label = bool(truth[key])
        pred = bool(p["is_anomaly"])
        y_true.append(int(label))
        y_score.append(float(p["score"]))
        by_rig[key[0]].append((key[1], label, pred))

        if pred and label:
            tp += 1
        elif pred and not label:
            fp += 1
        elif label and not pred:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    pr_auc = float(average_precision_score(y_true, y_score)) if sum(y_true) > 0 else 0.0

    total_rig_hours = len(y_true) * (bucket_minutes / 60.0)
    rig_days = max(total_rig_hours / 24.0, 1e-6)
    false_alerts_per_rig_day = fp / rig_days

    delays: list[int] = []
    for _, seq in by_rig.items():
        seq.sort(key=lambda item: item[0])
        in_incident = False
        onset_idx = 0
        detected = False
        for idx, (_, label, pred) in enumerate(seq):
            if label and not in_incident:
                in_incident = True
                onset_idx = idx
                detected = False
            if in_incident and label and pred and not detected:
                delays.append(idx - onset_idx)
                detected = True
            if not label:
                in_incident = False

    mean_delay_buckets = (sum(delays) / len(delays)) if delays else None

    return {
        "evaluated_buckets": len(y_true),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pr_auc": round(pr_auc, 4),
        "false_alerts_per_rig_day": round(false_alerts_per_rig_day, 4),
        "mean_detection_delay_buckets": (
            round(mean_delay_buckets, 2) if mean_delay_buckets is not None else None
        ),
        "inference_latency_ms": round(latency_ms, 3),
    }


def run_experiment_comparison(
    db: str | Path,
    truth_path: str | Path,
    out_path: str | Path | None = None,
    config: ModelConfig | None = None,
) -> dict[str, Any]:
    """Compare univariate rules, robust Z, Isolation Forest, and ensemble across temporal & LORO splits."""
    cfg = config or ModelConfig()
    readings = ordered_readings(db)
    aligned = align_series(readings, bucket_minutes=cfg.bucket_minutes)
    features = rolling_features(aligned, window=cfg.window)
    truth = load_bucket_ground_truth(truth_path, bucket_minutes=cfg.bucket_minutes)

    splits = temporal_split_with_embargo(features)
    model = IsolationForestDetector.fit(splits["train"], config=cfg)

    test_keys = {(str(r["rig_id"]), str(r["bucket_end_utc"])) for r in splits["test"]}
    baselines_all = _score_univariate_baselines(features)
    rule_test = [
        p for p in baselines_all["operating_limit"] if (p["rig_id"], p["bucket_end_utc"]) in test_keys
    ]
    rz_test = [
        p for p in baselines_all["rolling_robust_z"] if (p["rig_id"], p["bucket_end_utc"]) in test_keys
    ]

    t0 = time.perf_counter()
    if_test = model.score_features(splits["test"])
    if_latency_ms = (time.perf_counter() - t0) * 1000.0

    rz_by_key = {(p["rig_id"], p["bucket_end_utc"]): p for p in rz_test}
    ens_test = []
    for p in if_test:
        k = (p["rig_id"], p["bucket_end_utc"])
        rz_p = rz_by_key[k]
        ens_score = max(float(p["score"]) - model.threshold, float(rz_p["score"]) - 4.5)
        ens_test.append(
            {
                "rig_id": p["rig_id"],
                "bucket_end_utc": p["bucket_end_utc"],
                "score": ens_score,
                "is_anomaly": bool(p["is_anomaly"] or rz_p["is_anomaly"]),
            }
        )

    temporal_results = {
        "operating_limit": compute_metrics(rule_test, truth, cfg.bucket_minutes),
        "rolling_robust_z": compute_metrics(rz_test, truth, cfg.bucket_minutes),
        "isolation_forest": compute_metrics(
            if_test, truth, cfg.bucket_minutes, latency_ms=if_latency_ms
        ),
        "ensemble_rz_or_if": compute_metrics(
            ens_test, truth, cfg.bucket_minutes, latency_ms=if_latency_ms
        ),
    }

    rigs = sorted({str(r["rig_id"]) for r in features})
    loro_preds: list[dict[str, Any]] = []
    if len(rigs) >= 2:
        for held_out_rig in rigs:
            train_r = [r for r in features if str(r["rig_id"]) != held_out_rig]
            test_r = [r for r in features if str(r["rig_id"]) == held_out_rig]
            if len(train_r) >= 10 and test_r:
                fold_model = IsolationForestDetector.fit(train_r, config=cfg)
                loro_preds.extend(fold_model.score_features(test_r))

    loro_metrics = (
        compute_metrics(loro_preds, truth, cfg.bucket_minutes)
        if loro_preds
        else {"status": "skipped_requires_at_least_2_rigs"}
    )

    report = {
        "experiment": "M1_multivariate_ablation",
        "config": asdict(cfg),
        "split_counts": {k: len(v) for k, v in splits.items()},
        "temporal_test_metrics": temporal_results,
        "leave_one_rig_out_isolation_forest": loro_metrics,
        "note": "Evaluated on held-out temporal window (with embargo) and unseen rigs using synthetic labels.",
    }

    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return report
