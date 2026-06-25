"""다음 거래일 예정 주문 미리보기 — 브로커 호출 없이 DB+settings 기반 시뮬레이션."""

from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy.orm import Session

from maps.api.schemas import OrderPreviewResponse, PreviewOrderItem
from maps.common.models import CandidateSnapshot, HistoricalOHLCV, PortfolioSnapshot, PromotionHistory
from maps.common.settings import MapsSettings
from maps.market.trading_rules import round_up_krx_price
from maps.ops.order_state import claimed_candidate_tickers
from maps.ops.scheduler import OperationalPipeline, _is_krx_market_day

logger = logging.getLogger(__name__)

# 실주문(scheduler._order_candidates)과 동일한 자격 — mock_candidate는 주문 대상이 아니므로 제외
_MAX_ORDERS = 3
_ELIGIBLE_STAGES = {"live_candidate", "live"}
_MOCK_STAGE = "mock_candidate"
_LOOKAHEAD_DAYS = 14  # 최대 탐색 일수 (긴 연휴 대비)


# ── 핵심 계산 함수 ────────────────────────────────────────────────────────────


def next_trading_day(from_date: dt.date) -> dt.date:
    """from_date 다음 날부터 KRX 거래일인 최초 날짜를 반환한다."""
    candidate = from_date + dt.timedelta(days=1)
    for _ in range(_LOOKAHEAD_DAYS):
        if _is_krx_market_day(candidate):
            return candidate
        candidate += dt.timedelta(days=1)
    return candidate


def prev_trading_day(from_date: dt.date) -> dt.date:
    """from_date 이전 날부터 KRX 거래일인 최초(가장 가까운 과거) 날짜를 반환한다."""
    candidate = from_date - dt.timedelta(days=1)
    for _ in range(_LOOKAHEAD_DAYS):
        if _is_krx_market_day(candidate):
            return candidate
        candidate -= dt.timedelta(days=1)
    return candidate


def _latest_promotions(db: Session) -> dict[str, str]:
    """전략별 최신 단계 반환 (passed=True 레코드만).

    승격 실패(passed=False)는 무시한다 — 다음 단계 승격 실패가 이미 획득한
    단계를 강등시켜 주문 자격을 박탈하면 안 되기 때문이다. 마지막 성공 단계가
    곧 현재 단계다.
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


def _get_order_candidates(db: Session, min_score: float = 0.0) -> list[CandidateSnapshot]:
    """주문 가능 전략의 최신 후보 종목을 final_score 내림차순으로 반환한다."""
    latest_date = (
        db.query(CandidateSnapshot.ref_date)
        .order_by(CandidateSnapshot.ref_date.desc())
        .limit(1)
        .scalar()
    )
    if latest_date is None:
        return []

    promotions = _latest_promotions(db)
    rows = (
        db.query(CandidateSnapshot)
        .filter(CandidateSnapshot.ref_date == latest_date)
        .filter(CandidateSnapshot.weekly_pass.is_(True))
        .filter(CandidateSnapshot.final_score >= min_score)
        .order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.trend_strength.desc())
        .all()
    )
    claimed = claimed_candidate_tickers(db, since=latest_date)
    held = _held_tickers(db)
    # ticker당 최고 score 전략 1개만 사용 (동일 종목 중복 표시 제거)
    seen_tickers: set[str] = set()
    result: list[CandidateSnapshot] = []
    for row in rows:
        if promotions.get(row.strategy_id) not in _ELIGIBLE_STAGES:
            continue
        if row.ticker in claimed or row.ticker in held:
            continue
        if row.ticker in seen_tickers:
            continue
        seen_tickers.add(row.ticker)
        result.append(row)
    return result


def _mock_candidate_count(db: Session, latest_date: dt.date, min_score: float) -> int:
    """최신 스냅샷에서 mock_candidate 단계(주문 대상 아님)의 종목 수를 센다(중복 제거)."""
    promotions = _latest_promotions(db)
    mock_strategies = {s for s, stage in promotions.items() if stage == _MOCK_STAGE}
    if not mock_strategies:
        return 0
    rows = (
        db.query(CandidateSnapshot.ticker, CandidateSnapshot.strategy_id)
        .filter(CandidateSnapshot.ref_date == latest_date)
        .filter(CandidateSnapshot.weekly_pass.is_(True))
        .filter(CandidateSnapshot.final_score >= min_score)
        .all()
    )
    return len({t for t, s in rows if s in mock_strategies})


def _latest_close(db: Session, ticker: str, ref_date: dt.date) -> float:
    """ref_date 이하 가장 최근 종가를 반환한다."""
    row = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
        .order_by(HistoricalOHLCV.date.desc())
        .first()
    )
    return float(row.close) if row and row.close and row.close > 0 else 0.0


def _estimated_qty(
    total_value: float,
    cash: float,
    limit_price: float,
    remaining_slots: int,
    max_single_exposure: float,
) -> int:
    if limit_price <= 0:
        return 0
    max_pos_value = total_value * max_single_exposure
    cash_budget = cash / max(remaining_slots, 1)
    budget = min(max_pos_value, cash_budget)
    return int(budget // limit_price)


def _get_assumed_balance(db: Session) -> tuple[float, float]:
    """portfolio_snapshot 최신값 또는 기본 fallback으로 총자산·현금을 반환한다."""
    row = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.source == "broker")
        .order_by(PortfolioSnapshot.ref_date.desc())
        .first()
    )
    if row and row.total_assets and row.total_assets > 0:
        return float(row.total_assets), float(row.cash)
    return 100_000_000.0, 100_000_000.0  # 기본 1억원


def _held_tickers(db: Session) -> set[str]:
    """broker_sync가 저장한 최신 보유 종목 집합을 반환한다."""
    row = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.source == "broker")
        .order_by(PortfolioSnapshot.ref_date.desc())
        .first()
    )
    if row and row.holdings:
        return {t for t, qty in row.holdings.items() if qty > 0}
    return set()


# ── 최상위 조립 함수 ──────────────────────────────────────────────────────────


def build_order_preview(db: Session, settings: MapsSettings) -> OrderPreviewResponse:
    """다음 거래일 예정 주문 미리보기를 계산한다."""
    today = dt.date.today()
    next_day = next_trading_day(today)

    # 장세 분석
    try:
        from maps.market.regime import MarketRegimeAnalyzer
        from maps.api.market import _CombinedWeeklyProvider
        _regime = MarketRegimeAnalyzer(_CombinedWeeklyProvider()).analyze()
        market_regime = _regime.regime.value
        weekly_trend = _regime.weekly_trend.value
        entry_limit_ratio = _regime.entry_limit_ratio
    except Exception as _e:
        logger.warning("장세 분석 실패, 기본값 사용: %s", _e)
        market_regime = "unknown"
        weekly_trend = "unknown"
        entry_limit_ratio = 0.5
    effective_max = max(1, math.ceil(_MAX_ORDERS * entry_limit_ratio))

    # 최신 스냅샷 날짜 (후보 필터 전 — stage 정렬로 candidates가 비어도 스냅샷 존재 여부는 별개)
    latest_date = (
        db.query(CandidateSnapshot.ref_date)
        .order_by(CandidateSnapshot.ref_date.desc())
        .limit(1)
        .scalar()
    )
    data_available = latest_date is not None
    snapshot_date = latest_date.isoformat() if latest_date else None

    # 계좌 잔고 추정 + 설정
    total_value, cash = _get_assumed_balance(db)
    slippage = settings.maps_order_slippage_pct
    max_gap = settings.maps_order_max_gap_pct
    max_exposure = settings.max_single_exposure
    min_score = settings.maps_candidate_min_score

    # 실주문 자격(live_candidate/live) 전략 + mock 단계 종목 수(주문 대상 아님)
    promotions = _latest_promotions(db)
    eligible_strategies = sorted({s for s, stage in promotions.items() if stage in _ELIGIBLE_STAGES})
    mock_count = _mock_candidate_count(db, latest_date, min_score) if latest_date else 0

    def _resp(items, skipped, *, ref, stale, reason) -> OrderPreviewResponse:
        return OrderPreviewResponse(
            next_trading_day=next_day.isoformat(),
            as_of_date=ref,
            assumed_total_value=total_value,
            assumed_cash=cash,
            max_orders=_MAX_ORDERS,
            slippage_pct=slippage,
            max_gap_pct=max_gap,
            items=items,
            eligible_strategies=eligible_strategies,
            data_available=data_available,
            market_regime=market_regime,
            entry_limit_ratio=entry_limit_ratio,
            weekly_trend=weekly_trend,
            max_orders_effective=effective_max,
            snapshot_date=snapshot_date,
            snapshot_stale=stale,
            snapshot_reason=reason,
            skipped_summary=skipped,
            mock_candidate_count=mock_count,
        )

    if not data_available:
        return _resp([], {}, ref=today.isoformat(), stale=False, reason=None)

    # 신선도 게이트: 최신 스냅샷이 직전 거래일보다 오래되면 옛 후보를 "예정 주문"으로 나열하지 않는다.
    expected = prev_trading_day(today)
    if latest_date < expected:
        reason = (
            f"최신 후보 미생성 — 사용 가능한 최신 스냅샷 {snapshot_date} "
            f"(기대 {expected.isoformat()} 이후). 현재 장세 regime={market_regime}. 익일 예정 주문 없음."
        )
        return _resp([], {}, ref=snapshot_date, stale=True, reason=reason)

    candidates = _get_order_candidates(db, min_score=min_score)
    ref_date = candidates[0].ref_date if candidates else latest_date

    items: list[PreviewOrderItem] = []
    skipped_summary: dict[str, int] = {}
    submitted = 0
    seen_tickers: set[str] = set()
    remaining_cash = cash

    def _skip(reason: str) -> None:
        skipped_summary[reason] = skipped_summary.get(reason, 0) + 1

    for candidate in candidates:
        if submitted >= effective_max:
            break
        if candidate.ticker in seen_tickers:
            continue

        signal_close = _latest_close(db, candidate.ticker, ref_date)
        if signal_close <= 0:
            _skip("no_price")
            continue

        current_close = _latest_close(db, candidate.ticker, today)
        if current_close <= 0:
            current_close = signal_close

        gap_pct = (current_close - signal_close) / signal_close
        limit_price = round_up_krx_price(current_close * (1 + slippage), market=candidate.market)
        remaining_slots = max(effective_max - submitted, 1)

        # 진입신호 미발생 → 스킵 (행 대신 집계)
        sig = OperationalPipeline._latest_strategy_signal(
            db, ticker=candidate.ticker, strategy_id=candidate.strategy_id, ref_date=today
        )
        if sig is None or not sig.entry_signal:
            _skip("no_entry_signal")
            continue
        if gap_pct > max_gap:
            _skip("gap_exceeded")
            continue
        qty = _estimated_qty(total_value, remaining_cash, limit_price, remaining_slots, max_exposure)
        if qty <= 0:
            _skip("insufficient_cash")
            continue

        amount = limit_price * qty
        items.append(PreviewOrderItem(
            ticker=candidate.ticker,
            name=candidate.name,
            strategy_id=candidate.strategy_id,
            signal_date=ref_date.isoformat(),
            signal_close=signal_close,
            current_close=current_close,
            gap_pct=round(gap_pct, 4),
            gap_exceeded=False,
            limit_price=limit_price,
            estimated_qty=qty,
            estimated_amount=amount,
            skipped=False,
            skip_reason=None,
        ))
        seen_tickers.add(candidate.ticker)
        submitted += 1
        remaining_cash = max(remaining_cash - amount, 0.0)

    return _resp(items, skipped_summary, ref=ref_date.isoformat(), stale=False, reason=None)
