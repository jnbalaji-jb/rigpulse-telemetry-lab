"""CLI commands are deterministic and operate on user-controlled local paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rigpulse.detection import detect
from rigpulse.drift import drift_report
from rigpulse.evaluation import evaluate
from rigpulse.features import export_features
from rigpulse.ingest import ingest_csv
from rigpulse.simulate import generate
from rigpulse.storage import summary


def emit(data: dict, output: str | None = None) -> None:
    result = json.dumps(data, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result + "\n", encoding="utf-8")
    print(result)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rigpulse", description="Industrial telemetry reliability lab")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("generate", help="create reproducible synthetic readings and ground truth")
    a.add_argument("--out", default="demo_data")
    a.add_argument("--rigs", type=int, default=4)
    a.add_argument("--hours", type=int, default=240)
    a.add_argument("--seed", type=int, default=42)
    a = sub.add_parser("ingest", help="validate CSV and idempotently load it into SQLite")
    a.add_argument("path")
    a.add_argument("--db", default="rigpulse.db")
    a = sub.add_parser("detect", help="compute explainable rules + past-only robust z scores")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--window", type=int, default=48)
    a.add_argument("--min-history", type=int, default=16)
    a.add_argument("--z-threshold", type=float, default=4.5)
    a = sub.add_parser("summary", help="read API-style dashboard summary")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--out", default=None)
    a = sub.add_parser("drift", help="compare early/late windows using PSI")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--out", default=None)
    a = sub.add_parser("evaluate", help="evaluate alert accuracy against synthetic labels")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--truth", required=True)
    a.add_argument("--out", default=None)
    a = sub.add_parser("features", help="export causal, multivariate sensor features to CSV")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--out", default="reports/features.csv")
    a.add_argument("--bucket-minutes", type=int, default=60)
    a.add_argument("--window", type=int, default=12)
    a = sub.add_parser("demo", help="generate, ingest, detect, evaluate in a single reproducible run")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--out", default="demo_data")
    a.add_argument("--rigs", type=int, default=4)
    a.add_argument("--hours", type=int, default=240)
    a.add_argument("--seed", type=int, default=42)
    a = sub.add_parser("serve", help="start local development dashboard + read API")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = args.command
    if command == "generate":
        emit(generate(args.out, args.rigs, args.hours, args.seed))
    elif command == "ingest":
        emit(ingest_csv(args.path, args.db))
    elif command == "detect":
        emit(detect(args.db, args.window, args.min_history, args.z_threshold))
    elif command == "summary":
        emit(summary(args.db), args.out)
    elif command == "drift":
        emit(drift_report(args.db), args.out)
    elif command == "evaluate":
        emit(evaluate(args.db, args.truth), args.out)
    elif command == "features":
        emit(export_features(args.db, args.out, args.bucket_minutes, args.window))
    elif command == "demo":
        generated = generate(args.out, args.rigs, args.hours, args.seed)
        loaded = ingest_csv(generated["telemetry_path"], args.db)
        scored = detect(args.db)
        metrics = evaluate(args.db, generated["truth_path"])
        emit({"generation": generated, "ingestion": loaded, "detection": scored,
              "evaluation": metrics, "summary": summary(args.db), "drift": drift_report(args.db)})
    elif command == "serve":
        import uvicorn
        from rigpulse.api import create_app
        print(f"Serving local dashboard at http://{args.host}:{args.port}")
        uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
