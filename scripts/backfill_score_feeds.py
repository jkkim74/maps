#!/usr/bin/env python3
"""Backfill measured investor-flow inputs used by candidate score readiness."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps.common.db import SessionLocal
from maps.data.collector import DataCollector
from maps.data.krx_adapter import KRXAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--calendar-days", type=int, default=10)
    args = parser.parse_args()
    start = args.end - dt.timedelta(days=max(args.calendar_days, 1))
    db = SessionLocal()
    try:
        result = DataCollector(KRXAdapter(), db).collect_investor_flow_history(start, args.end)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success_days"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
