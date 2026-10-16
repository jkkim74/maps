from datetime import datetime, date, timezone
from decimal import Decimal
import pytest
from maps.market.us_trading_rules import is_us_market_open, us_session, validate_us_limit_price


def test_dst_and_standard_open_boundaries():
    assert is_us_market_open(datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc))
    assert not is_us_market_open(datetime(2026, 3, 9, 13, 29, tzinfo=timezone.utc))
    assert is_us_market_open(datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc))
    assert not is_us_market_open(datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc))


def test_holiday_and_early_close():
    assert us_session(date(2026, 7, 3)) is None
    session = us_session(date(2026, 11, 27))
    assert session.close.hour == 18
    assert not is_us_market_open(datetime(2026, 11, 27, 18, tzinfo=timezone.utc))


def test_naive_time_and_unsupported_prices_fail_closed():
    with pytest.raises(ValueError):
        is_us_market_open(datetime(2026, 3, 9, 13, 30))
    for value in ('NaN', 'Infinity', '0', '-1', '0.9999', '10.001'):
        with pytest.raises(ValueError):
            validate_us_limit_price(Decimal(value))
    assert validate_us_limit_price(Decimal('10.01')) == Decimal('10.01')
