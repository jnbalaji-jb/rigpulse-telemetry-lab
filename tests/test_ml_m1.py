"""Tests for Milestone M1: Multivariate Isolation Forest, temporal & LORO backtesting."""
from __future__ import annotations

import json

from rigpulse.backtest import temporal_split_with_embargo
from rigpulse.cli import main
from rigpulse.detection import detect
from rigpulse.features import align_series, rolling_features
from rigpulse.ingest import ingest_csv
from rigpulse.ml import IsolationForestDetector, ModelConfig, train_model
from rigpulse.simulate import generate
from rigpulse.storage import fetch_alerts, ordered_readings


def test_isolation_forest_detects_joint_cross_sensor_anomaly(tmp_path):
    """Joint shift across sensors is caught by Isolation Forest and persisted to SQLite."""
    generated = generate(tmp_path / "data", rigs=2, hours=120, seed=7)
    db = tmp_path / "m1.db"
    ingest_csv(generated["telemetry_path"], db)

    model_path = tmp_path / "models" / "if.json"
    meta = train_model(db, model_path, train_fraction=0.6, config=ModelConfig(seed=7))
    assert meta["training_rows"] > 0

    loaded_detector = IsolationForestDetector.load(model_path)
    summary = detect(db, model_path=model_path)
    assert summary["by_detector"]["isolation_forest"] > 0

    alerts = fetch_alerts(db, limit=200)
    ml_alerts = [a for a in alerts if a["detector"] == "isolation_forest"]
    assert len(ml_alerts) == summary["by_detector"]["isolation_forest"]
    assert "Isolation Forest score" in ml_alerts[0]["explanation"]
    assert loaded_detector.name == "isolation_forest"


def test_temporal_split_enforces_embargo_and_no_overlap(tmp_path):
    generated = generate(tmp_path / "data", rigs=2, hours=100, seed=11)
    db = tmp_path / "split.db"
    ingest_csv(generated["telemetry_path"], db)

    features = rolling_features(align_series(ordered_readings(db)))
    splits = temporal_split_with_embargo(features, train_ratio=0.5, val_ratio=0.2, embargo_buckets=3)

    train_ts = {r["bucket_end_utc"] for r in splits["train"]}
    val_ts = {r["bucket_end_utc"] for r in splits["validation"]}
    test_ts = {r["bucket_end_utc"] for r in splits["test"]}

    assert train_ts.isdisjoint(val_ts)
    assert val_ts.isdisjoint(test_ts)
    assert train_ts.isdisjoint(test_ts)
    assert max(train_ts) < min(val_ts)
    assert max(val_ts) < min(test_ts)


def test_cli_train_and_compare_end_to_end(tmp_path, capsys):
    generated = generate(tmp_path / "data", rigs=3, hours=120, seed=42)
    db = tmp_path / "exp.db"
    ingest_csv(generated["telemetry_path"], db)

    model_out = tmp_path / "artifacts" / "model.json"
    report_out = tmp_path / "reports" / "compare.json"

    assert main(["train", "--db", str(db), "--out", str(model_out), "--seed", "42"]) == 0
    train_res = json.loads(capsys.readouterr().out)
    assert train_res["training_rigs"] == ["RIG-001", "RIG-002", "RIG-003"]

    assert (
        main(
            [
                "compare",
                "--db",
                str(db),
                "--truth",
                generated["truth_path"],
                "--out",
                str(report_out),
                "--seed",
                "42",
            ]
        )
        == 0
    )
    comp = json.loads(capsys.readouterr().out)
    assert "isolation_forest" in comp["temporal_test_metrics"]
    assert "pr_auc" in comp["temporal_test_metrics"]["isolation_forest"]
    assert "false_alerts_per_rig_day" in comp["temporal_test_metrics"]["isolation_forest"]
    assert comp["leave_one_rig_out_isolation_forest"]["evaluated_buckets"] > 0
