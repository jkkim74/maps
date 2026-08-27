"""다음 거래일 예정 주문 미리보기 — 브로커 호출 없이 DB+settings 기반 시뮬레이션."""

from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.schemas import OrderPreviewResponse, PreviewOrderItem
from maps.common.models import CandidateSnapshot, HistoricalOHLCV, PortfolioSnapshot, PromotionHistory
from maps.common.settings import MapsSettings
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.market.trading_rules import previous_trading_day, round_up_krx_price
from maps.ops.liquidity_cap import BLOCKING_REASONS, apply_liquidity_cap
from maps.ops.candidate_selection import (
    candidate_min_score_expression,
    candidate_recommendation_eligible_expression,
)
from maps.ops.order_state import claimed_candidate_tickers
from maps.ops.score_readiness import candidate_score_ready
from maps.ops.scheduler import OperationalPipeline, _RUNNABLE_STRATEGIES, _is_krx_market_day

logger = logging.getLogger(__name__)

_MAX_ORDERS = 3
_ELIGIBLE_STAGES = {"mock_candidate", "live_candidate", "live"}
_LIVE_STAGES = {"live_candidate", "live"}  # 실제 08:55 주문 대상 단계 (모의 후보 구분용)
_LOOKAHEAD_DAYS = 14  # 최대 탐색 일수 (긴 연휴 대비)
_MAX_PREVIEW_ITEMS = 10  # 차단 사유 행 포함 미리보기 최대 표시 건수


# ── 핵심 계산 함수 ────────────────────────────────────────────────────────────


def next_trading_day(from_date: dt.date) -> dt.date:
    """from_date 다음 날부터 KRX 거래일인 최초 날짜를 반환한다."""
    candidate = from_date + dt.timedelta(days=1)
    for _ in range(_LOOKAHEAD_DAYS):
        if _is_krx_market_day(candidate):
            return candidate
        candidate += dt.timedelta(days=1)
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
        .filter(candidate_min_score_expression() >= min_score)
        .filter(candidate_recommendation_eligible_expression())
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


def _has_candidate_snapshot(db: Session) -> bool:
    return db.query(CandidateSnapshot.id).limit(1).scalar() is not None


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

    # 장세 분석 — 스케줄러 주문 경로와 동일하게 설정 오버라이드를 존중한다.
    regime_result = None
    try:
        from maps.market.regime import RegimeLabel, create_regime_analyzer
        from maps.market.regime_history import latest_applied_regime
        regime_result = create_regime_analyzer(settings).analyze()
        # 스케줄러가 기록한 최근 판정(히스테리시스 적용)이 있으면 우선 사용한다.
        # 오버라이드가 걸려 있으면 오버라이드를 존중한다.
        if settings.maps_market_regime_override == "auto":
            log_row = latest_applied_regime(db, today)
            if log_row is not None:
                regime_result.regime = RegimeLabel(log_row.applied_regime)
                regime_result.floor_applied = bool(log_row.floor_applied)
        market_regime = regime_result.regime.value
        weekly_trend = regime_result.weekly_trend.value
        entry_limit_ratio = regime_result.entry_limit_ratio
    except Exception as _e:
        logger.warning("장세 분석 실패, 기본값 사용: %s", _e)
        market_regime = "unknown"
        weekly_trend = "unknown"
        entry_limit_ratio = 0.5
    # 주문 경로(_submit_candidate_orders)와 동일한 상한 계산을 미러링한다.
    max_policy_ratio = max(
        entry_limit_ratio,
        settings.maps_contrarian_max_entry_ratio
        if settings.maps_contrarian_accumulation_enabled
        else 0.0,
    )
    effective_max = max(1, round(_MAX_ORDERS * max_policy_ratio))

    # 주문 대상일·신선도 판정은 08:55 주문 사이클과 동일하게 "최신 스냅샷"을 기준으로 삼는다.
    # 예전엔 next_trading_day(today) 기준 expected_ref를 써서, 자정에 날짜가 넘어가면
    # (당일 16:20 후보 생성 전) 직전 세션 스냅샷을 stale로 오판, 거래일 아침 내내
    # 예정목록이 통째로 비는 버그가 있었다. 이제 스냅샷 기준일(latest_ref)에서 실제
    # 주문될 다음 거래일을 도출하고, 신선도는 OHLCV 수집 상태로 판정한다.
    latest_ref = db.query(func.max(CandidateSnapshot.ref_date)).scalar()
    latest_ohlcv_date: dt.date | None = db.query(func.max(HistoricalOHLCV.date)).scalar()

    # 이 스냅샷이 실제 주문에 쓰일 다음 거래일 (저녁·아침 모두 일관된 기준일).
    next_day = next_trading_day(latest_ref) if latest_ref is not None else next_trading_day(today)

    # 존재해야 하는 최신 스냅샷 세션 = 직전 완료 세션이되, 오늘 세션 OHLCV가 이미
    # 수집됐으면(=당일 후보 생성도 완료됐어야) 오늘까지다. 시계 시각에 의존하지 않아
    # 테스트가 결정적이고, 아침(당일 수집 전)엔 직전 세션 스냅샷을 정상으로 본다.
    last_completed = previous_trading_day(today, extra_closed_dates=settings.krx_closed_dates)
    expected_session = last_completed
    if latest_ohlcv_date is not None and latest_ohlcv_date >= today:
        expected_session = latest_ohlcv_date
    data_stale = latest_ref is not None and latest_ref < expected_session

    # stale 사유 판별: OHLCV가 직전 완료 세션까지 수집돼 있으면 수집은 정상 —
    # 후보 생성이 장세 차단 등으로 스냅샷을 저장하지 않은 것.
    stale_reason: str | None = None
    if data_stale:
        if latest_ohlcv_date is not None and latest_ohlcv_date >= last_completed:
            stale_reason = "regime_blocked"
        else:
            stale_reason = "data_collection_failed"

    candidates = [] if data_stale else _get_order_candidates(db, min_score=settings.maps_candidate_min_score)
    data_available = _has_candidate_snapshot(db)

    if data_stale:
        ref_date = latest_ref            # 투명성: 마지막으로 생성된(오래된) 기준일을 노출
    elif candidates:
        ref_date = candidates[0].ref_date
    else:
        ref_date = today

    # 계좌 잔고 추정
    total_value, cash = _get_assumed_balance(db)
    slippage = settings.maps_order_slippage_pct
    max_gap = settings.maps_order_max_gap_pct
    max_exposure = settings.max_single_exposure

    # 주문 가능 전략 목록
    promotions = _latest_promotions(db)
    eligible_strategies = sorted(
        {s for s, stage in promotions.items() if stage in _ELIGIBLE_STAGES}
    )

    order_stages = _LIVE_STAGES | ({"mock_candidate"} if settings.is_paper_account else set())

    items: list[PreviewOrderItem] = []
    submitted = 0
    seen_tickers: set[str] = set()
    remaining_cash = cash

    # 유동성 한도는 후보 전체를 한 번에 조회한다. 기준일은 주문 경로
    # (`ops/scheduler`)와 **같은 후보 스냅샷 기준일**이어야 한다 — 다르면
    # 화면 수량과 실주문 수량이 갈린다.
    turnover_by_ticker = (
        HistoricalOHLCVRepository(db).avg_turnover_20d(
            [c.ticker for c in candidates], candidates[0].ref_date
        )
        if candidates
        else {}
    )

    for candidate in candidates:
        if submitted >= effective_max:
            break
        if len(items) >= _MAX_PREVIEW_ITEMS:
            break
        if candidate.ticker in seen_tickers:
            continue

        # 실제 08:55 주문이 나가는 항목인지 구분하는 플래그.
        # 주문 경로(_order_candidates)와 동일하게, 모의 계좌에서는 mock_candidate 도 주문 대상이다.
        live_eligible = promotions.get(candidate.strategy_id) in order_stages

        signal_close = _latest_close(db, candidate.ticker, ref_date)
        if signal_close <= 0:
            continue

        current_close = _latest_close(db, candidate.ticker, today)
        if current_close <= 0:
            current_close = signal_close

        gap_pct = (current_close - signal_close) / signal_close
        gap_exceeded = gap_pct > max_gap

        limit_price = round_up_krx_price(
            current_close * (1 + slippage),
            market=candidate.market,
        )
        remaining_slots = max(effective_max - submitted, 1)

        # 장세 게이트 미러링: 주문 경로와 동일하게 preferred_regimes + entry_policy를
        # 재검사하고, 차단 후보는 사유와 함께 표시한다 (관측·주문 분리).
        regime_block_reason: str | None = None
        if settings.maps_score_readiness_required:
            ready, readiness_reason = candidate_score_ready(db, candidate)
            if not ready:
                regime_block_reason = readiness_reason
        strategy_cls = _RUNNABLE_STRATEGIES.get(candidate.strategy_id)
        if regime_block_reason is None and regime_result is not None and strategy_cls is not None:
            if market_regime not in strategy_cls.preferred_regimes:
                regime_block_reason = f"preferred_regime_mismatch:{market_regime}"
            else:
                policy = regime_result.entry_policy_for_strategy(
                    getattr(strategy_cls, "strategy_type", None),
                    contrarian_enabled=settings.maps_contrarian_accumulation_enabled,
                    contrarian_entry_limit_ratio=settings.maps_contrarian_max_entry_ratio,
                )
                if not policy.allowed:
                    regime_block_reason = policy.reason
        if regime_block_reason is not None:
            items.append(PreviewOrderItem(
                ticker=candidate.ticker,
                name=candidate.name,
                strategy_id=candidate.strategy_id,
                signal_date=ref_date.isoformat(),
                signal_close=signal_close,
                current_close=current_close,
                gap_pct=round(gap_pct, 4),
                gap_exceeded=gap_exceeded,
                limit_price=limit_price,
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                skip_reason=regime_block_reason,
                live_eligible=live_eligible,
            ))
            continue

        # entry_signal 체크: 전략이 현재 날짜 기준 진입 신호를 발생시키지 않으면 스킵
        sig = OperationalPipeline._latest_strategy_signal(
            db, ticker=candidate.ticker, strategy_id=candidate.strategy_id, ref_date=today
        )
        if sig is None or not sig.entry_signal:
            items.append(PreviewOrderItem(
                ticker=candidate.ticker,
                name=candidate.name,
                strategy_id=candidate.strategy_id,
                signal_date=ref_date.isoformat(),
                signal_close=signal_close,
                current_close=current_close,
                gap_pct=round(gap_pct, 4),
                gap_exceeded=gap_exceeded,
                limit_price=limit_price,
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                skip_reason="no_entry_signal",
                live_eligible=live_eligible,
            ))
            continue

        if gap_exceeded:
            items.append(PreviewOrderItem(
                ticker=candidate.ticker,
                name=candidate.name,
                strategy_id=candidate.strategy_id,
                signal_date=ref_date.isoformat(),
                signal_close=signal_close,
                current_close=current_close,
                gap_pct=round(gap_pct, 4),
                gap_exceeded=True,
                limit_price=limit_price,
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                skip_reason="gap_exceeded",
                live_eligible=live_eligible,
            ))
            continue

        qty = _estimated_qty(total_value, remaining_cash, limit_price, remaining_slots, max_exposure)
        cap = apply_liquidity_cap(
            qty=qty,
            price=limit_price,
            turnover_20d=turnover_by_ticker.get(candidate.ticker),
            settings=settings,
        )
        qty = cap.qty
        if qty <= 0:
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
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                # 유동성 차단은 현금 부족과 다른 사유다 — 뭉뚱그리면 사용자가
                # 현금을 채워도 안 사지는 이유를 알 수 없다.
                skip_reason=(
                    "insufficient_liquidity"
                    if cap.reason in BLOCKING_REASONS
                    else "insufficient_cash"
                ),
                live_eligible=live_eligible,
                original_qty=cap.original_qty,
                liquidity_reason=cap.reason,
                turnover_20d=cap.turnover_20d,
                liquidity_limit_amount=cap.limit_amount,
            ))
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
            live_eligible=live_eligible,
            original_qty=cap.original_qty,
            liquidity_reason=cap.reason,
            turnover_20d=cap.turnover_20d,
            liquidity_limit_amount=cap.limit_amount,
        ))
        seen_tickers.add(candidate.ticker)
        submitted += 1
        remaining_cash = max(remaining_cash - amount, 0.0)

    return OrderPreviewResponse(
        next_trading_day=next_day.isoformat(),
        as_of_date=ref_date.isoformat(),
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
        data_stale=data_stale,
        expected_ref_date=expected_session.isoformat(),
        stale_reason=stale_reason,
        latest_ohlcv_date=latest_ohlcv_date.isoformat() if (data_stale and latest_ohlcv_date) else None,
    )
