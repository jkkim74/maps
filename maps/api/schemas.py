"""공유 Pydantic 스키마."""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel


class AlertItem(BaseModel):
    level: str          # WARN | INFO | PASS | ERROR
    message: str
    timestamp: str


class EmptyResponse(BaseModel):
    message: str = "데이터 없음"


# ── SCR-01 Dashboard ──────────────────────────────────────────────────────────

class StrategyContribution(BaseModel):
    strategy_id: str
    name: str
    contribution_pct: float
    stage: str


class DashboardResponse(BaseModel):
    total_assets: float
    total_assets_mom_pct: float
    ytd_cagr: float
    current_mdd: float
    sharpe_1y: float
    active_strategies: int
    live_count: int
    mock_count: int
    last_updated: str
    contributions: list[StrategyContribution]
    alerts: list[AlertItem]


# ── SCR-02 Strategies ─────────────────────────────────────────────────────────

class StrategyItem(BaseModel):
    strategy_id: str
    name: str
    stage: str
    tradeability_score: float | None
    plateau_score: float | None
    mc_mdd_p95: float | None
    wfa_passed: bool | None
    wfa_cv: float | None
    promotion_pending: bool
    fail_reasons: list[str]


class PromotionHistoryItem(BaseModel):
    id: int
    strategy_id: str
    from_stage: str
    to_stage: str
    tradeability_score: float
    passed: bool
    fail_reasons: list[str]
    evaluated_at: str


class StrategiesResponse(BaseModel):
    strategies: list[StrategyItem]
    pending_promotions: int
    total: int


# ── SCR-03 Market ─────────────────────────────────────────────────────────────

class AssetTrend(BaseModel):
    name: str
    direction: str      # up | down | flat
    value: float | None


class MarketResponse(BaseModel):
    regime: str         # strong | mixed | weak
    weekly_trend: str   # pass | fail
    limit_ratio: float
    kospi_ts: float | None
    assets: list[AssetTrend]
    updated_at: str | None
    legacy_regime: str | None = None
    composite_regime: str | None = None
    market_mode: str | None = None
    price_trend_score: float | None = None
    volatility_score: float | None = None
    liquidity_score: float | None = None
    foreign_fx_score: float | None = None
    psychology_score: float | None = None
    final_market_score: float | None = None
    contrarian_entry_limit_ratio: float | None = None
    reason: str | None = None


# ── SCR-04 Candidates ─────────────────────────────────────────────────────────

class CandidateItem(BaseModel):
    ticker: str
    name: str
    market: str
    factor_score: float
    trend_strength: float
    ts_bucket: str
    final_score: float
    score_type: str | None = None
    strategy_type: str | None = None
    component_scores: dict[str, float] | None = None
    score_reason: str | None = None
    excluded_reason: str | None = None
    weekly_pass: bool
    estimated_qty: int | None
    ai_technical_score: float | None = None
    ai_buy_price: float | None = None
    ai_stop_price: float | None = None
    ai_target_price: float | None = None
    ai_analysis_memo: str | None = None
    valuation_margin_score: float | None = None
    valuation_margin_reason: str | None = None


class CandidatesResponse(BaseModel):
    strategy_id: str
    universe_count: int
    s5_excluded: int
    missing_count: int
    final_count: int
    candidates: list[CandidateItem]
    ref_date: str


# ── SCR-05 Orders ─────────────────────────────────────────────────────────────

class OrderQueueItem(BaseModel):
    order_id: str
    strategy_id: str | None
    ticker: str
    name: str
    side: str
    qty: int
    order_price: float | None
    status: str
    created_at: str


class FillItem(BaseModel):
    order_id: str
    ticker: str
    name: str
    side: str
    fill_price: float | None
    fill_qty: int
    status: str
    created_at: str


class SlippageStats(BaseModel):
    large_cap_actual: float | None
    large_cap_assumed: float
    mid_small_actual: float | None
    mid_small_assumed: float


class OrdersResponse(BaseModel):
    auto_order_active: bool
    pending: list[OrderQueueItem]
    fills_today: list[FillItem]
    expired: list[OrderQueueItem]
    slippage: SlippageStats


class PreviewOrderItem(BaseModel):
    ticker: str
    name: str
    strategy_id: str
    signal_date: str
    signal_close: float
    current_close: float
    gap_pct: float
    gap_exceeded: bool
    limit_price: int
    estimated_qty: int
    estimated_amount: int
    skipped: bool
    skip_reason: str | None
    live_eligible: bool = True   # True=실주문 대상(live_candidate/live), False=모의(mock_candidate)


class OrderPreviewResponse(BaseModel):
    next_trading_day: str
    as_of_date: str
    assumed_total_value: float
    assumed_cash: float
    max_orders: int
    slippage_pct: float
    max_gap_pct: float
    items: list[PreviewOrderItem]
    eligible_strategies: list[str]
    data_available: bool
    market_regime: str = "unknown"
    entry_limit_ratio: float = 0.5
    weekly_trend: str = "unknown"
    max_orders_effective: int = 3
    data_stale: bool = False
    expected_ref_date: str | None = None
    stale_reason: str | None = None
    latest_ohlcv_date: str | None = None


# ── SCR-06 Risk ───────────────────────────────────────────────────────────────

class RiskGaugeItem(BaseModel):
    strategy_id: str
    current_risk: float
    limit: float
    ratio: float


class HoldingItem(BaseModel):
    ticker: str
    name: str
    strategy_id: str
    entry_price: float
    current_price: float | None
    pnl_pct: float | None
    exposure_pct: float
    stop_price: float | None


class RiskResponse(BaseModel):
    short_term_risk: float
    short_term_limit: float
    long_term_risk: float
    long_term_limit: float
    max_exposure_pct: float
    position_count: int
    gauges: list[RiskGaugeItem]
    holdings: list[HoldingItem]
    broker_status: str = "ok"           # ok | fallback | unavailable
    broker_error: str | None = None


# ── SCR-07 Backtest ───────────────────────────────────────────────────────────

class BacktestRunItem(BaseModel):
    run_id: str
    strategy_id: str
    status: str         # queued | running | done | error
    progress_pct: float
    net_cagr: float | None
    mdd: float | None
    sharpe: float | None
    trade_count: int | None
    started_at: str | None


class BacktestRunRequest(BaseModel):
    strategy_id: str = "pullback_v3"
    params: dict | None = None


class BacktestResponse(BaseModel):
    recent_runs: list[BacktestRunItem]
    available_strategies: list[str] = []


# ── SCR-08 Robustness ─────────────────────────────────────────────────────────

class TradeabilityBreakdown(BaseModel):
    robustness: float | None
    risk: float | None
    recovery: float | None
    ret: float | None
    weight_preset: str
    weights: dict[str, float]   # 프리셋별 실제 가중치


class RobustnessResponse(BaseModel):
    strategy_id: str
    tradeability_score: float | None
    plateau_score: float | None
    mc_mdd_p95: float | None
    mc_mdd_limit: float | None
    bboot_mdd_p95: float | None
    oos_is_g2p: float | None
    cross_market_score: float | None
    breakdown: TradeabilityBreakdown | None
    plateau_grade: str | None
    plateau_total: int | None
    plateau_positive: int | None
    plateau_best_params: dict | None
    run_date: str | None


# ── SCR-09 TrendStrength ──────────────────────────────────────────────────────

class TsBucketItem(BaseModel):
    grade: str
    label: str
    count: int
    ratio: float


class TsHistoryPoint(BaseModel):
    date: str
    s1: int
    s2: int
    s3: int
    s4: int
    s5: int
    missing: int


class TrendStrengthResponse(BaseModel):
    ref_date: str
    universe_count: int
    missing_count: int
    buckets: list[TsBucketItem]
    history_30d: list[TsHistoryPoint]


# ── SCR-10 Research ───────────────────────────────────────────────────────────

class ResearchStrategyItem(BaseModel):
    strategy_id: str
    strategy_type: str
    stage: str
    signal_count: int | None
    mock_cagr: float | None
    mock_mdd: float | None
    observation_months: float | None
    next_gate: str


class ResearchResponse(BaseModel):
    strategies: list[ResearchStrategyItem]
    total: int
    alert_only_count: int
    mock_count: int


# ── SCR-11 WFA ────────────────────────────────────────────────────────────────

class FoldResultItem(BaseModel):
    fold_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_sharpe: float
    oos_sharpe: float
    is_g2p: float
    oos_g2p: float
    g2p_ratio: float
    best_params: dict[str, Any] | None


class WfaResponse(BaseModel):
    strategy_id: str
    passed: bool
    sharpe_mean: float | None
    cv: float | None
    negative_folds: int | None
    mean_g2p: float | None
    fail_reasons: list[str]
    folds: list[FoldResultItem]
    run_date: str | None


# ── SCR-12 Cost Sensitivity ───────────────────────────────────────────────────

class CostAssumption(BaseModel):
    tax_rate: float
    commission_rate: float
    slippage_large: float
    slippage_mid_small: float
    effective_at: str


class CostScenarioItem(BaseModel):
    label: str
    slip_delta_pct: float
    net_cagr: float | None
    net_sharpe: float | None
    tradeability: float | None
    status: str


class CostSensitivityResponse(BaseModel):
    strategy_id: str
    assumption: CostAssumption | None
    scenarios: list[CostScenarioItem]
    actual_large_slip: float | None
    actual_mid_small_slip: float | None


# ── SCR-13 Live Monitor ───────────────────────────────────────────────────────

class KillSwitchLogItem(BaseModel):
    id: int
    strategy_id: str | None
    event_type: str
    reason: str
    value: str | None
    new_entry_blocked: bool
    approved_by: str | None
    created_at: str


class LiveMonitorResponse(BaseModel):
    auto_response_active: bool
    pending_approval_count: int   # trigger 이벤트 중 미승인 건수
    pending_release_count: int    # approved 이벤트 중 미해제 건수
    actual_mdd: float | None
    large_slip_actual: float | None
    mid_small_slip_actual: float | None
    consec_failures: dict[str, int]
    pending_approvals: list[KillSwitchLogItem]   # 청산 승인 대기
    pending_releases: list[KillSwitchLogItem]    # Kill Switch 해제 대기
    recent_events: list[KillSwitchLogItem]


# ── SCR-14 Data Quality ───────────────────────────────────────────────────────

class RejectionReasonItem(BaseModel):
    reason_code: str
    description: str
    count: int
    ratio: float


class QualityHistoryPoint(BaseModel):
    date: str
    rejection_ratio: float
    total: int
    kept: int


class DataQualityResponse(BaseModel):
    ref_date: str
    mode: str
    total_candidates: int
    kept_count: int
    rejected_count: int
    rejection_ratio: float
    alert_sent: bool
    rejection_reasons: list[RejectionReasonItem]
    history_90d: list[QualityHistoryPoint]


# Ops configuration

class OpsConfigField(BaseModel):
    name: str
    env_var: str
    configured: bool
    required: bool
    value: str
    description: str


class OpsConfigSection(BaseModel):
    key: str
    title: str
    status: str
    fields: list[OpsConfigField]


class OpsConfigResponse(BaseModel):
    ready: bool
    broker_mode: str
    live_trading_enabled: bool
    data_provider: str
    missing_required: list[str]
    warnings: list[str]
    sections: list[OpsConfigSection]


class BrokerHealthResponse(BaseModel):
    """브로커 연결 진단 결과 (주문은 제출하지 않음)."""

    ok: bool
    broker_mode: str
    trading_mode: str            # describe_trading_mode() 결과 (PAPER/REAL/MOCK 등)
    account_masked: str          # 마스킹된 계좌번호
    cash: float | None = None
    positions_value: float | None = None
    total_assets: float | None = None
    error: str | None = None


class ReconciliationSideStat(BaseModel):
    side: str
    submitted: int
    filled: int
    partially_filled: int
    expired: int
    rejected: int
    fill_rate: float


class ReconciliationUnfilledItem(BaseModel):
    kst_date: str
    strategy_id: str
    ticker: str
    side: str
    status: str
    qty: int
    order_price: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    day_close: float | None = None
    reachable: bool | None = None


class ReconciliationResponse(BaseModel):
    """주문 정산 리포트 — 제출/체결/만료 집계 + 미체결 진단."""

    start_kst: str
    end_kst: str
    total_orders: int
    by_side: list[ReconciliationSideStat]
    unfilled: list[ReconciliationUnfilledItem]


# ── Stock Report ──────────────────────────────────────────────────────────────

class StockReportRunItem(BaseModel):
    id: int
    report_type: str
    report_name: str
    status: str          # running | completed | failed
    trade_date: str
    has_html: bool
    error_message: str | None
    created_at: str
    completed_at: str | None


class StockReportRunsResponse(BaseModel):
    runs: list[StockReportRunItem]
    running_count: int
    total: int


# ── Daily PnL ─────────────────────────────────────────────────────────────────

class DailyReturnItem(BaseModel):
    date: str
    total_assets: float
    pnl_pct: float        # 전일 대비 수익률 (소수, 예: 0.012 = +1.2%)
    pnl_amount: float     # 전일 대비 손익 금액 (원)


class DailyPnlResponse(BaseModel):
    days: int
    cumulative_pct: float     # 기간 전체 누적 수익률
    items: list[DailyReturnItem]


# ── Mobile Portfolio History (추이 차트) ───────────────────────────────────────

class PortfolioHistoryPoint(BaseModel):
    """모바일 추이 차트의 한 시점(일별) 값."""

    date: str            # ISO date (YYYY-MM-DD)
    total_value: float   # 해당일 총 자산(원)
    pnl_pct: float       # 전일 대비 수익률 (소수, 예: 0.012 = +1.2%)


class PortfolioHistoryResponse(BaseModel):
    """모바일 자산 추이 차트용 시계열 응답."""

    days: int
    cumulative_pct: float                  # 기간 전체 누적 수익률
    points: list[PortfolioHistoryPoint]


# ── SCR-17 Trade Review ───────────────────────────────────────────────────────

class TradeReviewItem(BaseModel):
    ticker: str
    name: str
    strategy_id: str
    entry_date: str
    entry_price: float
    qty: int
    entry_cost: float
    exit_date: str | None
    exit_price: float | None
    exit_proceeds: float | None
    pnl: float | None
    pnl_pct: float | None
    hold_days: int | None
    status: str             # open | closed | estimated_exit
    note: str | None


class TradeReviewSummary(BaseModel):
    initial_assets: float
    current_assets: float
    total_return_pct: float
    total_trades: int
    open_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


class StrategyTradeStats(BaseModel):
    strategy_id: str
    total_trades: int
    wins: int
    losses: int
    unknown: int
    win_rate: float | None
    total_pnl: float
    total_cost: float
    return_pct: float | None


class TradeReviewResponse(BaseModel):
    summary: TradeReviewSummary
    trades: list[TradeReviewItem]
    by_strategy: list[StrategyTradeStats]


# ── SCR-19 Analysis Picks (분석 워치리스트) ──────────────────────────────────

class AnalysisPickItem(BaseModel):
    id: int
    ref_date: str
    ticker: str
    name: str
    market: str | None = None
    current_price: float | None = None     # 현재가(최신 일봉 종가 기준, 수집 후 당일 종가)
    source: str
    buy_price: float | None = None
    fill_price: float | None = None        # 실 진입 체결가(entry_order_id→OrderLog.fill_price)
    target_price: float | None = None
    stop_price: float | None = None
    qty: int | None = None
    rationale: str | None = None
    regime: str | None = None
    strategy_context: str | None = None
    strategy_trade_enabled: bool
    state: str
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    exit_reason: str | None = None         # take_profit | stop_loss (완료 목록 구분용)
    last_action_at: str | None = None
    rr_ratio: float | None = None          # 손익비 = (목표가-진입가)/(진입가-손절가), 진입가=체결가 우선
    created_at: str


class AnalysisPicksResponse(BaseModel):
    total: int
    picks: list[AnalysisPickItem]


class AnalysisPickCreate(BaseModel):
    ticker: str
    name: str | None = None
    market: str | None = None
    source: str = "analyze"
    ref_date: str | None = None            # 미지정 시 오늘(KST 날짜)
    buy_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    qty: int | None = None
    rationale: str | None = None
    regime: str | None = None
    strategy_context: str | None = None


class AnalysisPickBatchCreate(BaseModel):
    """단건 또는 배열 일괄 적재 — 항상 picks 리스트로 받는다."""
    picks: list[AnalysisPickCreate]


class AnalysisPickUpdate(BaseModel):
    """가격/메모만 수정한다. state·strategy_trade_enabled는 arm/disarm 전용."""
    buy_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    qty: int | None = None
    rationale: str | None = None
