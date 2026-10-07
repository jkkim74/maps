"""Durable ownership, monotone fills and conservative monetary sizing."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from maps.common.exceptions import DataQualityError
from maps.fujimoto.domain import CycleState, Decision, Mode


def api():
    """Import inside tests so absent implementation produces an explicit red."""
    from maps.fujimoto.repository import FujimotoRepository, FillEvent, AccountLimits
    return FujimotoRepository, FillEvent, AccountLimits


def setup_cycle(db, mode=Mode.ORIGINAL, budget=1000000):
    Repo, _, _ = api()
    repo = Repo(db)
    config = repo.configure("mock:one", 1, mode, budget)
    cycle = repo.create_cycle(config.id, "005930")
    return repo, config, cycle


def buy(repo, cycle, stage=1, quantity=10, price=1000):
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 5, 7),
                                    datetime(2026, 1, 5, 7), {"source": "offline"})
    decision = Decision("buy", f"buy_stage_{stage}", buy_stage=stage,
                        buy_weight=(1, 2, 6)[stage - 1], price_cap=price)
    return repo.reserve_order(cycle.id, decision, evidence.id, quantity, price,
                              signal_date=date(2026, 1, 5))


def event(order, qty, gross, fees=0, status="PARTIAL", day=date(2026, 1, 6)):
    _, FillEvent, _ = api()
    return FillEvent(order.id, order.account_key, order.intent_id, qty, gross, fees, 0, status, day)


def test_partial_holdings_can_precede_terminal_stage():
    state = CycleState(quantity=2, first_fill_price=1000, last_buy_date=date(2026, 1, 6), pending_order=True)
    assert state.buy_stage == 0


def test_partial_terminal_duplicate_restart_and_cash(db):
    repo, config, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    result = repo.apply_fill(event(order, 2, 2000, 2))
    assert result.quantity == 2 and result.buy_stage == 0 and result.pending_order
    assert repo.cash(config.id) == Decimal("997998")
    assert repo.reserved_cash(config.id) >= 8000
    result = repo.apply_fill(event(order, 5, 5100, 5, "CANCELLED"))
    assert result.buy_stage == 1 and result.quantity == 5 and not result.pending_order
    assert result.first_fill_price == 1020
    assert repo.apply_fill(event(order, 5, 5100, 5, "CANCELLED")) == result
    db.commit()
    db.expire_all()
    Repo, _, _ = api()
    assert Repo(db).state(cycle.id) == result
    assert repo.cash(config.id) == Decimal("994895")
    assert repo.reserved_cash(config.id) == 0
    with pytest.raises(DataQualityError):
        repo.apply_fill(event(order, 4, 4000, 4))


def test_unknown_cancel_requested_keep_reservation_and_zero_cancel_does_not_advance(db):
    repo, config, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    for status in ("UNKNOWN", "CANCEL_REQUESTED"):
        assert repo.apply_fill(event(order, 0, 0, status=status)).pending_order
        assert repo.reserved_cash(config.id) >= 10000
        with pytest.raises(DataQualityError):
            repo.configure("mock:one", 1, Mode.ORIGINAL, 2000000)
    assert repo.apply_fill(event(order, 0, 0, status="CANCELLED")).buy_stage == 0
    assert repo.reserved_cash(config.id) == 0


def test_mode_separation_sell_cost_conservation_and_oversell(db):
    repo, config, safe = setup_cycle(db, Mode.SAFE)
    safe_order = buy(repo, safe)
    repo.apply_fill(event(safe_order, 10, 10000, 10, "FILLED"))
    original_config = repo.configure("mock:one", 1, Mode.ORIGINAL, 1000000)
    original = repo.create_cycle(original_config.id, "005930")
    original_order = buy(repo, original)
    repo.apply_fill(event(original_order, 10, 10000, 10, "FILLED"))
    evidence = repo.record_evidence("candidate", safe.ticker, datetime(2026, 1, 7), datetime(2026, 1, 7), {})
    with pytest.raises(DataQualityError):
        repo.reserve_order(safe.id, Decision("sell", "cloud_exit", sell_quantity=11), evidence.id, 11, 1200)
    sell = repo.reserve_order(safe.id, Decision("sell", "cloud_exit", sell_quantity=10,
                              sell_target_ninths=9, sell_basis_quantity=10), evidence.id, 10, 1200)
    repo.apply_fill(event(sell, 10, 12000, 12, "FILLED", date(2026, 1, 8)))
    assert repo.state(safe.id).quantity == 0 and repo.state(original.id).quantity == 10
    assert repo.cash(config.id) == Decimal("1001978")
    assert safe.realized_pnl == Decimal("1978") and safe.cost_basis == 0
    assert repo.owned_quantity("mock:one", "005930") == 10


def test_immutable_evidence_asof_and_config_deposit(db):
    repo, config, cycle = setup_cycle(db)
    first = repo.record_evidence("annual", cycle.ticker, datetime(2026, 1, 5), datetime(2026, 1, 6), {"receipt": "a", "amount": 1})
    second = repo.record_evidence("annual", cycle.ticker, datetime(2026, 1, 8), datetime(2026, 1, 9), {"receipt": "b", "amount": None})
    assert [r.id for r in repo.evidence_as_of("annual", cycle.ticker, datetime(2026, 1, 7))] == [first.id]
    assert second.fingerprint != first.fingerprint
    updated = repo.configure("mock:one", 1, Mode.ORIGINAL, 1200000, deposit=200000)
    assert updated.version == 2 and config.budget == 1000000
    assert repo.cash(updated.id) == 1200000
    assert repo.state(cycle.id).quantity == 0


def test_sizing_monetary_weights_stop_risk_account_exposure_and_reserve(db):
    repo, config, cycle = setup_cycle(db, Mode.SAFE)
    _, _, Limits = api()
    limits = Limits(nav=2000000, cash=2000000, ticker_exposure=0, reserved_cash=0,
                    max_ticker_fraction=.1, minimum_cash_fraction=.325)
    decision = Decision("buy", "buy_stage_1", buy_stage=1, buy_weight=1, price_cap=1000)
    plan = repo.plan_buy(cycle.id, decision, limits, {}, atr14=20)
    assert plan.quantity == 11 and plan.stop_price == 920
    exhausted = Limits(nav=2000000, cash=2000000, ticker_exposure=200000, reserved_cash=0,
                       max_ticker_fraction=.1, minimum_cash_fraction=.325)
    assert repo.plan_buy(cycle.id, decision, exhausted, {}, atr14=20).quantity == 0
    order = buy(repo, cycle, quantity=11)
    repo.apply_fill(event(order, 11, 11000, status="FILLED"))
    second = Decision("buy", "buy_stage_2", buy_stage=2, buy_weight=2, price_cap=900)
    plan = repo.plan_buy(cycle.id, second, limits, {cycle.ticker: 900}, atr14=100)
    assert plan.stop_price >= repo.state(cycle.id).stop_price
    assert plan.quantity == 0  # a buy below its protected stop would immediately exit


def test_wrong_account_or_intent_and_cumulative_cost_regressions_rejected(db):
    repo, _, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    _, FillEvent, _ = api()
    with pytest.raises(DataQualityError):
        repo.apply_fill(FillEvent(order.id, "foreign", None, 1, 1000, 0, 0, "PARTIAL", date(2026, 1, 6)))
    repo.apply_fill(event(order, 2, 2000, 2))
    with pytest.raises(DataQualityError):
        repo.apply_fill(event(order, 3, 3000, 1))


def test_zero_rounded_sell_target_blocks_additions_and_final_sells_residual(db):
    from maps.fujimoto.domain import RuleEvidence, evaluate
    repo, _, cycle = setup_cycle(db)
    order = buy(repo, cycle, quantity=2)
    repo.apply_fill(event(order, 2, 2000, status="FILLED"))
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 7), datetime(2026, 1, 7), {})
    target = evaluate(Mode.ORIGINAL, RuleEvidence(date(2026, 1, 7), 1000, rsi_cross_70=True), repo.state(cycle.id))
    assert target.action == "hold"
    repo.record_decision(cycle.id, evidence.id, target)
    assert repo.state(cycle.id).sell_basis_quantity == 2
    assert repo.state(cycle.id).sell_target_ninths == 1
    stronger = evaluate(Mode.ORIGINAL, RuleEvidence(date(2026, 1, 8), 1000,
                        tenkan_cross_down=True, cloud_bearish=True), repo.state(cycle.id))
    assert stronger.sell_quantity == 2
    with pytest.raises(DataQualityError):
        buy(repo, cycle, stage=2, quantity=1)


def test_reserve_cannot_exceed_leg_budget_or_same_day_or_closed_cycle(db):
    repo, _, cycle = setup_cycle(db)
    with pytest.raises(DataQualityError):
        buy(repo, cycle, quantity=1000)
    order = buy(repo, cycle, quantity=10)
    repo.apply_fill(event(order, 10, 10000, status="FILLED"))
    with pytest.raises(DataQualityError):
        buy(repo, cycle, stage=2, quantity=10)


def test_five_positions_and_immutable_record_update(db):
    repo, config, first = setup_cycle(db)
    for ticker in ("000001", "000002", "000003", "000004"):
        repo.create_cycle(config.id, ticker)
    with pytest.raises(DataQualityError):
        repo.create_cycle(config.id, "000005")
    evidence = repo.record_evidence("annual", first.ticker, datetime(2026, 1, 5), datetime(2026, 1, 6), {"receipt": "a"})
    with pytest.raises(DataQualityError, match="immutable"):
        with db.begin_nested():
            evidence.payload = {"receipt": "forged"}
            db.flush()


def test_partial_rebound_cancel_preserves_original_reduction_target(db):
    from maps.fujimoto.domain import RuleEvidence, evaluate
    repo, _, cycle = setup_cycle(db)
    order = buy(repo, cycle, quantity=12)
    repo.apply_fill(event(order, 12, 12000, status="FILLED"))
    # Actual stage2 averaging-down state; original first-leg basis remains 1000.
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 7), datetime(2026, 1, 7), {})
    second = Decision("buy", "averaging_down", buy_stage=2, buy_weight=2, price_cap=900, averaging_down=True)
    order = repo.reserve_order(cycle.id, second, evidence.id, 6, 900, signal_date=date(2026, 1, 7))
    repo.apply_fill(event(order, 6, 5400, status="FILLED", day=date(2026, 1, 8)))
    decision = evaluate(Mode.ORIGINAL, RuleEvidence(date(2026, 1, 9), 1000, macd_golden=True), repo.state(cycle.id))
    sell = repo.reserve_order(cycle.id, decision, evidence.id, decision.sell_quantity, 1000)
    repo.apply_fill(event(sell, 2, 2000, status="CANCELLED", day=date(2026, 1, 12)))
    retry = evaluate(Mode.ORIGINAL, RuleEvidence(date(2026, 1, 13), 1000, macd_golden=True), repo.state(cycle.id))
    assert retry.sell_quantity == 4


def test_terminal_cumulative_fee_and_average_corrections_are_idempotent(db):
    repo, config, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    repo.apply_fill(event(order, 5, 5000, 5, "CANCELLED"))
    corrected = event(order, 5, 5100, 6, "CANCELLED")
    state = repo.apply_fill(corrected)
    assert state.first_fill_price == 1020 and state.buy_stage == 1
    assert repo.cash(config.id) == 994894
    assert repo.apply_fill(corrected) == state


def test_historical_sell_fee_correction_preserves_newer_sell_reservation(db):
    repo, config, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    repo.apply_fill(event(order, 10, 10000, status="FILLED"))
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 7), datetime(2026, 1, 7), {})
    old = repo.reserve_order(cycle.id, Decision("sell", "cloud_exit", sell_quantity=2), evidence.id, 2, 1100)
    repo.apply_fill(event(old, 2, 2200, 2, "FILLED", date(2026, 1, 8)))
    newer = repo.reserve_order(cycle.id, Decision("sell", "cloud_exit", sell_quantity=8), evidence.id, 8, 1100)
    before = repo.state(cycle.id)
    corrected = event(old, 2, 2200, 3, "FILLED", date(2026, 1, 8))
    assert repo.apply_fill(corrected) == before
    assert repo.cash(config.id) == 992197
    assert newer.status == "RESERVED"
    with pytest.raises(DataQualityError):
        repo.reserve_order(cycle.id, Decision("sell", "cloud_exit", sell_quantity=8), evidence.id, 8, 1100)


def test_historical_buy_refresh_preserves_newer_buy_stage_and_reservation(db):
    repo, _, cycle = setup_cycle(db)
    first = buy(repo, cycle)
    repo.apply_fill(event(first, 10, 10000, status="FILLED"))
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 7), datetime(2026, 1, 7), {})
    decision = Decision("buy", "buy_stage_2", buy_stage=2, buy_weight=2, price_cap=900)
    newer = repo.reserve_order(cycle.id, decision, evidence.id, 10, 900, signal_date=date(2026, 1, 7))
    before = repo.state(cycle.id)
    assert repo.apply_fill(event(first, 10, 10000, status="FILLED", day=date(2026, 1, 7))) == before
    with pytest.raises(DataQualityError):
        repo.reserve_order(cycle.id, decision, evidence.id, 10, 900, signal_date=date(2026, 1, 7))
    repo.apply_fill(event(newer, 10, 9000, status="FILLED", day=date(2026, 1, 8)))
    completed = repo.state(cycle.id)
    assert completed.buy_stage == 2
    assert repo.apply_fill(event(first, 10, 10000, status="FILLED", day=date(2026, 1, 9))) == completed


def test_forged_existing_intent_cross_ticker_or_cycle_is_rejected(db):
    from maps.common.models import OrderIntent
    repo, _, cycle = setup_cycle(db)
    order = buy(repo, cycle)
    now = datetime(2026, 1, 6)
    db.add(OrderIntent(id="foreign", account_key="mock:one", environment="mock", event_key="wrong",
                      strategy_id="fujimoto_original_v1", ticker="000001", side="buy", status="RESERVED",
                      request={"source": "fujimoto", "source_id": cycle.id}, quantity=10,
                      valid_until=now, created_at=now, updated_at=now))
    db.flush()
    with pytest.raises(DataQualityError, match="intent"):
        repo.bind_intent(order.id, "foreign")


def test_reserve_with_prebound_intent_cannot_bypass_identity_check(db):
    from maps.common.models import OrderIntent
    repo, _, cycle = setup_cycle(db)
    evidence = repo.record_evidence("candidate", cycle.ticker, datetime(2026, 1, 5), datetime(2026, 1, 5), {})
    now = datetime(2026, 1, 6)
    db.add(OrderIntent(id="foreign", account_key="foreign", environment="mock", event_key="wrong",
                      strategy_id="fujimoto_original_v1", ticker=cycle.ticker, side="buy", status="RESERVED",
                      request={"source": "fujimoto", "source_id": cycle.id}, quantity=1,
                      valid_until=now, created_at=now, updated_at=now))
    db.flush()
    with pytest.raises(DataQualityError, match="intent"):
        repo.reserve_order(cycle.id, Decision("buy", "buy_stage_1", buy_stage=1, buy_weight=1, price_cap=1000),
                           evidence.id, 1, 1000, intent_id="foreign", signal_date=date(2026, 1, 5))
    assert not repo.state(cycle.id).pending_order


def test_budget_withdrawal_is_explicit_prospective_and_cash_bounded(db):
    repo, config, _ = setup_cycle(db)
    updated = repo.configure("mock:one", 1, Mode.ORIGINAL, 900000, deposit=-100000)
    assert config.budget == 1000000 and repo.cash(updated.id) == 900000
    with pytest.raises(DataQualityError):
        repo.configure("mock:one", 2, Mode.SAFE, 1000000)


def test_buy_plan_rounds_cap_down_before_quantity_and_safe_loss_cap(db):
    repo, _, cycle = setup_cycle(db, Mode.SAFE, budget=100000000)
    _, _, Limits = api()
    limits = Limits(nav=200000000, cash=200000000, ticker_exposure=0, reserved_cash=0,
                    max_ticker_fraction=.1, minimum_cash_fraction=.325)
    decision = Decision("buy", "buy_stage_1", buy_stage=1, buy_weight=1, price_cap=32487)
    plan = repo.plan_buy(cycle.id, decision, limits, {}, atr14=1000)
    assert plan.limit_price == 32450
    order = repo.reserve_order(cycle.id, decision, repo.record_evidence("candidate", cycle.ticker,
        datetime(2026, 1, 5), datetime(2026, 1, 5), {}).id, plan.quantity, plan.limit_price,
        stop_price=plan.stop_price, signal_date=date(2026, 1, 5))
    repo.apply_fill(event(order, plan.quantity, plan.quantity * plan.limit_price, status="FILLED"))
    second = Decision("buy", "buy_stage_2", buy_stage=2, buy_weight=2, price_cap=40000)
    plan2 = repo.plan_buy(cycle.id, second, limits, {cycle.ticker: 40000}, atr14=5000)
    assert float(cycle.cost_basis) + plan2.quantity * 40000 - (repo.state(cycle.id).quantity + plan2.quantity) * plan2.stop_price <= 500000
