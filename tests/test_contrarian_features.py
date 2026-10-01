import datetime as dt

import pandas as pd
import pytest

from maps.market.trading_rules import previous_trading_day
from maps.strategy.base import StrategyType
from maps.strategy.scoring import StrategyAwareScoreCalculator, StrategyScoreInput


def sample():
    dates = [dt.date(2026, 9, 30)]
    for _ in range(79):
        dates.append(previous_trading_day(dates[-1]))
    dates.reverse()
    prices = pd.DataFrame({"close": [100.] * 80, "low": [100.] * 80,
                           "volume": [200.] * 60 + [100.] * 20}, index=dates)
    flows = pd.DataFrame({"foreign_net_value": [0.] * 20,
                          "institutional_net_value": [0.] * 20}, index=dates[-20:])
    earnings = dict(revenue=100, prior_revenue=100, operating_profit=10,
                    prior_operating_profit=10, evidence={"rcept_no": "20260901000001"})
    return prices, flows, earnings


def calculate(prices, flows, earnings):
    from maps.strategy.contrarian_features import contrarian_extra_scores
    return contrarian_extra_scores(prices, flows, earnings, ref_date=dt.date(2026, 9, 30))


def test_complete_research_score_uses_observations_and_new_earnings_key():
    extra = calculate(*sample())
    assert extra["earnings_improvement_score"] == 50
    assert extra["crowd_neglect_score"] == 50
    assert extra["accumulation_flow_score"] == 50
    assert extra["technical_bottom_score"] == 35
    assert "earnings_revision_score" not in extra
    result = StrategyAwareScoreCalculator().calculate(StrategyScoreInput(
        strategy_type=StrategyType.CONTRARIAN_QUALITY, valuation_margin_score=80,
        liquidity_score=None, trend_strength=None,
        extra_scores=extra,
    ))
    assert result.score_ready and result.coverage_ratio == 1
    assert result.final_score == 57.5
    assert extra["_evidence"]["earnings_improvement_score"]["rcept_no"] == "20260901000001"


@pytest.mark.parametrize("net,expected", [(500, 100), (-500, 0), (0, 50)])
def test_accumulation_boundaries(net, expected):
    prices, flows, earnings = sample()
    flows["foreign_net_value"] = net
    assert calculate(prices, flows, earnings)["accumulation_flow_score"] == expected


def test_missing_flow_is_not_neutral_or_zero():
    prices, flows, earnings = sample()
    flows.iloc[0, 1] = float("nan")
    extra = calculate(prices, flows, earnings)
    assert "accumulation_flow_score" not in extra
    assert extra["_evidence"]["accumulation_flow_score"]["missing_reason"]


def test_future_rows_ignored_and_calendar_gap_fails_closed():
    prices, flows, earnings = sample()
    expected = calculate(prices, flows, earnings)
    prices.loc[dt.date(2026, 10, 1)] = [10000, 10000, 10000]
    assert calculate(prices, flows, earnings) == expected
    prices = prices.drop(prices.index[0])
    extra = calculate(prices, flows, earnings)
    assert "crowd_neglect_score" not in extra
    assert "technical_bottom_score" in extra


def test_zero_turnover_and_invalid_revenue_never_become_complete():
    prices, flows, earnings = sample()
    prices["volume"] = 0
    earnings["prior_revenue"] = 0
    extra = calculate(prices, flows, earnings)
    assert "earnings_improvement_score" not in extra
    assert "crowd_neglect_score" not in extra
    assert "accumulation_flow_score" not in extra


def test_loss_reduction_and_revenue_growth_are_measured():
    prices, flows, earnings = sample()
    earnings.update(revenue=120, prior_revenue=100, operating_profit=-6,
                    prior_operating_profit=-10)
    assert calculate(prices, flows, earnings)["earnings_improvement_score"] == 100
