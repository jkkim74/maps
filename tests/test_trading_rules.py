from __future__ import annotations

import datetime as dt

from maps.market.trading_rules import (
    is_krx_closed_date,
    krx_tick_size,
    parse_closed_dates,
    round_up_krx_price,
)


def test_parse_closed_dates() -> None:
    assert parse_closed_dates("2026-06-03, 2026-07-17") == {
        dt.date(2026, 6, 3),
        dt.date(2026, 7, 17),
    }


def test_explicit_krx_closure_date_is_closed() -> None:
    assert is_krx_closed_date(
        dt.date(2026, 6, 3),
        extra_closed_dates={dt.date(2026, 6, 3)},
    )


def test_krx_tick_size_for_kospi_and_kosdaq() -> None:
    assert krx_tick_size(317_000, market="KOSPI") == 500
    assert krx_tick_size(317_000, market="KOSDAQ") == 100
    assert krx_tick_size(2_333_000, market="KOSPI") == 1_000


def test_round_up_krx_price_uses_valid_tick() -> None:
    assert round_up_krx_price(2_356_330, market="KOSPI") == 2_357_000
    assert round_up_krx_price(320_170, market="KOSPI") == 320_500
