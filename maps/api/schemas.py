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


# ── SCR-04 Candidates ─────────────────────────────────────────────────────────

class CandidateItem(BaseModel):
    ticker: str
    name: str
    factor_score: float
    trend_strength: float
    ts_bucket: str
    final_score: float
    weekly_pass: bool
    estimated_qty: int | None


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
    side: str
    qty: int
    order_price: float | None
    status: str
    created_at: str


class FillItem(BaseModel):
    order_id: str
    ticker: str
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
    slippage: SlippageStats


# ── SCR-06 Risk ───────────────────────────────────────────────────────────────

class RiskGaugeItem(BaseModel):
    strategy_id: str
    current_risk: float
    limit: float
    ratio: float


class HoldingItem(BaseModel):
    ticker: str
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
