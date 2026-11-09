"""CSV parsing is deliberately strict: one invalid row aborts before database writes."""
from __future__ import annotations

import csv
from pathlib import Path

from rigpulse.models import Reading
from rigpulse.storage import insert_readings

REQUIRED = {"observed_at", "rig_id", "metric", "value"}


def load_csv(path: str | Path) -> list[Reading]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError(f"CSV must have columns: {', '.join(sorted(REQUIRED))}")
        rows = []
        for line, item in enumerate(reader, start=2):
            try:
                rows.append(Reading.parse(item))
            except ValueError as exc:
                raise ValueError(f"CSV line {line}: {exc}") from exc
    if not rows:
        raise ValueError("CSV is empty")
    return rows


def ingest_csv(path: str | Path, db: str | Path) -> dict:
    rows = load_csv(path)
    inserted, duplicates = insert_readings(db, rows)
    return {"rows_read": len(rows), "inserted": inserted, "duplicates_skipped": duplicates}
