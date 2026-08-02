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
    # 화면 표시용 설명. 카탈로그에 없는 전략이면 display_name 은 ID 로 폴백한다.
    display_name: str
    summary: str | None = None
    preferred_regimes: list[str] = []
    stop_loss_pct: float | None = None
    has_guide: bool = False


class StrategyGuideResponse(BaseModel):
    """전략 상세 설명 — 산문 + 코드에서 읽어온 규칙 값."""

    strategy_id: str
    display_name: str
    summary: str
    idea: str
    entry_rules: list[str]
    exit_rules: list[str]
    strategy_group: str | None
    preferred_regimes: list[str]
    stop_loss_pct: float | None
    atr_multiplier: float | None
    mdd_limit: float | None
    default_params: dict
    # 네이버 블로그에 그대로 붙여넣을 수 있는 원고 전문. 파일이 없으면 None.
    guide_text: str | None


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
    # 체결 카드가 전략과 주문 수량을 표시할 수 있도록 내려준다. 없으면 화면이
    # 전략을 'broker'로, 수량을 공백으로 찍는다(모바일 주문 탭 표시 결함).
    strategy_id: str | None = None
    qty: int = 0


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
    # 실시간 잔고에서만 채워진다. DB 근사(fallback) 경로는 수량·평가금액을 알 수
    # 없어 0/None으로 남으며, 화면은 그때 해당 줄을 숨긴다.
    quantity: int = 0
    market_value: float | None = None


class RiskResponse(BaseModel):
    short_term_risk: float
    short_term_limit: float
    long_term_risk: float
    long_term_limit: float
    max_exposure_pct: float
    position_count: int                 # 보유 종목 수 — holdings 길이와 항상 일치
    gauges: list[RiskGaugeItem]
    holdings: list[HoldingItem]
    broker_status: str = "ok"           # ok | fallback | unavailable
    broker_error: str | None = None
    active_kill_count: int = 0          # 발동 중인 Kill Switch 수 (position_count와 별개)


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
    # 기간 지정·판정 실행 (0017) — 구 실행 행은 전부 None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    mode: str | None = None                      # per_ticker | portfolio
    universe: str | None = None                  # all | market:KOSPI | sector:반도체 | ...
    verdict: str | None = None                   # PASS | FAIL
    verdict_criteria: list[dict] | None = None   # [{criterion, value, threshold, passed}]
    stats: dict | None = None                    # {win_rate, payoff_ratio, yearly_returns, ...}


class BacktestRunRequest(BaseModel):
    strategy_id: str = "pullback_v3"
    params: dict | None = None
    start: datetime.date | None = None   # None = DB 보유 전체 (하위 호환)
    end: datetime.date | None = None
    mode: str = "per_ticker"             # per_ticker(종목별 평균) | portfolio(슬롯 리플레이)
    universe: str = "all"                # all | market | sector | theme | index | recent_ipo | custom
    universe_arg: str | None = None      # KOSPI·업종명·테마명·kospi200·일수 등
    tickers: list[str] | None = None     # custom 전용 (최대 30)


class BacktestResponse(BaseModel):
    recent_runs: list[BacktestRunItem]
    available_strategies: list[str] = []
    # 실행 설정 패널 표시용 실측값 — 하드코딩 문구가 실제 동작과 어긋나
    # "기간을 왜 못 바꾸나" 오해를 낳았다. 기간 미지정 시 DB 보유 전체를 쓴다.
    data_start: datetime.date | None = None
    data_end: datetime.date | None = None
    max_tickers: int = 30
    cost_summary: str | None = None
    # 대상 선택 드롭다운 채움용 (업종·테마는 security_metadata distinct)
    universe_options: dict = {}


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
    # 기준일 만료 여부. 필드명은 OrderPreviewResponse 의 data_stale/stale_reason 과 맞춘다
    # — 두 화면이 같은 어휘로 읽히도록. 날짜 연산은 전부 백엔드에서 끝낸다.
    data_stale: bool = False
    stale_reason: str | None = None        # expired
    age_trading_days: int | None = None    # 화면이 "만료 N거래일"을 그대로 찍을 수 있게


class AnalysisPicksResponse(BaseModel):
    total: int
    picks: list[AnalysisPickItem]
    expected_ref_date: str | None = None   # 이 날짜 이후 기준일만 신선(= cutoff)
    stale_count: int = 0


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


# ── 일일 다이제스트 (블로그 생성용 결정적 데이터팩) ────────────────────────────
#
# 이 모델들은 "하루치 매매 기록"의 유일한 수치 출처다. 블로그를 쓰는 에이전트는
# 여기 담긴 값만 인용할 수 있고 DB를 직접 조회하지 않는다. 없는 값은 지어내는
# 대신 measured=False / None 으로 내려보내 "미측정"임을 글에서 명시하게 한다.

class DigestFactor(BaseModel):
    """시장 팩터 1개. measured=False면 실제 데이터가 없어 중립값(50)이 들어간 것이다."""
    name: str
    score: float | None = None
    measured: bool = True
    note: str | None = None


class DigestMarket(BaseModel):
    regime: str                      # 히스테리시스 적용 후 최종 국면
    raw_regime: str | None = None    # 당일 지표만으로 계산한 국면
    weekly_trend: str
    vol_regime: str | None = None
    market_mode: str | None = None
    entry_limit_ratio: float | None = None
    breadth_pct: float | None = None
    breadth_label: str | None = None
    up_count: int | None = None
    total_assets: int | None = None
    kospi_above_ma5w: bool | None = None
    kospi_above_ma10w: bool | None = None
    kospi_ts: float | None = None
    floor_applied: bool = False
    # Korea weak guard(mixed→weak 하향) 적용 여부 — floor_applied의 대칭
    korea_weak_guard_applied: bool = False
    final_market_score: float | None = None
    factors: list[DigestFactor] = []
    reason: str | None = None
    source: str = "market_regime_log"


class DigestSector(BaseModel):
    sector: str
    score: float | None = None      # 레거시 선택기는 순위만 내므로 점수가 없다
    momentum_20d: float | None = None
    momentum_60d: float | None = None
    turnover_growth: float | None = None
    reason: str | None = None
    overheat_warning: str | None = None


class DigestSectors(BaseModel):
    """강세업종 — 매매 적용 여부와 무관하게 항상 관측한다.

    필터가 꺼져 있어도 "켰다면 어떤 업종이 뽑혔을지"를 매일 남겨두면, 나중에
    활성화를 판단할 때 쓸 근거가 쌓인다. 지금 없는 게 정확히 그 데이터다.
    """
    applied_to_trading: bool         # False면 관측만 — 후보 필터링에 쓰이지 않았다
    selector: str                    # kostolany | legacy — 실제 매매 경로와 같은 선택기
    selected: list[DigestSector] = []
    watchlist: list[str] = []
    overheated: list[str] = []
    excluded: dict[str, str] = {}
    # 스코어러가 중립값(50)으로 채운 입력들. 점수의 절반 가까이가 여기 걸려 있다.
    placeholder_inputs: list[str] = []
    reason: str | None = None


class DigestStrategy(BaseModel):
    strategy_id: str
    strategy_type: str | None = None
    stage: str | None = None                 # 승격 이력 없으면 "research" (암묵 초기 단계)
    preferred_regimes: list[str] = []
    active: bool = False                     # 오늘 진입 허용 여부 (국면 판정)
    block_reason: str | None = None          # 차단 사유 (active=False일 때)
    entry_limit_ratio: float | None = None
    # 국면상 active 여도 승격 단계가 주문 자격에 못 미치면 False — 주문 경로의
    # eligible_stages 판정과 동일 기준. "활성인데 왜 주문이 없나"를 다이제스트만으로 읽게 한다.
    orderable: bool = False


class DigestCandidate(BaseModel):
    ticker: str
    name: str
    market: str | None = None
    strategy_id: str
    final_score: float
    factor_score: float | None = None
    trend_strength: float | None = None
    ts_bucket: str | None = None
    score_reason: str | None = None
    excluded_reason: str | None = None
    holding_type: str | None = None
    estimated_qty: int | None = None
    ai_technical_score: float | None = None
    ai_analysis_memo: str | None = None
    ai_buy_price: float | None = None
    ai_stop_price: float | None = None
    ai_target_price: float | None = None
    # ai_* 가격 컬럼은 AI 미작동 시 룰 기반 폴백 값이 들어간다 — 출처를 명시해야
    # "AI 제시 매수가"로 잘못 읽히지 않는다. ai | analysis_pick | rule
    price_source: str | None = None
    ai_contrarian_opinion: str | None = None
    ai_contrarian_thesis: str | None = None
    ai_contrarian_anti_thesis: str | None = None
    valuation_margin_score: float | None = None
    valuation_margin_reason: str | None = None


class DigestExecution(BaseModel):
    order_id: str
    strategy_id: str | None = None
    ticker: str
    name: str | None = None
    side: str
    qty: int
    order_price: float | None = None
    fill_price: float | None = None
    fill_qty: int = 0
    status: str
    mode: str | None = None
    exit_reason: str | None = None           # 매도만. 없으면 컬럼 도입 전 기록이다.
    created_at: str
    entry_rationale: str | None = None       # 매수 시 후보 스냅샷의 score_reason


class DigestReportExcerpt(BaseModel):
    report_type: str
    trade_date: str | None = None
    excerpt: str


class DailyDigest(BaseModel):
    ref_date: str
    generated_at: str
    market: DigestMarket | None = None
    sectors: DigestSectors | None = None
    strategies: list[DigestStrategy] = []
    candidates: list[DigestCandidate] = []
    candidate_total: int = 0                 # 고유 ticker 수 (전략 중복 제거)
    candidate_excluded: int = 0              # 점수식이 excluded_reason 을 남긴 스냅샷 수
    # 데이터 품질 필터(universe_quality_log) 통계 — 후보 스냅샷과 별개의 상류 단계.
    # 스냅샷 행 수를 세면 "유니버스 × 전략 수"가 되어 제외율이 항상 0으로 보인다.
    universe_total: int | None = None
    universe_kept: int | None = None
    universe_excluded: int | None = None
    universe_rejection_ratio: float | None = None
    tomorrow_orders: OrderPreviewResponse | None = None
    executions: list[DigestExecution] = []
    market_context: list[DigestReportExcerpt] = []
    errors: list[str] = []


# ── SCR-21 Batch Monitor ──────────────────────────────────────────────────────
class BatchJobCell(BaseModel):
    date: str
    status: str                        # success | failed | missed | skipped | pending | running
    started_at: str | None = None
    duration_sec: float | None = None
    message: str | None = None
    detail: str | None = None          # picks_count / 하트비트 경과 / 파일 크기 등 요약


class BatchJobRow(BaseModel):
    name: str
    label: str                         # 한국어 표기
    schedule: str                      # "16:40" / "60초 간격" / "cron 16:00"
    rerunnable: bool                   # 오늘 실패/미실행 시 ↻ 버튼 노출 대상
    cells: list[BatchJobCell]


class BatchMonitorResponse(BaseModel):
    days: list[str]                    # 최신순
    jobs: list[BatchJobRow]
    generated_at: str
