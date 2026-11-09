"""Development-only read API; authentication, RBAC, and rate limits are on the roadmap."""
from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rigpulse.models import METRICS
from rigpulse.storage import fetch_alerts, fetch_series, initialize, rig_names, summary


def create_app(db: str | Path = "rigpulse.db") -> FastAPI:
    db = str(db)
    initialize(db)
    app = FastAPI(title="RigPulse Telemetry Lab", version="0.1.0",
                  description="Development-only telemetry monitoring and anomaly analysis API")
    web_dir = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(web_dir / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

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
        return {"rig_id": rig_id, "metric": metric,
                "points": fetch_series(db, rig_id, metric, limit)}

    return app
