"""Shared live-trading rules derived from strategy definitions."""

from __future__ import annotations

from maps.market.trading_rules import round_down_krx_price


_STOP_LOSS_PCTS: dict[str, float] = {
    "pullback_v3": 0.05,
    "pullback_v2": 0.06,
    "ath_breakout_v1": 0.10,
    "ath_breakout_v2": 0.12,
    "multi_asset_trend_v1": 0.08,
    "donchian_v1": 0.08,
    "donchian_v2": 0.10,
    # 역발상 분할 매수 — thesis 무효화 기준 손절 (기술적 기본값)
    "contrarian_quality_accumulation_v1": 0.08,
}

# thesis 무효화 기반 손절 비율 (단순 기술적 손절과 별도 관리)
# KostolanyPriceCalculator가 thesis_stop을 산출할 때 참조한다.
_THESIS_STOP_TRIGGERS: dict[str, list[str]] = {
    "contrarian_quality_accumulation_v1": [
        "operating_profit_drop_over_30pct",
        "debt_ratio_surge",
        "business_cycle_peak_confirmed",
        "major_support_broken_with_credit_deterioration",
    ],
}

# ATR(14) 기반 동적 손절 배율.
# 변동성 장세에서 고정 % 손절이 노이즈에 조기 발동되는 것을 방지한다.
# 실제 손절가는 :func:`effective_stop_price` 로만 구한다 — 고정%와 ATR 중
# 손절 폭이 **넓은**(= 가격이 낮은) 쪽이다.
_ATR_MULTIPLIERS: dict[str, float] = {
    "pullback_v3": 2.0,
    "pullback_v2": 2.0,
    "ath_breakout_v1": 2.5,
    "ath_breakout_v2": 2.5,
    "multi_asset_trend_v1": 2.0,
    "donchian_v1": 2.0,
    "donchian_v2": 2.0,
    # 역발상 전략은 변동성이 크므로 ATR 배율을 높게 유지
    "contrarian_quality_accumulation_v1": 3.0,
}


def stop_loss_pct(strategy_id: str | None) -> float | None:
    """전략의 고정 손절 비율을 반환한다 (``0.05`` = 5%).

    화면·문서가 손절률을 직접 표시할 때 쓴다. 값을 복사해 두지 말고 이 함수를
    거쳐야 규칙이 바뀔 때 표시도 함께 바뀐다.

    :param strategy_id: 전략 ID.
    :return: 손절 비율. 미등록 전략이면 ``None``.
    """
    return _STOP_LOSS_PCTS.get(strategy_id) if strategy_id else None


def atr_multiplier(strategy_id: str | None) -> float | None:
    """전략의 ATR(14) 손절 배수를 반환한다.

    :param strategy_id: 전략 ID.
    :return: ATR 배수. 미등록 전략이면 ``None``.
    """
    return _ATR_MULTIPLIERS.get(strategy_id) if strategy_id else None


def stop_loss_price(strategy_id: str | None, entry_price: float | None) -> float | None:
    """Return the persisted live stop level based on the filled entry price."""
    if not strategy_id or entry_price is None or entry_price <= 0:
        return None
    stop_loss_pct = _STOP_LOSS_PCTS.get(strategy_id)
    if stop_loss_pct is None:
        return None
    return entry_price * (1.0 - stop_loss_pct)


def atr_stop_price(
    strategy_id: str | None,
    entry_price: float | None,
    atr14: float | None,
) -> float | None:
    """ATR(14) 기반 동적 손절가를 반환한다.

    변동성이 클수록 손절 거리가 넓어져 노이즈에 의한 조기 손절을 방지한다.
    ATR 정보가 없으면 None을 반환하고, 호출부에서 고정% 손절로 폴백한다.
    """
    if not strategy_id or entry_price is None or entry_price <= 0:
        return None
    if atr14 is None or atr14 <= 0:
        return None
    multiplier = _ATR_MULTIPLIERS.get(strategy_id)
    if multiplier is None:
        return None
    return entry_price - multiplier * atr14


def effective_stop_price(
    strategy_id: str | None,
    entry_price: float | None,
    atr14: float | None = None,
    *,
    security_type: str = "stock",
) -> float | None:
    """실제로 적용할 손절가 — **이것이 정본이다.**

    고정% 손절과 ATR 손절 중 손절 폭이 더 **넓은**(가격이 더 낮은) 쪽을 고른다.
    두 규칙의 역할이 다르기 때문이다.

    * 고정% — 손절이 아무리 타이트해져도 넘지 못하는 하한선.
    * ATR   — 변동성이 큰 종목에서 잔진동에 조기 손절되는 것을 막는 완충.

    따라서 ATR 이 고정%보다 **좁을 때는 고정%를 써야 한다**. ATR 이 있다고
    무조건 ATR 을 쓰면 저변동성 종목에서 백테스트보다 일찍 털린다.

    청산 판정·사이징·화면 표시가 모두 이 함수를 거쳐야 한다. 손절가가 경로마다
    달라지면 백테스트와 실거래 성과가 체계적으로 어긋난다.

    결과는 **KRX 호가 단위로 내림**한다. 정렬하지 않으면 21,322 처럼 시장에 존재하지
    않는 가격이 나와 화면·판정·사이징이 모두 실제와 어긋난다(2026-07-31 확인).
    반올림이 아니라 내림인 이유는 위와 같다 — 호가 정렬이 손절을 조이면 안 된다.

    :param strategy_id: 전략 ID. 미등록 전략이면 ``None`` 을 반환한다.
    :param entry_price: 체결 진입가.
    :param atr14: ATR(14) 값. ``None`` 이거나 0 이하이면 고정% 손절만 쓴다.
    :param security_type: 호가 단위 판정용. ETF/ETN/ELW 는 5원 고정이다.
    :return: 손절가(유효 호가). 두 규칙 모두 산출 불가하면 ``None``.
    """
    candidates = [
        price
        for price in (
            stop_loss_price(strategy_id, entry_price),
            atr_stop_price(strategy_id, entry_price, atr14),
        )
        if price is not None and price > 0
    ]
    if not candidates:
        return None
    aligned = round_down_krx_price(min(candidates), security_type=security_type)
    return float(aligned) if aligned > 0 else None
