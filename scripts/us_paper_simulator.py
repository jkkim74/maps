"""Explicit local USD quote/fill injection. Never connects to a securities broker.

Run from the repository root with ``python -m scripts.us_paper_simulator --help``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json

from maps.common.db import SessionLocal
from maps.common.exceptions import ExecutionBlockedError
from maps.common.settings import get_settings
from maps.execution.us_mock_broker import USMockBroker


def require_local_mock(settings) -> None:
    """Reject broker-connected modes and implicit test capital."""
    if settings.maps_us_broker_mode != "mock":
        raise ExecutionBlockedError("local_mock_only")
    if settings.maps_us_mock_cash_usd is None:
        raise ExecutionBlockedError("us_mock_cash_not_configured")


def main() -> None:
    """Persist an operator-specified simulated quote or execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    quote = commands.add_parser("quote", help="Inject a newly simulated quote (not market data)")
    quote.add_argument("ticker")
    quote.add_argument("exchange", choices=("NASD", "NYSE", "AMEX"))
    quote.add_argument("price", type=Decimal)
    fill = commands.add_parser("fill", help="Inject an execution for an existing local mock order")
    fill.add_argument("broker_order_id")
    fill.add_argument("quantity", type=int)
    fill.add_argument("price", type=Decimal)
    fill.add_argument("--commission", type=Decimal, default=Decimal(0))
    fill.add_argument("--tax", type=Decimal, default=Decimal(0))
    args = parser.parse_args()
    settings = get_settings()
    require_local_mock(settings)
    if not args.price.is_finite() or args.price <= 0:
        parser.error("price must be finite and positive")
    if args.command == "quote" and f"{args.ticker}:{args.exchange}" not in settings.maps_us_allowed_symbols.split(","):
        parser.error("symbol must be explicitly allowlisted")
    if args.command == "fill" and (args.quantity <= 0 or any(not v.is_finite() or v < 0 for v in (args.commission, args.tax))):
        parser.error("quantity must be positive and costs must be finite/nonnegative")
    with SessionLocal() as db:
        broker = USMockBroker(db, settings)
        try:
            if args.command == "quote":
                broker.set_quote(args.ticker, args.exchange, args.price, as_of=datetime.now(timezone.utc), delayed=False)
            else:
                broker.fill(args.broker_order_id, args.quantity, args.price, args.commission, args.tax)
        finally:
            broker.db.close()
    print(json.dumps({"environment": "local_mock", "action": args.command, "result": "recorded"}))


if __name__ == "__main__":
    main()
