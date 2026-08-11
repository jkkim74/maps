"""Pure safe-budget and order-plan validation for analysis strategies."""

from __future__ import annotations

import datetime
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict

from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import AccountBalance
from maps.market.trading_rules import round_to_krx_tick


class TradePlanLegInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    entry_price: float
    weight_pct: int


class StrategyTradePlanInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    name: str
    market: str = "KOSPI"
    ref_date: datetime.date
    source: str = "analyze"
    trade_mode: Literal["single", "split"]
    total_budget: float
    entries: tuple[TradePlanLegInput, ...]
    target_price: float
    stop_price: float
    rationale: str | None = None
    regime: str | None = None
    strategy_context: str | None = None


class ValidatedTradePlanLeg(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    entry_price: float
    weight_pct: int
    planned_qty: int
    order_amount: float


class TradePlanBlocker(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class ValidatedTradePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocked: bool
    blockers: tuple[TradePlanBlocker, ...]
    limits: dict[str, float]
    safe_max_amount: float
    expected_remaining_cash: float
    planned_order_amount: float
    legs: tuple[ValidatedTradePlanLeg, ...]


def _minimum_cash_ratio(request: StrategyTradePlanInput, settings: MapsSettings) -> float:
    regime = (request.regime or "mixed").lower()
    if regime == "strong":
        return settings.maps_min_cash_ratio_strong
    if regime == "weak":
        return settings.maps_min_cash_ratio_weak
    return settings.maps_min_cash_ratio_mixed


def validate_trade_plan(
    request: StrategyTradePlanInput,
    *,
    account: AccountBalance,
    settings: MapsSettings,
    existing_position_value: float,
    has_active_pick: bool = False,
) -> ValidatedTradePlan:
    """Calculate quantities and return every blocker without side effects."""
    blockers: list[TradePlanBlocker] = []

    def block(code: str, message: str) -> None:
        if not any(item.code == code for item in blockers):
            blockers.append(TradePlanBlocker(code=code, message=message))

    if not settings.maps_strategy_trade_enabled:
        block("GATE_OFF", "전략매매 마스터 스위치가 꺼져 있습니다.")
    if has_active_pick:
        block("DUPLICATE_ACTIVE_TICKER", "동일 종목의 활성 전략이 이미 있습니다.")

    legs = tuple(sorted(request.entries, key=lambda item: item.sequence))
    expected_sequences = [1] if request.trade_mode == "single" else [1, 2, 3]
    expected_weights = [100] if request.trade_mode == "single" else [30, 30, 40]
    if [leg.sequence for leg in legs] != expected_sequences:
        block("INVALID_LEGS", "매매 방식에 맞는 회차가 필요합니다.")
    if (
        sum(leg.weight_pct for leg in legs) != 100
        or [leg.weight_pct for leg in legs] != expected_weights
    ):
        block("INVALID_WEIGHTS", "회차 비중은 단일 100 또는 분할 30/30/40이어야 합니다.")

    all_prices = [leg.entry_price for leg in legs] + [
        request.target_price,
        request.stop_price,
    ]
    if (
        request.total_budget <= 0
        or account.cash < 0
        or account.positions_value < 0
        or account.total_value <= 0
        or existing_position_value < 0
        or any(not math.isfinite(value) or value <= 0 for value in all_prices)
    ):
        block("INVALID_VALUE", "금액과 가격은 유한한 양수여야 합니다.")

    for price in all_prices:
        if price > 0 and math.isfinite(price):
            if float(round_to_krx_tick(price, market=request.market)) != float(price):
                block("INVALID_TICK", "모든 가격은 KRX 호가단위에 맞아야 합니다.")
                break

    entry_prices = [leg.entry_price for leg in legs]
    ordered = (
        len(entry_prices) == len(expected_sequences)
        and request.target_price > entry_prices[0]
        and all(a > b for a, b in zip(entry_prices, entry_prices[1:]))
        and entry_prices[-1] > request.stop_price
    )
    if not ordered:
        block("INVALID_PRICE_ORDER", "목표가 > 진입가 순서 > 손절가여야 합니다.")

    total_value = max(float(account.total_value), 0.0)
    broker_cash = max(float(account.cash), 0.0)
    single_exposure = max(
        total_value * settings.max_single_exposure - existing_position_value,
        0.0,
    )
    portfolio_capacity = max(
        total_value * (1.0 - _minimum_cash_ratio(request, settings))
        - float(account.positions_value),
        0.0,
    )
    risk_fraction = 0.0
    if ordered:
        risk_fraction = sum(
            (leg.weight_pct / 100.0)
            * ((leg.entry_price - request.stop_price) / leg.entry_price)
            for leg in legs
        )
    stop_risk = (
        total_value * settings.maps_strategy_trade_account_risk_pct / risk_fraction
        if risk_fraction > 0
        else 0.0
    )
    limits = {
        "broker_cash": round(broker_cash, 2),
        "single_exposure": round(single_exposure, 2),
        "portfolio_capacity": round(portfolio_capacity, 2),
        "stop_risk": round(max(stop_risk, 0.0), 2),
    }
    safe_max_amount = min(limits.values())

    validated_legs: list[ValidatedTradePlanLeg] = []
    for leg in legs:
        planned_qty = (
            math.floor(request.total_budget * leg.weight_pct / 100.0 / leg.entry_price)
            if request.total_budget > 0 and leg.entry_price > 0
            else 0
        )
        if planned_qty <= 0:
            block("ZERO_QUANTITY", "모든 회차의 계산 수량이 1주 이상이어야 합니다.")
        validated_legs.append(
            ValidatedTradePlanLeg(
                sequence=leg.sequence,
                entry_price=leg.entry_price,
                weight_pct=leg.weight_pct,
                planned_qty=max(planned_qty, 0),
                order_amount=max(planned_qty, 0) * leg.entry_price,
            )
        )

    if request.total_budget > safe_max_amount:
        block(
            "BUDGET_EXCEEDS_SAFE_MAX",
            f"총 매수금액이 안전 최대금액 {safe_max_amount:,.0f}원을 초과합니다.",
        )

    planned_order_amount = sum(leg.order_amount for leg in validated_legs)
    return ValidatedTradePlan(
        blocked=bool(blockers),
        blockers=tuple(blockers),
        limits=limits,
        safe_max_amount=safe_max_amount,
        expected_remaining_cash=max(broker_cash - planned_order_amount, 0.0),
        planned_order_amount=planned_order_amount,
        legs=tuple(validated_legs),
    )
