"""Shared live-trading rules derived from strategy definitions."""

from __future__ import annotations


_STOP_LOSS_PCTS: dict[str, float] = {
    "pullback_v3": 0.05,
    "pullback_v2": 0.06,
    "ath_breakout_v1": 0.10,
    "ath_breakout_v2": 0.12,
    "multi_asset_trend_v1": 0.08,
    "donchian_v1": 0.08,
    "donchian_v2": 0.10,
}

# ATR(14) 기반 동적 손절 배율.
# 변동성 장세에서 고정 % 손절이 노이즈에 조기 발동되는 것을 방지한다.
# 실제 손절가 = max(고정%손절, ATR 손절) 중 넓은 쪽을 선택한다.
_ATR_MULTIPLIERS: dict[str, float] = {
    "pullback_v3": 2.0,
    "pullback_v2": 2.0,
    "ath_breakout_v1": 2.5,
    "ath_breakout_v2": 2.5,
    "multi_asset_trend_v1": 2.0,
    "donchian_v1": 2.0,
    "donchian_v2": 2.0,
}


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
