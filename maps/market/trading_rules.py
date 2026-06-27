"""KRX trading-day and quotation-price rules."""

from __future__ import annotations

import datetime as dt
import logging
import math
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_FIXED_KRX_CLOSED_MONTH_DAYS = {
    (5, 1),   # Labor Day
    (12, 31), # Year-end closing day
}


def parse_closed_dates(value: str) -> frozenset[dt.date]:
    """Parse comma-separated YYYY-MM-DD dates from operations config."""
    dates: set[dt.date] = set()
    for item in value.split(","):
        text = item.strip()
        if text:
            dates.add(dt.date.fromisoformat(text))
    return frozenset(dates)


def is_krx_closed_date(target: dt.date, *, extra_closed_dates: Iterable[dt.date] = ()) -> bool:
    """Return whether a date is known to be closed before live OHLCV exists."""
    if target.weekday() >= 5:
        return True
    if (target.month, target.day) in _FIXED_KRX_CLOSED_MONTH_DAYS:
        return True
    if target in extra_closed_dates:
        return True

    try:
        import holidays
    except ImportError:
        logger.warning("holidays package unavailable; checking configured KRX closure dates only")
        return False
    return target in holidays.KR(years=[target.year])


def previous_trading_day(ref_date: dt.date, *, extra_closed_dates: Iterable[dt.date] = ()) -> dt.date:
    """Return the most recent KRX trading day strictly before ``ref_date``."""
    candidate = ref_date - dt.timedelta(days=1)
    while is_krx_closed_date(candidate, extra_closed_dates=extra_closed_dates):
        candidate -= dt.timedelta(days=1)
    return candidate


def krx_tick_size(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Return the KRX quotation-price unit for an equity-like instrument."""
    if security_type.upper() in {"ETF", "ETN", "ELW"}:
        return 5
    if price < 1_000:
        return 1
    if price < 5_000:
        return 5
    if price < 10_000:
        return 10
    if price < 50_000:
        return 50
    if price < 100_000:
        return 100
    if market.upper() == "KOSDAQ":
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_up_krx_price(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Round a buy limit price up to the next valid KRX quotation unit."""
    tick = krx_tick_size(price, market=market, security_type=security_type)
    return int(math.ceil(price / tick) * tick)
