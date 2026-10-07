"""Pure decisions; callers persist targets and apply only confirmed actual fills."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from collections.abc import Iterable

import pandas as pd

from maps.common.exceptions import DataQualityError
from maps.fujimoto.evidence import SelectionResult, _number


class Mode(str, Enum):
    """Independent research mode identifiers with explicit stop policy."""
    SAFE = "safe"
    ORIGINAL = "original"

    @property
    def strategy_id(self) -> str:
        """Return the catalog/stop-rule strategy ID."""
        return f"fujimoto_{self.value}_v1"


@dataclass(frozen=True)
class CycleState:
    """Fill-derived owned state, independent of account holdings and order intentions."""
    buy_stage: int = 0
    quantity: int = 0
    first_fill_price: float | None = None
    last_buy_date: date | None = None
    stop_price: float | None = None
    pending_order: bool = False
    averaging_down: bool = False
    rebound_reduced: bool = False
    rebound_basis_quantity: int = 0
    rebound_sold_quantity: int = 0
    sell_basis_quantity: int = 0
    ordinary_sold_quantity: int = 0
    sell_target_ninths: int = 0

    def __post_init__(self) -> None:
        for value in (self.buy_stage, self.sell_target_ninths):
            if isinstance(value, bool) or not isinstance(value, int):
                raise DataQualityError("invalid_cycle_stage")
        for name in ("pending_order", "averaging_down", "rebound_reduced"):
            if not isinstance(getattr(self, name), bool):
                raise DataQualityError("invalid_cycle_boolean")
        if self.buy_stage not in range(4) or self.sell_target_ninths not in (0, 1, 3, 9):
            raise DataQualityError("invalid_cycle_stage")
        for name in ("quantity", "sell_basis_quantity", "ordinary_sold_quantity", "rebound_basis_quantity", "rebound_sold_quantity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DataQualityError("invalid_cycle_quantity")
        if self.ordinary_sold_quantity > self.sell_basis_quantity:
            raise DataQualityError("ordinary_sales_exceed_basis")
        if self.rebound_sold_quantity > self.rebound_basis_quantity // 3:
            raise DataQualityError("rebound_sales_exceed_target")
        if self.quantity > 0 and ((self.buy_stage == 0 and not self.pending_order) or self.first_fill_price is None or self.last_buy_date is None):
            raise DataQualityError("holding_without_fill_provenance")
        _number(self.first_fill_price, "first_fill_price", positive=True)
        _number(self.stop_price, "stop_price", positive=True)


@dataclass(frozen=True)
class RuleEvidence:
    """After-close evidence plus independently validated current intraday exit signals.

    None means insufficient evidence; boolean signals must come from completed
    bars. live_price and orderbook_take_profit must be prevalidated for freshness,
    exchange continuity and net sale costs by the eventual feed integration.
    """
    as_of: date
    close: float | None
    selection_passed: bool = False
    blocking_reasons: tuple[str, ...] = ()
    financial_status: str = "missing"
    daily_rsi: float | None = None
    weekly_rsi: float | None = None
    macd_golden: bool = False
    macd_dead: bool = False
    tenkan_cross_up: bool = False
    tenkan_cross_down: bool = False
    ichimoku_bullish: bool = False
    cloud_bearish: bool = False
    weekly_macd_rising: bool = False
    weekly_histogram_rising: bool = False
    rsi_cross_70: bool = False
    live_price: float | None = None
    orderbook_take_profit: bool = False
    atr14: float | None = None

    def __post_init__(self) -> None:
        for name in ("selection_passed", "macd_golden", "macd_dead", "tenkan_cross_up",
                     "tenkan_cross_down", "ichimoku_bullish", "cloud_bearish", "weekly_macd_rising",
                     "weekly_histogram_rising", "rsi_cross_70", "orderbook_take_profit"):
            if not isinstance(getattr(self, name), bool):
                raise DataQualityError("invalid_signal_boolean")
        _number(self.close, "close", positive=True)
        _number(self.live_price, "live_price", positive=True)
        _number(self.atr14, "atr14")
        if self.atr14 is not None and self.atr14 < 0:
            raise DataQualityError("invalid_atr14")
        for value in (self.daily_rsi, self.weekly_rsi):
            _number(value, "rsi")
            if value is not None and not 0 <= value <= 100:
                raise DataQualityError("invalid_rsi")
        if self.financial_status not in {"missing", "maintained", "deteriorated"}:
            raise DataQualityError("invalid_financial_status")


@dataclass(frozen=True)
class Decision:
    """Intent recommendation, never a fill or mutation of CycleState.

    buy_weight is monetary 1/2/6, not a share count. Ordinary sell_quantity is
    remaining quantity toward a cumulative target on sell_basis_quantity.
    A zero-rounded hold target still must persist its sell basis/target.
    """
    action: str
    reason: str
    reasons: tuple[str, ...] = ()
    buy_stage: int = 0
    buy_weight: int = 0
    price_cap: float | None = None
    averaging_down: bool = False
    sell_target_ninths: int = 0
    sell_basis_quantity: int = 0
    sell_quantity: int = 0
    stop_policy: str = "required"
    timing: str = "next_session"


def evaluate(mode: Mode, evidence: RuleEvidence, cycle: CycleState, *, first_rsi_threshold: float = 40,
             decision_date: date | None = None) -> Decision:
    """Emergency exits > ordinary targets > rebound > sequential fill-driven buys."""
    if not isinstance(mode, Mode):
        raise DataQualityError("invalid_mode")
    _number(first_rsi_threshold, "first_rsi_threshold")
    if isinstance(first_rsi_threshold, bool) or first_rsi_threshold is None or not 0 <= first_rsi_threshold <= 100:
        raise DataQualityError("invalid_first_rsi_threshold")
    policy = "required" if mode == Mode.SAFE else "intentional_none"
    decision_date = decision_date or evidence.as_of
    if evidence.as_of > decision_date:
        raise DataQualityError("future_rule_evidence")
    def hold(reason: str, reasons: tuple[str, ...] = ()) -> Decision:
        return Decision("hold", reason, reasons or (reason,), stop_policy=policy)
    if cycle.pending_order:
        return hold("pending_order")
    if cycle.last_buy_date is not None and cycle.last_buy_date > decision_date:
        raise DataQualityError("future_cycle_fill")
    if cycle.buy_stage and not cycle.quantity:
        return hold("cycle_closed")
    if cycle.quantity:
        if evidence.financial_status == "deteriorated":
            return Decision("sell", "fundamental_deterioration", sell_quantity=cycle.quantity,
                            stop_policy=policy, timing="first_available")
        if mode == Mode.SAFE and cycle.stop_price is not None and evidence.live_price is not None and evidence.live_price <= cycle.stop_price:
            return Decision("sell", "price_stop", sell_quantity=cycle.quantity,
                            stop_policy=policy, timing="intraday")
        target = cycle.sell_target_ninths
        reason = "ordinary_sell_pending"
        timing = "next_session"
        if evidence.tenkan_cross_down and evidence.cloud_bearish:
            target, reason = 9, "cloud_exit"
        elif evidence.macd_dead and target < 3:
            target, reason = 3, "macd_dead"
        elif (evidence.rsi_cross_70 or evidence.orderbook_take_profit) and target < 1:
            target = 1
            reason = "orderbook_take_profit" if evidence.orderbook_take_profit else "rsi_cross_70"
            if evidence.orderbook_take_profit:
                timing = "intraday"
        if target:
            basis = cycle.sell_basis_quantity or cycle.quantity
            quantity = cycle.quantity if target == 9 else min(cycle.quantity, max(0, basis * target // 9 - cycle.ordinary_sold_quantity))
            return Decision("sell" if quantity else "hold", reason, sell_target_ninths=target,
                            sell_basis_quantity=basis, sell_quantity=quantity, stop_policy=policy, timing=timing)
        if mode == Mode.ORIGINAL and cycle.averaging_down and not cycle.rebound_reduced and evidence.macd_golden:
            quantity = min(cycle.quantity, max(0, (cycle.rebound_basis_quantity or cycle.quantity) // 3 - cycle.rebound_sold_quantity))
            return Decision("sell" if quantity else "hold", "rebound_reduction", sell_quantity=quantity, stop_policy=policy)
    reasons = list(evidence.blocking_reasons)
    if not evidence.selection_passed:
        reasons.append("selection_failed")
    if evidence.financial_status != "maintained":
        reasons.append("financial_" + evidence.financial_status)
    if evidence.close is None:
        reasons.append("missing_close")
    if cycle.quantity and mode == Mode.SAFE and cycle.stop_price is None:
        reasons.append("missing_required_stop")
    if cycle.last_buy_date is not None and (cycle.last_buy_date >= evidence.as_of
                                           or cycle.last_buy_date == decision_date):
        reasons.append("same_day_advancement")
    if reasons:
        return hold("buy_blocked", tuple(dict.fromkeys(reasons)))
    stage = cycle.buy_stage + 1
    averaging = False
    if stage == 1:
        trigger = (evidence.daily_rsi is not None and evidence.daily_rsi <= first_rsi_threshold
                   and evidence.weekly_rsi is not None and evidence.weekly_rsi < 70)
    elif stage == 2:
        reversal = evidence.macd_golden or evidence.tenkan_cross_up
        averaging = (mode == Mode.ORIGINAL and not reversal and cycle.first_fill_price is not None
                     and evidence.close < cycle.first_fill_price and evidence.weekly_rsi is not None
                     and 30 <= evidence.weekly_rsi < 40)
        trigger = reversal or averaging
    elif stage == 3:
        trigger = evidence.ichimoku_bullish and evidence.weekly_macd_rising and evidence.weekly_histogram_rising
    else:
        trigger = False
    if not trigger:
        return hold("no_buy_signal")
    return Decision("buy", "averaging_down" if averaging else f"buy_stage_{stage}",
                    buy_stage=stage, buy_weight=(1, 2, 6)[stage - 1], price_cap=evidence.close,
                    averaging_down=averaging, stop_policy=policy)


def build_rule_evidence(prices: pd.DataFrame, cutoff: date, selection: SelectionResult,
                        financial_status: str, *, closed_dates: Iterable[date] | None = None) -> RuleEvidence:
    """Derive causal daily/weekly signals and visible buy blocks from validated OHLCV.

    Intraday inputs deliberately remain absent: a feed must validate their actual
    timestamps and continuity before replacing live_price/orderbook_take_profit.
    """
    import math
    from maps.fujimoto.indicators import indicators, weekly_indicators, has_session_gap

    daily = indicators(prices, cutoff)
    weekly = weekly_indicators(prices, cutoff, closed_dates=closed_dates)
    reasons = list(selection.reasons)
    if daily.empty or daily.index[-1].date() != cutoff:
        reasons.append("missing_current_daily_bar")
    if has_session_gap(daily, cutoff, closed_dates=closed_dates):
        reasons.append("price_session_gap")
    # Crossovers are one-session events, not reusable signals under a later date.
    last = daily.iloc[-1] if len(daily) and daily.index[-1].date() == cutoff else None
    week = weekly.iloc[-1] if len(weekly) else None
    def value(row: pd.Series | None, field: str) -> float | None:
        number = None if row is None else row[field]
        return float(number) if number is not None and math.isfinite(number) else None
    if last is None or value(last, "rsi") is None:
        reasons.append("daily_indicator_warmup")
    if week is None or value(week, "rsi") is None:
        reasons.append("weekly_indicator_warmup")
    signals = {name: bool(last[name]) if last is not None else False for name in (
        "macd_golden", "macd_dead", "tenkan_cross_up", "tenkan_cross_down",
        "ichimoku_bullish", "cloud_bearish", "rsi_cross_70")}
    for column, name in (("macd", "weekly_macd_rising"), ("macd_histogram", "weekly_histogram_rising")):
        signals[name] = bool(len(weekly) >= 2 and weekly[column].iloc[-1] > weekly[column].iloc[-2])
    return RuleEvidence(cutoff, value(last, "close"), selection_passed=selection.passed,
                        blocking_reasons=tuple(dict.fromkeys(reasons)), financial_status=financial_status,
                        daily_rsi=value(last, "rsi"), weekly_rsi=value(week, "rsi"),
                        atr14=value(last, "atr14"), **signals)
