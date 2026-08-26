import datetime as dt

from maps.risk.holding_regime_overlay import (
    HoldingRegimeAction,
    HoldingRegimeSnapshot,
    evaluate_holding_regime,
)


def _snapshot(
    day: int,
    regime: str,
    *,
    weekly_trend: str = "pass",
    vol_regime: str = "normal",
) -> HoldingRegimeSnapshot:
    return HoldingRegimeSnapshot(
        ref_date=dt.date(2026, 8, day),
        regime=regime,
        weekly_trend=weekly_trend,
        vol_regime=vol_regime,
    )


def test_same_weekly_fail_cause_twice_marks_eligible_strategy_for_exit():
    decision = evaluate_holding_regime(
        strategy_id="donchian_v2",
        entry=_snapshot(20, "mixed"),
        previous=_snapshot(24, "mixed", weekly_trend="fail"),
        current=_snapshot(25, "mixed", weekly_trend="fail"),
        as_of=dt.date(2026, 8, 26),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.EXIT
    assert decision.reason_code == "CONFIRMED_ADVERSE_REGIME"
    assert decision.confirmed is True
    assert decision.confirmed_adverse_causes == ("weekly_fail",)


def test_different_adverse_causes_do_not_confirm_each_other():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "mixed", weekly_trend="fail"),
        current=_snapshot(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.WATCH
    assert decision.reason_code == "ADVERSE_REGIME_UNCONFIRMED"
    assert decision.confirmed is False
    assert decision.current_adverse_causes == ("weak_transition",)


def test_confirmed_adverse_non_exit_group_has_distinct_watch_reason():
    decision = evaluate_holding_regime(
        strategy_id="multi_asset_trend_v1",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "weak"),
        current=_snapshot(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.WATCH
    assert decision.reason_code == "CONFIRMED_ADVERSE_NON_ENFORCEABLE"
    assert decision.confirmed is True


def test_stale_previous_observation_cannot_confirm_current_adverse_state():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(10, "strong"),
        previous=_snapshot(20, "weak"),
        current=_snapshot(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.WATCH
    assert decision.reason_code == "ADVERSE_REGIME_UNCONFIRMED"
    assert decision.confirmed is False


def test_unknown_strategy_fails_open():
    decision = evaluate_holding_regime(
        strategy_id="unknown",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "weak"),
        current=_snapshot(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "UNKNOWN_STRATEGY"


def test_missing_entry_regime_fails_open():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=None,
        previous=_snapshot(24, "weak"),
        current=_snapshot(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "ENTRY_REGIME_UNAVAILABLE"


def test_missing_current_regime_fails_open():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "weak"),
        current=None,
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "CURRENT_REGIME_UNAVAILABLE"


def test_stale_current_regime_fails_open():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(15, "strong"),
        previous=_snapshot(20, "weak"),
        current=_snapshot(21, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "CURRENT_REGIME_STALE"


def test_invalid_current_regime_fails_open():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "weak"),
        current=_snapshot(25, "surprise"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "CURRENT_REGIME_INVALID"


def test_nonpreferred_regime_watches_without_adverse_transition():
    decision = evaluate_holding_regime(
        strategy_id="ath_breakout_v1",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "strong"),
        current=_snapshot(25, "mixed"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.WATCH
    assert decision.reason_code == "CURRENT_REGIME_NOT_PREFERRED"


def test_high_volatility_alone_watches():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "strong"),
        current=_snapshot(25, "strong", vol_regime="high"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.WATCH
    assert decision.reason_code == "HIGH_VOLATILITY"


def test_compatible_regime_holds_and_serializes_policy_inputs():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_snapshot(20, "strong"),
        previous=_snapshot(24, "strong"),
        current=_snapshot(25, "mixed"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )

    assert decision.action is HoldingRegimeAction.HOLD
    assert decision.reason_code == "REGIME_COMPATIBLE"
    assert decision.to_dict() == {
        "action": "hold",
        "reason_code": "REGIME_COMPATIBLE",
        "strategy_id": "pullback_v3",
        "strategy_group": "pullback_short",
        "entry": {
            "ref_date": "2026-08-20",
            "regime": "strong",
            "weekly_trend": "pass",
            "vol_regime": "normal",
        },
        "previous": {
            "ref_date": "2026-08-24",
            "regime": "strong",
            "weekly_trend": "pass",
            "vol_regime": "normal",
        },
        "current": {
            "ref_date": "2026-08-25",
            "regime": "mixed",
            "weekly_trend": "pass",
            "vol_regime": "normal",
        },
        "confirmed": False,
        "current_adverse_causes": [],
        "confirmed_adverse_causes": [],
    }
