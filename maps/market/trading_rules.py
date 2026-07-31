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


# 신선도 판정용 lookback 상한. is_krx_closed_date 가 호출마다 holidays.KR(...) 을
# 만들기 때문에 n 을 열어 두면 실수 한 번이 그대로 비용이 된다.
_MAX_TRADING_DAYS_LOOKBACK = 60


def trading_days_ago(
    ref_date: dt.date,
    n: int,
    *,
    extra_closed_dates: Iterable[dt.date] = (),
) -> dt.date:
    """``ref_date`` 로부터 KRX 거래일 기준 ``n`` 일 이전 날짜를 반환한다.

    데이터 신선도를 "며칠 지났나"로 판정할 때 쓴다. **달력일이 아니라 거래일**이어야
    한다 — 달력일로 세면 금요일에 만든 데이터가 월요일 아침에 이미 3일이 되어,
    임계값이 작을 때 주말마다 전량 만료된다.

    :param ref_date: 기준 날짜.
    :param n: 거슬러 올라갈 거래일 수. ``0`` 이면 ``ref_date`` 를 그대로 돌려준다.
    :param extra_closed_dates: 운영 설정으로 추가된 휴장일.
    :return: ``n`` 거래일 이전 날짜.
    :raises ValueError: ``n`` 이 음수이거나 상한(60)을 넘을 때.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative: {n}")
    if n > _MAX_TRADING_DAYS_LOOKBACK:
        raise ValueError(f"n exceeds lookback cap {_MAX_TRADING_DAYS_LOOKBACK}: {n}")
    candidate = ref_date
    for _ in range(n):
        candidate = previous_trading_day(candidate, extra_closed_dates=extra_closed_dates)
    return candidate


def krx_tick_size(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Return the KRX quotation-price unit for an equity-like instrument.

    2023-01-25 호가가격단위 개편을 반영한다. 동 개편으로 코스피·코스닥 주식의
    호가단위가 동일하게 통일되었으므로 ``market`` 인자는 주식에서 더 이상 분기하지
    않는다(호출부 시그니처 호환성을 위해 유지). ETF/ETN/ELW 는 5원 고정.

    구간(원): <2,000→1, ~5,000→5, ~20,000→10, ~50,000→50, ~200,000→100,
    ~500,000→500, 500,000 이상→1,000.
    """
    if security_type.upper() in {"ETF", "ETN", "ELW"}:
        return 5
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_up_krx_price(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Round a buy limit price up to the next valid KRX quotation unit."""
    tick = krx_tick_size(price, market=market, security_type=security_type)
    return int(math.ceil(price / tick) * tick)


def round_down_krx_price(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Round a price **down** to a valid KRX quotation unit.

    손절가 전용이다. 반올림(:func:`round_to_krx_tick`)을 쓰면 32,487 이 32,500 으로
    올라가 손절이 **조여진다**. 손절가는 느슨해질지언정 조여지면 안 된다 —
    백테스트·사이징이 가정한 손절폭보다 좁아져 실거래에서만 일찍 털린다.

    :param price: 원본 가격.
    :param market: 시장 구분 (주식은 분기하지 않는다. 호출부 호환성 유지용).
    :param security_type: ETF/ETN/ELW 는 5원 고정.
    :return: 유효 호가로 내림한 가격. 0 이하는 그대로 정수 변환해 반환한다.
    """
    if price <= 0:
        return int(price)
    tick = krx_tick_size(price, market=market, security_type=security_type)
    return int(math.floor(price / tick) * tick)


def round_to_krx_tick(price: float, *, market: str = "KOSPI", security_type: str = "stock") -> int:
    """Round a price to the nearest valid KRX quotation unit.

    Unlike :func:`round_up_krx_price` (ceil, for buy limits), this snaps to the
    closest tick and is suited to display/target/stop prices where a raw value
    such as 54,912 must land on a valid grid point (54,900). Non-positive prices
    are returned as an int unchanged.
    """
    if price <= 0:
        return int(price)
    tick = krx_tick_size(price, market=market, security_type=security_type)
    return int(round(price / tick) * tick)
