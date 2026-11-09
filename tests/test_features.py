"""Issue #1, session 1: deterministic, causal alignment and feature export."""
import csv
from datetime import datetime, timedelta, timezone

import pytest

from rigpulse.cli import main
from rigpulse.features import align_series, export_features, rolling_features
from rigpulse.ingest import ingest_csv
from rigpulse.models import METRICS
from rigpulse.simulate import generate


def r(hour, metric, value, rig="RIG-001", minute=0):
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour, minutes=minute)
    return {"observed_at": dt.isoformat(), "rig_id": rig, "metric": metric, "value": value}


def test_shuffled_input_is_deterministic():
    readings = [r(0, "pressure_bar", 11), r(0, "temperature_c", 22),
                r(1, "pressure_bar", 33), r(0, "pressure_bar", 44, minute=30)]
    expected = align_series(readings)
    assert align_series(list(reversed(readings))) == expected
    assert expected[0]["pressure_bar"] == 44  # latest sample within hour
    assert expected[0]["bucket_end_utc"] == "2026-01-01T01:00:00Z"
    assert expected[0]["flow_m3_h_missing"] == 1


def test_missing_sensor_and_empty_bucket_are_explicit():
    rows = align_series([r(0, "temperature_c", 5), r(2, "temperature_c", 9)])
    assert len(rows) == 3
    assert rows[1]["present_metrics"] == 0
    assert all(rows[1][f"{m}_missing"] == 1 and rows[1][m] is None for m in METRICS)
    features = rolling_features(rows, window=3)
    assert features[2]["temperature_c_delta_1"] is None  # gap not erased
    assert features[2]["temperature_c_past_median"] == 5


def test_bucket_boundary_and_cross_sensor_ratios():
    readings = [r(0, "pressure_bar", 200, minute=59), r(0, "flow_m3_h", 100),
                r(1, "temperature_c", 50), r(1, "pressure_bar", 300)]
    aligned = align_series(readings)
    assert len(aligned) == 2
    assert aligned[0]["pressure_bar"] == 200
    assert aligned[1]["pressure_bar"] == 300
    f = rolling_features(aligned)
    assert f[0]["pressure_flow_ratio"] == pytest.approx(2)
    assert f[1]["pressure_flow_ratio"] is None  # no forward-fill of flow


def test_future_outlier_cannot_change_past_features():
    first = [r(0, "temperature_c", 10), r(1, "temperature_c", 12),
             r(2, "temperature_c", 14)]
    early = rolling_features(align_series(first), window=3)
    extended = rolling_features(align_series(first + [r(3, "temperature_c", 1e9)]), window=3)
    assert extended[:len(early)] == early
    assert early[2]["temperature_c_past_median"] == 11
    assert early[2]["temperature_c_delta_1"] == 2


def test_rig_isolation_and_no_cross_rig_imputation():
    a = [r(0, "temperature_c", 1), r(1, "temperature_c", 2)]
    b = [r(0, "temperature_c", 1000, "RIG-002"),
         r(1, "temperature_c", 2000, "RIG-002")]
    per_rig = rolling_features(align_series(a), 2)
    combined = rolling_features(align_series(a + b), 2)
    assert [row for row in combined if row["rig_id"] == "RIG-001"] == per_rig
    assert combined[-1]["temperature_c_past_median"] == 1000


def test_invalid_settings():
    for size in (0, -1, 61, 1441):
        with pytest.raises(ValueError, match="bucket_minutes"):
            align_series([], bucket_minutes=size)
    with pytest.raises(ValueError, match="window"):
        rolling_features([], window=1)
    with pytest.raises(ValueError, match="duplicate bucket"):
        sample = {"rig_id": "RIG-001", "bucket_end_utc": "2026-01-01T01:00:00Z",
                  **{metric: None for metric in METRICS}}
        rolling_features([sample, sample])


def test_cli_exports_features_without_labels(tmp_path, capsys):
    db = tmp_path / "demo.db"
    out = tmp_path / "reports" / "features.csv"
    generated = generate(tmp_path / "data", rigs=2, hours=48, seed=21)
    ingest_csv(generated["telemetry_path"], db)
    assert main(["features", "--db", str(db), "--out", str(out),
                 "--bucket-minutes", "60", "--window", "12"]) == 0
    with out.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 96
    assert rows[0]["present_metrics"] == "4"
    assert "is_anomaly" not in rows[0]
    assert "temperature_c_past_median" in rows[0]
    assert rows[0]["temperature_c_past_median"] == ""
    assert rows[1]["temperature_c_history_count"] == "1"
    assert "Prototype" in capsys.readouterr().out


def test_empty_database_export_header_only(tmp_path):
    report = export_features(tmp_path / "empty.db", tmp_path / "empty.csv")
    assert report["rows"] == 0
    with (tmp_path / "empty.csv").open() as f:
        assert len(f.readlines()) == 1
