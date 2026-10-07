"""Pure fill-driven decision boundaries; no broker or database."""
from dataclasses import replace
from datetime import date

import pytest

from maps.common.exceptions import DataQualityError
from maps.fujimoto.domain import Mode, CycleState, RuleEvidence, Decision, evaluate
from maps.strategy.live_rules import effective_stop_price

DAY = date(2026, 4, 7)


def evidence(**changes):
    return replace(RuleEvidence(DAY, 100, selection_passed=True,
                   financial_status="maintained", daily_rsi=40, weekly_rsi=69), **changes)


def cycle(**changes):
    return replace(CycleState(buy_stage=1, quantity=90, first_fill_price=110,
                   last_buy_date=date(2026, 4, 6), stop_price=90), **changes)


@pytest.mark.parametrize("mode", list(Mode))
def test_initial_entry_boundaries_and_signal_cap(mode):
    decision = evaluate(mode, evidence(), CycleState())
    assert decision.action == "buy" and decision.buy_stage == 1
    assert decision.buy_weight == 1 and decision.price_cap == 100
    assert decision.stop_policy == ("required" if mode == Mode.SAFE else "intentional_none")
    assert evaluate(mode, evidence(daily_rsi=40.1), CycleState()).action == "hold"
    assert evaluate(mode, evidence(weekly_rsi=70), CycleState()).action == "hold"


def test_missing_and_pending_evidence_block_buys():
    assert evaluate(Mode.SAFE, RuleEvidence(DAY, 100), CycleState()).action == "hold"
    assert "pending_order" in evaluate(Mode.SAFE, evidence(), CycleState(pending_order=True)).reasons
    assert "same_day_advancement" in evaluate(Mode.SAFE, evidence(macd_golden=True), cycle(last_buy_date=DAY)).reasons
    assert evaluate(Mode.SAFE, evidence(), cycle(stop_price=None)).action == "hold"


def test_safe_requires_reversal_original_allows_dip_with_maintained_financials():
    dip = evidence(weekly_rsi=30)
    assert evaluate(Mode.SAFE, dip, cycle()).action == "hold"
    original = evaluate(Mode.ORIGINAL, dip, cycle())
    assert original.action == "buy" and original.buy_stage == 2 and original.buy_weight == 2
    assert original.averaging_down
    assert evaluate(Mode.ORIGINAL, evidence(weekly_rsi=40), cycle()).action == "hold"
    assert evaluate(Mode.ORIGINAL, evidence(weekly_rsi=29.9), cycle()).action == "hold"
    assert evaluate(Mode.ORIGINAL, evidence(weekly_rsi=35, financial_status="missing"), cycle()).action == "hold"
    assert evaluate(Mode.SAFE, evidence(macd_golden=True), cycle()).buy_stage == 2
    assert evaluate(Mode.SAFE, evidence(tenkan_cross_up=True), cycle()).buy_stage == 2


def test_third_stage_requires_all_cloud_and_weekly_conditions():
    ev = evidence(ichimoku_bullish=True, weekly_macd_rising=True, weekly_histogram_rising=True)
    for mode in Mode:
        assert evaluate(mode, ev, cycle(buy_stage=2)).buy_weight == 6
        assert evaluate(mode, replace(ev, weekly_histogram_rising=False), cycle(buy_stage=2)).action == "hold"


def test_emergency_exits_precede_sells_and_missing_buy_data():
    ev = evidence(selection_passed=False, financial_status="missing", macd_dead=True, live_price=85)
    safe = evaluate(Mode.SAFE, ev, cycle())
    assert safe.action == "sell" and safe.sell_quantity == 90 and safe.reason == "price_stop"
    assert evaluate(Mode.ORIGINAL, ev, cycle()).reason == "macd_dead"
    result = evaluate(Mode.ORIGINAL, replace(ev, financial_status="deteriorated"), cycle())
    assert result.sell_quantity == 90 and result.reason == "fundamental_deterioration"
    assert evaluate(Mode.ORIGINAL, evidence(financial_status="missing"), cycle()).action == "hold"


def test_stronger_sell_stage_first_and_integer_final_residual():
    ev = evidence(macd_dead=True, rsi_cross_70=True, macd_golden=True)
    result = evaluate(Mode.ORIGINAL, ev, cycle(quantity=10, averaging_down=True))
    assert result.sell_target_ninths == 3 and result.sell_quantity == 3
    assert result.sell_basis_quantity == 10
    state = cycle(quantity=7, sell_basis_quantity=10, ordinary_sold_quantity=3, sell_target_ninths=3)
    assert evaluate(Mode.SAFE, ev, state).action == "hold"
    final = evaluate(Mode.SAFE, evidence(tenkan_cross_down=True, cloud_bearish=True), state)
    assert final.sell_quantity == 7 and final.sell_target_ninths == 9
    assert evaluate(Mode.SAFE, evidence(macd_golden=True), state).action == "hold"


def test_rebound_reduction_is_once_and_separate_from_ordinary_sales():
    state = cycle(buy_stage=2, averaging_down=True)
    ev = evidence(macd_golden=True)
    result = evaluate(Mode.ORIGINAL, ev, state)
    assert result.reason == "rebound_reduction" and result.sell_quantity == 30
    assert result.sell_target_ninths == 0
    assert evaluate(Mode.ORIGINAL, ev, replace(state, rebound_reduced=True)).action == "hold"
    assert evaluate(Mode.SAFE, ev, state).action == "hold"
    third = replace(ev, ichimoku_bullish=True, weekly_macd_rising=True, weekly_histogram_rising=True)
    assert evaluate(Mode.ORIGINAL, third, replace(state, rebound_reduced=True)).buy_weight == 6


def test_orderbook_and_rsi_first_sell_stage():
    assert evaluate(Mode.SAFE, evidence(orderbook_take_profit=True), cycle()).sell_quantity == 10
    assert evaluate(Mode.SAFE, evidence(rsi_cross_70=True), cycle()).sell_target_ninths == 1
    # A zero-rounded target still starts ordinary selling and blocks further buys.
    result = evaluate(Mode.SAFE, evidence(rsi_cross_70=True), cycle(quantity=2))
    assert result.action == "hold" and result.sell_target_ninths == 1 and result.sell_basis_quantity == 2


def test_safe_common_stop_has_eight_percent_floor_sixteen_percent_cap():
    assert effective_stop_price("fujimoto_safe_v1", 10000) == 9200
    assert effective_stop_price("fujimoto_safe_v1", 10000, 400) == 8800
    assert effective_stop_price("fujimoto_safe_v1", 10000, 1000) == 8400
    assert effective_stop_price("fujimoto_original_v1", 10000, 1000) is None


def test_invalid_state_and_nonfinite_market_data_rejected():
    with pytest.raises(DataQualityError):
        cycle(quantity=-1)
    with pytest.raises(DataQualityError):
        evidence(close=float("nan"))
    with pytest.raises(DataQualityError):
        evidence(weekly_rsi=101)
    with pytest.raises(DataQualityError):
        evaluate("unrecognized", evidence(), CycleState())


def test_empty_completed_cycle_cannot_start_later_buy_leg():
    assert evaluate(Mode.SAFE, evidence(macd_golden=True), cycle(quantity=0)).action == "hold"


def test_indicator_evidence_builder_blocks_gaps_but_preserves_valid_exit():
    import pandas as pd
    from maps.fujimoto.domain import build_rule_evidence
    from maps.fujimoto.evidence import SelectionResult
    from maps.market.trading_rules import is_krx_closed_date
    dates = [d for d in pd.date_range("2024-01-01", DAY) if not is_krx_closed_date(d.date())]
    frame = pd.DataFrame(dict(open=100, high=102, low=98, close=100, volume=100), index=pd.DatetimeIndex(dates))
    selection = SelectionResult("A", True, ())
    full = build_rule_evidence(frame, DAY, selection, "maintained")
    assert full.daily_rsi == 50 and full.weekly_rsi == 50
    gap = build_rule_evidence(frame.drop(frame.index[-3]), DAY, selection, "maintained")
    assert "price_session_gap" in gap.blocking_reasons
    assert evaluate(Mode.SAFE, replace(gap, daily_rsi=30), CycleState()).action == "hold"
    assert evaluate(Mode.SAFE, replace(gap, financial_status="deteriorated"), cycle()).sell_quantity == 90
    future = frame.copy()
    future.loc[pd.Timestamp("2026-04-08")] = 1000
    assert build_rule_evidence(future, DAY, selection, "maintained") == full


def test_boolean_and_quantity_trust_boundaries():
    with pytest.raises(DataQualityError):
        evidence(macd_golden="false")
    with pytest.raises(DataQualityError):
        cycle(buy_stage=True)


def test_first_leg_does_not_wait_for_weekly_macd_warmup():
    import pandas as pd
    from maps.fujimoto.domain import build_rule_evidence
    from maps.fujimoto.evidence import SelectionResult
    from maps.market.trading_rules import is_krx_closed_date
    dates = [d for d in pd.date_range("2025-10-01", DAY) if not is_krx_closed_date(d.date())][-100:]
    close = pd.Series(range(200, 100, -1), index=pd.DatetimeIndex(dates))
    frame = pd.DataFrame(dict(open=close, high=close + 2, low=close - 2, close=close, volume=100))
    ev = build_rule_evidence(frame, DAY, SelectionResult("A", True, ()), "maintained")
    assert evaluate(Mode.SAFE, ev, CycleState()).buy_stage == 1


def test_stale_daily_crossovers_do_not_create_new_exits():
    import pandas as pd
    from maps.fujimoto.domain import build_rule_evidence
    from maps.fujimoto.evidence import SelectionResult
    from maps.fujimoto.indicators import indicators
    idx = pd.bdate_range(end="2025-06-02", periods=100)
    close = pd.Series([100] * 70 + list(range(101, 130)) + [90], index=idx)
    frame = pd.DataFrame(dict(open=close, high=close + 2, low=close - 2, close=close, volume=100))
    assert indicators(frame, date(2025, 6, 2)).macd_dead.iloc[-1]
    cutoff = date(2025, 6, 10)
    ev = build_rule_evidence(frame, cutoff, SelectionResult("A", True, ()), "maintained")
    owned = cycle(last_buy_date=date(2025, 5, 30))
    assert "missing_current_daily_bar" in ev.blocking_reasons
    assert not any(getattr(ev, name) for name in (
        "macd_golden", "macd_dead", "tenkan_cross_up", "tenkan_cross_down",
        "ichimoku_bullish", "cloud_bearish", "rsi_cross_70"))
    assert evaluate(Mode.SAFE, ev, owned).action == "hold"
    assert evaluate(Mode.SAFE, replace(ev, financial_status="deteriorated"), owned).reason == "fundamental_deterioration"
    assert evaluate(Mode.SAFE, replace(ev, live_price=85), owned).reason == "price_stop"
    pending_target = replace(owned, sell_basis_quantity=90, sell_target_ninths=3)
    assert evaluate(Mode.SAFE, ev, pending_target).sell_quantity == 30


@pytest.mark.parametrize("target", [True, 1.0])
def test_sell_target_requires_nonbool_integer(target):
    with pytest.raises(DataQualityError, match="invalid_cycle_stage"):
        cycle(sell_target_ninths=target)
