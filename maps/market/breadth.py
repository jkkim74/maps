"""시장폭(breadth) 계산 — 상승종목 비율 기반.

KOSPI 하한선(floor)으로 빌려온 MIXED 국면에서 "지수만 상승하고 종목 확산은 약한
좁은 장"을 식별하기 위한 보조 지표. `historical_ohlcv`에서 각 종목의 이동평균 대비
종가 위치를 집계해 상승종목 비율을 구한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.market.regime import BreadthLabel


def compute_pct_above_ma(
    db: Session,
    ref_date: dt.date,
    *,
    ma_window: int = 20,
    min_tickers: int = 50,
) -> float | None:
    """`ref_date` 기준 종가가 자신의 `ma_window`일 단순이동평균 위에 있는 종목 비율.

    Args:
        db: DB 세션.
        ref_date: 기준일 (이 날짜에 거래된 종목을 대상으로 한다).
        ma_window: 이동평균 윈도우(일). 유효 종목은 최소 이만큼의 봉이 있어야 한다.
        min_tickers: 신뢰 가능한 비율 산출에 필요한 최소 유효 종목 수.

    Returns:
        상승종목 비율(0.0~1.0). 유효 종목이 `min_tickers` 미만이면 `None`을 반환해
        호출자가 시장폭 가드를 적용하지 않도록 한다(안전 기본값).
    """
    repo = HistoricalOHLCVRepository(db)
    tickers = repo.list_tickers_on_date(ref_date)
    if len(tickers) < min_tickers:
        return None

    # ``recent_dataframes``의 row_number() 윈도우 함수는 ``start`` 없이 호출하면
    # 종목별 전체 이력을 정렬한다. 운영 데이터가 수백만 행으로 커지면서 이 한 번의
    # 조회가 수 분 이상 걸렸으므로, 필요한 거래일 수를 넉넉히 덮는 캘린더 구간만 읽는다.
    start = ref_date - dt.timedelta(days=ma_window * 3 + 10)
    frames = repo.recent_dataframes(
        tickers,
        start=start,
        end=ref_date,
        bars=ma_window,
    )
    up = 0
    valid = 0
    for df in frames.values():
        if df.empty:
            continue
        closes = df["close"].dropna()
        if len(closes) < ma_window:
            continue
        sma = float(closes.mean())
        last = float(closes.iloc[-1])
        if sma <= 0:
            continue
        valid += 1
        if last > sma:
            up += 1

    if valid < min_tickers:
        return None
    return up / valid


def classify_breadth(
    pct: float | None,
    *,
    weak_threshold: float = 0.40,
    strong_threshold: float = 0.60,
) -> BreadthLabel:
    """상승종목 비율을 시장폭 국면 라벨로 분류한다.

    Args:
        pct: `compute_pct_above_ma` 결과(또는 None).
        weak_threshold: 이 비율 미만이면 WEAK.
        strong_threshold: 이 비율 이상이면 STRONG.

    Returns:
        `BreadthLabel`. `pct`가 None이면 UNKNOWN(가드 미적용).
    """
    if pct is None:
        return BreadthLabel.UNKNOWN
    if pct < weak_threshold:
        return BreadthLabel.WEAK
    if pct >= strong_threshold:
        return BreadthLabel.STRONG
    return BreadthLabel.NORMAL
