"""Safe-budget and shared strategy trade-plan validation tests."""

from __future__ import annotations

from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import AccountBalance


def _settings(**overrides) -> MapsSettings:
    values = {
        "maps_strategy_trade_enabled": True,
        "maps_strategy_trade_account_risk_pct": 0.01,
        "max_single_exposure": 0.10,
        "maps_min_cash_ratio_strong": 0.15,
    }
    values.update(overrides)
    return MapsSettings(_env_file=None, **values)


def _split_request(**overrides):
    from maps.ops.strategy_trade_plan import StrategyTradePlanInput, TradePlanLegInput

    values = {
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "ref_date": "2026-08-10",
        "trade_mode": "split",
        "total_budget": 9_900_000,
        "entries": (
            TradePlanLegInput(sequence=1, entry_price=70_000, weight_pct=30),
            TradePlanLegInput(sequence=2, entry_price=67_000, weight_pct=30),
            TradePlanLegInput(sequence=3, entry_price=64_000, weight_pct=40),
        ),
        "target_price": 80_000,
        "stop_price": 60_000,
        "regime": "strong",
    }
    values.update(overrides)
    return StrategyTradePlanInput(**values)


def test_safe_budget_is_minimum_of_all_limits() -> None:
    from maps.ops.strategy_trade_plan import validate_trade_plan

    plan = validate_trade_plan(
        _split_request(),
        account=AccountBalance(
            cash=12_500_000,
            positions_value=50_000_000,
            total_assets=100_000_000,
        ),
        settings=_settings(),
        existing_position_value=0,
    )

    assert plan.safe_max_amount == min(plan.limits.values())
    assert plan.limits["broker_cash"] == 12_500_000
    assert plan.limits["single_exposure"] == 10_000_000
    assert plan.limits["portfolio_capacity"] == 35_000_000
    assert [leg.weight_pct for leg in plan.legs] == [30, 30, 40]
    assert all(leg.planned_qty > 0 for leg in plan.legs)
    assert plan.blocked is False


def test_limits_do_not_require_total_budget() -> None:
    """매수금액을 정하기 전에 현재 계좌의 안전 최대치를 계산한다."""
    from maps.ops.strategy_trade_plan import (
        StrategyTradeLimitInput,
        calculate_trade_limits,
    )

    request = StrategyTradeLimitInput(**{
        key: value
        for key, value in _split_request().model_dump().items()
        if key != "total_budget"
    })
    result = calculate_trade_limits(
        request,
        account=AccountBalance(
            cash=12_500_000,
            positions_value=50_000_000,
            total_assets=100_000_000,
        ),
        settings=_settings(),
        existing_position_value=0,
    )

    assert result.safe_max_amount == 10_000_000
    assert result.minimum_orderable_amount == 233_334
    assert result.blocked is False
    assert result.regime_used == "strong"


def test_safe_budget_reports_all_relevant_blocker_codes() -> None:
    from maps.ops.strategy_trade_plan import validate_trade_plan

    plan = validate_trade_plan(
        _split_request(total_budget=20_000_000),
        account=AccountBalance(
            cash=12_500_000,
            positions_value=50_000_000,
            total_assets=100_000_000,
        ),
        settings=_settings(maps_strategy_trade_enabled=False),
        existing_position_value=0,
        has_active_pick=True,
    )

    assert plan.blocked is True
    assert {blocker.code for blocker in plan.blockers} >= {
        "GATE_OFF",
        "DUPLICATE_ACTIVE_TICKER",
        "BUDGET_EXCEEDS_SAFE_MAX",
    }


def test_safe_budget_rejects_invalid_tick_and_price_order() -> None:
    from maps.ops.strategy_trade_plan import validate_trade_plan

    plan = validate_trade_plan(
        _split_request(
            entries=(
                {"sequence": 1, "entry_price": 70_001, "weight_pct": 30},
                {"sequence": 2, "entry_price": 71_000, "weight_pct": 30},
                {"sequence": 3, "entry_price": 64_000, "weight_pct": 40},
            )
        ),
        account=AccountBalance(cash=100_000_000, positions_value=0),
        settings=_settings(),
        existing_position_value=0,
    )

    assert {blocker.code for blocker in plan.blockers} >= {
        "INVALID_TICK",
        "INVALID_PRICE_ORDER",
    }
