"""Development-only API; authentication, RBAC, and rate limits are on the roadmap."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rigpulse.incidents import (
    add_incident_note,
    correlate_alerts_into_incidents,
    ensure_incident_schema,
    fetch_incident_summary,
    get_incident_detail,
    list_incidents,
    transition_incident,
)
from rigpulse.models import METRICS
from rigpulse.storage import fetch_alerts, fetch_series, initialize, rig_names, summary


class CorrelateRequest(BaseModel):
    window_minutes: int = Field(60, ge=1, le=10080)


class IncidentTransitionRequest(BaseModel):
    status: str
    actor: str = "operator"
    note: str | None = None


class IncidentNoteRequest(BaseModel):
    note: str
    author: str = "operator"


def create_app(db: str | Path = "rigpulse.db") -> FastAPI:
    db = str(db)
    initialize(db)
    ensure_incident_schema(db)
    app = FastAPI(
        title="RigPulse Telemetry Lab",
        version="0.2.0",
        description="Development-only telemetry monitoring and anomaly analysis API",
    )
    web_dir = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(web_dir / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/v1/rigs")
    def rigs():
        return {"rigs": rig_names(db), "metrics": list(METRICS)}

    @app.get("/api/v1/summary")
    def get_summary():
        return summary(db)

    @app.get("/api/v1/alerts")
    def get_alerts(limit: int = Query(50, ge=1, le=500), rig_id: str | None = None):
        return {"items": fetch_alerts(db, limit=limit, rig_id=rig_id)}

    @app.get("/api/v1/series")
    def series(rig_id: str, metric: str, limit: int = Query(240, ge=1, le=5000)):
        if metric not in METRICS:
            raise HTTPException(status_code=422, detail=f"metric must be one of {METRICS}")
        return {
            "rig_id": rig_id,
            "metric": metric,
            "points": fetch_series(db, rig_id, metric, limit),
        }

    @app.get("/api/v1/incidents/summary")
    def incidents_summary():
        return fetch_incident_summary(db)

    @app.get("/api/v1/incidents")
    def get_incidents(
        status: str | None = None,
        rig_id: str | None = None,
        limit: int = Query(50, ge=1, le=500),
    ):
        return {"items": list_incidents(db, status=status, rig_id=rig_id, limit=limit)}

    @app.post("/api/v1/incidents/correlate")
    def correlate_incidents(payload: CorrelateRequest | None = None):
        req = payload or CorrelateRequest()
        return correlate_alerts_into_incidents(db, window_minutes=req.window_minutes)

    @app.get("/api/v1/incidents/{incident_id}")
    def incident_detail(incident_id: str):
        detail = get_incident_detail(db, incident_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
        return detail

    @app.post("/api/v1/incidents/{incident_id}/transition")
    def post_incident_transition(incident_id: str, payload: IncidentTransitionRequest):
        try:
            return transition_incident(
                db,
                incident_id,
                payload.status,
                actor=payload.actor,
                note=payload.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/incidents/{incident_id}/notes")
    def post_incident_note(incident_id: str, payload: IncidentNoteRequest):
        try:
            return add_incident_note(db, incident_id, payload.note, author=payload.author)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app