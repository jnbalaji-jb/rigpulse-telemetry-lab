from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATES = ("open", "acknowledged", "investigating", "resolved")

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("acknowledged", "investigating", "resolved"),
    "acknowledged": ("investigating", "resolved", "open"),
    "investigating": ("resolved", "acknowledged", "open"),
    "resolved": ("open",),
}

SEVERITY_RANK: dict[str, int] = {
    "info": 1,
    "warning": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    rig_id: str
    status: str
    severity: str
    title: str
    summary: str
    alert_count: int
    detectors: list[str]
    metrics: list[str]
    opened_at: str
    last_seen_at: str
    updated_at: str
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(raw: str) -> datetime:
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_incident_schema(db: str | Path) -> None:
    """Create M3 incident tracking, alert linkage, and analyst note tables if missing."""
    with _connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                rig_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL DEFAULT 'warning',
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                alert_count INTEGER NOT NULL DEFAULT 0,
                detectors_json TEXT NOT NULL DEFAULT '[]',
                metrics_json TEXT NOT NULL DEFAULT '[]',
                opened_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                resolved_by TEXT,
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_incidents_rig_status
                ON incidents (rig_id, status, last_seen_at DESC);

            CREATE TABLE IF NOT EXISTS incident_alerts (
                alert_key TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                rig_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                metric TEXT NOT NULL,
                detector TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                linked_at TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_incident_alerts_incident
                ON incident_alerts (incident_id, observed_at ASC);

            CREATE TABLE IF NOT EXISTS incident_notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                author TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_incident_notes_incident
                ON incident_notes (incident_id, created_at ASC);
            """
        )


def _row_to_incident(row: sqlite3.Row) -> IncidentRecord:
    return IncidentRecord(
        incident_id=row["incident_id"],
        rig_id=row["rig_id"],
        status=row["status"],
        severity=row["severity"],
        title=row["title"],
        summary=row["summary"],
        alert_count=int(row["alert_count"]),
        detectors=json.loads(row["detectors_json"]),
        metrics=json.loads(row["metrics_json"]),
        opened_at=row["opened_at"],
        last_seen_at=row["last_seen_at"],
        updated_at=row["updated_at"],
        acknowledged_by=row["acknowledged_by"],
        acknowledged_at=row["acknowledged_at"],
        resolved_by=row["resolved_by"],
        resolved_at=row["resolved_at"],
    )


def _max_severity(current: str, candidate: str) -> str:
    cur_norm = current.lower() if current else "warning"
    cand_norm = candidate.lower() if candidate else "warning"
    if SEVERITY_RANK.get(cand_norm, 0) > SEVERITY_RANK.get(cur_norm, 0):
        return cand_norm
    return cur_norm


def _build_alert_key(alert: dict[str, Any]) -> str:
    if alert.get("alert_key"):
        return str(alert["alert_key"])
    if alert.get("id") is not None:
        return f"alert-id-{alert['id']}"
    raw = "|".join(
        [
            str(alert.get("rig_id", "")),
            str(alert.get("observed_at", "")),
            str(alert.get("metric", "")),
            str(alert.get("detector", "")),
            str(alert.get("severity", "")),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"alert-{digest}"


def _build_incident_id(rig_id: str, opened_at: str, first_alert_key: str) -> str:
    seed = f"{rig_id}|{opened_at}|{first_alert_key}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"INC-{rig_id}-{digest}"


def _load_db_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    alerts_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alerts'"
    ).fetchone()
    if not alerts_exists:
        return []

    alert_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(alerts)").fetchall()
    }
    id_col = "id" if "id" in alert_cols else ("alert_id" if "alert_id" in alert_cols else "rowid")
    detector_col = "a.detector" if "detector" in alert_cols else "'unknown'"
    severity_col = "a.severity" if "severity" in alert_cols else "'warning'"
    msg_name = next(
        (c for c in ("message", "explanation", "reason") if c in alert_cols),
        None,
    )
    msg_col = f"a.{msg_name}" if msg_name else "''"

    if "rig_id" in alert_cols:
        ts_name = next(
            (c for c in ("observed_at", "timestamp", "created_at") if c in alert_cols),
            None,
        )
        if not ts_name:
            return []
        metric_col = "a.metric" if "metric" in alert_cols else "'multi_sensor'"
        query = f"""
            SELECT
                a.{id_col} AS id,
                a.rig_id AS rig_id,
                a.{ts_name} AS observed_at,
                {metric_col} AS metric,
                {detector_col} AS detector,
                {severity_col} AS severity,
                {msg_col} AS message
            FROM alerts a
            ORDER BY a.rig_id ASC, a.{ts_name} ASC, a.{id_col} ASC
        """
        return [dict(r) for r in conn.execute(query).fetchall()]

    readings_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'readings'"
    ).fetchone()
    fk_name = next(
        (c for c in ("reading_id", "measurement_id") if c in alert_cols),
        None,
    )
    if not readings_exists or not fk_name:
        return []

    reading_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(readings)").fetchall()
    }
    ts_col = next(
        (f"a.{c}" for c in ("observed_at", "timestamp") if c in alert_cols),
        None,
    ) or next(
        (f"r.{c}" for c in ("observed_at", "timestamp", "created_at") if c in reading_cols),
        None,
    )
    if not ts_col or "rig_id" not in reading_cols:
        return []

    metric_col = (
        "a.metric"
        if "metric" in alert_cols
        else ("r.metric" if "metric" in reading_cols else "'multi_sensor'")
    )
    query = f"""
        SELECT
            a.{id_col} AS id,
            r.rig_id AS rig_id,
            {ts_col} AS observed_at,
            {metric_col} AS metric,
            {detector_col} AS detector,
            {severity_col} AS severity,
            {msg_col} AS message
        FROM alerts a
        JOIN readings r ON a.{fk_name} = r.id
        ORDER BY r.rig_id ASC, {ts_col} ASC, a.{id_col} ASC
    """
    return [dict(r) for r in conn.execute(query).fetchall()]


def correlate_alerts_into_incidents(
    db: str | Path,
    alerts: list[dict[str, Any]] | None = None,
    window_minutes: int = 60,
) -> dict[str, Any]:
    """Group correlated per-rig alerts within `window_minutes` into tracked incidents."""
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")

    ensure_incident_schema(db)
    window_seconds = window_minutes * 60
    now_iso = _utc_now_iso()

    with _connect(db) as conn:
        source_alerts = alerts if alerts is not None else _load_db_alerts(conn)
        normalized: list[dict[str, Any]] = []
        for item in source_alerts:
            rig_id = str(item["rig_id"]).strip()
            obs_dt = _parse_utc(str(item["observed_at"]))
            normalized.append(
                {
                    "alert_key": _build_alert_key(item),
                    "rig_id": rig_id,
                    "observed_at": _format_utc(obs_dt),
                    "observed_dt": obs_dt,
                    "metric": str(item.get("metric") or "multi_sensor"),
                    "detector": str(item.get("detector") or "unknown"),
                    "severity": str(item.get("severity") or "warning").lower(),
                    "message": str(item.get("message") or ""),
                }
            )

        normalized.sort(key=lambda a: (a["rig_id"], a["observed_dt"], a["alert_key"]))

        incidents_created = 0
        alerts_linked = 0
        duplicates_skipped = 0

        for alert in normalized:
            already = conn.execute(
                "SELECT 1 FROM incident_alerts WHERE alert_key = ?",
                (alert["alert_key"],),
            ).fetchone()
            if already:
                duplicates_skipped += 1
                continue

            open_rows = conn.execute(
                """
                SELECT * FROM incidents
                WHERE rig_id = ? AND status != 'resolved'
                ORDER BY last_seen_at DESC
                """,
                (alert["rig_id"],),
            ).fetchall()

            matched_row: sqlite3.Row | None = None
            for candidate in open_rows:
                opened_dt = _parse_utc(candidate["opened_at"])
                last_dt = _parse_utc(candidate["last_seen_at"])
                if (
                    abs((alert["observed_dt"] - last_dt).total_seconds()) <= window_seconds
                    or abs((alert["observed_dt"] - opened_dt).total_seconds()) <= window_seconds
                ):
                    matched_row = candidate
                    break

            if matched_row is None:
                incident_id = _build_incident_id(
                    alert["rig_id"], alert["observed_at"], alert["alert_key"]
                )
                detectors = [alert["detector"]]
                metrics = [alert["metric"]]
                title = f"{alert['rig_id']}: {alert['metric']} anomaly ({alert['detector']})"
                summary = (
                    f"Correlated 1 alert on {alert['rig_id']} across {alert['metric']} "
                    f"detected by {alert['detector']}."
                )
                conn.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, rig_id, status, severity, title, summary,
                        alert_count, detectors_json, metrics_json,
                        opened_at, last_seen_at, updated_at
                    ) VALUES (?, ?, 'open', ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id,
                        alert["rig_id"],
                        alert["severity"],
                        title,
                        summary,
                        json.dumps(detectors),
                        json.dumps(metrics),
                        alert["observed_at"],
                        alert["observed_at"],
                        now_iso,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO incident_notes (incident_id, author, action, note, created_at)
                    VALUES (?, 'system', 'opened', ?, ?)
                    """,
                    (incident_id, f"Incident opened from alert {alert['alert_key']}.", now_iso),
                )
                incidents_created += 1
            else:
                incident_id = matched_row["incident_id"]
                detectors = sorted(
                    set(json.loads(matched_row["detectors_json"])) | {alert["detector"]}
                )
                metrics = sorted(
                    set(json.loads(matched_row["metrics_json"])) | {alert["metric"]}
                )
                new_count = int(matched_row["alert_count"]) + 1
                new_sev = _max_severity(matched_row["severity"], alert["severity"])
                opened_dt = min(_parse_utc(matched_row["opened_at"]), alert["observed_dt"])
                last_dt = max(_parse_utc(matched_row["last_seen_at"]), alert["observed_dt"])
                title = (
                    f"{alert['rig_id']}: correlated anomaly across "
                    f"{', '.join(metrics)} ({new_count} alerts)"
                )
                summary = (
                    f"Grouped {new_count} correlated alerts on {alert['rig_id']} across "
                    f"{', '.join(metrics)} via {', '.join(detectors)}."
                )
                conn.execute(
                    """
                    UPDATE incidents
                    SET severity = ?, title = ?, summary = ?, alert_count = ?,
                        detectors_json = ?, metrics_json = ?,
                        opened_at = ?, last_seen_at = ?, updated_at = ?
                    WHERE incident_id = ?
                    """,
                    (
                        new_sev,
                        title,
                        summary,
                        new_count,
                        json.dumps(detectors),
                        json.dumps(metrics),
                        _format_utc(opened_dt),
                        _format_utc(last_dt),
                        now_iso,
                        incident_id,
                    ),
                )

            conn.execute(
                """
                INSERT INTO incident_alerts (
                    alert_key, incident_id, rig_id, observed_at,
                    metric, detector, severity, message, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert["alert_key"],
                    incident_id,
                    alert["rig_id"],
                    alert["observed_at"],
                    alert["metric"],
                    alert["detector"],
                    alert["severity"],
                    alert["message"],
                    now_iso,
                ),
            )
            alerts_linked += 1

    return {
        "alerts_evaluated": len(normalized),
        "alerts_linked": alerts_linked,
        "duplicates_skipped": duplicates_skipped,
        "incidents_created": incidents_created,
        "window_minutes": window_minutes,
    }


def list_incidents(
    db: str | Path,
    status: str | None = None,
    rig_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_incident_schema(db)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status.lower())
    if rig_id:
        clauses.append("rig_id = ?")
        params.append(rig_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, int(limit)))

    with _connect(db) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM incidents
            {where_sql}
            ORDER BY last_seen_at DESC, incident_id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_incident(r).to_dict() for r in rows]


def get_incident_detail(db: str | Path, incident_id: str) -> dict[str, Any] | None:
    ensure_incident_schema(db)
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            return None

        alerts = [
            dict(r)
            for r in conn.execute(
                """
                SELECT alert_key, rig_id, observed_at, metric, detector, severity, message, linked_at
                FROM incident_alerts
                WHERE incident_id = ?
                ORDER BY observed_at ASC, alert_key ASC
                """,
                (incident_id,),
            ).fetchall()
        ]
        notes = [
            dict(r)
            for r in conn.execute(
                """
                SELECT note_id, author, action, note, created_at
                FROM incident_notes
                WHERE incident_id = ?
                ORDER BY note_id ASC
                """,
                (incident_id,),
            ).fetchall()
        ]

    payload = _row_to_incident(row).to_dict()
    payload["alerts"] = alerts
    payload["notes"] = notes
    return payload


def add_incident_note(
    db: str | Path,
    incident_id: str,
    note: str,
    author: str = "operator",
) -> dict[str, Any]:
    cleaned_note = note.strip()
    if not cleaned_note:
        raise ValueError("note cannot be empty")

    ensure_incident_schema(db)
    now_iso = _utc_now_iso()
    with _connect(db) as conn:
        exists = conn.execute(
            "SELECT 1 FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if exists is None:
            raise KeyError(f"Unknown incident_id: {incident_id}")

        conn.execute(
            """
            INSERT INTO incident_notes (incident_id, author, action, note, created_at)
            VALUES (?, ?, 'comment', ?, ?)
            """,
            (incident_id, author.strip() or "operator", cleaned_note, now_iso),
        )
        conn.execute(
            "UPDATE incidents SET updated_at = ? WHERE incident_id = ?",
            (now_iso, incident_id),
        )

    detail = get_incident_detail(db, incident_id)
    assert detail is not None
    return detail


def transition_incident(
    db: str | Path,
    incident_id: str,
    target_status: str,
    actor: str = "operator",
    note: str | None = None,
) -> dict[str, Any]:
    next_state = target_status.strip().lower()
    if next_state not in VALID_STATES:
        raise ValueError(f"Invalid incident status '{target_status}'. Expected one of {VALID_STATES}.")

    ensure_incident_schema(db)
    now_iso = _utc_now_iso()
    actor_name = actor.strip() or "operator"

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown incident_id: {incident_id}")

        current_state = row["status"]
        if next_state == current_state:
            raise ValueError(f"Incident {incident_id} is already in state '{current_state}'.")

        allowed = ALLOWED_TRANSITIONS.get(current_state, ())
        if next_state not in allowed:
            raise ValueError(
                f"Cannot transition incident from '{current_state}' to '{next_state}'. "
                f"Allowed transitions: {allowed}."
            )

        ack_by = row["acknowledged_by"]
        ack_at = row["acknowledged_at"]
        res_by = row["resolved_by"]
        res_at = row["resolved_at"]

        if next_state == "acknowledged":
            ack_by = actor_name
            ack_at = now_iso
        elif next_state == "resolved":
            res_by = actor_name
            res_at = now_iso
        elif next_state == "open":
            res_by = None
            res_at = None

        conn.execute(
            """
            UPDATE incidents
            SET status = ?, updated_at = ?,
                acknowledged_by = ?, acknowledged_at = ?,
                resolved_by = ?, resolved_at = ?
            WHERE incident_id = ?
            """,
            (next_state, now_iso, ack_by, ack_at, res_by, res_at, incident_id),
        )
        audit_text = (
            note.strip()
            if note and note.strip()
            else f"Status transitioned from {current_state} to {next_state} by {actor_name}."
        )
        conn.execute(
            """
            INSERT INTO incident_notes (incident_id, author, action, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (incident_id, actor_name, f"transition:{next_state}", audit_text, now_iso),
        )

    detail = get_incident_detail(db, incident_id)
    assert detail is not None
    return detail


def fetch_incident_summary(db: str | Path) -> dict[str, Any]:
    ensure_incident_schema(db)
    with _connect(db) as conn:
        counts = {state: 0 for state in VALID_STATES}
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM incidents GROUP BY status"
        ).fetchall():
            counts[row["status"]] = int(row["n"])

        critical_active = conn.execute(
            """
            SELECT COUNT(*) AS n FROM incidents
            WHERE status != 'resolved' AND severity = 'critical'
            """
        ).fetchone()["n"]

    return {
        "total_incidents": sum(counts.values()),
        "by_status": counts,
        "critical_active": int(critical_active),
    }