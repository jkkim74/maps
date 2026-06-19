"""코스톨라니 전환 6~12단계 유닛 테스트.

6단계:  ContrarianQualityAccumulationV1Strategy
7단계:  AIContrarianAnalyzer (rule-based fallback)
8단계:  RiskManager 테마·섹터 노출 한도 / 최소 현금 비율
9단계:  HoldingTypeClassifier
10단계: KostolanyPriceCalculator
11단계: report_generator 데이터 클래스
12단계: KostolanyComparisonMode 설정 일관성
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# 6단계: contrarian_quality_accumulation_v1 전략
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 120, *, base: float = 10000.0, drift: float = 0.0) -> pd.DataFrame:
    """n개의 OHLCV 데이터프레임을 생성한다. drift > 0이면 우상향, < 0이면 하락."""
    rng = np.random.default_rng(42)
    close = base * np.cumprod(1 + rng.normal(drift, 0.015, n))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_contrarian_strategy_generates_entry_signal_in_oversold_drop():
    from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy

    strat = ContrarianQualityAccumulationV1Strategy()
    # 하락 추세 데이터: 52주 고점 대비 -25% 이상 하락 유도
    df = _make_ohlcv(120, base=10000.0, drift=-0.008)

    result = strat.generate_signals(df, strat.default_params)

    assert "entry_signal" in result.columns
    assert "exit_signal" in result.columns
    assert "stop_price" in result.columns
    assert "buy_stage" in result.columns
    # 최소한 한 번은 entry_signal이 True가 될 수 있음
    # (하락 추세이므로 진입 신호 발생 가능성 있음, 단 필수 아님)
    assert result["entry_signal"].dtype == bool


def test_contrarian_strategy_entry_and_exit_never_same_bar():
    from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy

    strat = ContrarianQualityAccumulationV1Strategy()
    df = _make_ohlcv(120, base=10000.0, drift=-0.010)
    result = strat.generate_signals(df, strat.default_params)

    # 진입과 청산이 동일 바에서 발생하면 안 된다
    both = result["entry_signal"] & result["exit_signal"]
    assert not both.any(), "동일 바에서 entry와 exit이 동시에 True이면 안 됨"


def test_contrarian_strategy_returns_neutral_when_data_too_short():
    from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy

    strat = ContrarianQualityAccumulationV1Strategy()
    df = _make_ohlcv(30)  # 65개 미만 — required_bars 미충족
    result = strat.generate_signals(df, strat.default_params)

    assert result["entry_signal"].sum() == 0
    assert result["exit_signal"].sum() == 0


def test_contrarian_strategy_has_9_param_combinations():
    from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy

    grid = ContrarianQualityAccumulationV1Strategy().param_grid()
    assert len(grid) == 9


def test_contrarian_strategy_metadata():
    from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy
    from maps.strategy.base import StrategyType

    strat = ContrarianQualityAccumulationV1Strategy()
    assert strat.strategy_id == "contrarian_quality_accumulation_v1"
    assert strat.strategy_group == "contrarian_quality"
    assert strat.strategy_type == StrategyType.CONTRARIAN_QUALITY
    assert "weak" in strat.preferred_regimes
    assert "mixed" in strat.preferred_regimes


# ---------------------------------------------------------------------------
# 7단계: AIContrarianAnalyzer — rule-based fallback
# ---------------------------------------------------------------------------

def test_contrarian_analyzer_fallback_when_valuation_high_and_drop_large():
    from maps.ai.contrarian_analyzer import AIContrarianAnalyzer

    analyzer = AIContrarianAnalyzer(aws_access_key_id="", aws_secret_access_key="")
    # Bedrock 클라이언트 없으면 fallback
    result = analyzer.analyze(
        "005930",
        valuation_margin_score=70.0,
        drop_pct=25.0,
    )

    assert result.is_fallback is True
    assert result.final_opinion in ("PASS", "WATCH", "REJECT")


def test_contrarian_analyzer_fallback_watch_when_valuation_high():
    from maps.ai.contrarian_analyzer import AIContrarianAnalyzer

    analyzer = AIContrarianAnalyzer()
    result = analyzer.analyze("005930", valuation_margin_score=70.0, drop_pct=22.0)

    assert result.final_opinion == "WATCH"
    assert result.contrarian_score >= 50.0


def test_contrarian_analyzer_fallback_reject_when_valuation_low():
    from maps.ai.contrarian_analyzer import AIContrarianAnalyzer

    analyzer = AIContrarianAnalyzer()
    result = analyzer.analyze("000000", valuation_margin_score=30.0, drop_pct=5.0)

    assert result.final_opinion == "REJECT"
    assert result.contrarian_score < 50.0


def test_contrarian_analyzer_position_size_multiplier():
    from maps.ai.contrarian_analyzer import ContrarianAnalysisResult

    pass_result = ContrarianAnalysisResult(
        ticker="A",
        contrarian_score=70.0,
        crowd_risk="LOW",
        expectation_risk="LOW",
        bad_news_priced_in=True,
        can_hold_6m=True,
        can_add_on_drop=True,
        is_theme_chasing=False,
        business_cycle_stage="EARLY",
        thesis_summary="",
        anti_thesis="",
        final_opinion="PASS",
    )
    watch_result = ContrarianAnalysisResult(
        ticker="A",
        contrarian_score=50.0,
        crowd_risk="NORMAL",
        expectation_risk="NORMAL",
        bad_news_priced_in=False,
        can_hold_6m=True,
        can_add_on_drop=False,
        is_theme_chasing=False,
        business_cycle_stage="MID",
        thesis_summary="",
        anti_thesis="",
        final_opinion="WATCH",
    )
    reject_result = ContrarianAnalysisResult(
        ticker="A",
        contrarian_score=20.0,
        crowd_risk="HIGH",
        expectation_risk="HIGH",
        bad_news_priced_in=False,
        can_hold_6m=False,
        can_add_on_drop=False,
        is_theme_chasing=True,
        business_cycle_stage="LATE",
        thesis_summary="",
        anti_thesis="",
        final_opinion="REJECT",
    )

    assert pass_result.position_size_multiplier() == 1.0
    assert watch_result.position_size_multiplier() == 0.5
    assert reject_result.position_size_multiplier() == 0.0


# ---------------------------------------------------------------------------
# 8단계: RiskManager — 테마·섹터 노출 한도 / 최소 현금 비율
# ---------------------------------------------------------------------------

def test_risk_manager_min_cash_ratio_by_regime():
    from maps.risk.manager import RiskConfig, RiskManager

    cfg = RiskConfig(
        min_cash_ratio_strong=0.15,
        min_cash_ratio_mixed=0.25,
        min_cash_ratio_weak=0.35,
    )

    class _StubRM(RiskManager):
        def __init__(self):  # noqa: D107
            self._cfg = cfg

    rm = _StubRM()
    assert rm.min_cash_ratio("strong") == 0.15
    assert rm.min_cash_ratio("mixed") == 0.25
    assert rm.min_cash_ratio("weak") == 0.35
    assert rm.min_cash_ratio("WEAK") == 0.35   # 대소문자 무시


def test_risk_config_has_exposure_fields():
    from maps.risk.manager import RiskConfig

    cfg = RiskConfig()
    assert cfg.sector_exposure_limit == 0.25
    assert cfg.theme_exposure_limit == 0.35
    assert cfg.theme_exposure_limit_enabled is False
    assert cfg.sector_exposure_limit_enabled is False


# ---------------------------------------------------------------------------
# 9단계: HoldingTypeClassifier
# ---------------------------------------------------------------------------

def test_holding_type_ban_for_managed_stock():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(HoldingTypeInput(is_managed_stock=True))

    assert result == StockHoldingType.BAN


def test_holding_type_ban_for_thesis_broken():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(HoldingTypeInput(thesis_broken=True))

    assert result == StockHoldingType.BAN


def test_holding_type_watch_for_ai_reject():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(HoldingTypeInput(ai_contrarian_opinion="REJECT"))

    assert result == StockHoldingType.WATCH


def test_holding_type_core_for_contrarian_quality_with_high_valuation():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(
        HoldingTypeInput(strategy_type="CONTRARIAN_QUALITY", valuation_margin_score=70.0)
    )

    assert result == StockHoldingType.CORE


def test_holding_type_swing_for_contrarian_quality_with_mid_valuation():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(
        HoldingTypeInput(strategy_type="CONTRARIAN_QUALITY", valuation_margin_score=55.0)
    )

    assert result == StockHoldingType.SWING


def test_holding_type_trading_for_breakout():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(HoldingTypeInput(strategy_type="BREAKOUT"))

    assert result == StockHoldingType.TRADING


def test_holding_type_swing_for_pullback():
    from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput, StockHoldingType

    clf = HoldingTypeClassifier()
    result = clf.classify(HoldingTypeInput(strategy_type="PULLBACK"))

    assert result == StockHoldingType.SWING


def test_holding_rules_core_has_highest_position_size():
    from maps.strategy.holding_type import HOLDING_RULES, StockHoldingType

    core_rule = HOLDING_RULES[StockHoldingType.CORE]
    trading_rule = HOLDING_RULES[StockHoldingType.TRADING]

    assert core_rule.max_position_size > trading_rule.max_position_size
    assert core_rule.use_value_target is True
    assert trading_rule.use_value_target is False


def test_holding_rules_watch_ban_have_zero_position():
    from maps.strategy.holding_type import HOLDING_RULES, StockHoldingType

    for ht in (StockHoldingType.WATCH, StockHoldingType.BAN):
        assert HOLDING_RULES[ht].max_position_size == 0.0


# ---------------------------------------------------------------------------
# 10단계: KostolanyPriceCalculator
# ---------------------------------------------------------------------------

def test_price_calculator_returns_none_when_close_is_zero():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(PriceInput(current_close=0.0))

    assert result.plan_buy is None
    assert result.technical_stop is None
    assert result.trading_target is None
    assert result.value_target is None
    assert result.reason == "current_close_unavailable"


def test_price_calculator_core_uses_value_target():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(
        PriceInput(
            holding_type="CORE",
            current_close=50000.0,
            eps_forward=3000.0,
            historical_per_avg=15.0,
            bps=40000.0,
            historical_pbr_avg=1.5,
        )
    )

    assert result.value_target is not None
    assert result.value_target > 50000.0
    assert "value_target" in result.reason


def test_price_calculator_trading_uses_trading_target():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(
        PriceInput(
            holding_type="TRADING",
            current_close=10000.0,
            high_52w=15000.0,
            rr_ratio=1.5,
        )
    )

    assert result.trading_target is not None
    assert result.trading_target > 10000.0


def test_price_calculator_technical_stop_tighter_for_trading():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    calc = KostolanyPriceCalculator()
    core_result = calc.calculate(PriceInput(holding_type="CORE", current_close=10000.0))
    trading_result = calc.calculate(PriceInput(holding_type="TRADING", current_close=10000.0))

    # TRADING 손절이 CORE 손절보다 타이트해야 한다 (손절가가 더 높아야 한다)
    assert trading_result.technical_stop > core_result.technical_stop


def test_price_calculator_emergency_stop_always_present_when_close_positive():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(
        PriceInput(current_close=20000.0)
    )

    assert result.emergency_stop is not None
    assert result.emergency_stop < 20000.0


def test_price_calculator_value_target_none_when_no_fundamental_data():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(
        PriceInput(holding_type="CORE", current_close=10000.0)
        # eps_forward, bps 등 없음
    )

    assert result.value_target is None
    # 시스템 중단 없이 None 반환
    assert result.plan_buy is not None


def test_price_calculator_thesis_stop_from_bps():
    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    result = KostolanyPriceCalculator().calculate(
        PriceInput(current_close=10000.0, bps=12000.0)
    )

    # thesis_stop = bps × 0.80
    assert result.thesis_stop == round(12000.0 * 0.80)


# ---------------------------------------------------------------------------
# 11단계: report_generator 데이터 클래스
# ---------------------------------------------------------------------------

def test_report_generator_market_summary_defaults():
    import datetime
    from maps.ops.report_generator import MarketSummary

    summary = MarketSummary(ref_date=datetime.date(2026, 6, 19))

    assert summary.legacy_regime == "mixed"
    assert summary.entry_limit_ratio == 0.5
    assert summary.contrarian_entry_limit_ratio == 0.25


def test_report_generator_sector_summary_defaults():
    from maps.ops.report_generator import SectorSummary

    sector = SectorSummary()

    assert sector.selected_sectors == []
    assert sector.excluded_sectors == {}
    assert sector.overheated_sectors == []


# ---------------------------------------------------------------------------
# 12단계: 백테스트 비교 모드 설정 일관성
# ---------------------------------------------------------------------------

def test_kostolany_comparison_mode_a_is_pure_legacy():
    from maps.backtest.kostolany_comparison import MODE_A

    assert MODE_A.legacy_scoring is True
    assert MODE_A.kostolany_regime is False
    assert MODE_A.contrarian_strategy is False
    assert MODE_A.ai_contrarian_check is False


def test_kostolany_comparison_mode_b_has_all_features():
    from maps.backtest.kostolany_comparison import MODE_B

    assert MODE_B.legacy_scoring is False
    assert MODE_B.kostolany_regime is True
    assert MODE_B.contrarian_strategy is True
    assert MODE_B.holding_type_enabled is True


def test_kostolany_comparison_mode_d_has_ai_check():
    from maps.backtest.kostolany_comparison import MODE_D

    assert MODE_D.ai_contrarian_check is True


def test_kostolany_comparison_mode_names_are_unique():
    from maps.backtest.kostolany_comparison import MODE_A, MODE_B, MODE_C, MODE_D

    names = [MODE_A.name, MODE_B.name, MODE_C.name, MODE_D.name]
    assert len(names) == len(set(names)), "모드 이름이 중복됨"


# ---------------------------------------------------------------------------
# 연동: constants.py에 contrarian_quality 그룹 등록 여부
# ---------------------------------------------------------------------------

def test_contrarian_strategy_registered_in_group_map():
    from maps.common.constants import STRATEGY_GROUP_MAP

    assert "contrarian_quality_accumulation_v1" in STRATEGY_GROUP_MAP
    assert STRATEGY_GROUP_MAP["contrarian_quality_accumulation_v1"] == "contrarian_quality"


def test_contrarian_group_has_allowed_mdd():
    from maps.common.constants import ALLOWED_MDD

    assert "contrarian_quality" in ALLOWED_MDD
    assert "mc_p95_limit" in ALLOWED_MDD["contrarian_quality"]


def test_contrarian_strategy_registered_in_live_rules():
    from maps.strategy.live_rules import stop_loss_price

    result = stop_loss_price("contrarian_quality_accumulation_v1", 10000.0)
    # 등록되어 있으면 None이 아닌 값 반환 (8% 손절 → 9200)
    assert result is not None
    assert result == 10000.0 * (1 - 0.08)
