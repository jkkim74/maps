"""Offline Fujimoto JSON research runner and explicit account-bound validation storage.

Usage: python scripts/fujimoto_research.py --input observations.json --output report.json
Export: add --export --database sqlite:///research.db --account-key HASH --budget AMOUNT
Persist: add --persist --database URL --account-key HASH (never grants promotion).
"""
from __future__ import annotations

import argparse
import base64
from datetime import date
import json
from pathlib import Path
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps.fujimoto.replay import ReplayInput, input_from_json
from maps.fujimoto.repository import FujimotoRepository, json_data
from maps.fujimoto.validation import run_research, persist_validation


def export_inputs(db, key: str, budget: float) -> dict:
    """Read only recorded snapshots, exact candidate order and actual normalized tape."""
    from maps.common.models import FujimotoEvidence
    from maps.common.settings import get_settings
    settings = get_settings()
    evidence, screening, bars, ranking, tape = {}, {}, {}, {}, []
    candidate_ids, annual_ids, gaps = [], [], []
    rows = db.query(FujimotoEvidence).order_by(FujimotoEvidence.id)
    for row in rows:
        if row.account_key not in (None, key):
            continue
        if row.kind == "candidate":
            day = row.payload["ref_date"]
            raw = json.loads(zlib.decompress(base64.b64decode(row.payload["raw_zlib"])))
            evidence.setdefault(day, {})[row.ticker] = row.payload["rule"]
            screening.setdefault(day, {})[row.ticker] = raw
            candidate_ids.append(row.id)
            for bar in raw["prices"]:
                bars.setdefault(bar["date"], {})[row.ticker] = {k: bar[k] for k in
                    ("open", "high", "low", "close", "volume")}
        elif row.kind == "screen":
            ranking[row.payload["ref_date"]] = row.payload.get("ranked", [])
            if row.payload.get("reason"):
                gaps.append({"id": row.id, "reason": row.payload["reason"]})
        elif row.kind in {"annual_source", "comparability"}:
            annual_ids.append(row.id)
        elif row.kind == "quote":
            tape.append(row.payload)
        elif row.kind == "feed_quality" and row.payload.get("reason") == "tape_capacity_exhausted":
            gaps.append({"id": row.id, "reason": row.payload["reason"]})
    return {"budget": budget, "evidence": evidence, "screening": screening,
        "fee_rate": .00015, "tax_rate": .002, "slippage": .001,
        "account_ticker_limit": settings.max_single_exposure,
        "minimum_cash_fraction": max(.325, settings.maps_min_cash_ratio_weak,
            settings.maps_min_cash_ratio_mixed, settings.maps_min_cash_ratio_strong),
        "bars": {d: v for d, v in bars.items() if d in evidence}, "candidate_order": ranking,
        "tape": tape, "provenance": {"candidate_evidence": candidate_ids,
            "annual_evidence": annual_ids, "coverage_gaps": gaps}}


def main() -> None:
    """No broker, promotion or activation path exists in this command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database")
    parser.add_argument("--account-key")
    parser.add_argument("--budget", type=float)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--without-orderbook", action="store_true")
    parser.add_argument("--account-mdd-limit", type=float, default=.15)
    args = parser.parse_args()
    if args.export or args.persist:
        if not args.database or not args.account_key:
            parser.error("explicit --database and --account-key required")
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        import maps.common.models
        engine = create_engine(args.database)
    if args.export:
        if not args.budget or args.budget <= 0:
            parser.error("explicit --budget required")
        with Session(engine) as db:
            payload = export_inputs(db, args.account_key, args.budget)
        args.output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return
    if args.demo:
        data = ReplayInput(1000000, {}, {})
    elif args.input:
        data = input_from_json(json.loads(args.input.read_text(encoding="utf-8")))
    else:
        parser.error("--input or --demo required")
    report = run_research(data)
    output = {"report": report, "execution_permission": False}
    if args.persist:
        with Session(engine) as db:
            repo = FujimotoRepository(db)
            record = repo.store_replay(data, report, account_key=args.account_key)
            result, runs = persist_validation(repo, record.id, account_mdd_limit=args.account_mdd_limit,
                                              with_orderbook=not args.without_orderbook)
            db.commit()
            output.update(validation=json_data(result), replay_id=record.id,
                          validation_run_ids=[r.id for r in runs])
    args.output.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    print("Research written. No promotion, activation or orders were performed.")


if __name__ == "__main__":
    main()
