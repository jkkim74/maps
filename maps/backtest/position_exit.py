"""백테스트 엔진이 공유하는 상태 기반 포지션 청산 판정."""

from __future__ import annotations

from dataclasses import dataclass

from maps.strategy.base import PositionExitPolicy


@dataclass(frozen=True)
class PositionExitDecision:
    """한 봉의 청산 결과."""

    reason: str
    price: float


def evaluate_position_exit(
    policy: PositionExitPolicy,
    *,
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    entry_price: float,
    stop_price: float,
    initial_risk: float,
    prior_high_water_mark: float,
    strategy_exit: bool,
    next_open: bool,
    is_last: bool,
) -> PositionExitDecision | None:
    """상태 기반 청산을 보수적인 우선순위로 판정한다.

    OHLC 한 봉 안의 실제 가격 경로는 알 수 없으므로 손실 방향을 먼저 처리한다.
    트레일링은 현재 봉 고점이 아니라 이전 봉까지 확정된 HWM만 사용한다.
    """
    if initial_risk <= 0:
        return None

    if next_open:
        if bar_low <= stop_price:
            return PositionExitDecision("stop_loss", min(stop_price, bar_open))

        activation = entry_price + policy.trailing_activate_r * initial_risk
        trailing_stop = prior_high_water_mark - policy.trailing_distance_r * initial_risk
        if prior_high_water_mark >= activation and bar_low <= trailing_stop:
            return PositionExitDecision("trailing_stop", min(trailing_stop, bar_open))

        target = entry_price + policy.target_r * initial_risk
        if bar_high >= target:
            # 지정 목표가는 보수적으로 목표 가격에 체결된 것으로 본다.
            return PositionExitDecision("take_profit", target)

        if strategy_exit:
            return PositionExitDecision("strategy_exit", bar_open)
    else:
        if bar_close <= stop_price:
            return PositionExitDecision("stop_loss", stop_price)

        activation = entry_price + policy.trailing_activate_r * initial_risk
        trailing_stop = prior_high_water_mark - policy.trailing_distance_r * initial_risk
        if prior_high_water_mark >= activation and bar_close <= trailing_stop:
            return PositionExitDecision("trailing_stop", trailing_stop)

        target = entry_price + policy.target_r * initial_risk
        if bar_close >= target:
            return PositionExitDecision("take_profit", target)

        if strategy_exit:
            return PositionExitDecision("strategy_exit", bar_close)

    if is_last:
        return PositionExitDecision("end_of_period", bar_close)
    return None
