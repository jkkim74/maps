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
    weekly_pass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    estimated_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
