"""CLI commands are deterministic and operate on user-controlled local paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rigpulse.backtest import run_experiment_comparison
from rigpulse.detection import detect
from rigpulse.drift import drift_report
from rigpulse.evaluation import evaluate
from rigpulse.features import export_features
from rigpulse.ingest import ingest_csv
from rigpulse.ml import ModelConfig, train_model
from rigpulse.simulate import generate
from rigpulse.storage import summary
from rigpulse.streaming import run_stream_benchmark


def emit(data: dict, output: str | None = None) -> None:
    result = json.dumps(data, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result + "
", encoding="utf-8")
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
    a = sub.add_parser("detect", help="compute explainable rules + past-only robust z + optional ML")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--window", type=int, default=48)
    a.add_argument("--min-history", type=int, default=16)
    a.add_argument("--z-threshold", type=float, default=4.5)
    a.add_argument("--model", default=None, help="optional path to trained Isolation Forest artifact")
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
    a = sub.add_parser("train", help="fit multivariate Isolation Forest on causal training history")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--out", default="models/isolation_forest.json")
    a.add_argument("--train-fraction", type=float, default=0.6)
    a.add_argument("--bucket-minutes", type=int, default=60)
    a.add_argument("--window", type=int, default=12)
    a.add_argument("--contamination", type=float, default=0.04)
    a.add_argument("--seed", type=int, default=42)
    a = sub.add_parser("compare", help="run temporal and leave-one-rig-out baseline vs ML experiment")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--truth", required=True)
    a.add_argument("--out", default="reports/m1_comparison.json")
    a.add_argument("--bucket-minutes", type=int, default=60)
    a.add_argument("--window", type=int, default=12)
    a.add_argument("--contamination", type=float, default=0.04)
    a.add_argument("--seed", type=int, default=42)
    a = sub.add_parser("stream-demo", help="run MQTT stream simulation with duplicates, late arrivals, and checkpoint recovery")
    a.add_argument("--db", default="rigpulse.db")
    a.add_argument("--rigs", type=int, default=4)
    a.add_argument("--events", type=int, default=240)
    a.add_argument("--seed", type=int, default=42)
    a.add_argument("--duplicate-rate", type=float, default=0.1)
    a.add_argument("--late-rate", type=float, default=0.1)
    a.add_argument("--checkpoint-every", type=int, default=50)
    a.add_argument("--out", default=None)
    a = sub.add_parser("demo", help="generate, ingest, train, detect, evaluate in a single run")
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
        emit(detect(args.db, args.window, args.min_history, args.z_threshold, args.model))
    elif command == "summary":
        emit(summary(args.db), args.out)
    elif command == "drift":
        emit(drift_report(args.db), args.out)
    elif command == "evaluate":
        emit(evaluate(args.db, args.truth), args.out)
    elif command == "features":
        emit(export_features(args.db, args.out, args.bucket_minutes, args.window))
    elif command == "train":
        cfg = ModelConfig(
            bucket_minutes=args.bucket_minutes,
            window=args.window,
            contamination=args.contamination,
            seed=args.seed,
        )
        emit(train_model(args.db, args.out, args.train_fraction, cfg))
    elif command == "compare":
        cfg = ModelConfig(
            bucket_minutes=args.bucket_minutes,
            window=args.window,
            contamination=args.contamination,
            seed=args.seed,
        )
        emit(run_experiment_comparison(args.db, args.truth, args.out, cfg))
    elif command == "stream-demo":
        emit(
            run_stream_benchmark(
                db=args.db,
                rigs=args.rigs,
                events=args.events,
                seed=args.seed,
                duplicate_rate=args.duplicate_rate,
                late_rate=args.late_rate,
                checkpoint_every=args.checkpoint_every,
                out_path=args.out,
            )
        )
    elif command == "demo":
        generated = generate(args.out, args.rigs, args.hours, args.seed)
        loaded = ingest_csv(generated["telemetry_path"], args.db)
        model_path = Path(args.out) / "isolation_forest.json"
        trained = train_model(args.db, model_path, train_fraction=0.6)
        scored = detect(args.db, model_path=model_path)
        metrics = evaluate(args.db, generated["truth_path"])
        comparison = run_experiment_comparison(args.db, generated["truth_path"])
        emit(
            {
                "generation": generated,
                "ingestion": loaded,
                "training": trained,
                "detection": scored,
                "evaluation": metrics,
                "comparison": comparison,
                "summary": summary(args.db),
                "drift": drift_report(args.db),
            }
        )
    elif command == "serve":
        import uvicorn
        from rigpulse.api import create_app

        print(f"Serving local dashboard at http://{args.host}:{args.port}")
        uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
