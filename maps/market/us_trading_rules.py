"""US regular-session calendar and conservative initial limit-price rules."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars

NEW_YORK = ZoneInfo('America/New_York')


@dataclass(frozen=True)
class USSession:
    """One exchange session with UTC open and exclusive close."""
    trading_date: date
    open: datetime
    close: datetime


@lru_cache(maxsize=16)
def _calendar(year: int):
    """Load an explicit year range rather than package-relative defaults."""
    return exchange_calendars.get_calendar('XNYS', start=f'{year}-01-01', end=f'{year}-12-31')


def us_session(day: date) -> USSession | None:
    """Return XNYS regular hours, including holidays and early closes."""
    calendar = _calendar(day.year)
    label = day.isoformat()
    if not calendar.is_session(label):
        return None
    return USSession(day, calendar.session_open(label).to_pydatetime(), calendar.session_close(label).to_pydatetime())


def is_us_market_open(now: datetime | None = None) -> bool:
    """Require a timezone-aware instant inside regular US exchange hours."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('US market time must be timezone-aware')
    session = us_session(now.astimezone(NEW_YORK).date())
    return session is not None and session.open <= now < session.close


def validate_us_limit_price(price: Decimal) -> Decimal:
    """Initial release accepts USD prices >= 1 with at most two decimals."""
    value = Decimal(str(price))
    if not value.is_finite() or value < 1 or value != value.quantize(Decimal('.01')):
        raise ValueError('USD limit price must be >= 1 and a whole cent')
    return value
