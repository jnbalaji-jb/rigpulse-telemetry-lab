"""Evaluate row-level alerts against independently generated synthetic ground truth."""
from __future__ import annotations

import csv
from pathlib import Path

from rigpulse.models import normalize_timestamp
from rigpulse.storage import connect, initialize


def evaluate(db: str, truth_path: str | Path) -> dict:
    truth = {}
    with open(truth_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"observed_at", "rig_id", "metric", "is_anomaly"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("truth CSV must contain observed_at, rig_id, metric, is_anomaly")
        for row in reader:
            if row["is_anomaly"] not in ("0", "1"):
                raise ValueError("is_anomaly must be 0 or 1")
            key = (normalize_timestamp(row["observed_at"]), row["rig_id"], row["metric"])
            if key in truth:
                raise ValueError(f"duplicate truth row: {key}")
            truth[key] = row["is_anomaly"] == "1"
    initialize(db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT r.observed_at,r.rig_id,r.metric, "
            "EXISTS(SELECT 1 FROM alerts a WHERE a.reading_id=r.id) AS flagged FROM readings r"
        ).fetchall()
    tp = fp = tn = fn = 0
    matched = set()
    for row in rows:
        key = (row["observed_at"], row["rig_id"], row["metric"])
        if key not in truth:
            continue
        matched.add(key)
        prediction, label = bool(row["flagged"]), truth[key]
        if prediction and label:
            tp += 1
        elif prediction and not label:
            fp += 1
        elif label and not prediction:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"matched": len(matched), "unmatched_truth": len(truth) - len(matched),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "note": "Synthetic-label evaluation only. This is not evidence of real-world safety performance."}
