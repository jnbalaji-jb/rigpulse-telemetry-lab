"""SQLite persistence; no global connection and no silent data replacement."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rigpulse.models import Alert, Reading

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    rig_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(observed_at, rig_id, metric)
);
CREATE INDEX IF NOT EXISTS idx_readings_series ON readings(rig_id, metric, observed_at);
CREATE TABLE IF NOT EXISTS alerts (
    reading_id INTEGER NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
    detector TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('warning','critical')),
    score REAL NOT NULL,
    explanation TEXT NOT NULL,
    PRIMARY KEY(reading_id, detector)
);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE TABLE IF NOT EXISTS stream_events (
    event_id TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    rig_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    is_late INTEGER NOT NULL DEFAULT 0 CHECK(is_late IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_stream_events_observed ON stream_events(observed_at, rig_id);
CREATE TABLE IF NOT EXISTS detector_checkpoints (
    consumer_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    watermark_utc TEXT,
    processed_events INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL
);
"""


def connect(db: str | Path) -> sqlite3.Connection:
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize(db: str | Path) -> None:
    with connect(db) as conn:
        conn.executescript(SCHEMA)


def insert_readings(db: str | Path, readings: Iterable[Reading]) -> tuple[int, int]:
    initialize(db)
    with connect(db) as conn:
        before = conn.total_changes
        count = 0
        for r in readings:
            count += 1
            conn.execute(
                "INSERT OR IGNORE INTO readings(observed_at,rig_id,metric,value) VALUES (?,?,?,?)",
                (r.observed_at, r.rig_id, r.metric, r.value),
            )
        inserted = conn.total_changes - before
        return inserted, count - inserted


def insert_single_reading(db: str | Path, reading: Reading) -> tuple[int, bool]:
    """Insert a single reading idempotently and return (reading_id, was_inserted)."""
    initialize(db)
    with connect(db) as conn:
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO readings(observed_at,rig_id,metric,value) VALUES (?,?,?,?)",
            (reading.observed_at, reading.rig_id, reading.metric, reading.value),
        )
        inserted = (conn.total_changes - before) > 0
        row = conn.execute(
            "SELECT id FROM readings WHERE observed_at=? AND rig_id=? AND metric=?",
            (reading.observed_at, reading.rig_id, reading.metric),
        ).fetchone()
        return int(row["id"]), inserted


def insert_alerts(db: str | Path, alerts: Iterable[Alert]) -> int:
    """Append alerts idempotently without wiping existing findings."""
    initialize(db)
    with connect(db) as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO alerts(reading_id,detector,severity,score,explanation) "
            "VALUES(?,?,?,?,?)",
            [(a.reading_id, a.detector, a.severity, a.score, a.explanation) for a in alerts],
        )
        return conn.total_changes - before


def ordered_readings(db: str | Path) -> list[sqlite3.Row]:
    initialize(db)
    with connect(db) as conn:
        return conn.execute(
            "SELECT id, observed_at, rig_id, metric, value FROM readings "
            "ORDER BY observed_at, rig_id, metric"
        ).fetchall()


def replace_alerts(db: str | Path, alerts: list[Alert]) -> int:
    initialize(db)
    with connect(db) as conn:
        conn.execute("DELETE FROM alerts")
        conn.executemany(
            "INSERT INTO alerts(reading_id,detector,severity,score,explanation) "
            "VALUES(?,?,?,?,?)",
            [(a.reading_id, a.detector, a.severity, a.score, a.explanation) for a in alerts],
        )
    return len(alerts)


def save_checkpoint(
    db: str | Path,
    consumer_id: str,
    updated_at: str,
    watermark_utc: str | None,
    processed_events: int,
    state: dict[str, Any],
) -> None:
    initialize(db)
    payload = json.dumps(state, sort_keys=True)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO detector_checkpoints(consumer_id, updated_at, watermark_utc, processed_events, state_json) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(consumer_id) DO UPDATE SET "
            "updated_at=excluded.updated_at, "
            "watermark_utc=excluded.watermark_utc, "
            "processed_events=excluded.processed_events, "
            "state_json=excluded.state_json",
            (consumer_id, updated_at, watermark_utc, processed_events, payload),
        )


def load_checkpoint(db: str | Path, consumer_id: str) -> dict[str, Any] | None:
    initialize(db)
    with connect(db) as conn:
        row = conn.execute(
            "SELECT consumer_id, updated_at, watermark_utc, processed_events, state_json "
            "FROM detector_checkpoints WHERE consumer_id=?",
            (consumer_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "consumer_id": str(row["consumer_id"]),
        "updated_at": str(row["updated_at"]),
        "watermark_utc": row["watermark_utc"],
        "processed_events": int(row["processed_events"]),
        "state": json.loads(str(row["state_json"])),
    }


def stream_status(db: str | Path) -> dict[str, Any]:
    initialize(db)
    with connect(db) as conn:
        total_events = conn.execute("SELECT COUNT(*) FROM stream_events").fetchone()[0]
        late_events = conn.execute(
            "SELECT COUNT(*) FROM stream_events WHERE is_late=1"
        ).fetchone()[0]
        checkpoints = [
            dict(r)
            for r in conn.execute(
                "SELECT consumer_id, updated_at, watermark_utc, processed_events "
                "FROM detector_checkpoints ORDER BY updated_at DESC"
            )
        ]
    return {
        "stream_events_total": int(total_events),
        "late_events_total": int(late_events),
        "checkpoints": checkpoints,
    }


def summary(db: str | Path) -> dict:
    initialize(db)
    with connect(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        rigs = conn.execute("SELECT COUNT(DISTINCT rig_id) FROM readings").fetchone()[0]
        flagged = conn.execute("SELECT COUNT(DISTINCT reading_id) FROM alerts").fetchone()[0]
        counts = {
            row["severity"]: row["n"]
            for row in conn.execute(
                "SELECT severity, COUNT(*) AS n FROM alerts GROUP BY severity"
            )
        }
        metrics = [
            dict(r)
            for r in conn.execute(
                "SELECT metric, COUNT(*) AS n FROM readings GROUP BY metric ORDER BY metric"
            )
        ]
        return {
            "readings": total,
            "rigs": rigs,
            "flagged_readings": flagged,
            "alerts": sum(counts.values()),
            "warning_alerts": counts.get("warning", 0),
            "critical_alerts": counts.get("critical", 0),
            "metrics": metrics,
        }


def rig_names(db: str | Path) -> list[str]:
    initialize(db)
    with connect(db) as conn:
        return [
            r[0] for r in conn.execute("SELECT DISTINCT rig_id FROM readings ORDER BY rig_id")
        ]


def fetch_series(db: str | Path, rig_id: str, metric: str, limit: int = 240) -> list[dict]:
    initialize(db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT r.id, r.observed_at, r.value, "
            "EXISTS(SELECT 1 FROM alerts a WHERE a.reading_id=r.id) AS is_alert "
            "FROM readings r WHERE r.rig_id=? AND r.metric=? "
            "ORDER BY r.observed_at DESC LIMIT ?",
            (rig_id, metric, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def fetch_alerts(db: str | Path, limit: int = 100, rig_id: str | None = None) -> list[dict]:
    initialize(db)
    where, params = ("WHERE r.rig_id=?", [rig_id]) if rig_id else ("", [])
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT r.observed_at,r.rig_id,r.metric,r.value,a.detector,a.severity,"
            "a.score,a.explanation FROM alerts a JOIN readings r ON r.id=a.reading_id "
            + where + " ORDER BY r.observed_at DESC, a.score DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]
