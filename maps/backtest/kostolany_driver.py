"""코스톨라니 vs 레거시 전략단위 백테스트 드라이버.

`kostolany_comparison.py`의 모드/시나리오/집계 harness에 실제 `BacktestEngine` 실행을
주입한다. 시나리오를 실제 KRX 기간 윈도우로 매핑하고, 각 모드에 속한 전략들을 표본 종목에
돌려 모드별 성과(`ModeResult`)를 집계한다.

한계(전략단위 드라이버의 의도된 범위):
  - 모드 차이 중 regime/sector/scoring/AI 검증은 후보 *선정*에 영향하므로 순수 엔진에는
    반영되지 않는다. 실질적으로 구분되는 축은 contrarian_quality 전략 포함 여부다
    (MODE_A/C = 기본 전략, MODE_B/D = 기본 + contrarian).
  - contrarian 전략의 valuation_margin_score≥65 게이트는 스케줄러/주문 단계 필터이므로
    엔진 백테스트에는 적용되지 않는다(기술적 진입만 반영 → 거래빈도 상한).
  - 포트폴리오 수준 지표(현금비중·테마노출)는 산출 불가 → avg_cash_ratio=None.

실주문은 발생하지 않는다(`BacktestEngine`은 순수 시뮬레이션). dry-run 안전.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from maps.backtest.engine import BacktestEngine, BacktestResult
from maps.backtest.kostolany_comparison import (
    ALL_MODES,
    SCENARIOS,
    ComparisonModeConfig,
    ComparisonResult,
    ModeResult,
    ScenarioSpec,
)
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.base import BaseStrategy
from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy

logger = logging.getLogger(__name__)

# 기본(레거시 포함 전 모드 공통) 전략군
_BASE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3": PullbackV3Strategy,
    "pullback_v2": PullbackV2Strategy,
    "ath_breakout_v1": ATHBreakoutV1Strategy,
    "ath_breakout_v2": ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1": DonchianV1Strategy,
    "donchian_v2": DonchianV2Strategy,
}
# 코스톨라니 모드에서만 추가되는 역발상 전략
_CONTRARIAN_STRATEGY: dict[str, type[BaseStrategy]] = {
    "contrarian_quality_accumulation_v1": ContrarianQualityAccumulationV1Strategy,
}
_ALL_STRATEGIES: dict[str, type[BaseStrategy]] = {**_BASE_STRATEGIES, **_CONTRARIAN_STRATEGY}


# 추상 시나리오 → 실제 KRX 기간 윈도우 (로컬 데이터 2018-02 ~ 2026-05 범위 내).
# 각 윈도우는 해당 국면을 대표하는 KOSPI 실제 구간으로 매핑한다.
SCENARIO_WINDOWS: dict[str, tuple[dt.date, dt.date]] = {
    "crash":                  (dt.date(2020, 1, 20), dt.date(2020, 4, 30)),   # COVID 급락
    "post_crash_rebound":     (dt.date(2020, 4, 1),  dt.date(2020, 12, 31)),  # V자 반등
    "box_range":              (dt.date(2019, 4, 1),  dt.date(2019, 12, 31)),  # 박스권
    "high_vol_sideways":      (dt.date(2018, 2, 1),  dt.date(2018, 12, 31)),  # 고변동 약세
    "retail_bubble":          (dt.date(2020, 11, 1), dt.date(2021, 6, 30)),   # 개인 과열(KOSPI 3000)
    "stable_bull":            (dt.date(2023, 1, 1),  dt.date(2023, 7, 31)),   # 안정 상승
    "mixed_bear":             (dt.date(2022, 8, 1),  dt.date(2022, 12, 31)),  # 혼조 약세
    "rate_hike":              (dt.date(2022, 1, 1),  dt.date(2022, 10, 31)),  # 금리 인상
    "correction_after_surge": (dt.date(2021, 7, 1),  dt.date(2022, 6, 30)),   # 급등 후 조정
}


def mode_strategy_ids(mode: ComparisonModeConfig) -> list[str]:
    """모드 설정에 따라 실행할 전략 ID 목록을 반환한다.

    contrarian_strategy=True 인 모드만 contrarian_quality 전략을 포함한다.
    나머지 축(regime/scoring/sector/AI)은 후보 선정 단계 영향이라 전략 집합을 바꾸지 않는다.
    """
    ids = list(_BASE_STRATEGIES.keys())
    if mode.contrarian_strategy:
        ids += list(_CONTRARIAN_STRATEGY.keys())
    return ids


@dataclass
class _Agg:
    """집계 누적기."""

    cagrs: list[float]
    sharpes: list[float]
    win_rates: list[float]
    mdds: list[float]
    trade_count: int
    hold_days: list[float]


def _aggregate(mode_name: str, scenario_name: str, results: list[BacktestResult]) -> ModeResult:
    """모드에 속한 전략·종목 백테스트 결과들을 단일 ModeResult로 집계한다."""
    valid = [r for r in results if r.total_trades > 0]
    if not valid:
        return ModeResult(
            mode_name=mode_name,
            scenario_name=scenario_name,
            trade_count=0,
            error="유효 거래 없음 (윈도우 내 신호 미발생)",
        )

    hold_days: list[float] = []
    for r in valid:
        for tr in r.trade_list:
            hold_days.append(float((tr.exit_date - tr.entry_date).days))

    n = len(valid)
    return ModeResult(
        mode_name=mode_name,
        scenario_name=scenario_name,
        cagr=sum(r.cagr for r in valid) / n,
        mdd=min(r.mdd for r in valid),  # 최악(가장 깊은) MDD
        sharpe=sum(r.sharpe for r in valid) / n,
        win_rate=sum(r.win_rate for r in valid) / n,
        avg_hold_days=(sum(hold_days) / len(hold_days)) if hold_days else None,
        trade_count=sum(r.total_trades for r in valid),
        avg_cash_ratio=None,  # 포트폴리오 수준 지표 — 전략단위 드라이버에서는 N/A
    )


def run_comparison(
    db: Session,
    *,
    engine: BacktestEngine | None = None,
    scenarios: list[ScenarioSpec] | None = None,
    sample_size: int = 40,
) -> list[ComparisonResult]:
    """시나리오 × 모드 전략단위 백테스트를 실행해 비교 결과를 반환한다.

    효율을 위해 시나리오별로 전 전략 결과를 한 번만 계산하고(모드 간 공유),
    각 모드는 자신의 전략 부분집합만 골라 집계한다.
    """
    engine = engine or BacktestEngine()
    scenarios = scenarios or SCENARIOS
    repo = HistoricalOHLCVRepository(db)

    comparison_results: list[ComparisonResult] = []

    for spec in scenarios:
        window = SCENARIO_WINDOWS.get(spec.name)
        if window is None:
            logger.warning("시나리오 '%s' 윈도우 미정의 — 스킵", spec.name)
            continue
        start, end = window

        # 윈도우 내 충분한 봉을 가진 표본 종목 선정
        min_window_bars = 40
        tickers = repo.list_tickers_with_history(
            start=start, end=end, min_bars=min_window_bars
        )[:sample_size]

        # 시나리오별로 전 전략 결과를 1회만 계산 (모드 간 재사용)
        per_strategy: dict[str, list[BacktestResult]] = {sid: [] for sid in _ALL_STRATEGIES}
        for sid, strategy_cls in _ALL_STRATEGIES.items():
            strategy = strategy_cls()
            params = strategy.default_params
            min_bars = strategy.required_bars(params)
            for ticker in tickers:
                df = repo.to_dataframe(ticker, start=start, end=end)
                if len(df) < max(min_bars, min_window_bars):
                    continue
                try:
                    r = engine.run(strategy, params, df)
                except Exception as exc:  # noqa: BLE001 — 단일 종목 실패는 스킵
                    logger.debug("백테스트 실패 [%s %s %s]: %s", spec.name, sid, ticker, exc)
                    continue
                if r.total_trades > 0:
                    per_strategy[sid].append(r)

        cr = ComparisonResult(scenario_name=spec.name)
        for mode in ALL_MODES:
            mode_results: list[BacktestResult] = []
            for sid in mode_strategy_ids(mode):
                mode_results.extend(per_strategy.get(sid, []))
            cr.results.append(_aggregate(mode.name, spec.name, mode_results))
        comparison_results.append(cr)
        logger.info(
            "시나리오 '%s' (%s~%s) 완료: 표본 %d종목",
            spec.name, start, end, len(tickers),
        )

    return comparison_results
