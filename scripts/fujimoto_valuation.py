"""Bounded official PER evidence collection, separate review, and offline comparison.

Examples:
  python scripts/fujimoto_valuation.py collect --database URL --batch 20 --apply --output collect.json
  python scripts/fujimoto_valuation.py report --database URL --output coverage.json
  python scripts/fujimoto_valuation.py review --database URL --source-id 42 --explanation TEXT --output review.json
Review and ingestion default to dry-run. No execution, promotion or shared fundamentals writes.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main() -> None:
    """Make source ingestion and reviewed decisions explicit independent operations."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from maps.data.dart_financials import KST
    from maps.common.settings import get_settings
    from maps.fujimoto.repository import json_data
    from maps.fujimoto.valuation import (EPSCollector, collect_metadata, record_security_source,
        record_eps_source, review_eps_source, valuation_report, _source_eps)
    from maps.common.models import FujimotoEvidence
    from maps.common.exceptions import DataQualityError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=("collect","report","review","ingest-eps","ingest-metadata"))
    parser.add_argument("--database",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--ref-date",type=date.fromisoformat)
    parser.add_argument("--tickers")
    parser.add_argument("--batch",type=int,default=20)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--source-id",type=int)
    parser.add_argument("--explanation")
    parser.add_argument("--owner",type=int)
    parser.add_argument("--apply",action="store_true")
    args = parser.parse_args()
    if not 1 <= args.batch <= 500:
        parser.error("--batch must be in 1..500")
    day = args.ref_date or datetime.now(timezone.utc).astimezone(KST).date()
    targets = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers is not None else None
    result = dict(execution_permission=False,applied=args.apply)
    with Session(create_engine(args.database)) as db:
        if args.operation == "report":
            result = valuation_report(db,day,tickers=targets)
        elif args.operation == "collect":
            if args.apply:
                result.update(metadata=collect_metadata(db,day),eps=EPSCollector(db,get_settings().dart_api_key).collect(
                    day,batch=args.batch,tickers=targets))
            else:
                result.update(ref_date=day,batch=args.batch,tickers=targets,reason="collection_requires_apply")
        elif args.operation == "review":
            if args.source_id is None or not args.explanation or len(args.explanation.strip()) < 20:
                parser.error("review requires --source-id and substantive --explanation")
            source = db.query(FujimotoEvidence).filter_by(id=args.source_id,kind="eps_source").one_or_none()
            if source is None:
                raise DataQualityError("recorded_eps_source_required")
            eps = _source_eps(source.payload)
            if eps >= 0:
                raise DataQualityError("negative_common_eps_required")
            result.update(source_id=source.id,common_eps=eps,receipt=source.payload["receipt"])
            if args.apply:
                review = review_eps_source(db,source.id,owner=args.owner,explanation=args.explanation)
                result.update(review_id=review.id,available_at=review.available_at)
        else:
            if args.input is None:
                parser.error("ingestion requires --input")
            records = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(records,list) or len(records) > (5000 if args.operation == "ingest-metadata" else 500):
                parser.error("input must be a bounded JSON list")
            ids = []
            if args.apply:
                for raw in records:
                    if args.operation == "ingest-metadata":
                        row = record_security_source(db,day,raw)
                    else:
                        entry = dict(raw)
                        entry["publication_date"] = date.fromisoformat(entry["publication_date"])
                        row = record_eps_source(db,**entry)
                    ids.append(row.id)
            result.update(sources=ids,records=len(records))
    args.output.write_text(json.dumps(json_data(result),ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
