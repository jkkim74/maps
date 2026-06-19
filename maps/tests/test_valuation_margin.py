from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maps.ai.valuation_margin import ValuationMarginInput, ValuationMarginScorer  # noqa: E402


def test_missing_per_pbr_returns_neutral_score() -> None:
    result = ValuationMarginScorer().score(ValuationMarginInput(ticker="005930"))

    assert result.valuation_score == 50.0
    assert "valuation data unavailable" in result.reason


def test_historical_band_bottom_returns_high_score() -> None:
    result = ValuationMarginScorer().score(
        ValuationMarginInput(
            ticker="005930",
            per=8.0,
            pbr=0.8,
            historical_valuation_band=10.0,
            debt_ratio=60.0,
            operating_profit_growth=15.0,
            consensus_revision=10.0,
        )
    )

    assert result.valuation_score >= 70.0
    assert "historical_band" in result.reason


def test_downward_revision_and_high_per_returns_low_score() -> None:
    result = ValuationMarginScorer().score(
        ValuationMarginInput(
            ticker="000000",
            per=45.0,
            forward_per=40.0,
            pbr=5.0,
            debt_ratio=260.0,
            operating_profit_growth=-30.0,
            consensus_revision=-20.0,
            historical_valuation_band=95.0,
        )
    )

    assert result.valuation_score <= 30.0
    assert "consensus_revision" in result.reason
