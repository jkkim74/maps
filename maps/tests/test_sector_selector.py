from maps.market.regime import (
    MarketRegimeResult,
    RegimeLabel,
    RegimeResult,
    VolRegimeLabel,
    WeeklyTrendLabel,
)
from maps.market.sector_selector import SectorRegimeSelector, SectorScorer


def _regime(regime: RegimeLabel, vol: VolRegimeLabel = VolRegimeLabel.NORMAL, liquidity: float = 50.0):
    return RegimeResult(
        regime=regime,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=1.0,
        kospi_ts=None,
        vol_regime=vol,
        composite=MarketRegimeResult(
            legacy_regime=regime.value,
            composite_regime=regime.value,
            price_trend_score=50.0,
            volatility_score=50.0,
            liquidity_score=liquidity,
            foreign_fx_score=50.0,
            psychology_score=50.0,
            final_market_score=50.0,
            reason="test",
        ),
    )


def _scores():
    scorer = SectorScorer()
    return [
        scorer.score(
            sector="반도체",
            momentum_20d=0.18,
            momentum_60d=0.22,
            earnings_revision=80.0,
            flow_improvement=85.0,
            valuation_attractiveness=55.0,
            turnover_growth=90.0,
        ),
        scorer.score(
            sector="유틸리티",
            momentum_20d=0.02,
            momentum_60d=0.03,
            earnings_revision=55.0,
            flow_improvement=45.0,
            valuation_attractiveness=60.0,
            turnover_growth=55.0,
        ),
        scorer.score(
            sector="2차전지",
            momentum_20d=0.20,
            momentum_60d=0.15,
            earnings_revision=45.0,
            flow_improvement=80.0,
            valuation_attractiveness=30.0,
            overheat=85.0,
            turnover_growth=95.0,
        ),
    ]


def test_strong_selects_top_momentum_sector():
    result = SectorRegimeSelector(top_n=2).select_from_scores(
        _scores(),
        _regime(RegimeLabel.STRONG),
    )

    assert "반도체" in result.selected_sectors
    assert result.reason.startswith("strong")


def test_weak_prioritizes_defensive_sector():
    result = SectorRegimeSelector(top_n=2).select_from_scores(
        _scores(),
        _regime(RegimeLabel.WEAK),
    )

    assert result.selected_sectors[0] == "유틸리티"


def test_weak_high_blocks_breakout_momentum_sectors():
    result = SectorRegimeSelector(top_n=2).select_from_scores(
        _scores(),
        _regime(RegimeLabel.WEAK, VolRegimeLabel.HIGH),
    )

    assert result.selected_sectors == ["유틸리티"]
    assert result.excluded_sectors["반도체"] == "weak_high_blocks_new_momentum_sector"


def test_liquidity_improvement_adds_next_leaders_to_watchlist():
    result = SectorRegimeSelector(top_n=2).select_from_scores(
        _scores(),
        _regime(RegimeLabel.WEAK, liquidity=70.0),
    )

    assert "반도체" in result.watchlist_sectors
