"""Offline operational price inventory/plan/apply/rollback; default is read-only.

Use ``python -m scripts.recover_operational_data --help``. Database selection
comes from normal MAPS settings; URLs/credentials are never command arguments.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy.orm import Session

from maps.common.db import get_engine
from maps.common.exceptions import DataQualityError
from maps.data.recovery import apply_manifest, build_manifest, inventory, rollback_manifest

MAX_FILE_BYTES = 100_000_000


def _read(path: str) -> object:
    """Bound trusted prepared input files before decoding strict JSON."""
    item = Path(path)
    if item.stat().st_size > MAX_FILE_BYTES:
        raise DataQualityError("recovery_input_file_too_large")
    return json.loads(item.read_text(encoding="utf-8"))


def _write(path: str, value: dict) -> None:
    """Atomically publish a complete report; never leave a partial manifest."""
    target = Path(path).resolve()
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None, *, session: Session | None = None) -> int:
    """Run one bounded operation; return safe codes without printing DB errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="write only the explicit verified --manifest")
    modes.add_argument("--rollback", action="store_true", help="reverse only the audited --manifest")
    parser.add_argument("--manifest", help="prepared manifest for explicit apply/rollback")
    parser.add_argument("--source", help="JSON list of trusted prepared KRX raw envelopes; creates a dry-run manifest")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--tickers", help="exact comma-separated six-character input universe")
    parser.add_argument("--closed-dates", default="", help="additional verified KRX closures, YYYY-MM-DD CSV")
    parser.add_argument("--output", required=True, help="atomic JSON report/manifest path")
    args = parser.parse_args(argv)
    writing = args.apply or args.rollback
    if writing and (not args.manifest or args.source or args.start or args.end or args.tickers):
        parser.error("apply/rollback require --manifest and prohibit source/scope overrides")
    if not writing and (args.manifest or not args.start or not args.end or not args.tickers):
        parser.error("inventory/plan require --start --end --tickers; --manifest requires --apply/--rollback")
    if args.manifest and Path(args.output).resolve() == Path(args.manifest).resolve():
        parser.error("output must not overwrite the input manifest")
    db = session
    engine = None
    try:
        if db is None:
            engine = get_engine()
            db = Session(engine, autoflush=False)
        if writing:
            result = (rollback_manifest if args.rollback else apply_manifest)(db, _read(args.manifest))
        else:
            scope = {"start": args.start, "end": args.end, "tickers": args.tickers.split(","),
                     "extra_closed_dates": tuple(date.fromisoformat(s) for s in args.closed_dates.split(",") if s)}
            result = build_manifest(db, _read(args.source), **scope) if args.source else inventory(db, **scope)
        _write(args.output, result)
        print(json.dumps({"operation": result.get("operation", "plan" if args.source else "inventory"),
                          "manifest_id": result.get("manifest_id"), "changed": result.get("changed", 0)}))
        return 0
    except (DataQualityError, OSError, ValueError, TypeError):
        print("Operational recovery rejected input or could not write its report; no source fetch was attempted.")
        return 2
    except Exception:
        print("Operational recovery failed; database diagnostics are intentionally not printed.")
        return 3
    finally:
        if session is None and db is not None:
            db.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
