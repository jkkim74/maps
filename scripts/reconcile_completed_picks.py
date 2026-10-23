"""Review/repair explicit historical pick IDs using terminal order evidence only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps.common.db import SessionLocal
from maps.common.models import AnalysisPick, OrderLog
from maps.common.exceptions import ExecutionBlockedError
from maps.execution.pick_reconciliation import reconcile_completed_picks
from maps.execution.safety import account_execution_lock
from contextlib import ExitStack


def main() -> None:
    """Default to preview; --apply repeats evidence checks under account locks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pick-ids", type=int, nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db, ExitStack() as locks:
        picks = db.query(AnalysisPick).filter(AnalysisPick.id.in_(args.pick_ids)).all()
        if len(picks) != len(set(args.pick_ids)):
            parser.error("One or more pick IDs do not exist")
        keys = {r.account_key for r in db.query(OrderLog).filter(
            OrderLog.order_id.in_([p.exit_order_id for p in picks if p.exit_order_id])) if r.account_key}
        if args.apply:
            for key in sorted(keys):
                locks.enter_context(account_execution_lock(key))
            db.expire_all()
            current = db.query(AnalysisPick).filter(AnalysisPick.id.in_(args.pick_ids)).with_for_update().all()
            current_keys = {row.account_key for row in db.query(OrderLog).filter(
                OrderLog.order_id.in_([p.exit_order_id for p in current if p.exit_order_id])) if row.account_key}
            if current_keys != keys:
                raise ExecutionBlockedError("repair_account_changed")
        result = reconcile_completed_picks(db, pick_ids=args.pick_ids, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(json.dumps({"applied": args.apply, "results": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
