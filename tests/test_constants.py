"""ALLOWED_MDD, PROMOTION_GATES 구조 검증."""

from maps.common.constants import (
    ALLOWED_MDD,
    PLATEAU_GRADES,
    PROMOTION_GATES,
    STRATEGY_GROUP_MAP,
    TRADEABILITY_THRESHOLDS,
    TREND_STRENGTH_BUCKETS,
    WEIGHT_PRESETS,
)


def test_allowed_mdd_has_required_groups() -> None:
    required = {"pullback_short", "ath_outlier", "multi_asset", "donchian_research", "portfolio_total"}
    assert required <= set(ALLOWED_MDD.keys())


def test_allowed_mdd_limits_are_positive() -> None:
    for group, limits in ALLOWED_MDD.items():
        assert limits["mc_p95_limit"] > 0, group
        assert limits["expected"] > 0, group
        assert limits["expected"] <= limits["mc_p95_limit"], group


def test_promotion_gates_has_all_stages() -> None:
    required = {"research", "alert_only", "mock_candidate", "live_candidate", "live"}
    assert required <= set(PROMOTION_GATES.keys())


def test_promotion_gates_mock_candidate_has_mc_within_limit() -> None:
    gate = PROMOTION_GATES["mock_candidate"]
    assert gate["mc_within_limit"] is True
    assert gate["wfa_required"] is True


def test_promotion_gates_score_thresholds() -> None:
    assert PROMOTION_GATES["mock_candidate"]["min_tradeability_score"] == TRADEABILITY_THRESHOLDS["mock_candidate"]
    assert PROMOTION_GATES["live_candidate"]["min_tradeability_score"] == TRADEABILITY_THRESHOLDS["live_candidate"]


def test_tradeability_thresholds_fixed() -> None:
    assert TRADEABILITY_THRESHOLDS["mock_candidate"] == 60
    assert TRADEABILITY_THRESHOLDS["live_candidate"] == 75


def test_weight_presets_sum_to_one() -> None:
    for name, weights in WEIGHT_PRESETS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"{name}: {total}"


def test_weight_presets_has_all_keys() -> None:
    required_keys = {"robustness", "risk", "recovery", "return"}
    for name, weights in WEIGHT_PRESETS.items():
        assert required_keys == set(weights.keys()), name


def test_trend_strength_buckets_cover_0_to_100() -> None:
    assert TREND_STRENGTH_BUCKETS[0]["lo"] == 0
    assert TREND_STRENGTH_BUCKETS[-1]["hi"] >= 100
    for i in range(len(TREND_STRENGTH_BUCKETS) - 1):
        assert TREND_STRENGTH_BUCKETS[i]["hi"] == TREND_STRENGTH_BUCKETS[i + 1]["lo"]


def test_strategy_group_map_values_in_allowed_mdd() -> None:
    for sid, group in STRATEGY_GROUP_MAP.items():
        assert group in ALLOWED_MDD, f"{sid} -> {group} 이 ALLOWED_MDD에 없음"


def test_plateau_grades_ordered_by_min_ratio() -> None:
    ratios = [v["min_ratio"] for v in PLATEAU_GRADES.values()]
    assert ratios == sorted(ratios, reverse=True)
