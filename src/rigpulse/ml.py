"""Multivariate anomaly detection using causal features and Isolation Forest.

All imputers, scalers, and tree splits are fit strictly on the designated
training window. No target labels are used during unsupervised fitting.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import numpy as np
from sklearn.ensemble import IsolationForest

from rigpulse.features import (
    _bucket_start,
    _iso,
    _utc_datetime,
    align_series,
    feature_columns,
    rolling_features,
)
from rigpulse.models import METRICS, Alert
from rigpulse.storage import ordered_readings

MODEL_SCHEMA_VERSION = "1.0.0"

NUMERIC_FEATURE_COLUMNS: tuple[str, ...] = tuple(
    col for col in feature_columns() if col not in ("rig_id", "bucket_end_utc")
)


class Detector(Protocol):
    """Unified interface for univariate and multivariate anomaly detectors."""

    name: str

    def score_features(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return per-row anomaly scores and binary predictions without mutating history."""
        ...


@dataclass(frozen=True, slots=True)
class ModelConfig:
    bucket_minutes: int = 60
    window: int = 12
    n_estimators: int = 150
    contamination: float = 0.04
    max_samples: str | int = "auto"
    seed: int = 42
    score_quantile: float = 0.96


@dataclass(slots=True)
class IsolationForestDetector:
    """Leak-free multivariate detector over causal bucketed telemetry features."""

    config: ModelConfig
    feature_names: tuple[str, ...]
    medians: list[float]
    means: list[float]
    stds: list[float]
    threshold: float
    critical_threshold: float
    estimator: IsolationForest
    trained_at_utc: str
    training_rows: int
    training_rigs: list[str]
    name: str = "isolation_forest"

    @classmethod
    def fit(
        cls,
        feature_rows: Sequence[Mapping[str, Any]],
        config: ModelConfig | None = None,
    ) -> "IsolationForestDetector":
        cfg = config or ModelConfig()
        if len(feature_rows) < 10:
            raise ValueError("require at least 10 feature rows to fit IsolationForestDetector")

        cols = NUMERIC_FEATURE_COLUMNS
        raw = _extract_matrix(feature_rows, cols)

        col_medians = np.nanmedian(raw, axis=0)
        col_medians = np.where(np.isfinite(col_medians), col_medians, 0.0)
        imputed = np.where(np.isnan(raw), col_medians, raw)

        means = np.mean(imputed, axis=0)
        stds = np.std(imputed, axis=0)
        safe_stds = np.where(stds > 1e-9, stds, 1.0)
        scaled = (imputed - means) / safe_stds

        iso = IsolationForest(
            n_estimators=cfg.n_estimators,
            contamination=cfg.contamination,
            max_samples=cfg.max_samples,
            random_state=cfg.seed,
            n_jobs=1,
        )
        iso.fit(scaled)

        train_scores = -iso.decision_function(scaled)
        threshold = float(np.quantile(train_scores, cfg.score_quantile))
        critical_threshold = float(
            np.quantile(train_scores, min(0.995, cfg.score_quantile + 0.025))
        )
        if critical_threshold <= threshold:
            critical_threshold = threshold + 0.05

        rigs = sorted({str(r["rig_id"]) for r in feature_rows})
        return cls(
            config=cfg,
            feature_names=cols,
            medians=[float(x) for x in col_medians],
            means=[float(x) for x in means],
            stds=[float(x) for x in safe_stds],
            threshold=threshold,
            critical_threshold=critical_threshold,
            estimator=iso,
            trained_at_utc=datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            training_rows=len(feature_rows),
            training_rigs=rigs,
        )

    def _transform(self, feature_rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        raw = _extract_matrix(feature_rows, self.feature_names)
        medians = np.asarray(self.medians, dtype=float)
        means = np.asarray(self.means, dtype=float)
        stds = np.asarray(self.stds, dtype=float)
        imputed = np.where(np.isnan(raw), medians, raw)
        return (imputed - means) / stds

    def score_features(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        scaled = self._transform(rows)
        raw_scores = -self.estimator.decision_function(scaled)
        results: list[dict[str, Any]] = []
        for idx, (row, score_val) in enumerate(zip(rows, raw_scores)):
            score = float(score_val)
            flagged = score >= self.threshold
            severity = "critical" if score >= self.critical_threshold else "warning"
            top_contributors = _explain_row(scaled[idx], self.feature_names, top_k=3)
            results.append(
                {
                    "rig_id": row["rig_id"],
                    "bucket_end_utc": row["bucket_end_utc"],
                    "score": round(score, 5),
                    "is_anomaly": flagged,
                    "severity": severity,
                    "explanation": (
                        f"Multivariate Isolation Forest score {score:.3f} >= {self.threshold:.3f} "
                        f"(top shifts: {top_contributors})"
                    ),
                }
            )
        return results

    def save(self, path: str | Path) -> dict[str, Any]:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "detector": self.name,
            "config": asdict(self.config),
            "feature_names": list(self.feature_names),
            "medians": self.medians,
            "means": self.means,
            "stds": self.stds,
            "threshold": self.threshold,
            "critical_threshold": self.critical_threshold,
            "trained_at_utc": self.trained_at_utc,
            "training_rows": self.training_rows,
            "training_rigs": self.training_rigs,
            "estimator_bytes_hex": pickle.dumps(self.estimator).hex(),
        }
        serialized = json.dumps(payload, indent=2)
        target.write_text(serialized + "\n", encoding="utf-8")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
        return {
            "artifact_path": str(target),
            "sha256_prefix": digest,
            "training_rows": self.training_rows,
            "training_rigs": self.training_rigs,
            "threshold": round(self.threshold, 5),
            "critical_threshold": round(self.critical_threshold, 5),
        }

    @classmethod
    def load(cls, path: str | Path) -> "IsolationForestDetector":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != MODEL_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported model schema version: {payload.get('schema_version')!r}"
            )
        estimator = pickle.loads(bytes.fromhex(payload["estimator_bytes_hex"]))
        return cls(
            config=ModelConfig(**payload["config"]),
            feature_names=tuple(payload["feature_names"]),
            medians=list(payload["medians"]),
            means=list(payload["means"]),
            stds=list(payload["stds"]),
            threshold=float(payload["threshold"]),
            critical_threshold=float(payload["critical_threshold"]),
            estimator=estimator,
            trained_at_utc=str(payload["trained_at_utc"]),
            training_rows=int(payload["training_rows"]),
            training_rigs=list(payload["training_rigs"]),
        )


def _extract_matrix(rows: Sequence[Mapping[str, Any]], cols: Sequence[str]) -> np.ndarray:
    matrix = np.full((len(rows), len(cols)), np.nan, dtype=float)
    for i, row in enumerate(rows):
        for j, col in enumerate(cols):
            val = row.get(col)
            if val is not None and val != "":
                matrix[i, j] = float(val)
    return matrix


def _explain_row(scaled_row: np.ndarray, cols: Sequence[str], top_k: int = 3) -> str:
    ranked = sorted(
        zip(cols, scaled_row),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )[:top_k]
    return ", ".join(f"{name}={val:+.1f}sd" for name, val in ranked)


def train_model(
    db: str | Path,
    out_path: str | Path = "models/isolation_forest.json",
    train_fraction: float = 0.6,
    config: ModelConfig | None = None,
) -> dict[str, Any]:
    """Fit an Isolation Forest strictly on the earliest `train_fraction` of buckets."""
    if not 0.1 <= train_fraction <= 1.0:
        raise ValueError("train_fraction must be in [0.1, 1.0]")
    cfg = config or ModelConfig()
    readings = ordered_readings(db)
    aligned = align_series(readings, bucket_minutes=cfg.bucket_minutes)
    features = rolling_features(aligned, window=cfg.window)
    if not features:
        raise ValueError("no readings available in database for training")

    timestamps = sorted({str(r["bucket_end_utc"]) for r in features})
    cutoff_idx = max(1, int(len(timestamps) * train_fraction)) - 1
    cutoff_ts = timestamps[cutoff_idx]
    train_rows = [r for r in features if str(r["bucket_end_utc"]) <= cutoff_ts]

    detector = IsolationForestDetector.fit(train_rows, config=cfg)
    meta = detector.save(out_path)
    meta["train_cutoff_utc"] = cutoff_ts
    meta["total_available_buckets"] = len(timestamps)
    return meta


def score_db_multivariate(db: str | Path, model_path: str | Path) -> list[Alert]:
    """Score readings in SQLite using a saved IsolationForestDetector and map back to reading IDs."""
    detector = IsolationForestDetector.load(model_path)
    readings = ordered_readings(db)
    if not readings:
        return []

    aligned = align_series(readings, bucket_minutes=detector.config.bucket_minutes)
    features = rolling_features(aligned, window=detector.config.window)
    scored = detector.score_features(features)

    step = timedelta(minutes=detector.config.bucket_minutes)
    anchor_reading: dict[tuple[str, str], int] = {}
    for r in readings:
        ts = _utc_datetime(str(r["observed_at"]))
        b_end = _iso(_bucket_start(ts, detector.config.bucket_minutes) + step)
        key = (str(r["rig_id"]), b_end)
        if key not in anchor_reading or r["metric"] in METRICS:
            anchor_reading[key] = int(r["id"])

    alerts: list[Alert] = []
    for item in scored:
        if not item["is_anomaly"]:
            continue
        key = (str(item["rig_id"]), str(item["bucket_end_utc"]))
        rid = anchor_reading.get(key)
        if rid is not None:
            alerts.append(
                Alert(
                    reading_id=rid,
                    detector=detector.name,
                    severity=str(item["severity"]),
                    score=float(item["score"]),
                    explanation=str(item["explanation"]),
                )
            )
    return alerts
