from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rigpulse.api import create_app
from rigpulse.cli import main as cli_main
from rigpulse.incidents import (
    add_incident_note,
    correlate_alerts_into_incidents,
    fetch_incident_summary,
    get_incident_detail,
    list_incidents,
    transition_incident,
)


def test_correlates_same_rig_alerts_and_escalates_severity(tmp_path: Path) -> None:
    db = tmp_path / "m3.db"
    alerts = [
        {
            "id": 1,
            "rig_id": "RIG-001",
            "observed_at": "2026-01-05T10:00:00Z",
            "metric": "temperature_c",
            "detector": "rolling_robust_z",
            "severity": "warning",
            "message": "Temperature z-score > 4.5",
        },
        {
            "id": 2,
            "rig_id": "RIG-001",
            "observed_at": "2026-01-05T10:25:00Z",
            "metric": "vibration_mm_s",
            "detector": "isolation_forest",
            "severity": "critical",
            "message": "Joint thermal-vibration anomaly",
        },
        {
            "id": 3,
            "rig_id": "RIG-002",
            "observed_at": "2026-01-05T10:10:00Z",
            "metric": "pressure_bar",
            "detector": "operating_limit",
            "severity": "warning",
            "message": "Pressure exceeded limit",
        },
    ]

    result = correlate_alerts_into_incidents(db, alerts=alerts, window_minutes=60)
    assert result["incidents_created"] == 2
    assert result["alerts_linked"] == 3

    incidents = list_incidents(db)
    assert len(incidents) == 2

    rig1 = next(i for i in incidents if i["rig_id"] == "RIG-001")
    assert rig1["alert_count"] == 2
    assert rig1["severity"] == "critical"
    assert rig1["detectors"] == ["isolation_forest", "rolling_robust_z"]
    assert rig1["metrics"] == ["temperature_c", "vibration_mm_s"]

    repeat = correlate_alerts_into_incidents(db, alerts=alerts, window_minutes=60)
    assert repeat["incidents_created"] == 0
    assert repeat["alerts_linked"] == 0
    assert repeat["duplicates_skipped"] == 3


def test_incident_lifecycle_transitions_and_notes(tmp_path: Path) -> None:
    db = tmp_path / "m3_lifecycle.db"
    correlate_alerts_into_incidents(
        db,
        alerts=[
            {
                "id": 10,
                "rig_id": "RIG-003",
                "observed_at": "2026-01-06T08:00:00Z",
                "metric": "flow_m3_h",
                "detector": "operating_limit",
                "severity": "critical",
            }
        ],
    )
    incident_id = list_incidents(db)[0]["incident_id"]

    ack = transition_incident(
        db, incident_id, "acknowledged", actor="jnbalaji", note="Triaging pump flow drop"
    )
    assert ack["status"] == "acknowledged"
    assert ack["acknowledged_by"] == "jnbalaji"
    assert ack["acknowledged_at"] is not None

    inv = transition_incident(db, incident_id, "investigating", actor="jnbalaji")
    assert inv["status"] == "investigating"

    with_note = add_incident_note(
        db, incident_id, "Checked suction strainer differential pressure.", author="jnbalaji"
    )
    assert len(with_note["notes"]) == 4

    res = transition_incident(
        db, incident_id, "resolved", actor="jnbalaji", note="Cleared blockage; flow normal"
    )
    assert res["status"] == "resolved"
    assert res["resolved_by"] == "jnbalaji"

    with pytest.raises(ValueError):
        transition_incident(db, incident_id, "acknowledged", actor="jnbalaji")

    summary = fetch_incident_summary(db)
    assert summary["total_incidents"] == 1
    assert summary["by_status"]["resolved"] == 1
    assert summary["critical_active"] == 0

    detail = get_incident_detail(db, incident_id)
    assert detail is not None
    assert len(detail["alerts"]) == 1


def test_incident_api_and_cli_endpoints(tmp_path: Path) -> None:
    db = tmp_path / "m3_api_cli.db"
    correlate_alerts_into_incidents(
        db,
        alerts=[
            {
                "id": 21,
                "rig_id": "RIG-004",
                "observed_at": "2026-01-07T12:00:00Z",
                "metric": "pressure_bar",
                "detector": "operating_limit",
                "severity": "critical",
                "message": "High discharge pressure",
            }
        ],
    )

    client = TestClient(create_app(db))
    listing = client.get("/api/v1/incidents")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    incident_id = items[0]["incident_id"]

    detail_resp = client.get(f"/api/v1/incidents/{incident_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["rig_id"] == "RIG-004"

    trans_resp = client.post(
        f"/api/v1/incidents/{incident_id}/transition",
        json={"status": "acknowledged", "actor": "jnbalaji", "note": "Investigating via API"},
    )
    assert trans_resp.status_code == 200
    assert trans_resp.json()["status"] == "acknowledged"

    note_resp = client.post(
        f"/api/v1/incidents/{incident_id}/notes",
        json={"note": "Relief valve nominal", "author": "jnbalaji"},
    )
    assert note_resp.status_code == 200
    assert len(note_resp.json()["notes"]) == 3

    assert cli_main(["incidents", "summary", "--db", str(db)]) == 0
    assert (
        cli_main(
            [
                "incidents",
                "transition",
                incident_id,
                "--status",
                "resolved",
                "--actor",
                "jnbalaji",
                "--db",
                str(db),
            ]
        )
        == 0
    )

    sum_resp = client.get("/api/v1/incidents/summary")
    assert sum_resp.status_code == 200
    assert sum_resp.json()["by_status"]["resolved"] == 1
    
def test_correlate_reads_from_pipeline_sqlite_tables(tmp_path: Path) -> None:
    db = tmp_path / "pipeline_m3.db"
    out_dir = tmp_path / "gen"
    assert cli_main(["generate", "--out", str(out_dir), "--rigs", "2", "--hours", "72", "--seed", "7"]) == 0
    assert cli_main(["ingest", str(out_dir / "telemetry.csv"), "--db", str(db)]) == 0
    assert cli_main(["detect", "--db", str(db)]) == 0
    assert cli_main(["incidents", "correlate", "--db", str(db), "--window-minutes", "60"]) == 0

    summary = fetch_incident_summary(db)
    assert summary["total_incidents"] >= 1