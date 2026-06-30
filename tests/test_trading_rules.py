from __future__ import annotations

import datetime as dt

from maps.market.trading_rules import (
    is_krx_closed_date,
    krx_tick_size,
    parse_closed_dates,
    previous_trading_day,
    round_to_krx_tick,
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


def test_previous_trading_day_skips_weekend() -> None:
    # 2026-06-29는 월요일 → 직전 거래일은 금요일 2026-06-26
    assert previous_trading_day(dt.date(2026, 6, 29)) == dt.date(2026, 6, 26)


def test_previous_trading_day_skips_configured_closure() -> None:
    # 2026-06-03(수) 기준, 전날 2026-06-02(화)가 휴장이면 2026-06-01(월)로
    assert previous_trading_day(
        dt.date(2026, 6, 3),
        extra_closed_dates={dt.date(2026, 6, 2)},
    ) == dt.date(2026, 6, 1)


def test_krx_tick_size_for_kospi_and_kosdaq() -> None:
    # 2023-01-25 개편으로 코스피·코스닥 호가단위 통일 (200,000~500,000 → 500)
    assert krx_tick_size(317_000, market="KOSPI") == 500
    assert krx_tick_size(317_000, market="KOSDAQ") == 500
    assert krx_tick_size(2_333_000, market="KOSPI") == 1_000


def test_krx_tick_size_post_2023_bands() -> None:
    # 개편 핵심 구간: 1,000~2,000→1, 10,000~20,000→10, 100,000~200,000→100
    assert krx_tick_size(1_500) == 1
    assert krx_tick_size(15_000) == 10
    assert krx_tick_size(150_000) == 100
    # 변경 없는 구간
    assert krx_tick_size(3_000) == 5
    assert krx_tick_size(30_000) == 50


def test_round_up_krx_price_uses_valid_tick() -> None:
    assert round_up_krx_price(2_356_330, market="KOSPI") == 2_357_000
    assert round_up_krx_price(320_170, market="KOSPI") == 320_500


def test_round_to_krx_tick_snaps_to_nearest() -> None:
    # 54,912 → 호가단위 100원 구간(50,000~99,999) → 최근접 54,900
    assert round_to_krx_tick(54_912, market="KOSPI") == 54_900
    assert round_to_krx_tick(54_960, market="KOSPI") == 55_000
    # 1만원 미만 구간(5,000~9,999) → 10원 단위
    assert round_to_krx_tick(7_434, market="KOSDAQ") == 7_430
    # 200,000~500,000: 개편 후 코스피·코스닥 모두 500원 단위
    assert round_to_krx_tick(320_170, market="KOSPI") == 320_000
    assert round_to_krx_tick(320_170, market="KOSDAQ") == 320_000


def test_round_to_krx_tick_handles_nonpositive() -> None:
    assert round_to_krx_tick(0) == 0
    assert round_to_krx_tick(-5) == -5
