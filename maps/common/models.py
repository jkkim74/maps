"""SQLAlchemy ORM 모델 — 전체 DB 스키마.

설계서 v2.6.2 §16 + v2.6.3 §10 기준.
audit 로그 테이블은 Day 1부터 존재해야 한다.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from maps.common.db import Base


# ---------------------------------------------------------------------------
# 종목 메타데이터
# ---------------------------------------------------------------------------
class SecurityMetadata(Base):
    """security_metadata — 종목 기본 정보 (상장·폐지·정지 이력 포함)."""

    __tablename__ = "security_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)          # KOSPI | KOSDAQ | ETF
    security_type: Mapped[str] = mapped_column(String(16), nullable=False)   # STOCK | ETF | SPAC
    listing_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    has_adjusted_price: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)      # WICS 업종 분류
    theme: Mapped[str | None] = mapped_column(String(64), nullable=True)        # 8단계: 테마 분류 (AI반도체·HBM 등)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


# ---------------------------------------------------------------------------
# 데이터 품질 감사 로그
# ---------------------------------------------------------------------------
class UniverseQualityLog(Base):
    """universe_quality_log — as-of-date 유니버스 생성 감사 로그."""

    __tablename__ = "universe_quality_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="backtest")
    total_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    kept_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejection_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 후보 스냅샷
# ---------------------------------------------------------------------------
class CandidateSnapshot(Base):
    """candidate_snapshot — 일별 전략 후보 종목 스냅샷."""

    __tablename__ = "candidate_snapshot"
    __table_args__ = (
        UniqueConstraint("ref_date", "strategy_id", "ticker", name="uq_candidate_snapshot_day_strategy_ticker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    factor_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trend_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ts_bucket: Mapped[str] = mapped_column(String(8), nullable=False, default="S3")
    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    component_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    score_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excluded_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    weekly_pass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    estimated_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AI 기술적 분석 결과 (maps_ai_technical_scoring_enabled=true 시 채워짐)
    ai_technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_analysis_memo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    valuation_margin_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_margin_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 6단계: 역발상 분할 매수 단계 (1차=25%, 2차=35%, 3차=40%)
    buy_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 7단계: AI 역발상 검증 결과
    ai_contrarian_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_contrarian_opinion: Mapped[str | None] = mapped_column(String(16), nullable=True)   # PASS|WATCH|REJECT
    ai_contrarian_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_contrarian_thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_contrarian_anti_thesis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 9단계: 보유 성격 분류
    holding_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # CORE|SWING|TRADING|WATCH|BAN

    # 10단계: 코스톨라니 가격 산출 (기존 plan_buy/stop/target은 유지)
    technical_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    emergency_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# OHLCV 히스토리
# ---------------------------------------------------------------------------
class HistoricalOHLCV(Base):
    """historical_ohlcv — 검증/WFA/MC용 일봉 가격 히스토리."""

    __tablename__ = "historical_ohlcv"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_historical_ohlcv_ticker_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="krx")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


# ---------------------------------------------------------------------------
# 펀더멘털 히스토리 (안전마진·가치 목표가 산출용)
# ---------------------------------------------------------------------------
class SecurityFundamental(Base):
    """security_fundamental — pykrx 기반 일별 밸류에이션 지표 히스토리.

    안전마진(ValuationMarginScorer)과 가치 목표가(KostolanyPriceCalculator.value_target)
    산출의 데이터 소스다. pykrx ``get_market_fundamental`` 컬럼(BPS/PER/PBR/EPS/DIV/DPS)을 적재한다.
    forward_per/roe 등 pykrx 미제공 지표는 nullable이며, 스코어러가 결측을 중립 처리한다.
    """

    __tablename__ = "security_fundamental"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_security_fundamental_ticker_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    per: Mapped[float | None] = mapped_column(Float, nullable=True)
    pbr: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    div: Mapped[float | None] = mapped_column(Float, nullable=True)   # 배당수익률(%)
    dps: Mapped[float | None] = mapped_column(Float, nullable=True)   # 주당배당금
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="pykrx")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


# ---------------------------------------------------------------------------
# 데이터 수집 이력
# ---------------------------------------------------------------------------
class CollectionLog(Base):
    """collection_log — 일별 데이터 수집 이력."""

    __tablename__ = "collection_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)   # krx | broker | manual
    status: Mapped[str] = mapped_column(String(16), nullable=False)   # success | partial | failed
    items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 검증 결과
# ---------------------------------------------------------------------------
class PortfolioSnapshot(Base):
    """portfolio_snapshot - daily broker account value history for dashboard metrics."""

    __tablename__ = "portfolio_snapshot"
    __table_args__ = (
        UniqueConstraint("ref_date", "source", name="uq_portfolio_snapshot_day_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="broker")
    total_assets: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    positions_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    holdings: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class ParameterPlateauResults(Base):
    """parameter_plateau_results — Plateau 그리드 탐색 결과."""

    __tablename__ = "parameter_plateau_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    total_combinations: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_combinations: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[str] = mapped_column(String(4), nullable=False)    # A | B | C | D | F
    best_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class WalkForwardResults(Base):
    """walk_forward_results — WFA 요약 결과."""

    __tablename__ = "walk_forward_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    n_folds: Mapped[int] = mapped_column(Integer, nullable=False)
    sharpe_mean: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe_std: Mapped[float] = mapped_column(Float, nullable=False)
    negative_folds: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_g2p: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fail_reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class WalkForwardFoldResults(Base):
    """walk_forward_fold_results — WFA fold별 상세 결과."""

    __tablename__ = "walk_forward_fold_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wfa_run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fold_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    is_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    is_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    oos_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    oos_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    is_sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    oos_sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    is_g2p: Mapped[float] = mapped_column(Float, nullable=False)
    oos_g2p: Mapped[float] = mapped_column(Float, nullable=False)
    g2p_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    best_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class MonteCarloSequenceResults(Base):
    """monte_carlo_sequence_results — MC 시뮬레이션 결과."""

    __tablename__ = "monte_carlo_sequence_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_group: Mapped[str] = mapped_column(String(32), nullable=False)
    run_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    n_simulations: Mapped[int] = mapped_column(Integer, nullable=False)
    mdd_p95: Mapped[float] = mapped_column(Float, nullable=False)
    mdd_limit: Mapped[float] = mapped_column(Float, nullable=False)
    mc_within_limit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 승격 감사 로그
# ---------------------------------------------------------------------------
class PromotionHistory(Base):
    """promotion_history — 전략 승격 결정 감사 로그."""

    __tablename__ = "promotion_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    tradeability_score: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fail_reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class TradeabilityWeightLog(Base):
    """tradeability_weight_log — Tradeability 프리셋 변경 이력."""

    __tablename__ = "tradeability_weight_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    preset_name: Mapped[str] = mapped_column(String(32), nullable=False)
    weights_json: Mapped[str] = mapped_column(Text, nullable=False)
    score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_after: Mapped[float] = mapped_column(Float, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 주문 감사 로그
# ---------------------------------------------------------------------------
class OrderLog(Base):
    """order_log — Mock + Live 공통 주문 감사 로그."""

    __tablename__ = "order_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)        # BUY | SELL
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)     # FILLED | CANCELLED | REJECTED | PARTIAL
    broker: Mapped[str | None] = mapped_column(String(16), nullable=True)  # mock | kis | kiwoom
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)    # mock | live_small | live
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# Stock Report 실행 이력
# ---------------------------------------------------------------------------
class StockReportRun(Base):
    """stock_report_runs — Stock Report 생성 이력 및 HTML 저장."""

    __tablename__ = "stock_report_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # premium | updown | summary | supply
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # running | completed | failed
    trade_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Kill Switch 감사 로그
# ---------------------------------------------------------------------------
class KillSwitchLog(Base):
    """kill_switch_log — Kill Switch 발동/해제 감사 로그."""

    __tablename__ = "kill_switch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # trigger | approved | deactivate
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_entry_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 전략 파라미터 이력
# ---------------------------------------------------------------------------
class StrategyParamLog(Base):
    """strategy_param_log — 실거래 파라미터 변경 이력."""

    __tablename__ = "strategy_param_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    effective_at: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)   # initial | wfa_update | manual
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# 비용 모델 가정 이력
# ---------------------------------------------------------------------------
class CostModelAssumptions(Base):
    """cost_model_assumptions — CostModel 가정값 변경 이력."""

    __tablename__ = "cost_model_assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commission_rate: Mapped[float] = mapped_column(Float, nullable=False)
    slippage_rate: Mapped[float] = mapped_column(Float, nullable=False)
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False)
    effective_at: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ---------------------------------------------------------------------------
# AI 분석 워치리스트 (SCR-19) — 자동주문 파이프라인과 분리
# ---------------------------------------------------------------------------
class AnalysisPick(Base):
    """analysis_pick — /analyze 등으로 선정한 종목의 워치리스트.

    candidate_snapshot(검증 통과 전략 전용)과 분리된 별도 보관소다.
    종목별 매수가/목표가/손절가와 분석 근거를 영속화하며, 화면에서 종목을
    클릭하면 종합분석 딥다이브를 재실행하는 출발점이 된다.
    strategy_trade_enabled/state는 향후 브래킷 실행 엔진(Part B)을 위한
    필드로, 본 단계에서는 보관만 한다.
    """

    __tablename__ = "analysis_pick"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)        # KOSPI | KOSDAQ
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="analyze")  # analyze | manual

    buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    strategy_context: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 전략매매(브래킷 실행)용
    strategy_trade_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="WATCH")  # WATCH|ARMED|BOUGHT|CLOSED|CANCELLED
    entry_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 진입 주문 order_log 연결
    exit_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)    # 청산(익절/손절) 주문 연결
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)      # take_profit | stop_loss
    last_action_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)  # 마지막 엔진 처리 시각

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


# ---------------------------------------------------------------------------
# AI 분석 실행 감사 로그 (SCR-19) — 0종목 실행도 기록해 cron 실패와 구분
# ---------------------------------------------------------------------------
class AnalysisRun(Base):
    """analysis_run — /analyze 파이프라인의 실행 이력 감사 로그.

    완료된 실행은 항상 1건을 기록한다(픽이 0개여도 picks_count=0 row를 남긴다).
    실패한 실행은 cron 래퍼가 status=failed 로 기록한다. row의 존재/상태로
    '0종목 정상완료'와 'cron 실패(기록 없음)'를 구분할 수 있다.
    """

    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    # completed | failed
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="analyze")
    regime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    strategy_context: Mapped[str | None] = mapped_column(String(128), nullable=True)
    picks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 모바일 네이티브 푸시(FCM) 디바이스 토큰 등록 (Phase 4)
# ---------------------------------------------------------------------------
class DeviceToken(Base):
    """device_token — FCM 네이티브 푸시를 받을 모바일 기기 등록 토큰.

    앱이 로그인 후 획득한 FCM 등록 토큰을 저장한다. FcmNotifier는 active=True인
    토큰으로만 발송한다. 토큰은 기기/앱 재설치 시 바뀌므로 unique 제약으로 upsert하고,
    로그아웃/해지 시 active=False로 비활성화한다(행 삭제 대신 감사 흔적 보존).
    """

    __tablename__ = "device_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="android")  # android | ios | web
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# 장세 판정 이력 — 히스테리시스(전일 판정 유지)와 floor 2일 확인의 근거 데이터
# ---------------------------------------------------------------------------
class MarketRegimeLog(Base):
    """market_regime_log — 일별 장세 판정 결과 기록.

    raw_regime은 당일 지표만으로 계산한 국면, applied_regime은 히스테리시스
    (buffer band·전일 유지·floor 2일 확인)를 적용한 최종 국면이다.
    스케줄러(candidate_generation·order_cycle)가 upsert하며, 주문 미리보기는
    최근 행의 applied_regime을 우선 사용한다.
    """

    __tablename__ = "market_regime_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, unique=True, index=True)
    raw_regime: Mapped[str] = mapped_column(String(16), nullable=False)      # strong | mixed | weak
    applied_regime: Mapped[str] = mapped_column(String(16), nullable=False)  # 히스테리시스 적용 후
    up_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_assets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_trend: Mapped[str] = mapped_column(String(8), nullable=False, default="pass")
    vol_regime: Mapped[str] = mapped_column(String(8), nullable=False, default="normal")
    floor_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    breadth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    kospi_above_ma5w: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    kospi_above_ma10w: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduler")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
