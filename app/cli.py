from __future__ import annotations

import argparse
from datetime import datetime

from app.db import SessionLocal, init_db
from app.models import Checkpoint
from app.services.ingest import ingest_activity
from app.services.reports import export_report


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(prog="gitswarm")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--org")
    ingest.add_argument("--since", required=True, type=parse_dt)
    ingest.add_argument("--until", required=True, type=parse_dt)

    report = sub.add_parser("report")
    report.add_argument("--checkpoint-id", required=True, type=int)
    report.add_argument("--format", choices=["markdown", "pdf"], default="markdown")

    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
        print("Database initialized")
        return

    db = SessionLocal()
    try:
        if args.command == "ingest":
            checkpoint = ingest_activity(db, args.org, args.since, args.until)
            print(f"Checkpoint {checkpoint.id} ingested for {checkpoint.org_name}")
        elif args.command == "report":
            checkpoint = db.get(Checkpoint, args.checkpoint_id)
            if not checkpoint:
                raise SystemExit(f"Checkpoint {args.checkpoint_id} not found")
            path = export_report(db, checkpoint, args.format)
            print(path)
    finally:
        db.close()


if __name__ == "__main__":
    main()
