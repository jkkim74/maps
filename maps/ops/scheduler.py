"""Operational scheduler for data, candidates, validation, orders, and EOD.

The scheduler deliberately keeps live ordering behind
MAPS_LIVE_TRADING_ENABLED.  In paper/mock mode the order job only syncs
broker state and records an audit log.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func
from sqlalchemy.orm import Session, selectinload

from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.account_history import account_history_start_utc_naive, utc_datetime_to_kst_date
from maps.common.db import SessionLocal
from maps.common.sizing import risk_based_qty
from maps.common.models import (
    AnalysisPick,
    AnalysisPickLeg,
    BacktestRunLog,
    CandidateSnapshot,
    CollectionLog,
    HistoricalOHLCV,
    JobRunLog,
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    PortfolioSnapshot,
    PromotionHistory,
    OrderLog,
    SecurityMetadata,
    WalkForwardFoldResults,
    WalkForwardResults,
)
from maps.common.settings import MapsSettings, get_settings
from maps.backtest.engine import BacktestEngine, BacktestResult, _compute_atr14
from maps.common.exceptions import (
    BacktestError,
    BrokerAdapterError,
    DuplicateOrderError,
    ExposureCapError,
    KillSwitchError,
    ValidationError,
)
from maps.data.collector import DataCollector
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.data.krx_adapter import CollectionResult, KRXAdapter, MockKRXAdapter, SecurityMeta
from maps.data.security_repo import HaltPeriod, ManagedPeriod, Security
from maps.data_quality.universe_filter import DataQualityFilter, UniverseResult
from maps.execution.broker_adapter import (
    BrokerAdapter,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    get_broker,
)
from maps.execution.order_manager import OrderManager
from maps.market.breadth import classify_breadth, compute_pct_above_ma
from maps.market.regime import RegimeResult, WeeklyTrendLabel, create_regime_analyzer
from maps.market.regime_history import apply_hysteresis
from maps.market.sector_selector import SectorRegimeSelector, SectorSelector
from maps.market.trading_rules import (
    is_krx_closed_date,
    previous_trading_day,
    round_to_krx_tick,
    round_up_krx_price,
)
from maps.ops.notifications import Notification, SlackNotifier
from maps.ops.candidate_selection import (
    candidate_min_score_expression,
    candidate_recommendation_eligible_expression,
)
from maps.ops.order_state import claimed_candidate_tickers
from maps.ops.pick_freshness import is_pick_stale, pick_cutoff_date
from maps.promotion.gate import PromotionGate, PromotionStage
from maps.risk.manager import RiskConfig, RiskManager
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.live_rules import effective_stop_price
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.base import BaseStrategy, StrategyType
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.scoring import LegacyFinalScoreCalculator, StrategyAwareScoreCalculator, StrategyScoreInput
from maps.indicator.trend_strength import TrendStrengthCalculator
from maps.ai.scoring_service import AIScoringRunSummary, AIStockScoringService
from maps.ai.valuation_margin import ValuationMarginScorer
from maps.data.fundamental_repo import FundamentalValuationProvider
from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput
from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput
from maps.stock_report.runner import run_all_reports_if_idle
from maps.validation.monte_carlo import MonteCarloValidator
from maps.validation.plateau import ParameterPlateauTester
from maps.validation.walk_forward import WalkForwardAnalyzer

logger = logging.getLogger(__name__)

# ── KRX 거래일 캐시 ────────────────────────────────────────────────────────────
_krx_market_day_cache: dict[dt.date, bool] = {}


def _is_krx_market_day(date: dt.date | None = None) -> bool:
    """주어진 날짜가 KRX 거래일인지 확인한다.

    1. 토/일 → 즉시 False (빠른 경로)
    2. 평일 → pykrx로 한국 공휴일 여부 확인
    3. pykrx 조회 실패 시 True 반환(폴백)으로 스케쥴러가 멈추지 않도록 한다.
    결과는 날짜 단위로 캐싱해 interval 잡의 반복 호출 비용을 낮춘다.
    """
    target = date or dt.date.today()
    if target in _krx_market_day_cache:
        return _krx_market_day_cache[target]

    # 주말 체크 (토=5, 일=6)
    if is_krx_closed_date(target, extra_closed_dates=get_settings().krx_closed_dates):
        _krx_market_day_cache[target] = False
        return False

    # pykrx로 한국 공휴일 체크
    # Live OHLCV does not exist before the session opens. Weekdays that are
    # not known closure dates must remain runnable for pre-open order jobs.
    if target >= dt.date.today():
        return True

    date_str = target.strftime("%Y%m%d")
    try:
        from maps.data.krx_auth import ensure_krx_login_guard  # noqa: PLC0415

        ensure_krx_login_guard()
        from pykrx import stock as _pykrx_stock  # noqa: PLC0415 — lazy import
        df = _pykrx_stock.get_index_ohlcv(date_str, date_str, "1001")  # KOSPI
        result = len(df) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("KRX 거래일 확인 실패, 거래일로 간주합니다: %s", exc)
        result = True

    _krx_market_day_cache[target] = result
    return result


_RUNNABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3": PullbackV3Strategy,
    "pullback_v2": PullbackV2Strategy,
    "ath_breakout_v1": ATHBreakoutV1Strategy,
    "ath_breakout_v2": ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1": DonchianV1Strategy,
    "donchian_v2": DonchianV2Strategy,
    "contrarian_quality_accumulation_v1": ContrarianQualityAccumulationV1Strategy,
}

_VALIDATION_SAMPLE_TICKERS = 5

# 전략 신호 계산에 쓰는 봉 수. 후보 생성과 주문 시점이 **같은 값**을 써야 한다 —
# 워밍업 길이가 다르면 ATR 이 조용히 어긋난다 (CLAUDE.md 손절 항목).
_SIGNAL_LOOKBACK_BARS = 400

# WFA 에 사용할 선호 ticker 우선순위.
# 알파벳순 첫 번째(소형주)가 아닌 유동성 높은 대형주로 WFA 를 실행하여
# 전략 성과의 대표성을 높인다.
_WFA_PREFERRED_TICKERS: list[str] = [
    "005930",  # 삼성전자 (KOSPI 시총 1위)
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "068270",  # 셀트리온
    "207940",  # 삼성바이오로직스
    "006400",  # 삼성SDI
]


@dataclass
class JobRun:
    name: str
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TickerContext:
    """종목당 한 번만 계산하는 값들 — 전략 8개가 공유한다.

    전략마다 유니버스를 다시 도는 구조에서 종목별 OHLCV 를 8번 읽던 것을 1번으로 줄인다
    (운영 실측 하루 약 10,080회 → 1,260회). 전략에 **무관한** 값만 담는다 — 전략별
    점수·신호는 `_save_candidate_snapshot` 이 이 컨텍스트를 받아 계산한다.
    """

    frame: pd.DataFrame
    trend_strength: float
    ts_bucket: str
    close: float
    atr14: float | None
    valuation: object | None = None


@dataclass(frozen=True)
class StrategySignal:
    """Latest live decision produced by a strategy."""

    entry_signal: bool
    exit_signal: bool
    close: float
    atr14: float | None = None


def plan_exit_decision(
    *,
    current_price: float,
    entry_price: float,
    hwm: float,
    emergency_stop: float | None,
    technical_stop: float | None,
    target: float | None,
    fallback_stop: float | None,
    strategy_exit: bool,
    trail_activate_pct: float,
    trail_stop_pct: float,
) -> tuple[bool, str | None]:
    """매매계획 기반 전량 청산 판정(순수 함수).

    우선순위: 긴급손절 → 계획/폴백 손절 → 트레일링 → 목표 익절 → 전략 exit.
    반환: (청산 여부, 사유). 가격이 유효하지 않으면 청산하지 않는다.
    """
    if current_price is None or current_price <= 0:
        return False, None
    # 1) 긴급 손절 (하드 플로어)
    if emergency_stop and current_price <= emergency_stop:
        return True, "emergency_stop"
    # 2) 계획 손절(우선) 또는 기존 %/ATR 폴백
    effective_stop = technical_stop if (technical_stop and technical_stop > 0) else fallback_stop
    if effective_stop and current_price <= effective_stop:
        return True, "plan_stop" if (technical_stop and technical_stop > 0) else "stop_loss"
    # 3) 트레일링 스탑 — 수익 구간 진입(고점 ≥ 진입×(1+활성%)) 후 고점 대비 이탈
    if (
        entry_price > 0
        and trail_stop_pct > 0
        and hwm >= entry_price * (1.0 + trail_activate_pct)
        and current_price <= hwm * (1.0 - trail_stop_pct)
    ):
        return True, "trailing_stop"
    # 4) 목표 익절
    if target and current_price >= target:
        return True, "take_profit"
    # 5) 전략 exit 신호
    if strategy_exit:
        return True, "strategy_exit"
    return False, None


class OperationalPipeline:
    """Runs the individual MAPS operational steps."""

    def __init__(
        self,
        *,
        settings: MapsSettings | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        notifier: SlackNotifier | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._notifier = notifier or SlackNotifier(self._settings)
        self._last_collection: CollectionResult | None = None
        self._last_universe: UniverseResult | None = None

    @property
    def last_collection(self) -> CollectionResult | None:
        return self._last_collection

    @property
    def last_universe(self) -> UniverseResult | None:
        return self._last_universe

    def collect_data(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            collector = DataCollector(self._make_krx_adapter(), db)
            result = collector.collect_daily(ref_date)
            self._last_collection = result
            return {
                "ref_date": ref_date.isoformat(),
                "ohlcv_count": len(result.ohlcv),
                "meta_count": len(result.meta),
                "halt_count": len(result.halts),
                "managed_count": len(result.managed),
            }

        return self._job("data_collection", _run)

    def generate_candidates(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            collection = self._last_collection
            if collection is None or collection.ref_date != ref_date:
                collector = DataCollector(self._make_krx_adapter(), db)
                collection = collector.collect_daily(ref_date)
                self._last_collection = collection

            candidates = self._to_securities(db, collection.meta, collection, ref_date)
            result = DataQualityFilter(db=db, mode="live").generate(ref_date, candidates)
            # 등록된 모든 전략에 동일한 유니버스 스냅샷을 저장한다.
            # DataQualityFilter 유니버스는 유동성·데이터 품질 기반 공통 후보군이므로
            # 전략별로 별도 필터링 없이 공유할 수 있다.
            # 각 전략의 진입 신호는 order_cycle 에서 generate_signals() 로 별도 계산된다.
            # 시황 분석 → weekly_pass 결정
            regime = self._analyze_regime()
            weekly_pass = (regime.weekly_trend == WeeklyTrendLabel.PASS)
            logger.info(
                "시황 분석: regime=%s trend=%s entry_limit=%.2f → weekly_pass=%s",
                regime.regime.value,
                regime.weekly_trend.value,
                regime.entry_limit_ratio,
                weekly_pass,
            )

            # 시장폭(breadth) 가드: 빌려온 MIXED + 좁은 장이면 추격성 전략 보류.
            # 가드 비활성화 시 breadth는 UNKNOWN으로 남아 가드가 작동하지 않는다.
            breadth_pct: float | None = None
            if self._settings.maps_breadth_guard_enabled:
                breadth_pct = compute_pct_above_ma(
                    db,
                    ref_date,
                    ma_window=self._settings.maps_breadth_ma_window,
                    min_tickers=self._MIN_FRESH_TICKERS,
                )
                regime.breadth = classify_breadth(
                    breadth_pct,
                    weak_threshold=self._settings.maps_breadth_weak_threshold,
                )
                if regime.floor_applied:
                    logger.info(
                        "시장폭 분석: pct=%s breadth=%s floor_applied=%s",
                        f"{breadth_pct:.3f}" if breadth_pct is not None else "n/a",
                        regime.breadth.value,
                        regime.floor_applied,
                    )

            # 히스테리시스: buffer band·전일 유지·floor 2일 확인을 적용하고 이력을 기록한다.
            regime = apply_hysteresis(
                db, regime, ref_date,
                source="candidate_generation",
                breadth_pct=breadth_pct,
            )
            regime_label = regime.regime.value  # "strong" | "mixed" | "weak"

            # 업종 필터 (MAPS_SECTOR_FILTER_ENABLED=true 일 때만 적용)
            sector_filter_enabled = self._settings.maps_sector_filter_enabled
            filtered_universe = list(result.universe)
            strong_sectors: list[str] = []
            excluded_sectors: dict[str, str] = {}
            watchlist_sectors: list[str] = []
            sector_scores: dict[str, float] = {}
            overheated_sectors: list[str] = []
            sector_selection_reason: str | None = None
            sector_excluded_reason_by_ticker: dict[str, str] = {}
            if sector_filter_enabled:
                if self._settings.maps_sector_kostolany_mode_enabled:
                    selector = SectorRegimeSelector(
                        lookback_days=self._settings.maps_sector_lookback_days,
                        top_n=self._settings.maps_sector_top_n,
                    )
                    sector_result = selector.select(db, ref_date, regime)
                    strong_sectors = sector_result.selected_sectors
                    excluded_sectors = sector_result.excluded_sectors
                    watchlist_sectors = sector_result.watchlist_sectors
                    sector_scores = {
                        sector: score.score
                        for sector, score in sector_result.sector_scores.items()
                    }
                    overheated_sectors = sector_result.overheated_sectors
                    sector_selection_reason = sector_result.reason
                else:
                    selector = SectorSelector(
                        lookback_days=self._settings.maps_sector_lookback_days,
                        top_n=self._settings.maps_sector_top_n,
                    )
                    strong_sectors = selector.select_strong_sectors(db, ref_date, regime)
                    sector_selection_reason = "legacy sector selector"
                if strong_sectors:
                    pre_count = len(filtered_universe)
                    selected_sector_set = set(strong_sectors)
                    for stock in filtered_universe:
                        stock_sector = getattr(stock, "sector", None)
                        if stock_sector not in selected_sector_set:
                            sector_excluded_reason_by_ticker[stock.ticker] = (
                                excluded_sectors.get(stock_sector or "")
                                or f"sector_filter_excluded:{stock_sector or 'unknown'}"
                            )
                    filtered_universe = [
                        s for s in filtered_universe
                        if getattr(s, "sector", None) in selected_sector_set
                    ]
                    logger.info(
                        "업종 필터 적용: %d → %d종목 (강세업종=%s)",
                        pre_count, len(filtered_universe), strong_sectors,
                    )

            # 관측·주문 분리: 스냅샷은 장세와 무관하게 항상 저장한다(관측 지속).
            # 진입 차단(preferred_regimes·entry_policy)은 blocked_strategies로 기록하고
            # 실제 주문 차단은 order_cycle의 주문 시점 재검사에서 수행한다.
            saved_count = 0
            active_strategies: list[str] = []
            blocked_strategies: list[dict[str, str]] = []
            # 종목별 OHLCV·추세강도·밸류에이션은 전략과 무관하다 — 루프 **밖에서** 한 번만.
            ticker_contexts = self._build_ticker_contexts(db, result.universe, ref_date)
            funnel_stats: dict[str, int] = {}
            for strategy_id, strategy_cls in _RUNNABLE_STRATEGIES.items():
                entry_block_reason: str | None = None
                if regime_label not in strategy_cls.preferred_regimes:
                    entry_block_reason = f"preferred_regime_mismatch:{regime_label}"
                    blocked_strategies.append({
                        "strategy_id": strategy_id,
                        "strategy_type": getattr(getattr(strategy_cls, "strategy_type", None), "value", "MOMENTUM"),
                        "reason": entry_block_reason,
                    })
                else:
                    policy = regime.entry_policy_for_strategy(
                        getattr(strategy_cls, "strategy_type", None),
                        contrarian_enabled=self._settings.maps_contrarian_accumulation_enabled,
                        contrarian_entry_limit_ratio=self._settings.maps_contrarian_max_entry_ratio,
                    )
                    if not policy.allowed:
                        entry_block_reason = policy.reason
                        blocked_strategies.append({
                            "strategy_id": strategy_id,
                            "strategy_type": policy.strategy_type,
                            "reason": policy.reason,
                        })
                if entry_block_reason is not None:
                    logger.info(
                        "전략 진입 차단 [%s]: %s — 스냅샷은 저장(관측 지속)",
                        strategy_id,
                        entry_block_reason,
                    )
                saved_count += self._save_candidate_snapshot(
                    db,
                    ref_date,
                    strategy_id,
                    result.universe,
                    weekly_pass=weekly_pass,
                    excluded_reason_by_ticker=sector_excluded_reason_by_ticker,
                    contexts=ticker_contexts,
                    stats=funnel_stats,
                )
                if entry_block_reason is None:
                    active_strategies.append(strategy_id)

            ai_summary = AIScoringRunSummary()
            if self._settings.maps_ai_scoring_mode != "off":
                frames = {
                    ticker: context.frame
                    for ticker, context in ticker_contexts.items()
                }
                ai_summary = AIStockScoringService(settings=self._settings).apply(
                    db,
                    ref_date,
                    frames,
                    set(active_strategies),
                )
                logger.info(
                    "AI scoring: mode=%s model=%s targets=%d calls=%d "
                    "cache_hits=%d success=%d failed=%d skipped_limit=%d "
                    "input_tokens=%d output_tokens=%d",
                    self._settings.maps_ai_scoring_mode,
                    self._settings.maps_ai_scoring_model_id,
                    ai_summary.targets,
                    ai_summary.calls,
                    ai_summary.cache_hits,
                    ai_summary.successes,
                    ai_summary.failures,
                    ai_summary.skipped_limit,
                    ai_summary.input_tokens,
                    ai_summary.output_tokens,
                )
            self._last_universe = result
            return {
                "ref_date": ref_date.isoformat(),
                "total_candidates": len(candidates),
                "kept_count": len(result.universe),
                "rejected_count": len(result.rejected),
                "rejection_ratio": round(result.rejection_ratio, 4),
                "sector_filter_enabled": sector_filter_enabled,
                "strong_sectors": strong_sectors,
                "excluded_sectors": excluded_sectors,
                "watchlist_sectors": watchlist_sectors,
                "sector_scores": sector_scores,
                "overheated_sectors": overheated_sectors,
                "sector_selection_reason": sector_selection_reason,
                "sector_filtered_count": len(filtered_universe),
                "floor_applied": regime.floor_applied,
                "breadth_pct": round(breadth_pct, 4) if breadth_pct is not None else None,
                "breadth_label": regime.breadth.value,
                "saved_count": saved_count,
                # 후보가 0건일 때 어느 단계에서 끊겼는지 구분하기 위한 카운터.
                # 유니버스가 0인지, 신호가 0인지, 상위 N에서 잘린 것인지가 갈린다.
                "universe_size": len(result.universe),
                "signal_count": funnel_stats.get("signals", 0),
                "dropped_count": funnel_stats.get("dropped", 0),
                "strategies_saved": list(_RUNNABLE_STRATEGIES.keys()),
                "strategies_updated": active_strategies,
                "strategies_blocked": blocked_strategies,
                "strategies_skipped_regime": [
                    sid for sid, cls in _RUNNABLE_STRATEGIES.items()
                    if regime_label not in cls.preferred_regimes
                ],
                "ai_targets": ai_summary.targets,
                "ai_calls": ai_summary.calls,
                "ai_cache_hits": ai_summary.cache_hits,
                "ai_successes": ai_summary.successes,
                "ai_failures": ai_summary.failures,
                "ai_skipped_limit": ai_summary.skipped_limit,
                "ai_input_tokens": ai_summary.input_tokens,
                "ai_output_tokens": ai_summary.output_tokens,
            }

        return self._job("candidate_generation", _run)

    def backfill_ohlcv(self, start: dt.date, end: dt.date) -> JobRun:
        def _run(db: Session) -> dict:
            collector = DataCollector(self._make_krx_adapter(), db)
            return collector.collect_ohlcv_history(start, end)

        return self._job("ohlcv_backfill", _run)

    def backfill_fundamentals(self, start: dt.date, end: dt.date) -> JobRun:
        """기간 펀더멘털(PER/PBR/EPS/BPS)을 백필한다.

        역사적 밸류 밴드(`FundamentalRepository.historical_band`)·가치 목표가용 히스토리를
        적재한다. 가격 백필(`backfill_ohlcv`)과 분리된 일자별 펀더멘털 경로다.
        """
        def _run(db: Session) -> dict:
            collector = DataCollector(self._make_krx_adapter(), db)
            return collector.collect_fundamental_history(start, end)

        return self._job("fundamental_backfill", _run)

    def run_validation(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            generated = self._generate_validation_metrics(db, ref_date)
            promotion = self._evaluate_promotions(db, ref_date)
            if promotion["evaluated"] > 0:
                self._write_log(
                    db,
                    ref_date=ref_date,
                    source="scheduler.validation",
                    status="success",
                    items=int(promotion["evaluated"]),
                    note=(
                        f"Promotion evaluation complete: passed={promotion['passed']}, "
                        f"failed={promotion['failed']}."
                    ),
                )
                return {
                    "ref_date": ref_date.isoformat(),
                    "status": "success",
                    "reason": "promotion_evaluation_completed",
                    "generated": generated,
                    **promotion,
                }

            # Full WFA/MC requires persisted OHLCV history. If no validation or
            # candidate data is available yet, record readiness without creating
            # synthetic promotion decisions.
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.validation",
                status="skipped",
                items=0,
                note="No candidate or validation metrics available for promotion evaluation.",
            )
            return {
                "ref_date": ref_date.isoformat(),
                "status": "skipped",
                "reason": "promotion_inputs_missing",
                "generated": generated,
                **promotion,
            }

        return self._job("validation", _run)

    def run_order_cycle(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            manager = OrderManager(broker=broker, risk=self._make_risk_manager(broker, db), db=db)
            sync = manager.sync_broker_state()
            holdings = self._broker_positions(broker)
            self._save_portfolio_snapshot(db, ref_date, sync, holdings=holdings)
            live_enabled = self._settings.maps_live_trading_enabled
            submitted_orders = 0
            skipped_orders = 0
            submitted_buy_orders = 0
            submitted_sell_orders = 0
            skipped_buy_orders = 0
            skipped_sell_orders = 0
            note = None

            dry_run = getattr(self._settings, "maps_dry_run", False)

            if live_enabled and not dry_run:
                submitted_sell_orders, skipped_sell_orders, exit_tickers = self._submit_exit_orders(
                    db=db,
                    broker=broker,
                    manager=manager,
                    ref_date=ref_date,
                )
                # H-3: OHLCV 데이터 신선도 검증 — 5일 이상 오래된 데이터면 매수 스킵
                data_fresh, latest_ohlcv_date, expected_ohlcv_date = self._is_data_fresh(db, ref_date)
                if data_fresh:
                    submitted_buy_orders, skipped_buy_orders = self._submit_candidate_orders(
                        db=db,
                        broker=broker,
                        manager=manager,
                        ref_date=ref_date,
                        blocked_tickers=exit_tickers,
                    )
                else:
                    logger.warning(
                        "OHLCV 데이터 오래됨 (ref_date=%s) — 매수 주문 전량 스킵", ref_date
                    )
                    submitted_buy_orders, skipped_buy_orders = 0, 0
                    note = (
                        "stale_data: buy orders skipped "
                        f"latest={latest_ohlcv_date} expected>={expected_ohlcv_date}"
                    )
                submitted_orders = submitted_sell_orders + submitted_buy_orders
                skipped_orders = skipped_sell_orders + skipped_buy_orders
                final_balance = broker.get_account_balance()
                holdings = self._broker_positions(broker)
                self._save_portfolio_snapshot(db, ref_date, {
                    "cash": final_balance.cash,
                    "positions_value": final_balance.positions_value,
                    "total_assets": final_balance.total_value,
                }, holdings=holdings)
            elif dry_run:
                # Dry-run: 실거래 주문 없이 후보·포지션 계획만 로깅한다.
                self._log_dry_run_candidates(db, ref_date)
                note = "dry_run=True: order submission skipped, candidates logged only."
                logger.info("[DRY-RUN] ref_date=%s — 모든 주문 제출 생략", ref_date)
            else:
                note = "Order submission disabled by MAPS_LIVE_TRADING_ENABLED=false."

            status = "success" if live_enabled else "skipped"
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.orders",
                status=status,
                items=submitted_orders,
                note=note,
            )
            return {
                "ref_date": ref_date.isoformat(),
                "live_trading_enabled": live_enabled,
                "cash": sync["cash"],
                "positions_value": sync["positions_value"],
                "open_orders": sync["open_orders"],
                "updated_orders": sync["updated_orders"],
                "submitted_orders": submitted_orders,
                "skipped_orders": skipped_orders,
                "submitted_buy_orders": submitted_buy_orders,
                "submitted_sell_orders": submitted_sell_orders,
                "skipped_buy_orders": skipped_buy_orders,
                "skipped_sell_orders": skipped_sell_orders,
            }

        return self._job("order_cycle", _run)

    def sync_broker_state(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            manager = OrderManager(broker=broker, risk=self._make_risk_manager(broker, db), db=db)
            sync = manager.sync_broker_state()
            holdings = self._broker_positions(broker)
            self._save_portfolio_snapshot(db, ref_date, sync, holdings=holdings)
            live_enabled = self._settings.maps_live_trading_enabled
            market_open = False
            submitted_sell_orders = 0
            skipped_sell_orders = 0
            exit_tickers: set[str] = set()

            if live_enabled:
                try:
                    market_open = bool(broker.is_market_open())
                except NotImplementedError:
                    market_open = False

            exit_monitor_active = live_enabled and market_open
            strategy_trade_active = exit_monitor_active and self._settings.maps_strategy_trade_enabled
            st_submitted = 0
            st_closed = 0
            if exit_monitor_active:
                # H-2: 손절 정확도 향상 — 장중 현재가로 price_feed 갱신
                held_tickers = list(self._broker_position_details(broker).keys())
                # 전략매매 무장/보유 종목도 현재가 갱신 대상에 포함 (armed는 미보유라도 필요)
                st_picks = self._active_strategy_trade_picks(db) if strategy_trade_active else []
                monitor_tickers = set(held_tickers) | {p.ticker for p in st_picks}
                prices: dict[str, float] = {}
                if monitor_tickers:
                    prices = self._fetch_intraday_prices(list(monitor_tickers), broker=broker)
                    if prices:
                        broker.update_prices(prices)
                        logger.info("장중 현재가 갱신: %d/%d종목", len(prices), len(monitor_tickers))
                    # 일부만 조회돼도 나머지는 전일 종가로 손절을 판단하게 된다 — 반드시 남긴다.
                    stale = sorted(t for t in monitor_tickers if t not in prices)
                    if stale:
                        logger.warning(
                            "장중 현재가 조회 실패 — 손절 판단에 전일 종가 사용 (대상 %d종목: %s)",
                            len(stale), ", ".join(stale),
                        )
                # 브래킷이 관리하는 BOUGHT 종목은 전략 %/ATR 손절에서 제외(이중 매도 방지)
                bracket_tickers = {p.ticker for p in st_picks if p.state == "BOUGHT"}
                submitted_sell_orders, skipped_sell_orders, exit_tickers = self._submit_exit_orders(
                    db=db,
                    broker=broker,
                    manager=manager,
                    ref_date=ref_date,
                    exclude_tickers=bracket_tickers,
                )
                if strategy_trade_active:
                    st_submitted, st_closed = self._process_strategy_trades(
                        db=db, broker=broker, manager=manager, picks=st_picks, prices=prices,
                    )
                if submitted_sell_orders or st_submitted or st_closed:
                    final_balance = broker.get_account_balance()
                    sync = {
                        **sync,
                        "cash": final_balance.cash,
                        "positions_value": final_balance.positions_value,
                        "total_assets": final_balance.total_value,
                    }
                    holdings = self._broker_positions(broker)
                    self._save_portfolio_snapshot(db, ref_date, sync, holdings=holdings)

            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.broker_sync",
                status="success",
                items=int(sync["updated_orders"]) + submitted_sell_orders,
                note=(
                    f"open_orders={sync['open_orders']} "
                    f"exit_monitor={'on' if exit_monitor_active else 'off'} "
                    f"submitted_sell_orders={submitted_sell_orders} "
                    # 보유 종목 수 — KIS 연속조회(tr_cont) 페이지네이션이 잔고를 20종목에서
                    # 자르지 않는지 사후 확인하는 지표. portfolio_snapshot은 (ref_date, source)
                    # 유니크 upsert라 날짜당 마지막 값만 남지만, 이 note는 장중 변동까지 남는다.
                    f"holdings={'n/a' if holdings is None else len(holdings)}"
                ),
            )
            return {
                "ref_date": ref_date.isoformat(),
                **sync,
                "live_trading_enabled": live_enabled,
                "market_open": market_open,
                "exit_monitor_active": exit_monitor_active,
                "submitted_sell_orders": submitted_sell_orders,
                "skipped_sell_orders": skipped_sell_orders,
                "exit_tickers": sorted(exit_tickers),
                "strategy_trade_active": strategy_trade_active,
                "strategy_trades_submitted": st_submitted,
                "strategy_trades_closed": st_closed,
            }

        return self._job("broker_sync", _run)

    def run_eod_cleanup(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            manager = OrderManager(broker=broker, risk=self._make_risk_manager(broker, db), db=db)
            cancelled = 0
            try:
                open_orders = broker.get_open_orders()
            except NotImplementedError:
                open_orders = []
            for order in open_orders:
                if broker.cancel_order(order.order_id):
                    cancelled += 1
            if hasattr(broker, "eod_cleanup"):
                broker.eod_cleanup()  # type: ignore[attr-defined]
            # 만료 전 마지막 체결 동기화 — VTS 장전 주문 등 daily CCLD 누락 케이스 처리
            manager.sync_broker_state()
            expired = manager.expire_pending_orders(before=dt.datetime.now())
            # ponytail: job_run_log 보존 90일 하드코딩 — 설정화는 요구가 생기면
            purged = (
                db.query(JobRunLog)
                .filter(JobRunLog.ref_date < ref_date - dt.timedelta(days=90))
                .delete()
            )
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.eod",
                status="success",
                items=cancelled,
                note="Cancelled remaining open orders and ran broker EOD cleanup.",
            )
            return {
                "ref_date": ref_date.isoformat(),
                "open_orders_seen": len(open_orders),
                "cancelled_orders": cancelled,
                "expired_orders": expired,
                "job_run_log_purged": purged,
            }

        return self._job("eod_cleanup", _run)

    def _job(self, name: str, fn: Callable[[Session], dict]) -> JobRun:
        started = dt.datetime.now(dt.timezone.utc)
        run = JobRun(name=name, status="running", started_at=started)
        db = self._session_factory()
        try:
            run.details = fn(db)
            run.status = "success"
            run.message = "ok"
        except Exception as exc:
            db.rollback()
            run.status = "failed"
            run.message = str(exc)
            logger.exception("Operational job failed: %s", name)
            self._notifier.send_job_failed(name, str(exc))
        finally:
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.close()
        return run

    def _make_krx_adapter(self):
        if self._settings.maps_data_provider == "mock":
            return MockKRXAdapter()
        return KRXAdapter()

    def _generate_validation_metrics(self, db: Session, ref_date: dt.date) -> dict[str, int | list[dict[str, str]]]:
        strategy_ids = self._candidate_strategy_ids(db, ref_date)
        generated = {"wfa": 0, "plateau": 0, "mc": 0, "skipped": []}
        if not strategy_ids:
            return generated

        repo = HistoricalOHLCVRepository(db)
        for strategy_id in strategy_ids:
            strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id)
            if strategy_cls is None:
                generated["skipped"].append({"strategy_id": strategy_id, "reason": "strategy_not_runnable"})
                continue

            strategy = strategy_cls()
            min_bars = self._wfa_required_bars()
            tickers = repo.list_tickers_with_history(end=ref_date, min_bars=min_bars)
            if not tickers:
                generated["skipped"].append({
                    "strategy_id": strategy_id,
                    "reason": f"insufficient_history: need {min_bars} bars",
                })
                continue

            # 백테스트 샘플: 유동성 대형주 우선 선택 (알파벳 첫 번째 소형주 회피)
            sample_tickers = self._pick_sample_tickers(tickers, _VALIDATION_SAMPLE_TICKERS)
            backtests = self._run_backtest_grid(db, repo, strategy, sample_tickers, ref_date)
            if not backtests:
                generated["skipped"].append({"strategy_id": strategy_id, "reason": "no_backtest_results"})
                continue

            self._save_scheduled_backtest(db, strategy, ref_date, backtests)

            if self._save_plateau_result(db, strategy, ref_date, backtests):
                generated["plateau"] += 1
            if self._save_mc_result(db, strategy, ref_date, backtests):
                generated["mc"] += 1
            wfa_ticker = self._pick_wfa_ticker(tickers)
            if self._save_wfa_result(db, repo, strategy, wfa_ticker, ref_date):
                generated["wfa"] += 1

        return generated

    @staticmethod
    def _pick_sample_tickers(tickers: list[str], n: int) -> list[str]:
        """백테스트 샘플 ticker 목록을 구성한다.

        _WFA_PREFERRED_TICKERS 를 우선 포함하고, 나머지를 원래 순서(알파벳)로 채운다.
        소형·비유동 종목이 plateau/MC 점수를 왜곡하는 것을 방지한다.
        """
        ticker_set = set(tickers)
        preferred = [t for t in _WFA_PREFERRED_TICKERS if t in ticker_set]
        others = [t for t in tickers if t not in set(_WFA_PREFERRED_TICKERS)]
        return (preferred + others)[:n]

    @staticmethod
    def _pick_wfa_ticker(tickers: list[str]) -> str:
        """WFA 에 사용할 대표 ticker 를 선택한다.

        알파벳순 첫 번째(보통 소형주) 대신 _WFA_PREFERRED_TICKERS 목록에서
        데이터가 충분한 첫 번째 ticker 를 우선 사용한다.
        목록에 없는 경우 tickers[0] 으로 폴백한다.
        """
        ticker_set = set(tickers)
        for preferred in _WFA_PREFERRED_TICKERS:
            if preferred in ticker_set:
                return preferred
        return tickers[0]

    @staticmethod
    def _wfa_required_bars() -> int:
        analyzer = WalkForwardAnalyzer()
        return (36 + (5 * 12)) * 21

    @staticmethod
    def _candidate_strategy_ids(db: Session, ref_date: dt.date) -> list[str]:
        rows = (
            db.query(CandidateSnapshot.strategy_id)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .distinct()
            .order_by(CandidateSnapshot.strategy_id.asc())
            .all()
        )
        return [row.strategy_id for row in rows]

    def _run_backtest_grid(
        self,
        db: Session,
        repo: HistoricalOHLCVRepository,
        strategy: BaseStrategy,
        tickers: list[str],
        ref_date: dt.date,
    ) -> list[dict]:
        engine = BacktestEngine()
        rows: list[dict] = []
        for params in strategy.param_grid():
            results: list[BacktestResult] = []
            successful_tickers: list[str] = []
            min_bars = max(strategy.required_bars(params), 30)
            for ticker in tickers:
                df = repo.to_dataframe(ticker, end=ref_date)
                if len(df) < min_bars:
                    continue
                df.index.name = ticker
                try:
                    results.append(engine.run(strategy, params, df))
                    successful_tickers.append(ticker)
                except BacktestError as exc:
                    logger.debug("Validation backtest skipped [%s %s]: %s", strategy.strategy_id, ticker, exc)
            if not results:
                continue
            row = dict(params)
            row["sharpe"] = sum(r.sharpe for r in results) / len(results)
            row["mdd"] = min(r.mdd for r in results)
            row["daily_returns"] = self._average_daily_returns(results)
            row["_net_cagr"] = sum(r.cagr for r in results) / len(results)
            row["_trade_count"] = sum(r.total_trades for r in results)
            row["_ticker_count"] = len(results)
            row["_tickers"] = successful_tickers
            row["_start_date"] = min(r.start_date for r in results)
            row["_end_date"] = max(r.end_date for r in results)
            rows.append(row)
        return rows

    @staticmethod
    def _average_daily_returns(results: list[BacktestResult]) -> list[float]:
        series = [pd.Series(r.daily_returns, dtype=float) for r in results if len(r.daily_returns) >= 30]
        if not series:
            return []
        frame = pd.concat(series, axis=1).fillna(0.0)
        return frame.mean(axis=1).tolist()

    @staticmethod
    def _save_scheduled_backtest(
        db: Session,
        strategy: BaseStrategy,
        ref_date: dt.date,
        rows: list[dict],
    ) -> None:
        """자동 검증의 대표 백테스트를 콘솔 최근 실행 이력에 upsert한다."""
        best = max(rows, key=lambda row: float(row.get("sharpe", 0.0)))
        digest = hashlib.sha1(strategy.strategy_id.encode("utf-8")).hexdigest()[:12]
        run_id = f"val_{ref_date:%Y%m%d}_{digest}"
        log = db.query(BacktestRunLog).filter(BacktestRunLog.run_id == run_id).one_or_none()
        if log is None:
            log = BacktestRunLog(run_id=run_id, strategy_id=strategy.strategy_id)
            db.add(log)

        metric_keys = {"sharpe", "mdd", "daily_returns"}
        params = {
            key: value
            for key, value in best.items()
            if key not in metric_keys and not key.startswith("_")
        }
        log.strategy_id = strategy.strategy_id
        log.source = "scheduled_validation"
        log.params_json = json.dumps(params, ensure_ascii=False)
        log.status = "done"
        log.net_cagr = float(best["_net_cagr"])
        log.mdd = float(best["mdd"])
        log.sharpe = float(best["sharpe"])
        log.trade_count = int(best["_trade_count"])
        log.ticker_count = int(best["_ticker_count"])
        log.start_date = best["_start_date"]
        log.end_date = best["_end_date"]
        log.mode = "per_ticker"
        log.universe = "validation_sample"
        log.verdict = None
        log.verdict_json = None
        log.stats_json = json.dumps({
            "tickers": best["_tickers"],
            "selection": "max_sharpe",
            "parameter_combinations": len(rows),
        }, ensure_ascii=False)
        log.created_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

    @staticmethod
    def _save_plateau_result(db: Session, strategy: BaseStrategy, ref_date: dt.date, rows: list[dict]) -> bool:
        # param_keys: default_params 키 중 실제 row 에 존재하는 것만 사용한다.
        # param_grid() 에 포함되지 않은 파라미터(예: vol_period)가 default_params 에만
        # 있을 경우 KeyError 가 발생하므로 교집합으로 제한한다.
        _non_param = {"sharpe", "mdd", "daily_returns"}
        actual_param_keys = [
            key for key in (rows[0] if rows else {})
            if key not in _non_param and not key.startswith("_")
        ]
        param_keys = [k for k in strategy.default_params if k in set(actual_param_keys)]
        if not param_keys:
            logger.warning("Plateau validation skipped [%s]: no overlapping param keys", strategy.strategy_id)
            return False
        try:
            result = ParameterPlateauTester().run(rows, param_keys=param_keys)
        except ValueError as exc:
            logger.warning("Plateau validation skipped [%s]: %s", strategy.strategy_id, exc)
            return False

        grade_map = {"robust": "A", "moderate": "C", "fragile": "F"}
        db.add(ParameterPlateauResults(
            strategy_id=strategy.strategy_id,
            run_date=ref_date,
            total_combinations=len(rows),
            positive_combinations=result.passing_neighbors,
            positive_ratio=result.score / 100.0,
            grade=grade_map.get(result.grade, "F"),
            best_params_json=json.dumps(result.best_combo, ensure_ascii=False),
        ))
        db.commit()
        return True

    @staticmethod
    def _save_mc_result(db: Session, strategy: BaseStrategy, ref_date: dt.date, rows: list[dict]) -> bool:
        best = max(rows, key=lambda row: float(row.get("sharpe", 0.0)))
        daily_returns = list(best.get("daily_returns") or [])
        if len(daily_returns) < 30:
            logger.warning("Monte Carlo validation skipped [%s]: fewer than 30 returns", strategy.strategy_id)
            return False

        try:
            result = MonteCarloValidator(n_simulations=1000).validate(
                strategy.strategy_id,
                strategy.strategy_group,
                daily_returns,
            )
        except ValidationError as exc:
            logger.warning("Monte Carlo validation skipped [%s]: %s", strategy.strategy_id, exc)
            return False

        db.add(MonteCarloSequenceResults(
            strategy_id=result.strategy_id,
            strategy_group=result.strategy_group,
            run_date=ref_date,
            n_simulations=result.n_simulations,
            mdd_p95=result.mdd_p95,
            mdd_limit=result.mdd_limit,
            mc_within_limit=result.passed,
        ))
        db.commit()
        return True

    @staticmethod
    def _save_wfa_result(
        db: Session,
        repo: HistoricalOHLCVRepository,
        strategy: BaseStrategy,
        ticker: str,
        ref_date: dt.date,
    ) -> bool:
        df = repo.to_dataframe(ticker, end=ref_date)
        df.index.name = ticker
        result = WalkForwardAnalyzer().run(strategy, df, strategy.param_grid())
        summary = WalkForwardResults(
            strategy_id=strategy.strategy_id,
            run_date=ref_date,
            n_folds=len(result.folds),
            sharpe_mean=result.sharpe_mean,
            sharpe_std=result.sharpe_std,
            negative_folds=result.negative_folds,
            mean_g2p=result.mean_g2p,
            passed=result.passed,
            fail_reasons_json=json.dumps(result.fail_reasons, ensure_ascii=False) if result.fail_reasons else None,
        )
        db.add(summary)
        db.flush()
        for fold in result.folds:
            db.add(WalkForwardFoldResults(
                wfa_run_id=summary.id,
                strategy_id=strategy.strategy_id,
                fold_idx=fold.fold_idx,
                is_start=fold.is_start,
                is_end=fold.is_end,
                oos_start=fold.oos_start,
                oos_end=fold.oos_end,
                is_sharpe=fold.is_sharpe,
                oos_sharpe=fold.oos_sharpe,
                is_g2p=fold.is_g2p,
                oos_g2p=fold.oos_g2p,
                g2p_ratio=fold.g2p_ratio,
                best_params_json=None,
            ))
        db.commit()
        return True

    def _evaluate_promotions(self, db: Session, ref_date: dt.date) -> dict[str, int | list[str]]:
        latest_candidates = (
            db.query(CandidateSnapshot.strategy_id)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .distinct()
            .all()
        )
        latest_plateau = self._latest_rows_by_strategy(
            db.query(ParameterPlateauResults)
            .filter(ParameterPlateauResults.run_date <= ref_date)
            .order_by(ParameterPlateauResults.run_date.desc(), ParameterPlateauResults.id.desc())
            .all()
        )
        latest_mc = self._latest_rows_by_strategy(
            db.query(MonteCarloSequenceResults)
            .filter(MonteCarloSequenceResults.run_date <= ref_date)
            .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
            .all()
        )
        latest_wfa = self._latest_rows_by_strategy(
            db.query(WalkForwardResults)
            .filter(WalkForwardResults.run_date <= ref_date)
            .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
            .all()
        )
        latest_promotions = self._latest_promotion_rows(db)

        strategy_ids = sorted(
            {row.strategy_id for row in latest_candidates}
            | set(latest_plateau)
            | set(latest_mc)
            | set(latest_wfa)
        )
        if not strategy_ids:
            return {"evaluated": 0, "passed": 0, "failed": 0, "strategies": []}

        gate = PromotionGate(db=db)
        mock_months = self._mock_track_months(db, ref_date, self._settings)
        passed = 0
        failed = 0
        evaluated_strategies: list[str] = []
        demoted_strategies: list[str] = []
        for strategy_id in strategy_ids:
            current_stage = self._promotion_stage(latest_promotions.get(strategy_id))
            metrics = self._promotion_metrics(
                latest_plateau.get(strategy_id),
                latest_mc.get(strategy_id),
                latest_wfa.get(strategy_id),
            )
            metrics["mock_months"] = mock_months.get(strategy_id, 0.0)
            decision = gate.evaluate(
                strategy_id,
                metrics,
                current_stage,
                strategy_group=STRATEGY_GROUP_MAP.get(strategy_id),
            )
            evaluated_strategies.append(strategy_id)
            if decision.passed:
                passed += 1
            else:
                failed += 1
            if decision.demoted:
                demoted_strategies.append(strategy_id)
                self._notifier.send(
                    Notification(
                        level="WARN",
                        title=f"전략 자동 강등: {strategy_id}",
                        message=(
                            f"점수 {decision.score:.1f} 연속 미달로 "
                            "mock_candidate → research 강등. 신규 mock 주문이 차단됩니다 "
                            "(기존 보유는 청산 로직이 계속 관리)."
                        ),
                    )
                )

        return {
            "evaluated": len(evaluated_strategies),
            "passed": passed,
            "failed": failed,
            "demoted": demoted_strategies,
            "strategies": evaluated_strategies,
        }

    @staticmethod
    def _mock_track_months(
        db: Session,
        ref_date: dt.date,
        settings: MapsSettings | None = None,
    ) -> dict[str, float]:
        """전략별 최초 체결 매수 이후 경과 개월 수를 반환한다.

        승격 게이트의 `mock_months`(Live Small 진입에 필요한 실체결 트랙레코드
        길이) 입력값이다. 체결(fill_qty > 0)된 BUY 주문만 센다 — 미체결·취소는
        운용 실적이 아니다.
        """
        query = (
            # side 는 소문자("buy")로 저장된다 — 리터럴 "BUY" 로 비교하면 항상 0건이
            # 나와 mock_months 가 영구히 0.0 이 되고 Live Small 승격이 차단된다
            # (2026-07-31 운영 확인: 실제로는 2개월치가 이미 쌓여 있었다).
            db.query(OrderLog.strategy_id, func.min(OrderLog.created_at))
            .filter(OrderLog.side == OrderSide.BUY.value, OrderLog.fill_qty > 0)
        )
        account_start = account_history_start_utc_naive(settings)
        if account_start is not None:
            query = query.filter(OrderLog.created_at >= account_start)
        rows = query.group_by(OrderLog.strategy_id).all()
        return {
            strategy_id: max((ref_date - utc_datetime_to_kst_date(first_at)).days, 0) / 30.44
            for strategy_id, first_at in rows
            if strategy_id and first_at
        }

    @staticmethod
    def _latest_rows_by_strategy(rows) -> dict[str, object]:
        latest: dict[str, object] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row
        return latest

    @staticmethod
    def _latest_promotion_rows(db: Session) -> dict[str, PromotionHistory]:
        """전략별 마지막 '성공' 승격 이력을 반환한다.

        passed=True 레코드만 읽는다.  실패한 평가(passed=False)는 현재 단계에
        영향을 주지 않는다 — 실패한 평가가 이전에 승격된 단계를 덮어쓰는
        '강제 강등' 버그를 방지하기 위해서다.

        예) pullback_v3 가 mock_candidate 로 승격된 후, 다음 평가 주기에서
        "점수 71.8 < 임계값 75 (live_candidate)" 로 실패하면 passed=False 레코드가
        추가되는데, 이 레코드를 읽으면 stage=RESEARCH 로 되돌아간다.
        passed=True 만 읽으면 마지막 성공 기록(mock_candidate)이 유지된다.
        """
        rows = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .all()
        )
        latest: dict[str, PromotionHistory] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row
        return latest

    @staticmethod
    def _promotion_stage(row: PromotionHistory | None) -> PromotionStage:
        if row is None or row.to_stage == PromotionStage.REJECTED.value:
            return PromotionStage.RESEARCH
        try:
            return PromotionStage(row.to_stage)
        except ValueError:
            return PromotionStage.RESEARCH

    @staticmethod
    def _promotion_metrics(
        plateau: ParameterPlateauResults | None,
        mc: MonteCarloSequenceResults | None,
        wfa: WalkForwardResults | None,
    ) -> dict[str, float]:
        metrics: dict[str, float] = {}
        if plateau is not None:
            metrics["robustness"] = max(0.0, min(float(plateau.positive_ratio), 1.0))
        if mc is not None:
            ratio = abs(float(mc.mdd_p95)) / float(mc.mdd_limit) if mc.mdd_limit else 1.0
            metrics["risk"] = max(0.0, min(1.0 - ratio, 1.0))
            metrics["mc_mdd_p95"] = float(mc.mdd_p95)
        if wfa is not None:
            # WFA 통과 여부와 무관하게 실제 측정치 기반으로 항상 설정한다.
            # 음수 값은 0 으로 클램프 — "메트릭 누락" 대신 실제 점수로 gate 가 판단한다.
            # 과거에는 passed=True 일 때만 설정했는데, 그러면 모든 미통과 전략이
            # robustness/risk 점수만으로 평가돼 임계값(60)을 절대 넘지 못했다.
            metrics["recovery"] = max(0.0, min(float(wfa.mean_g2p) / 2.0, 1.0))
            metrics["return"] = max(0.0, min(float(wfa.sharpe_mean) / 2.0, 1.0))
        return metrics

    def _to_securities(
        self,
        db: Session,
        meta: list[SecurityMeta],
        collection: CollectionResult,
        ref_date: dt.date,
    ) -> list[Security]:
        ohlcv_by_ticker = {row.ticker: row for row in collection.ohlcv}
        halted = set(collection.halts)
        managed = set(collection.managed)
        sectors_by_ticker = {
            row.ticker: row.sector
            for row in db.query(SecurityMetadata.ticker, SecurityMetadata.sector)
            .filter(SecurityMetadata.ticker.in_([item.ticker for item in meta]))
            .all()
        }
        securities: list[Security] = []
        for item in meta:
            ohlcv = ohlcv_by_ticker.get(item.ticker)
            turnover = (ohlcv.close * ohlcv.volume) if ohlcv else 0.0
            missing_fields = self._missing_ohlcv_fields(ohlcv)
            securities.append(
                Security(
                    ticker=item.ticker,
                    name=item.name,
                    market=item.market,
                    security_type=item.security_type,
                    sector=getattr(item, "sector", None) or sectors_by_ticker.get(item.ticker),
                    listing_date=item.listing_date or dt.date(2000, 1, 1),
                    delisting_date=item.delisting_date,
                    # The current collector does not persist a separate
                    # adjusted-price history yet.  Treat present live OHLCV as
                    # usable so the daily candidate job remains operational.
                    has_adjusted_price=bool(ohlcv),
                    latest_ohlcv_date=ohlcv.date if ohlcv else None,
                    missing_ohlcv_fields=missing_fields,
                    halt_periods=(
                        [HaltPeriod(ticker=item.ticker, start=ref_date, end=ref_date)]
                        if item.ticker in halted else []
                    ),
                    managed_periods=(
                        [ManagedPeriod(ticker=item.ticker, start=ref_date, end=ref_date)]
                        if item.ticker in managed else []
                    ),
                    turnover_cache={ref_date: turnover},
                )
            )
        return securities

    @staticmethod
    def _missing_ohlcv_fields(ohlcv) -> set[str]:
        if ohlcv is None:
            return {"open", "high", "low", "close", "volume"}
        missing: set[str] = set()
        for field_name in ("open", "high", "low", "close"):
            value = getattr(ohlcv, field_name)
            if value is None or value <= 0:
                missing.add(field_name)
        if ohlcv.volume is None or ohlcv.volume < 0:
            missing.add("volume")
        return missing

    def _build_ticker_contexts(
        self,
        db: Session,
        universe: list[Security],
        ref_date: dt.date,
    ) -> dict[str, TickerContext]:
        """유니버스 전체의 종목별 컨텍스트를 **한 번만** 만든다.

        전략 루프 **밖에서** 호출해 `_save_candidate_snapshot` 에 넘긴다. 여기서 만드는 값은
        전부 전략과 무관하므로 전략 수만큼 다시 계산할 이유가 없다.

        프레임은 `_SIGNAL_LOOKBACK_BARS` 봉으로 잘라 주문 시점 경로와 워밍업을 맞춘다.
        `start` 는 거래일이 아니라 달력일이라 넉넉히 잡고 `tail()` 로 정확히 맞춘다.
        """
        repo = HistoricalOHLCVRepository(db)
        ts_calc = TrendStrengthCalculator()
        valuation_scorer = (
            ValuationMarginScorer() if self._settings.maps_valuation_margin_enabled else None
        )
        fundamental_provider = (
            FundamentalValuationProvider(db, ref_date)
            if (
                self._settings.maps_valuation_margin_enabled
                or self._settings.maps_kostolany_price_calculator_enabled
            )
            else None
        )
        # 400 거래일을 확보하려면 달력으로는 그 1.5배 이상이 필요하다(주말·공휴일).
        start = ref_date - dt.timedelta(days=_SIGNAL_LOOKBACK_BARS * 2)

        contexts: dict[str, TickerContext] = {}
        for stock in universe:
            frame = pd.DataFrame()
            trend_strength = 50.0
            ts_bucket = "S3"
            try:
                frame = repo.to_dataframe(stock.ticker, start=start, end=ref_date)
                if len(frame) > _SIGNAL_LOOKBACK_BARS:
                    frame = frame.tail(_SIGNAL_LOOKBACK_BARS)
                ts_score = ts_calc.score_one(stock.ticker, frame, ref_date)
                if ts_score is not None:
                    trend_strength = ts_score.score
                    ts_bucket = ts_score.bucket
            except Exception:  # noqa: BLE001
                pass  # OHLCV 없으면 중립값 유지 (기존 동작)

            close = float(frame["close"].iloc[-1]) if not frame.empty else 0.0
            atr14: float | None = None
            if len(frame) >= 14:
                try:
                    last_atr = _compute_atr14(frame).iloc[-1]
                    atr14 = float(last_atr) if pd.notna(last_atr) else None
                except Exception:  # noqa: BLE001
                    pass

            valuation = None
            if valuation_scorer is not None and fundamental_provider is not None:
                valuation = valuation_scorer.score(
                    fundamental_provider.get(
                        stock.ticker, current_price=close if close > 0 else None
                    )
                )

            contexts[stock.ticker] = TickerContext(
                frame=frame,
                trend_strength=trend_strength,
                ts_bucket=ts_bucket,
                close=close,
                atr14=atr14,
                valuation=valuation,
            )
        return contexts

    def _save_candidate_snapshot(
        self,
        db: Session,
        ref_date: dt.date,
        strategy_id: str,
        universe: list[Security],
        *,
        weekly_pass: bool = True,
        excluded_reason_by_ticker: dict[str, str] | None = None,
        contexts: dict[str, TickerContext] | None = None,
        stats: dict[str, int] | None = None,
    ) -> int:
        db.execute(
            delete(CandidateSnapshot).where(
                CandidateSnapshot.ref_date == ref_date,
                CandidateSnapshot.strategy_id == strategy_id,
            )
        )
        ranked = sorted(
            universe,
            key=lambda stock: stock.avg_turnover_20d_as_of(ref_date),
            reverse=True,
        )
        max_turnover = max((stock.avg_turnover_20d_as_of(ref_date) for stock in ranked), default=0.0)
        excluded_reason_by_ticker = excluded_reason_by_ticker or {}

        # 전략 루프 밖에서 만들어 넘기는 것이 정상 경로다. 단독 호출(테스트·수동 실행)에
        # 대비해 없으면 여기서 만든다 — 그 경우에만 전략 수만큼 재계산이 일어난다.
        if contexts is None:
            contexts = self._build_ticker_contexts(db, universe, ref_date)

        # 신호 게이트: "유동성 좋고 추세 강한 종목"이 아니라 "이 전략이 오늘 사겠다고 한
        # 종목"이 후보다. 신호는 전략별이라 컨텍스트에 담지 않고 여기서 계산한다(DB 접근 없음).
        signal_by_ticker: dict[str, bool] = {}
        for ticker, ctx in contexts.items():
            signal = self._signal_from_frame(strategy_id, ctx.frame)
            signal_by_ticker[ticker] = bool(signal is not None and signal.entry_signal)

        # (final_score, entry_signal, row) — 전량 만든 뒤 저장 대상을 고른다.
        # 상위 N은 전체 점수를 봐야 정해지므로 즉시 add 할 수 없다.
        pending: list[tuple[float, bool, CandidateSnapshot]] = []

        valuation_enabled = self._settings.maps_valuation_margin_enabled
        # 밸류에이션은 종목 컨텍스트가 이미 계산해 뒀다 (전략 무관) — 여기서 다시 하지 않는다.
        holding_type_enabled = self._settings.maps_holding_type_classification_enabled
        holding_type_classifier = HoldingTypeClassifier() if holding_type_enabled else None
        price_calc_enabled = self._settings.maps_kostolany_price_calculator_enabled
        price_calculator = KostolanyPriceCalculator() if price_calc_enabled else None
        # 펀더멘털 프로바이더(pykrx DB) — 안전마진·가치목표가의 실데이터 소스.
        # 데이터가 없으면 내부적으로 중립 입력을 반환하므로 항상 안전하다.
        fundamental_provider = (
            FundamentalValuationProvider(db, ref_date)
            if (valuation_enabled or price_calc_enabled)
            else None
        )
        strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id)
        strategy_type = getattr(strategy_cls, "strategy_type", StrategyType.MOMENTUM)
        legacy_score_calculator = LegacyFinalScoreCalculator()
        strategy_score_calculator = (
            StrategyAwareScoreCalculator()
            if self._settings.maps_strategy_aware_scoring_enabled
            else None
        )
        for stock in ranked:
            turnover = stock.avg_turnover_20d_as_of(ref_date)
            factor_score = (turnover / max_turnover * 100.0) if max_turnover > 0 else 0.0
            sector_excluded_reason = excluded_reason_by_ticker.get(stock.ticker)
            if sector_excluded_reason:
                pending.append((
                    0.0,
                    False,
                    CandidateSnapshot(
                        ref_date=ref_date,
                        strategy_id=strategy_id,
                        ticker=stock.ticker,
                        name=stock.name,
                        market=stock.market,
                        factor_score=round(factor_score, 2),
                        trend_strength=50.0,
                        ts_bucket="S3",
                        final_score=0.0,
                        rule_score=0.0,
                        recommendation_score=0.0,
                        score_source="RULE",
                        ai_scoring_mode=self._settings.maps_ai_scoring_mode,
                        score_type="SECTOR_FILTER",
                        strategy_type=getattr(strategy_type, "value", str(strategy_type)),
                        component_scores={},
                        score_reason="excluded before stock scoring by sector filter",
                        excluded_reason=sector_excluded_reason,
                        weekly_pass=False,
                        estimated_qty=None,
                        entry_signal=None,
                    ),
                ))
                continue

            # 종목 컨텍스트에서 조회 (데이터 부족 시 중립값 50.0 / "S3")
            ctx = contexts.get(stock.ticker)
            trend_strength = ctx.trend_strength if ctx else 50.0
            ts_bucket = ctx.ts_bucket if ctx else "S3"
            ohlcv_df = ctx.frame if ctx is not None and not ctx.frame.empty else None

            # 전략별 저장은 항상 rule-only다. 모든 전략 저장이 끝난 뒤 전역 AI pass가
            # 고유 ticker를 한 번씩 평가하고 recommendation_score만 갱신한다.
            valuation_result = ctx.valuation if ctx else None

            if strategy_score_calculator is not None:
                score_result = strategy_score_calculator.calculate(
                    StrategyScoreInput(
                        strategy_type=strategy_type,
                        liquidity_score=factor_score,
                        trend_strength=trend_strength,
                        ts_bucket=ts_bucket,
                        valuation_margin_score=(
                            valuation_result.valuation_score if valuation_result else None
                        ),
                        ai_technical_score=None,
                        ai_weight=0.0,
                    )
                )
            else:
                score_result = legacy_score_calculator.calculate(
                    factor_score=factor_score,
                    trend_strength=trend_strength,
                    ai_technical_score=None,
                    ai_weight=0.0,
                    strategy_type=strategy_type,
                    ts_bucket=ts_bucket,
                )
            final_score = score_result.final_score

            slippage = self._settings.maps_order_slippage_pct
            rr = self._settings.maps_trade_rr_ratio
            # 종가·ATR 은 컨텍스트에서 온다 — 전략마다 다시 계산하지 않는다
            current_close_val = ctx.close if ctx else 0.0
            atr14_val = ctx.atr14 if ctx else None
            plan_buy: float | None = None
            plan_stop: float | None = None
            plan_target: float | None = None
            if current_close_val > 0:
                plan_buy = float(round_up_krx_price(current_close_val * (1 + slippage), market=stock.market))
                plan_stop = effective_stop_price(strategy_id, plan_buy, atr14_val)
                if plan_stop is not None:
                    plan_stop = float(round_to_krx_tick(plan_stop, market=stock.market))
                    plan_target = float(
                        round_to_krx_tick(plan_buy + (plan_buy - plan_stop) * rr, market=stock.market)
                    )

            # 9단계: 보유 성격 분류 (CORE/SWING/TRADING/WATCH/BAN)
            computed_holding_type: str | None = None
            if holding_type_classifier is not None:
                ht_inp = HoldingTypeInput(
                    strategy_type=score_result.strategy_type,
                    valuation_margin_score=valuation_result.valuation_score if valuation_result else None,
                    excluded_reason=score_result.excluded_reason,
                    ai_contrarian_opinion=None,
                )
                computed_holding_type = holding_type_classifier.classify(ht_inp).value

            # 10단계: 코스톨라니 이중 목표가·손절가 산출
            price_result = None
            if price_calculator is not None and current_close_val > 0:
                try:
                    high52w_val: float | None = None
                    low52w_val: float | None = None
                    if ohlcv_df is not None and not ohlcv_df.empty:
                        window = min(252, len(ohlcv_df))
                        high52w_val = float(ohlcv_df["high"].rolling(window).max().iloc[-1])
                        low52w_val = float(ohlcv_df["low"].rolling(window).min().iloc[-1])
                    # 가치 목표가(value_target)·thesis_stop 산출용 펀더멘털 주입
                    pf = (
                        fundamental_provider.price_fundamentals(stock.ticker)
                        if fundamental_provider is not None
                        else None
                    )
                    price_inp = PriceInput(
                        holding_type=computed_holding_type or "SWING",
                        current_close=current_close_val,
                        atr14=atr14_val,
                        high_52w=high52w_val,
                        low_52w=low52w_val,
                        per=pf.per if pf else None,
                        pbr=pf.pbr if pf else None,
                        eps_forward=pf.eps_forward if pf else None,
                        bps=pf.bps if pf else None,
                        historical_per_avg=pf.historical_per_avg if pf else None,
                        historical_pbr_avg=pf.historical_pbr_avg if pf else None,
                        rr_ratio=rr,
                        slippage_pct=slippage,
                    )
                    price_result = price_calculator.calculate(price_inp)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("코스톨라니 가격 산출 오류 [%s]: %s", stock.ticker, exc)

            entry_signal = signal_by_ticker.get(stock.ticker, False)
            pending.append((
                float(final_score),
                entry_signal,
                CandidateSnapshot(
                    ref_date=ref_date,
                    strategy_id=strategy_id,
                    ticker=stock.ticker,
                    name=stock.name,
                    market=stock.market,
                    entry_signal=entry_signal,
                    factor_score=round(factor_score, 2),
                    trend_strength=round(trend_strength, 2),
                    ts_bucket=ts_bucket,
                    final_score=final_score,
                    rule_score=final_score,
                    recommendation_score=final_score,
                    score_source="RULE",
                    ai_scoring_mode=self._settings.maps_ai_scoring_mode,
                    score_type=score_result.score_type,
                    strategy_type=score_result.strategy_type,
                    component_scores=score_result.component_scores,
                    score_reason=score_result.reason,
                    excluded_reason=score_result.excluded_reason,
                    weekly_pass=weekly_pass,
                    estimated_qty=None,
                    ai_technical_score=None,
                    ai_buy_price=plan_buy,
                    ai_stop_price=plan_stop,
                    ai_target_price=plan_target,
                    ai_analysis_memo=None,
                    valuation_margin_score=valuation_result.valuation_score if valuation_result else None,
                    valuation_margin_reason=valuation_result.reason if valuation_result else None,
                    ai_contrarian_score=None,
                    ai_contrarian_opinion=None,
                    ai_contrarian_reason=None,
                    ai_contrarian_thesis=None,
                    ai_contrarian_anti_thesis=None,
                    holding_type=computed_holding_type,
                    technical_stop=price_result.technical_stop if price_result else None,
                    thesis_stop=price_result.thesis_stop if price_result else None,
                    emergency_stop=price_result.emergency_stop if price_result else None,
                    trading_target=price_result.trading_target if price_result else None,
                    value_target=price_result.value_target if price_result else None,
                    first_sell_price=price_result.first_sell_price if price_result else None,
                    final_sell_price=price_result.final_sell_price if price_result else None,
                ),
            ))

        # 저장 대상 = 신호 있는 종목 **전수** ∪ 나머지 중 final_score 상위 N.
        # 상위 N은 관측용이다 — 신호가 0건인 날에도 "왜 없었나"를 점수로 확인할 수 있어야 한다.
        top_n = self._settings.maps_candidate_snapshot_top_n
        signalled = [row for row in pending if row[1]]
        observed = sorted(
            (row for row in pending if not row[1]), key=lambda row: row[0], reverse=True
        )[:top_n]
        for _score, _signal, snapshot in signalled + observed:
            db.add(snapshot)
        db.commit()

        stored = len(signalled) + len(observed)
        dropped = len(pending) - stored
        logger.info(
            "후보 저장 [%s]: universe=%d signals=%d stored=%d dropped=%d (top_n=%d)",
            strategy_id, len(ranked), len(signalled), stored, dropped, top_n,
        )
        if stats is not None:
            # 전략별 호출을 누적한다 — 잡 details 에서 "어느 단계에서 0이 됐나"를 답하기 위해.
            stats["signals"] = stats.get("signals", 0) + len(signalled)
            stats["dropped"] = stats.get("dropped", 0) + dropped
        return stored

    @staticmethod
    def _save_portfolio_snapshot(
        db: Session,
        ref_date: dt.date,
        sync: dict[str, float | int],
        holdings: dict[str, int] | None = None,
    ) -> None:
        cash = float(sync.get("cash", 0.0))
        positions_value = float(sync.get("positions_value", 0.0))
        total_assets = float(sync.get("total_assets", cash + positions_value))
        row = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.ref_date == ref_date, PortfolioSnapshot.source == "broker")
            .first()
        )
        if row is None:
            db.add(
                PortfolioSnapshot(
                    ref_date=ref_date,
                    source="broker",
                    total_assets=total_assets,
                    cash=cash,
                    positions_value=positions_value,
                    holdings=holdings,
                )
            )
        else:
            row.total_assets = total_assets
            row.cash = cash
            row.positions_value = positions_value
            if holdings is not None:
                row.holdings = holdings
            row.updated_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

    def _submit_candidate_orders(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        ref_date: dt.date,
        blocked_tickers: set[str] | None = None,
    ) -> tuple[int, int]:
        # ── C-2/C-3: 시황 분석 → 진입 한도 비율 적용 ─────────────────────────
        regime = self._analyze_regime()
        regime = apply_hysteresis(db, regime, ref_date, source="order_cycle")
        limit_ratio = regime.entry_limit_ratio
        candidates = self._order_candidates(db, ref_date)
        if limit_ratio == 0.0:
            logger.warning(
                "시황 일반 진입 한도 0.0 (regime=%s trend=%s) — 전략별 정책으로 재확인",
                regime.regime.value,
                regime.weekly_trend.value,
            )

        # ── C-1: 당일 포트폴리오 손익률 계산 → Kill Switch 연동 ───────────────
        daily_pnl = self._calc_daily_pnl(db, ref_date)
        if daily_pnl != 0.0:
            logger.info("당일 손익률: %.2f%%", daily_pnl * 100)

        account = broker.get_account_balance()
        remaining_cash = account.cash
        positions = self._broker_positions(broker) or {}
        submitted = 0
        skipped = 0
        # STRONG(1.0)→3건, MIXED(0.5)→2건, WEAK(0.25)→1건
        max_policy_ratio = max(
            limit_ratio,
            self._settings.maps_contrarian_max_entry_ratio
            if self._settings.maps_contrarian_accumulation_enabled
            else 0.0,
        )
        max_orders = max(1, round(3 * max_policy_ratio))
        blocked_tickers = blocked_tickers or set()

        slippage_pct = self._settings.maps_order_slippage_pct
        max_gap_pct = self._settings.maps_order_max_gap_pct

        order_regime_label = regime.regime.value
        for candidate in candidates:
            # 스냅샷은 장세와 무관하게 항상 생성되므로(관측·주문 분리),
            # 생성 시점 게이트였던 preferred_regimes를 주문 시점 장세로 재검사한다.
            strategy_cls = _RUNNABLE_STRATEGIES.get(candidate.strategy_id)
            if strategy_cls is not None and order_regime_label not in strategy_cls.preferred_regimes:
                logger.info(
                    "Order skipped [%s %s]: preferred_regime_mismatch:%s",
                    candidate.strategy_id,
                    candidate.ticker,
                    order_regime_label,
                )
                skipped += 1
                continue
            strategy_type = self._strategy_type_for_id(candidate.strategy_id)
            policy = regime.entry_policy_for_strategy(
                strategy_type,
                contrarian_enabled=self._settings.maps_contrarian_accumulation_enabled,
                contrarian_entry_limit_ratio=self._settings.maps_contrarian_max_entry_ratio,
            )
            if not policy.allowed:
                logger.info(
                    "Order skipped [%s %s]: strategy_type=%s market_mode=%s reason=%s",
                    candidate.strategy_id,
                    candidate.ticker,
                    policy.strategy_type,
                    policy.market_mode.value,
                    policy.reason,
                )
                skipped += 1
                continue
            candidate_max_orders = max(1, round(3 * policy.entry_limit_ratio))
            if submitted >= min(max_orders, candidate_max_orders):
                logger.info(
                    "Order skipped [%s %s]: entry limit reached strategy_type=%s ratio=%.2f reason=%s",
                    candidate.strategy_id,
                    candidate.ticker,
                    policy.strategy_type,
                    policy.entry_limit_ratio,
                    policy.reason,
                )
                skipped += 1
                continue
            if candidate.ticker in blocked_tickers:
                skipped += 1
                continue
            if positions.get(candidate.ticker, 0) > 0:
                skipped += 1
                continue

            signal = self._latest_strategy_signal(
                db,
                ticker=candidate.ticker,
                strategy_id=candidate.strategy_id,
                ref_date=candidate.ref_date,
            )
            if signal is None or not signal.entry_signal:
                logger.info(
                    "Order skipped [%s %s]: strategy entry signal is not active",
                    candidate.strategy_id,
                    candidate.ticker,
                )
                skipped += 1
                continue

            # 신호 발생 시점 종가 (전략이 신호를 생성한 날 기준)
            signal_close = self._latest_close(db, candidate.ticker, candidate.ref_date)
            if signal_close <= 0:
                skipped += 1
                continue

            # 주문 시점 기준 DB 최신 종가 (당일 포함, 장 마감 후 수집된 전일 종가)
            current_close = self._latest_close(db, candidate.ticker, ref_date)
            if current_close <= 0:
                current_close = signal_close

            # 갭 체크: 신호 이후 시장이 MAX_GAP 이상 상승 → 신호 무효, 스킵
            gap_pct = (current_close - signal_close) / signal_close
            if gap_pct > max_gap_pct:
                logger.info(
                    "Order skipped [%s %s]: gap +%.1f%% since signal exceeds limit +%.1f%%",
                    candidate.strategy_id, candidate.ticker,
                    gap_pct * 100, max_gap_pct * 100,
                )
                skipped += 1
                continue

            # 지정가: ai_buy_price가 있고 현재 종가 대비 5% 이내면 우선 사용,
            # 그렇지 않으면 종가 × (1 + slippage) 계산
            if candidate.ai_buy_price and 0 < candidate.ai_buy_price <= current_close * 1.05:
                limit_price = float(candidate.ai_buy_price)
            else:
                limit_price = round_up_krx_price(
                    current_close * (1 + slippage_pct),
                    market=candidate.market,
                )

            remaining_slots = max(min(max_orders, candidate_max_orders) - submitted, 1)
            qty = self._order_qty(
                candidate,
                account.total_value,
                remaining_cash,
                limit_price,
                remaining_slots,
                atr14=signal.atr14,
            )
            if qty <= 0:
                skipped += 1
                continue

            order = Order(
                strategy_id=candidate.strategy_id,
                ticker=candidate.ticker,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=qty,
                limit_price=limit_price,
                current_price=current_close,
                # 사이징(_order_qty)에 넘긴 것과 **같은 변수**를 기록한다. 청산·화면이
                # 이 값을 재사용해야 손절폭이 진입 후에도 사이징 가정과 일치한다.
                atr14=signal.atr14,
                memo=(
                    f"candidate_snapshot:{candidate.ref_date.isoformat()} "
                    f"signal={signal_close:.0f} gap={gap_pct:+.3f}"
                ),
            )
            try:
                manager.submit(order, daily_pnl=daily_pnl)
            except KillSwitchError as exc:
                # Kill Switch 발동: 당일 추가 매수 전량 중단 (포지션 청산은 별도 승인 필요)
                logger.warning(
                    "Kill Switch 발동으로 매수 중단 [%s %s]: %s",
                    candidate.strategy_id,
                    candidate.ticker,
                    exc,
                )
                skipped += 1
                break
            except DuplicateOrderError:
                logger.info(
                    "Order skipped [%s %s]: already submitted",
                    candidate.strategy_id,
                    candidate.ticker,
                )
                skipped += 1
                continue
            except BrokerAdapterError as exc:
                # 브로커 거부(KIS 에러코드 등): 사유를 남기고 해당 후보만 건너뛴다 (잡 전체 중단 방지)
                logger.warning(
                    "매수 주문 실패 [%s %s]: %s",
                    candidate.strategy_id,
                    candidate.ticker,
                    exc,
                )
                skipped += 1
                continue
            submitted += 1
            remaining_cash = max(remaining_cash - qty * limit_price, 0.0)
            positions[candidate.ticker] = positions.get(candidate.ticker, 0) + qty

        # KillSwitch로 루프가 break 됐을 때 미처리 후보를 skipped에 반영한다.
        skipped += len(candidates) - submitted - skipped
        return submitted, skipped

    def _log_dry_run_candidates(self, db: Session, ref_date: dt.date) -> None:
        """Dry-run 모드: 실제 주문 없이 당일 후보 목록을 로깅한다."""
        candidates = (
            db.query(CandidateSnapshot)
            .filter(
                CandidateSnapshot.ref_date == ref_date,
                CandidateSnapshot.weekly_pass.is_(True),
            )
            .order_by(CandidateSnapshot.final_score.desc())
            .limit(20)
            .all()
        )
        for snap in candidates:
            holding = getattr(snap, "holding_type", None) or "SWING"
            ai_opinion = getattr(snap, "ai_contrarian_opinion", None) or "-"
            plan_buy = getattr(snap, "ai_buy_price", None)
            technical_stop = getattr(snap, "technical_stop", None)
            trading_target = getattr(snap, "trading_target", None)
            value_target = getattr(snap, "value_target", None)
            logger.info(
                "[DRY-RUN] %s %s | holding=%s | score=%.1f | ai=%s | "
                "plan_buy=%s | t_stop=%s | t_target=%s | v_target=%s",
                snap.ticker,
                snap.name,
                holding,
                snap.final_score,
                ai_opinion,
                f"{plan_buy:,.0f}" if plan_buy else "N/A",
                f"{technical_stop:,.0f}" if technical_stop else "N/A",
                f"{trading_target:,.0f}" if trading_target else "N/A",
                f"{value_target:,.0f}" if value_target else "N/A",
            )

    def _entry_trade_plan(
        self, db: Session, ticker: str, strategy_id: str, entry_date: dt.date
    ) -> CandidateSnapshot | None:
        """진입 시점에 해당하는 매매계획(candidate_snapshot)을 반환한다.

        ticker+strategy_id로, 진입일 이하 가장 최근 ref_date 스냅샷을 사용한다.
        """
        return (
            db.query(CandidateSnapshot)
            .filter(CandidateSnapshot.ticker == ticker)
            .filter(CandidateSnapshot.strategy_id == strategy_id)
            .filter(CandidateSnapshot.ref_date <= entry_date)
            .order_by(CandidateSnapshot.ref_date.desc())
            .first()
        )

    def _high_water_mark(
        self, db: Session, ticker: str, since_date: dt.date, fallback: float
    ) -> float:
        """진입일 이후 최고가(트레일링 스탑 기준). 데이터 없으면 fallback."""
        row = (
            db.query(func.max(HistoricalOHLCV.high))
            .filter(HistoricalOHLCV.ticker == ticker)
            .filter(HistoricalOHLCV.date >= since_date)
            .scalar()
        )
        return float(row) if row and row > 0 else fallback

    def _submit_exit_orders(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        ref_date: dt.date,
        exclude_tickers: set[str] | None = None,
    ) -> tuple[int, int, set[str]]:
        positions = self._broker_position_details(broker)
        if exclude_tickers:
            # 전략매매 브래킷이 직접 관리하는 종목은 전략 %/ATR 손절에서 제외 (이중 매도 방지)
            positions = {t: p for t, p in positions.items() if t not in exclude_tickers}
        if not positions:
            return 0, 0, set()

        rows = (
            db.query(OrderLog)
            .filter(OrderLog.ticker.in_(set(positions)))
            .filter(OrderLog.side == OrderSide.BUY.value)
            .filter(OrderLog.status.in_(["filled", "partially_filled"]))
            .order_by(OrderLog.created_at.desc(), OrderLog.id.desc())
            .all()
        )
        entries: dict[str, OrderLog] = {}
        for row in rows:
            if row.ticker not in entries:
                entries[row.ticker] = row

        # 체결 기록이 없는 포지션에 대한 폴백: expired 주문으로 진입 정보 복원
        # (당일 체결됐지만 sync에서 filled로 갱신되지 못한 경우 등)
        missing = set(positions) - set(entries)
        if missing:
            expired_rows = (
                db.query(OrderLog)
                .filter(OrderLog.ticker.in_(missing))
                .filter(OrderLog.side == OrderSide.BUY.value)
                .filter(OrderLog.status == "expired")
                .order_by(OrderLog.created_at.desc(), OrderLog.id.desc())
                .all()
            )
            for row in expired_rows:
                if row.ticker not in entries and row.strategy_id:
                    entries[row.ticker] = row
                    logger.warning(
                        "Stop-loss fallback: using expired buy order [%s %s] as entry (order_price=%.0f)",
                        row.strategy_id, row.ticker, row.order_price or 0,
                    )

        submitted = 0
        skipped = 0
        exit_tickers: set[str] = set()
        for ticker, position in positions.items():
            entry = entries.get(ticker)
            if entry is None or not entry.strategy_id:
                skipped += 1
                continue

            signal = self._latest_strategy_signal(
                db,
                ticker=ticker,
                strategy_id=entry.strategy_id,
                ref_date=ref_date,
            )
            current_price = (
                position.current_price
                if position.current_price is not None and position.current_price > 0
                else self._latest_close(db, ticker, ref_date)
            )
            entry_price = entry.fill_price or entry.order_price or position.avg_price
            # 감사 로그에 남길 가격. 현재가와 최근 종가가 모두 없으면(둘 다 0) 평균 단가로
            # 채운다 — order_price 가 NULL 이면 포지션 기반 체결 보정이 체결가를 채우지
            # 못해 매매일지 손익이 추정값이나 null 로 떨어진다(2026-06 매도 13건).
            # 청산 판정에는 쓰지 않는다. 폴백 가격으로 손절을 발동시키면 가짜 손절이 나간다.
            record_price = current_price if current_price > 0 else (position.avg_price or 0.0)
            # 진입 시점 ATR 을 우선한다. 그날그날의 ATR 로 다시 계산하면 손절가가
            # 보유 중에 움직여, 진입 시 한 번만 한 사이징이 가정한 손절폭과 어긋난다
            # (2026-07-31: 089860 위험 0.50% → 0.55%). 기록이 없는 옛 주문만 폴백.
            atr14 = entry.atr14 or (signal.atr14 if signal is not None else None)
            stop_price = effective_stop_price(entry.strategy_id, entry_price, atr14)
            strategy_exit = bool(signal and signal.exit_signal)

            if self._settings.maps_plan_based_exits_enabled:
                entry_date = entry.created_at.date() if entry.created_at else ref_date
                plan = self._entry_trade_plan(db, ticker, entry.strategy_id, entry_date)
                hwm = self._high_water_mark(
                    db, ticker, entry_date, fallback=max(entry_price or 0.0, current_price)
                )
                should_exit, reason = plan_exit_decision(
                    current_price=current_price,
                    entry_price=entry_price or 0.0,
                    hwm=hwm,
                    emergency_stop=getattr(plan, "emergency_stop", None),
                    technical_stop=getattr(plan, "technical_stop", None),
                    target=(getattr(plan, "final_sell_price", None) or getattr(plan, "trading_target", None)),
                    fallback_stop=stop_price,
                    strategy_exit=strategy_exit,
                    trail_activate_pct=self._settings.maps_trailing_activate_pct,
                    trail_stop_pct=self._settings.maps_trailing_stop_pct,
                )
            else:
                stop_triggered = (
                    stop_price is not None
                    and current_price > 0
                    and current_price <= stop_price
                )
                should_exit = stop_triggered or strategy_exit
                reason = "stop_loss" if stop_triggered else "strategy_exit"

            if not should_exit:
                continue

            order = Order(
                strategy_id=entry.strategy_id,
                ticker=ticker,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=position.quantity,
                current_price=record_price,
                memo=(
                    f"{reason} entry={entry_price:.0f} "
                    f"current={current_price:.0f} stop={stop_price or 0:.0f}"
                ),
            )
            try:
                manager.submit_exit(order, exit_reason=reason)
            except DuplicateOrderError:
                logger.info(
                    "Exit skipped [%s %s]: already submitted",
                    entry.strategy_id,
                    ticker,
                )
                skipped += 1
                exit_tickers.add(ticker)
                continue
            except BrokerAdapterError as exc:
                # 브로커 거부(KIS 에러코드 등): 사유를 남기고 해당 청산만 건너뛴다 (잡 전체 중단 방지)
                logger.warning(
                    "매도(청산) 주문 실패 [%s %s]: %s",
                    entry.strategy_id,
                    ticker,
                    exc,
                )
                skipped += 1
                continue
            submitted += 1
            exit_tickers.add(ticker)
            logger.info(
                "Exit submitted [%s %s]: %s current=%.0f stop=%s",
                entry.strategy_id,
                ticker,
                reason,
                current_price,
                f"{stop_price:.0f}" if stop_price is not None else "n/a",
            )

        return submitted, skipped, exit_tickers

    # ── 전략매매 브래킷 실행 엔진 (분석 워치리스트) ─────────────────────────
    def _active_strategy_trade_picks(self, db: Session) -> list[AnalysisPick]:
        """무장(ARMED) 또는 보유(BOUGHT) 상태의 전략매매 픽을 반환한다.

        기준일이 만료된 **ARMED** 픽은 제외한다. 진입 조건이 `현재가 <= 매수가` 라
        오래된(=높은) 매수가는 첫 틱에 즉시 발동하므로, 한 달 전 분석으로 오늘
        주문이 나가는 일을 여기서 끊는다(2026-07-30 실제 발생).

        **BOUGHT 는 절대 제외하지 않는다.** 실제 보유 주식이고 익절·손절을
        `_process_strategy_trades` 가 단독으로 관리한다 — 여기서 빼면 청산 관리
        없이 방치되어 원래 문제보다 나빠진다.
        """
        rows = (
            db.query(AnalysisPick).options(selectinload(AnalysisPick.legs))
            .filter(AnalysisPick.strategy_trade_enabled.is_(True))
            .filter(AnalysisPick.state.in_(["ARMED", "BOUGHT"]))
            .all()
        )
        cutoff = pick_cutoff_date(self._settings)
        active: list[AnalysisPick] = []
        for pick in rows:
            if pick.state == "ARMED" and is_pick_stale(pick, cutoff):
                logger.warning(
                    "전략매매 픽 만료 — 진입 제외 [%s] ref_date=%s 매수가=%s (기준일 >= %s 필요)",
                    pick.ticker, pick.ref_date, pick.buy_price, cutoff,
                )
                # 분할 계획은 이미 제출된 주문이 있을 수 있으므로 실행 경로에서
                # 취소·최종 체결 동기화를 마칠 때까지 제외하지 않는다.
                if pick.trade_mode != "split" or not pick.legs:
                    continue
            active.append(pick)
        return active

    def _strategy_trade_qty(self, broker, pick: AnalysisPick) -> int:
        """진입 수량을 산정한다. pick.qty 우선, 없으면 계좌 risk%(손절폭 기반).

        risk% 산정값은 현금 상한과 **단일종목 노출 한도(max_single_exposure)** 로 함께
        제한한다. 노출 한도를 적용하지 않으면 손절폭이 좁을 때 notional이 한도를 초과해
        OrderManager.submit이 ExposureCapError로 매 사이클 거부한다 → 진입 불가.
        """
        if pick.qty and pick.qty > 0:
            return int(pick.qty)
        if not (pick.buy_price and pick.stop_price and pick.buy_price > pick.stop_price):
            return 0
        try:
            account = broker.get_account_balance()
        except (NotImplementedError, BrokerAdapterError):
            return 0
        # 백테스트와 동일한 공용 계좌-위험 사이징 사용(C-2). 현금 상한·단일종목
        # 노출 상한을 함께 적용해 OrderManager.submit의 ExposureCapError 거부를 예방한다.
        return risk_based_qty(
            equity=account.total_value,
            entry_price=pick.buy_price,
            stop_price=pick.stop_price,
            account_risk=self._settings.maps_strategy_trade_account_risk_pct,
            max_exposure=self._settings.max_single_exposure,
            available_cash=account.cash if account.cash else None,
        )

    @staticmethod
    def _split_order_log(db: Session, leg: AnalysisPickLeg) -> OrderLog | None:
        if not leg.order_id:
            return None
        return db.query(OrderLog).filter(OrderLog.order_id == leg.order_id).first()

    @staticmethod
    def _split_entry_prefix(pick: AnalysisPick, leg: AnalysisPickLeg) -> str:
        return f"strategy_trade:{pick.id}:leg:{leg.sequence}:try:"

    @staticmethod
    def _reported_order_fill(row: OrderLog) -> int:
        reported = max(int(row.fill_qty or 0), 0)
        if (row.status or "").lower() == OrderStatus.FILLED.value and reported <= 0:
            reported = max(int(row.qty or 0), 0)
        return reported

    def _recover_split_leg_orders(self, db: Session, pick: AnalysisPick) -> bool:
        """Reattach audit rows left between broker submission and leg persistence."""
        changed = False
        live_statuses = {OrderStatus.PENDING.value, OrderStatus.PARTIALLY_FILLED.value}
        for leg in sorted(pick.legs, key=lambda item: item.sequence):
            prefix = self._split_entry_prefix(pick, leg)
            rows = (
                db.query(OrderLog)
                .filter(OrderLog.strategy_id.like(f"{prefix}%"))
                .filter(OrderLog.ticker == pick.ticker)
                .filter(OrderLog.side == OrderSide.BUY.value)
                .order_by(OrderLog.id.asc())
                .all()
            )
            if not rows:
                continue

            live_rows = [row for row in rows if (row.status or "").lower() in live_statuses]
            if leg.order_id is None and live_rows:
                leg.order_id = live_rows[0].order_id
                leg.current_order_fill_qty = self._reported_order_fill(live_rows[0])
                changed = True
            attached = next((row for row in rows if row.order_id == leg.order_id), None)
            attached_fill = self._reported_order_fill(attached) if attached is not None else 0
            historical_rows = [row for row in rows if row is not attached]
            historical_fill = sum(self._reported_order_fill(row) for row in historical_rows)
            # Some pre-recovery rows may not use the try-prefix. Preserve the part
            # already accumulated outside the prefixed audit set as a baseline.
            baseline_fill = max(
                int(leg.filled_qty or 0)
                - historical_fill
                - (int(leg.current_order_fill_qty or 0) if attached is not None else 0),
                0,
            )
            recovered_total = historical_fill + attached_fill
            # Broker/order-log reconciliation can temporarily report a lower fill
            # for the attached attempt.  Cumulative leg fills are irreversible;
            # never reduce them or the missing quantity could be bought again.
            target_fill = min(
                max(int(leg.filled_qty or 0), baseline_fill + recovered_total),
                leg.planned_qty,
            )
            if target_fill != int(leg.filled_qty or 0):
                priced = [
                    (self._reported_order_fill(row), float(row.fill_price or row.order_price or leg.entry_price))
                    for row in rows
                    if self._reported_order_fill(row) > 0
                ]
                weighted_total = baseline_fill + sum(qty for qty, _price in priced)
                if weighted_total > 0:
                    baseline_price = float(leg.fill_price or leg.entry_price)
                    leg.fill_price = (
                        baseline_fill * baseline_price
                        + sum(qty * price for qty, price in priced)
                    ) / weighted_total
                leg.filled_qty = target_fill
                changed = True
            if attached is not None:
                cursor_fill = max(int(leg.current_order_fill_qty or 0), attached_fill)
                if leg.current_order_fill_qty != cursor_fill:
                    # A broker report can temporarily move backwards. Keep this
                    # attempt's cursor at its high-water mark so a later recovery
                    # is counted only once; a newly submitted order resets it.
                    leg.current_order_fill_qty = cursor_fill
                    changed = True

            if leg.filled_qty >= leg.planned_qty and leg.status != "FILLED":
                leg.status = "FILLED"
                changed = True
            elif live_rows:
                next_status = "PARTIAL" if leg.filled_qty > 0 else "PENDING"
                if leg.status != next_status:
                    leg.status = next_status
                    changed = True
        return changed

    def _sync_split_legs(self, db: Session, pick: AnalysisPick) -> None:
        """Apply each current order's incremental fill exactly once."""
        changed = self._recover_split_leg_orders(db, pick)
        for leg in sorted(pick.legs, key=lambda item: item.sequence):
            row = self._split_order_log(db, leg)
            if row is None:
                continue
            status = (row.status or "").lower()
            reported = self._reported_order_fill(row)
            delta = max(reported - int(leg.current_order_fill_qty or 0), 0)
            if delta:
                old_qty = int(leg.filled_qty or 0)
                fill_price = float(row.fill_price or row.order_price or leg.entry_price)
                new_qty = min(old_qty + delta, leg.planned_qty)
                applied = new_qty - old_qty
                if applied > 0:
                    leg.fill_price = (
                        ((leg.fill_price or 0.0) * old_qty + fill_price * applied) / new_qty
                    )
                    leg.filled_qty = new_qty
                    changed = True
            cursor_fill = max(int(leg.current_order_fill_qty or 0), reported)
            if leg.current_order_fill_qty != cursor_fill:
                leg.current_order_fill_qty = cursor_fill
                changed = True

            if leg.filled_qty >= leg.planned_qty:
                if leg.status != "FILLED":
                    leg.status = "FILLED"
                    changed = True
            elif status in (OrderStatus.PENDING.value, OrderStatus.PARTIALLY_FILLED.value):
                next_status = "PARTIAL" if leg.filled_qty > 0 else "PENDING"
                if leg.status != next_status:
                    leg.status = next_status
                    changed = True
            elif status in (
                OrderStatus.CANCELLED.value,
                "expired",
                OrderStatus.REJECTED.value,
                OrderStatus.FILLED.value,
            ):
                leg.order_id = None
                leg.current_order_fill_qty = 0
                leg.status = "CANCELLED" if pick.entries_cancelled else "PENDING"
                changed = True

        if any(leg.filled_qty > 0 for leg in pick.legs) and pick.state == "ARMED":
            pick.state = "BOUGHT"
            changed = True
        if changed:
            db.commit()

    def _cancel_split_live_order(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        pick: AnalysisPick,
    ) -> bool:
        """Cancel the one attached live split order, returning confirmation."""
        live_statuses = {OrderStatus.PENDING.value, OrderStatus.PARTIALLY_FILLED.value}
        for leg in sorted(pick.legs, key=lambda item: item.sequence):
            row = self._split_order_log(db, leg)
            if row is None or (row.status or "").lower() not in live_statuses:
                continue
            try:
                cancelled = bool(broker.cancel_order(leg.order_id))
            except (NotImplementedError, BrokerAdapterError):
                cancelled = False
            if not cancelled:
                return False
            # 취소 응답 직전 체결을 먼저 브로커 결과로 동기화한 뒤, 여전히 live인
            # 감사 행만 cancelled로 확정한다. leg 연결은 동기화가 fill delta를
            # 반영할 때까지 유지한다.
            manager.sync_broker_state()
            row = db.query(OrderLog).filter(OrderLog.order_id == leg.order_id).first()
            if row is not None and (row.status or "").lower() in live_statuses:
                row.status = OrderStatus.CANCELLED.value
            db.commit()
            self._sync_split_legs(db, pick)
            return True
        return True

    def _process_split_strategy_trade(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        pick: AnalysisPick,
        pos: Position | None,
        current: float | None,
        now: dt.datetime,
    ) -> tuple[int, int]:
        """Process exits first, then submit at most one eligible split leg."""
        held_qty = pos.quantity if pos is not None and pos.quantity > 0 else 0

        exit_prefix = f"strategy_trade:{pick.id}:exit:try:"
        if pick.exit_pending_reason and not pick.exit_order_id:
            latest_exit = (
                db.query(OrderLog)
                .filter(OrderLog.strategy_id.like(f"{exit_prefix}%"))
                .filter(OrderLog.ticker == pick.ticker)
                .filter(OrderLog.side == OrderSide.SELL.value)
                .order_by(OrderLog.id.desc())
                .first()
            )
            if latest_exit is not None:
                pick.exit_order_id = latest_exit.order_id
                pick.exit_reason = latest_exit.exit_reason or pick.exit_pending_reason
                db.commit()

        if pick.exit_order_id:
            exit_row = db.query(OrderLog).filter(OrderLog.order_id == pick.exit_order_id).first()
            refreshed = broker.get_position(pick.ticker)
            held_qty = refreshed.quantity if refreshed is not None and refreshed.quantity > 0 else 0
            if held_qty <= 0:
                pick.state = "CLOSED"
                pick.strategy_trade_enabled = False
                pick.exit_pending_reason = None
                pick.last_action_at = now
                db.commit()
                return 0, 1
            if exit_row is not None and (exit_row.status or "").lower() in {
                OrderStatus.PENDING.value,
                OrderStatus.PARTIALLY_FILLED.value,
            }:
                return 0, 0
            if exit_row is not None and (exit_row.status or "").lower() == OrderStatus.FILLED.value:
                # Broker says the requested exit fully filled but still reports a
                # position. Treat the filled order as authoritative and wait for
                # position reconciliation; another sell could create a short.
                logger.error(
                    "Split exit filled but position remains [%s order=%s held=%d]",
                    pick.ticker,
                    pick.exit_order_id,
                    held_qty,
                )
                return 0, 0
            pick.exit_order_id = None
            pick.exit_pending_reason = pick.exit_pending_reason or pick.exit_reason
            db.commit()

        take = (
            current is not None
            and held_qty > 0
            and pick.target_price is not None
            and current >= pick.target_price
        )
        stop = (
            current is not None
            and held_qty > 0
            and pick.stop_price is not None
            and current <= pick.stop_price
        )
        if take or stop:
            pick.entries_cancelled = True
            pick.exit_pending_reason = "take_profit" if take else "stop_loss"
            for leg in pick.legs:
                if leg.filled_qty < leg.planned_qty and leg.order_id is None:
                    leg.status = "CANCELLED"
            db.commit()

        stale = is_pick_stale(pick, pick_cutoff_date(self._settings))
        if stale and not pick.entries_cancelled:
            pick.entries_cancelled = True
            for leg in pick.legs:
                if leg.filled_qty < leg.planned_qty and leg.order_id is None:
                    leg.status = "CANCELLED"
            db.commit()

        if pick.entries_cancelled or pick.exit_pending_reason:
            if not self._cancel_split_live_order(
                db=db, broker=broker, manager=manager, pick=pick
            ):
                return 0, 0
            refreshed = broker.get_position(pick.ticker)
            held_qty = refreshed.quantity if refreshed is not None and refreshed.quantity > 0 else 0

        if pick.exit_pending_reason and held_qty > 0:
            if current is None:
                return 0, 0
            reason = pick.exit_pending_reason
            attempt = (
                db.query(func.count(OrderLog.id))
                .filter(OrderLog.strategy_id.like(f"{exit_prefix}%"))
                .scalar()
                or 0
            ) + 1
            order = Order(
                strategy_id=f"{exit_prefix}{attempt}",
                ticker=pick.ticker,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=held_qty,
                current_price=current,
                memo=f"strategy_trade {reason} cur={current:.0f}",
            )
            try:
                result = manager.submit_exit(order, exit_reason=reason)
            except (DuplicateOrderError, BrokerAdapterError) as exc:
                logger.warning("Split strategy exit failed [%s] %s: %s", pick.ticker, reason, exc)
                return 0, 0
            pick.exit_order_id = result.order_id
            pick.exit_reason = reason
            pick.state = "BOUGHT"
            pick.last_action_at = now
            db.commit()
            refreshed = broker.get_position(pick.ticker)
            if refreshed is None or refreshed.quantity <= 0:
                pick.state = "CLOSED"
                pick.strategy_trade_enabled = False
                pick.exit_pending_reason = None
                db.commit()
                return 0, 1
            return 0, 0

        if pick.entries_cancelled:
            if held_qty > 0:
                pick.state = "BOUGHT"
                pick.strategy_trade_enabled = True
            else:
                pick.state = "WATCH"
                pick.strategy_trade_enabled = False
            db.commit()
            return 0, 0
        if current is None:
            return 0, 0

        legs = sorted(pick.legs, key=lambda item: item.sequence)
        next_leg = next((leg for leg in legs if leg.status != "FILLED"), None)
        if next_leg is None or next_leg.status == "CANCELLED" or next_leg.order_id:
            return 0, 0
        if any(
            leg.status != "FILLED"
            for leg in legs
            if leg.sequence < next_leg.sequence
        ):
            return 0, 0
        if current > next_leg.entry_price:
            return 0, 0
        remaining = max(next_leg.planned_qty - next_leg.filled_qty, 0)
        if remaining <= 0:
            next_leg.status = "FILLED"
            db.commit()
            return 0, 0
        try:
            account = broker.get_account_balance()
        except (NotImplementedError, BrokerAdapterError):
            return 0, 0
        required_cash = remaining * next_leg.entry_price
        if account.cash < required_cash:
            logger.warning(
                "Split entry held for cash [%s leg=%d]: need=%.0f cash=%.0f",
                pick.ticker,
                next_leg.sequence,
                required_cash,
                account.cash,
            )
            return 0, 0

        prefix = self._split_entry_prefix(pick, next_leg)
        attempt = (
            db.query(func.count(OrderLog.id))
            .filter(OrderLog.strategy_id.like(f"{prefix}%"))
            .scalar()
            or 0
        ) + 1
        order = Order(
            strategy_id=f"{prefix}{attempt}",
            ticker=pick.ticker,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=remaining,
            limit_price=next_leg.entry_price,
            current_price=current,
            memo=(
                f"strategy_trade split leg={next_leg.sequence} "
                f"remaining={remaining} cur={current:.0f}"
            ),
        )
        try:
            result = manager.submit(order)
        except (KillSwitchError, DuplicateOrderError, ExposureCapError, BrokerAdapterError) as exc:
            logger.warning("Split strategy entry failed [%s]: %s", pick.ticker, exc)
            return 0, 0
        next_leg.order_id = result.order_id
        next_leg.status = "PENDING"
        next_leg.current_order_fill_qty = 0
        pick.entry_order_id = result.order_id
        pick.last_action_at = now
        db.commit()
        return 1, 0

    def _process_strategy_trades(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        picks: list[AnalysisPick],
        prices: dict[str, float],
    ) -> tuple[int, int]:
        """무장된 워치리스트 픽에 대해 결정론적 브래킷(진입/익절/손절, OCO)을 집행한다.

        반환: (신규 진입 제출 수, 익절·손절로 종료된 수).
        한 픽의 주문 실패는 로깅 후 건너뛰어 broker_sync 잡 전체를 중단시키지 않는다.
        """
        if not picks:
            return 0, 0
        positions = self._broker_position_details(broker)
        now = dt.datetime.now(dt.timezone.utc)
        submitted = 0
        closed = 0

        def _current(ticker: str, pos: Position | None) -> float | None:
            if pos is not None and pos.current_price and pos.current_price > 0:
                return pos.current_price
            val = prices.get(ticker)
            return val if val and val > 0 else None

        for pick in picks:
            pos = positions.get(pick.ticker)
            is_split = pick.trade_mode == "split" and bool(pick.legs)
            if is_split:
                self._sync_split_legs(db, pick)

            # ARMED → BOUGHT 정산: 진입 주문이 체결되어 포지션이 생겼다.
            if not is_split and pick.state == "ARMED" and pick.entry_order_id and pos is not None and pos.quantity > 0:
                pick.state = "BOUGHT"
                pick.last_action_at = now
                db.commit()   # 브로커 체결과 즉시 동기화 (이후 단계 실패로 인한 롤백 desync 방지)

            current = _current(pick.ticker, pos)
            if is_split:
                split_submitted, split_closed = self._process_split_strategy_trade(
                    db=db,
                    broker=broker,
                    manager=manager,
                    pick=pick,
                    pos=pos,
                    current=current,
                    now=now,
                )
                submitted += split_submitted
                closed += split_closed
                continue
            if current is None:
                logger.info(
                    "전략매매 추적 [%s] %s: 현재가 없음 (장중 시세·포지션 모두 조회 실패)",
                    pick.ticker,
                    pick.state,
                )
                continue

            # 매 사이클 추적 로그: 현재가가 목표/손절(또는 진입가)에 얼마나 가까운지 기록한다.
            # 주문이 나가지 않는 사이클에도 추적 경과를 남겨 운영 중 가시성을 확보한다.
            if pick.state == "ARMED" and pick.buy_price:
                gap_pct = (current - pick.buy_price) / pick.buy_price * 100.0
                logger.info(
                    "전략매매 추적 [%s] ARMED: 현재가=%.0f 매수가=%.0f (%+.2f%%, %s)",
                    pick.ticker,
                    current,
                    pick.buy_price,
                    gap_pct,
                    "진입대기" if current > pick.buy_price else "진입조건 충족",
                )
            elif pick.state == "BOUGHT":
                to_target = (
                    (pick.target_price - current) / current * 100.0
                    if pick.target_price
                    else None
                )
                to_stop = (
                    (current - pick.stop_price) / current * 100.0
                    if pick.stop_price
                    else None
                )
                logger.info(
                    "전략매매 추적 [%s] BOUGHT: 현재가=%.0f 목표가=%s(%s) 손절가=%s(%s)",
                    pick.ticker,
                    current,
                    f"{pick.target_price:.0f}" if pick.target_price else "n/a",
                    f"+{to_target:.2f}%" if to_target is not None else "n/a",
                    f"{pick.stop_price:.0f}" if pick.stop_price else "n/a",
                    f"-{to_stop:.2f}%" if to_stop is not None else "n/a",
                )

            if pick.state == "ARMED":
                # 진입 주문이 체결 없이 종료(취소/만료/거부)되면 entry_order_id를 비워 재진입을 허용한다.
                if pick.entry_order_id and (pos is None or pos.quantity <= 0):
                    entry_log = (
                        db.query(OrderLog)
                        .filter(OrderLog.order_id == pick.entry_order_id)
                        .first()
                    )
                    if entry_log is not None and entry_log.status in ("cancelled", "expired", "rejected"):
                        logger.info(
                            "전략매매 진입 주문 %s — 재진입 허용 [%s]", entry_log.status, pick.ticker
                        )
                        pick.entry_order_id = None
                        db.commit()
                # 현재가 ≤ 매수가 & 미제출 → 지정가 진입
                if pick.entry_order_id is None and pick.buy_price and current <= pick.buy_price:
                    # 만료 픽 2차 가드. _active_strategy_trade_picks 와 중복이지만, 이 함수는
                    # picks 를 인자로 받으므로 그 필터를 우회한 호출부가 있으면 여기가 마지막
                    # 방어선이다 — 돈이 나가는 줄 바로 앞이라 중복을 감수한다.
                    if is_pick_stale(pick, pick_cutoff_date(self._settings)):
                        logger.warning(
                            "전략매매 진입 차단 — 픽 만료 [%s] ref_date=%s 매수가=%.0f 현재가=%.0f",
                            pick.ticker, pick.ref_date, pick.buy_price, current,
                        )
                        continue
                    qty = self._strategy_trade_qty(broker, pick)
                    if qty <= 0:
                        logger.warning("전략매매 진입 스킵 [%s]: 수량 0 (사이즈 산정 실패)", pick.ticker)
                        continue
                    order = Order(
                        strategy_id="strategy_trade",
                        ticker=pick.ticker,
                        side=OrderSide.BUY,
                        order_type=OrderType.LIMIT,
                        quantity=qty,
                        limit_price=pick.buy_price,
                        current_price=current,
                        memo=f"strategy_trade entry buy={pick.buy_price:.0f} cur={current:.0f}",
                    )
                    try:
                        result = manager.submit(order)
                    except (KillSwitchError, DuplicateOrderError, ExposureCapError, BrokerAdapterError) as exc:
                        logger.warning("전략매매 진입 실패 [%s]: %s", pick.ticker, exc)
                        continue
                    pick.entry_order_id = result.order_id
                    pick.last_action_at = now
                    db.commit()   # 주문(이미 커밋된 OrderLog)과 entry_order_id를 즉시 동기화
                    submitted += 1
                    logger.info("전략매매 진입 제출 [%s] qty=%d @%.0f", pick.ticker, qty, pick.buy_price)
                continue

            # BOUGHT: 목표/손절 도달 시 시장가 청산 (OCO — 먼저 발생한 쪽)
            if pick.state == "BOUGHT" and pos is not None and pos.quantity > 0:
                take = pick.target_price is not None and current >= pick.target_price
                stop = pick.stop_price is not None and current <= pick.stop_price
                if not take and not stop:
                    continue
                reason = "take_profit" if take else "stop_loss"
                order = Order(
                    strategy_id="strategy_trade",
                    ticker=pick.ticker,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    current_price=current,
                    memo=(
                        f"strategy_trade {reason} cur={current:.0f} "
                        f"target={pick.target_price or 0:.0f} stop={pick.stop_price or 0:.0f}"
                    ),
                )
                try:
                    result = manager.submit_exit(order, exit_reason=reason)
                except (DuplicateOrderError, BrokerAdapterError) as exc:
                    logger.warning("전략매매 청산 실패 [%s] %s: %s", pick.ticker, reason, exc)
                    continue
                pick.exit_order_id = result.order_id
                pick.exit_reason = reason
                pick.state = "CLOSED"
                pick.last_action_at = now
                db.commit()   # 청산(이미 커밋된 OrderLog)과 CLOSED 상태를 즉시 동기화
                closed += 1
                logger.info("전략매매 청산 [%s] %s qty=%d @%.0f", pick.ticker, reason, pos.quantity, current)

        return submitted, closed

    def _order_candidates(self, db: Session, ref_date: dt.date) -> list[CandidateSnapshot]:
        latest_date = (
            db.query(CandidateSnapshot.ref_date)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .order_by(CandidateSnapshot.ref_date.desc())
            .limit(1)
            .scalar()
        )
        # 신선도 가드: 최신 후보가 직전 거래 세션보다 오래되면(생성 실패·지연)
        # 오래된 신호로 실매수가 나가지 않도록 후보를 비운다.
        expected = previous_trading_day(ref_date, extra_closed_dates=self._settings.krx_closed_dates)
        if latest_date is None or latest_date < expected:
            logger.warning(
                "후보 스냅샷 오래됨 (latest=%s, expected>=%s) — 매수 후보 없음",
                latest_date, expected,
            )
            return []

        min_score = self._settings.maps_candidate_min_score
        eligible_stages = {"live_candidate", "live"}
        # 모의 계좌에서는 mock_candidate 전략도 주문을 낸다 — 그래야 live_candidate
        # 승격 조건인 mock_months(실제 체결 트랙레코드)가 쌓인다. 실계좌에서는 제외.
        if self._settings.is_paper_account:
            eligible_stages.add("mock_candidate")
        latest_promotions = self._latest_promotions(db)
        rows = (
            db.query(CandidateSnapshot)
            .filter(CandidateSnapshot.ref_date == latest_date)
            .filter(CandidateSnapshot.weekly_pass.is_(True))
            .filter(candidate_min_score_expression() >= min_score)
            .filter(candidate_recommendation_eligible_expression())
            .order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.trend_strength.desc())
            .all()
        )
        claimed = claimed_candidate_tickers(db, since=latest_date)
        # ticker당 최고 score 전략 1개만 사용 (동일 종목 중복 제거)
        seen_tickers: set[str] = set()
        result: list[CandidateSnapshot] = []
        for row in rows:
            if latest_promotions.get(row.strategy_id) not in eligible_stages:
                continue
            strategy_type = self._strategy_type_for_id(row.strategy_id)
            if (
                strategy_type == StrategyType.CONTRARIAN_QUALITY
                and row.valuation_margin_score is not None
                and row.valuation_margin_score < 60.0
            ):
                logger.info(
                    "Order candidate skipped [%s %s]: valuation_margin_score %.1f < 60 for contrarian quality",
                    row.strategy_id,
                    row.ticker,
                    row.valuation_margin_score,
                )
                continue
            if row.ticker in claimed:
                continue
            if row.ticker in seen_tickers:
                continue
            seen_tickers.add(row.ticker)
            result.append(row)
        return result

    @staticmethod
    def _latest_promotions(db: Session) -> dict[str, str]:
        """주문 주기에서 참조할 전략별 현재 단계를 반환한다.

        passed=True 레코드만 본다 (_latest_promotion_rows 와 동일 규칙).
        승격 실패(passed=False)는 이전에 획득한 단계를 강등시키지 않는다 —
        다음 단계 승격에 한 번 실패했다고 mock_candidate 전략의 주문 자격을
        박탈하면 안 되기 때문이다. 마지막 성공 단계가 곧 현재 단계다.
        """
        rows = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .all()
        )
        latest: dict[str, str] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row.to_stage
        return latest

    @staticmethod
    def _strategy_type_for_id(strategy_id: str | None):
        strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id or "")
        return getattr(strategy_cls, "strategy_type", None) if strategy_cls is not None else None

    @staticmethod
    def _latest_close(db: Session, ticker: str, ref_date: dt.date) -> float:
        row = (
            db.query(HistoricalOHLCV)
            .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
            .order_by(HistoricalOHLCV.date.desc())
            .first()
        )
        return float(row.close) if row and row.close > 0 else 0.0

    @staticmethod
    def _signal_from_frame(strategy_id: str, frame: pd.DataFrame) -> StrategySignal | None:
        """OHLCV 프레임에서 전략의 최신 신호를 뽑는다 — **신호 계산의 정본**.

        후보 생성(프레임을 이미 들고 있음)과 주문 시점(DB 조회)이 이 함수를 공유한다.
        같은 값을 두 곳에서 따로 계산하면 조용히 어긋난다 — 손절가에서 이미 겪었다
        (CLAUDE.md 손절 항목). 미등록 전략·빈 프레임은 예외가 아니라 None.
        """
        strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id)
        if strategy_cls is None or frame.empty:
            return None
        strategy = strategy_cls()
        signals = strategy.generate_signals(frame, strategy.default_params)
        if signals.empty:
            return None
        latest = signals.iloc[-1]
        atr_series = _compute_atr14(frame)
        last_atr = atr_series.iloc[-1] if not atr_series.empty else float("nan")
        return StrategySignal(
            entry_signal=bool(latest.get("entry_signal", False)),
            exit_signal=bool(latest.get("exit_signal", False)),
            close=float(latest.get("close", 0.0)),
            atr14=float(last_atr) if pd.notna(last_atr) else None,
        )

    @staticmethod
    def _latest_strategy_signal(
        db: Session,
        *,
        ticker: str,
        strategy_id: str,
        ref_date: dt.date,
    ) -> StrategySignal | None:
        """DB에서 최근 400봉을 읽어 `_signal_from_frame`에 넘기는 래퍼 (주문 시점 경로)."""
        if strategy_id not in _RUNNABLE_STRATEGIES:
            return None
        rows = (
            db.query(HistoricalOHLCV)
            .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
            .order_by(HistoricalOHLCV.date.desc())
            .limit(_SIGNAL_LOOKBACK_BARS)
            .all()
        )
        if not rows:
            return None
        frame = pd.DataFrame([
            {
                "date": row.date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in reversed(rows)
        ]).set_index("date")
        return OperationalPipeline._signal_from_frame(strategy_id, frame)

    def _order_qty(
        self,
        candidate: CandidateSnapshot,
        total_value: float,
        remaining_cash: float,
        price: float,
        remaining_slots: int,
        atr14: float | None = None,
    ) -> int:
        # 고정비중 예산: 단일종목 노출 상한과 잔여현금/슬롯 공정배분 중 작은 값.
        # 여러 후보 동시 진입 시 현금 고갈을 막는 상한으로 계속 사용한다.
        max_position_value = total_value * self._settings.max_single_exposure
        cash_budget = remaining_cash / max(remaining_slots, 1)
        budget = min(max_position_value, cash_budget)
        fixed_qty = int(budget // price) if price > 0 else 0

        # C-2: 백테스트와 동일한 계좌-위험 사이징을 우선 적용한다(손절폭 기반).
        # 손절가는 반드시 실제 청산에 쓰이는 값(effective_stop_price)이어야 한다.
        # 고정%만 쓰면 ATR 손절이 더 넓은 종목에서 손절폭을 과소평가해 포지션이
        # 과대 산정된다(계좌 위험이 설정값의 2배 이상으로 커진다).
        # 손절 정보가 없는 전략이면 고정비중으로 폴백한다. risk 기반 수량은
        # 고정비중 예산을 상한으로 두어 슬롯·현금 공정배분을 유지한다.
        stop = effective_stop_price(candidate.strategy_id, price, atr14)
        if stop is not None and 0 < stop < price:
            risk_qty = risk_based_qty(
                equity=total_value,
                entry_price=price,
                stop_price=stop,
                account_risk=self._settings.account_risk_per_trade,
                max_exposure=self._settings.max_single_exposure,
                available_cash=remaining_cash,
            )
            qty = min(risk_qty, fixed_qty) if fixed_qty > 0 else risk_qty
        else:
            qty = fixed_qty

        if candidate.estimated_qty and candidate.estimated_qty > 0:
            return min(int(candidate.estimated_qty), qty)
        return qty

    def _analyze_regime(self) -> RegimeResult:
        """현재 시황을 분석한다. 실패 시 WEAK+FAIL로 진입을 차단한다."""
        try:
            return create_regime_analyzer(self._settings).analyze()
        except Exception as exc:  # noqa: BLE001
            logger.warning("시황 분석 실패 — 진입 차단(WEAK+FAIL) 적용: %s", exc)
            from maps.market.regime import RegimeLabel  # noqa: PLC0415
            return RegimeResult(
                regime=RegimeLabel.WEAK,
                weekly_trend=WeeklyTrendLabel.FAIL,
                limit_ratio=0.0,
                kospi_ts=None,
            )

    def _make_risk_manager(self, broker, db: Session) -> RiskManager:
        return RiskManager(
            broker=broker,
            db=db,
            config=RiskConfig(
                daily_loss_limit=self._settings.daily_loss_limit,
                position_size_limit=self._settings.max_single_exposure,
                # 8단계: 테마·섹터 노출 한도 + 최소 현금 비중 (설정으로 활성화)
                sector_exposure_limit=self._settings.maps_max_sector_exposure,
                theme_exposure_limit=self._settings.maps_max_theme_exposure,
                sector_exposure_limit_enabled=self._settings.maps_sector_exposure_limit_enabled,
                theme_exposure_limit_enabled=self._settings.maps_theme_exposure_limit_enabled,
                min_cash_ratio_strong=self._settings.maps_min_cash_ratio_strong,
                min_cash_ratio_mixed=self._settings.maps_min_cash_ratio_mixed,
                min_cash_ratio_weak=self._settings.maps_min_cash_ratio_weak,
            ),
        )

    @staticmethod
    def _calc_daily_pnl(db: Session, ref_date: dt.date) -> float:
        """당일 포트폴리오 손익률을 계산한다.

        오늘 스냅샷과 직전 거래일 스냅샷의 total_assets 차이를 비율로 반환한다.
        스냅샷이 부족하면 0.0(Kill Switch 미발동)을 반환한다.
        """
        today_snap = (
            db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.ref_date == ref_date,
                PortfolioSnapshot.source == "broker",
            )
            .first()
        )
        prev_snap = (
            db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.ref_date < ref_date,
                PortfolioSnapshot.source == "broker",
            )
            .order_by(PortfolioSnapshot.ref_date.desc())
            .first()
        )
        if today_snap and prev_snap and prev_snap.total_assets > 0:
            return (today_snap.total_assets - prev_snap.total_assets) / prev_snap.total_assets
        return 0.0

    def _fetch_intraday_prices(
        self,
        tickers: list[str],
        broker: BrokerAdapter | None = None,
    ) -> dict[str, float]:
        """장중 현재가를 조회한다. 브로커 실시간 시세가 1순위, pykrx 배치는 폴백이다.

        pykrx 배치 조회는 15분 지연인 데다 장중 간헐적으로 실패한다. 실패하면 손절
        판단이 전일 종가로 퇴화해 당일 급락을 통째로 놓치므로(2026-07-27 475150 사례),
        임의 종목의 실시간 현재가를 주는 브로커(KIS inquire-price)를 먼저 쓰고
        조회되지 않은 종목만 pykrx로 보완한다.

        브로커 미지원(기본 no-op)·조회 실패 시에는 기존 pykrx 경로와 동일하게 동작한다.
        """
        target = [t for t in tickers if t]
        if not target:
            return {}

        prices: dict[str, float] = {}
        if broker is not None:
            try:
                live = broker.get_current_prices(target)
            except (BrokerAdapterError, NotImplementedError, ValueError) as exc:
                logger.warning("브로커 실시간 시세 조회 실패 — pykrx 폴백: %s", exc)
            else:
                prices = {t: float(p) for t, p in live.items() if p and float(p) > 0}

        missing = [t for t in target if t not in prices]
        if missing:
            prices.update(self._fetch_intraday_prices_pykrx(missing))
        return prices

    @staticmethod
    def _fetch_intraday_prices_pykrx(tickers: list[str]) -> dict[str, float]:
        """pykrx 배치 API로 장중 현재가를 조회한다 (15분 지연 KRX 데이터).

        KOSPI/KOSDAQ 전 종목을 각 1회 호출로 조회 후 보유 티커만 필터링한다.
        티커별 개별 호출 대신 배치 호출을 사용해 rate-limit 위험을 최소화한다.
        pykrx 미설치 또는 전체 조회 실패 시 빈 딕셔너리를 반환한다.
        """
        try:
            from maps.data.krx_auth import ensure_krx_login_guard  # noqa: PLC0415

            ensure_krx_login_guard()
            from pykrx import stock as _krx  # noqa: PLC0415
        except ImportError:
            return {}

        target = set(tickers)
        today = dt.date.today().strftime("%Y%m%d")
        prices: dict[str, float] = {}
        _COL_ALIASES = {"Close": "종가", "close": "종가"}

        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = _krx.get_market_ohlcv(today, market=market)
                if df is None or df.empty:
                    continue
                df = df.rename(columns=_COL_ALIASES)
                if "종가" not in df.columns:
                    continue
                for ticker in df.index:
                    if ticker not in target:
                        continue
                    val = df.at[ticker, "종가"]
                    if pd.notna(val) and float(val) > 0:
                        prices[ticker] = float(val)
            except Exception:  # noqa: BLE001
                logger.warning("pykrx 배치 현재가 조회 실패 [시장=%s 날짜=%s]", market, today)

        return prices

    def _expected_ohlcv_date(self, ref_date: dt.date) -> dt.date:
        """ref_date 기준으로 장 시작 전에 수집돼 있어야 할 가장 최근 KRX 거래일을 반환한다."""
        return previous_trading_day(ref_date, extra_closed_dates=self._settings.krx_closed_dates)

    # 수집 실패로 일부 티커만 갱신된 경우를 걸러내기 위한 최소 티커 수 기준
    _MIN_FRESH_TICKERS: int = 50

    def _is_data_fresh(self, db: Session, ref_date: dt.date) -> tuple[bool, dt.date | None, dt.date]:
        """OHLCV 데이터가 ref_date 기준으로 최신인지 확인한다.

        두 조건을 모두 충족해야 True를 반환한다:
        1. 전체 최신 날짜 >= expected_ohlcv_date (날짜 기준)
        2. expected 이후 데이터를 가진 티커 수 >= _MIN_FRESH_TICKERS (부분 수집 방지)

        반환: (is_fresh, latest_ohlcv_date, expected_ohlcv_date)
          - latest_ohlcv_date: 전체 최신 날짜 (진단/로그용). 데이터 없으면 None.
          - expected_ohlcv_date: 있어야 할 거래일.
        """
        expected = self._expected_ohlcv_date(ref_date)

        latest_row = (
            db.query(HistoricalOHLCV.date)
            .order_by(HistoricalOHLCV.date.desc())
            .first()
        )
        if latest_row is None:
            return False, None, expected
        latest_date: dt.date = latest_row[0]

        if latest_date < expected:
            return False, latest_date, expected

        fresh_count: int = (
            db.query(func.count(func.distinct(HistoricalOHLCV.ticker)))
            .filter(HistoricalOHLCV.date >= expected)
            .scalar()
        ) or 0
        return fresh_count >= self._MIN_FRESH_TICKERS, latest_date, expected

    @staticmethod
    def _broker_positions(broker) -> dict[str, int] | None:
        """브로커 보유 수량을 반환한다. 조회 미지원이면 None (빈 dict는 '전량 미보유'라는 유효한 상태)."""
        try:
            return broker.get_positions()
        except NotImplementedError:
            return None

    @staticmethod
    def _broker_position_details(broker) -> dict[str, Position]:
        fetch_positions = getattr(broker, "_fetch_positions_and_balance", None)
        if callable(fetch_positions):
            positions, _balance = fetch_positions()
            return positions
        return {
            ticker: position
            for ticker, qty in (OperationalPipeline._broker_positions(broker) or {}).items()
            if qty > 0 and (position := broker.get_position(ticker)) is not None
        }

    @staticmethod
    def _write_log(
        db: Session,
        *,
        ref_date: dt.date,
        source: str,
        status: str,
        items: int,
        note: str | None = None,
    ) -> None:
        db.add(CollectionLog(ref_date=ref_date, source=source, status=status, items=items, note=note))
        db.commit()


class MapsOperationalScheduler:
    """APScheduler wrapper for MAPS operational jobs."""

    def __init__(
        self,
        *,
        settings: MapsSettings | None = None,
        pipeline: OperationalPipeline | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline or OperationalPipeline(settings=self._settings)
        self._scheduler = BackgroundScheduler(timezone=self._settings.maps_scheduler_timezone)
        self._last_runs: dict[str, JobRun] = {}

    @property
    def running(self) -> bool:
        return self._scheduler.running

    def start(self) -> None:
        if self.running:
            return
        self._register_jobs()
        self._scheduler.start()
        logger.info("MAPS operational scheduler started")

    def shutdown(self) -> None:
        if self.running:
            self._scheduler.shutdown(wait=False)
            logger.info("MAPS operational scheduler stopped")

    def status(self) -> dict:
        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]
        return {
            "enabled": self._settings.maps_scheduler_enabled,
            "running": self.running,
            "timezone": self._settings.maps_scheduler_timezone,
            "jobs": jobs,
            "last_runs": {name: self._serialize_run(run) for name, run in self._last_runs.items()},
        }

    def run_once(self, job_name: str) -> JobRun:
        mapping = {
            "data_collection": self._pipeline.collect_data,
            "candidate_generation": self._pipeline.generate_candidates,
            "validation": self._pipeline.run_validation,
            "order_cycle": self._pipeline.run_order_cycle,
            "broker_sync": self._pipeline.sync_broker_state,
            "eod_cleanup": self._pipeline.run_eod_cleanup,
        }
        if job_name not in mapping:
            raise ValueError(f"Unknown scheduler job: {job_name}")
        return self._record(job_name, mapping[job_name])

    def backfill_ohlcv(self, start: dt.date, end: dt.date) -> JobRun:
        return self._record("ohlcv_backfill", lambda: self._pipeline.backfill_ohlcv(start, end))

    def backfill_fundamentals(self, start: dt.date, end: dt.date) -> JobRun:
        return self._record(
            "fundamental_backfill",
            lambda: self._pipeline.backfill_fundamentals(start, end),
        )

    def _make_krx_job(self, name: str) -> Callable:
        """KRX 거래일에만 실행되는 잡 콜러블을 반환한다.

        비거래일(주말·한국 공휴일)에 트리거되면 실행을 건너뛰고
        로그만 남긴다.
        """
        def _job() -> None:
            if not _is_krx_market_day():
                logger.info("Scheduler job [%s] skipped: KRX 비거래일", name)
                return
            self.run_once(name)
        return _job

    def _register_jobs(self) -> None:
        self._add_weekday_job("data_collection", self._settings.maps_data_collection_time)
        self._add_weekday_job("candidate_generation", self._settings.maps_candidate_time)
        self._add_weekday_job("validation", self._settings.maps_validation_time)
        self._add_weekday_job("order_cycle", self._settings.maps_order_time)
        self._scheduler.add_job(
            self._make_krx_job("broker_sync"),
            IntervalTrigger(seconds=self._settings.maps_broker_sync_interval_seconds),
            id="broker_sync",
            name="broker_sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # KIS 모의서버 timeout(30s) × 재시도(3회) = 최대 ~90s 소요 가능.
            # misfire_grace_time을 동일하게 설정해 정상 지연 시 missed 처리 방지.
            misfire_grace_time=self._settings.maps_broker_sync_interval_seconds * 2,
        )
        self._add_weekday_job("eod_cleanup", self._settings.maps_eod_time)
        hour, minute = _parse_hhmm(self._settings.maps_stock_report_time)
        self._scheduler.add_job(
            self._run_stock_report,
            CronTrigger(hour=hour, minute=minute),
            id="stock_report",
            name="stock_report",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    @staticmethod
    def _run_stock_report() -> None:
        """Generate all stock reports once per day, including weekends."""
        db = SessionLocal()
        try:
            run_ids = run_all_reports_if_idle(db)
            logger.info("Scheduler job stock_report: generated run_ids=%s", run_ids)
        finally:
            db.close()

    def _add_weekday_job(self, name: str, hhmm: str) -> None:
        """월~금 KRX 거래일(공휴일 제외)에만 실행되는 잡을 등록한다."""
        hour, minute = _parse_hhmm(hhmm)
        self._scheduler.add_job(
            self._make_krx_job(name),
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            id=name,
            name=name,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def _record(self, name: str, fn: Callable[[], JobRun]) -> JobRun:
        run = fn()
        self._last_runs[name] = run
        self._persist_run(run)
        details = json.dumps(run.details, ensure_ascii=False)
        if run.status == "failed":
            logger.error("Scheduler job %s: failed error=%s %s", name, run.message, details)
        else:
            logger.info("Scheduler job %s: %s %s", name, run.status, details)
        return run

    def _persist_run(self, run: JobRun) -> None:
        """잡 실행 결과를 job_run_log에 남긴다 (SCR-21 배치 모니터).

        인메모리 `_last_runs`는 재시작에 소실되므로 성공·실패를 DB에 영속한다.
        """
        # ponytail: broker_sync 성공은 60초마다 → 하루 ~500행 노이즈.
        # 성공 하트비트는 collection_log(source='scheduler.broker_sync')가 이미 담당.
        if run.name == "broker_sync" and run.status == "success":
            return
        try:
            db = self._pipeline._session_factory()
            try:
                db.add(
                    JobRunLog(
                        name=run.name,
                        status=run.status,
                        ref_date=dt.date.today(),
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        message=run.message,
                        details_json=json.dumps(run.details, ensure_ascii=False, default=str),
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - 기록 실패가 잡 자체를 죽이면 안 된다
            logger.exception("job_run_log persist failed: %s", run.name)

    @staticmethod
    def _serialize_run(run: JobRun) -> dict:
        return {
            "name": run.name,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "message": run.message,
            "details": run.details,
        }


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid HH:MM scheduler time: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid HH:MM scheduler time: {value!r}")
    return hour, minute


_scheduler: MapsOperationalScheduler | None = None


def get_operational_scheduler() -> MapsOperationalScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MapsOperationalScheduler()
    return _scheduler


def start_operational_scheduler_if_enabled() -> None:
    scheduler = get_operational_scheduler()
    if scheduler._settings.maps_scheduler_enabled:
        scheduler.start()


def shutdown_operational_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown()
