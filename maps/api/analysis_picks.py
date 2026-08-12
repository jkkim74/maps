"""SCR-19 분석 워치리스트 API.

`/analyze` 등으로 선정한 종목을 자동주문 파이프라인(candidate_snapshot)과 분리해
보관한다. 종목별 매수가/목표가/손절가와 근거를 영속화하며, 화면에서 종목을 클릭하면
종합분석 딥다이브를 재실행한다(프런트엔드 `openStockAnalysisModal`).
"""

from __future__ import annotations

import datetime
import logging

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from maps.api.auth import current_identity, load_user
from maps.api.deps import DbDep
from maps.api.schemas import (
    AnalysisPickBatchCreate,
    AnalysisPickItem,
    AnalysisPickLegItem,
    AnalysisPicksResponse,
    AnalysisPickUpdate,
    StrategyTradeLimitRequest,
    StrategyTradeLimitResponse,
    StrategyTradePlanRequest,
    StrategyTradePlanResponse,
)
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import AnalysisPick, AnalysisPickLeg, HistoricalOHLCV, OrderLog
from maps.common.settings import MapsSettings, get_settings
from maps.execution.broker_adapter import AccountBalance, get_broker
from maps.execution.order_manager import OrderManager
from maps.ops.pick_freshness import (
    is_pick_stale,
    pick_age_trading_days,
    pick_cutoff_date,
    pick_stale_reason,
)
from maps.ops.strategy_trade_plan import (
    ValidatedTradePlan,
    calculate_trade_limits,
    validate_trade_plan,
)
from maps.risk.manager import RiskManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analysis-picks", tags=["SCR-19 Analysis Picks"])


def _trade_account_context(
    ticker: str, db: Session
) -> tuple[MapsSettings, AccountBalance, float, bool]:
    """Load the current broker account and duplicate state once per request."""
    settings = get_settings()
    broker = get_broker(settings.maps_broker_mode)
    account = broker.get_account_balance()
    position = broker.get_position(ticker)
    existing_position_value = float(position.market_value) if position is not None else 0.0
    has_active_pick = (
        db.query(AnalysisPick.id)
        .filter(
            AnalysisPick.ticker == ticker.strip(),
            AnalysisPick.state.in_(["ARMED", "BOUGHT"]),
        )
        .first()
        is not None
    )
    return settings, account, existing_position_value, has_active_pick


def _validate_requested_plan(
    body: StrategyTradePlanRequest,
    db: Session,
) -> ValidatedTradePlan:
    """Refresh account and duplicate state, then run the shared pure validator."""
    settings, account, existing_position_value, has_active_pick = _trade_account_context(
        body.ticker, db
    )
    return validate_trade_plan(
        body,
        account=account,
        settings=settings,
        existing_position_value=existing_position_value,
        has_active_pick=has_active_pick,
    )


@router.post("/trade-limits", response_model=StrategyTradeLimitResponse)
def trade_limits(
    body: StrategyTradeLimitRequest,
    db: Session = DbDep,
) -> StrategyTradeLimitResponse:
    """Calculate current safe limits without a budget or database write."""
    settings, account, existing_position_value, has_active_pick = _trade_account_context(
        body.ticker, db
    )
    limits = calculate_trade_limits(
        body,
        account=account,
        settings=settings,
        existing_position_value=existing_position_value,
        has_active_pick=has_active_pick,
    )
    return StrategyTradeLimitResponse(**limits.model_dump())


@router.post("/trade-preview", response_model=StrategyTradePlanResponse)
def preview_trade_plan(
    body: StrategyTradePlanRequest,
    db: Session = DbDep,
) -> StrategyTradePlanResponse:
    """Calculate broker-backed limits without writing a pick or placing an order."""
    plan = _validate_requested_plan(body, db)
    return StrategyTradePlanResponse(**plan.model_dump())


@router.post("/arm-plan", response_model=StrategyTradePlanResponse)
def arm_trade_plan(
    body: StrategyTradePlanRequest,
    db: Session = DbDep,
) -> StrategyTradePlanResponse:
    """Revalidate current gates and atomically persist one ARMED trade plan."""
    plan = _validate_requested_plan(body, db)
    if plan.blocked:
        raise HTTPException(
            status_code=409,
            detail={"blockers": [item.model_dump() for item in plan.blockers]},
        )

    legs = sorted(plan.legs, key=lambda item: item.sequence)
    pick = AnalysisPick(
        ref_date=body.ref_date,
        ticker=body.ticker.strip(),
        name=body.name.strip() or body.ticker.strip(),
        market=body.market,
        source=body.source,
        buy_price=legs[0].entry_price,
        target_price=body.target_price,
        stop_price=body.stop_price,
        qty=sum(leg.planned_qty for leg in legs),
        trade_mode=body.trade_mode,
        total_budget=body.total_budget,
        rationale=body.rationale,
        regime=body.regime,
        strategy_context=body.strategy_context,
        strategy_trade_enabled=True,
        state="ARMED",
        last_action_at=datetime.datetime.now(datetime.timezone.utc),
    )
    if body.trade_mode == "split":
        pick.legs = [
            AnalysisPickLeg(
                sequence=leg.sequence,
                entry_price=leg.entry_price,
                weight_pct=leg.weight_pct,
                planned_qty=leg.planned_qty,
                status="PENDING",
            )
            for leg in legs
        ]
    db.add(pick)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "blockers": [{
                    "code": "DUPLICATE_ACTIVE_TICKER",
                    "message": "동일 종목의 활성 전략이 이미 있습니다.",
                }],
            },
        ) from exc
    db.refresh(pick)
    return StrategyTradePlanResponse(
        **plan.model_dump(),
        pick_id=pick.id,
        state=pick.state,
    )


def _rr_ratio(buy: float | None, target: float | None, stop: float | None) -> float | None:
    """손익비 = (목표가 - 매수가) / (매수가 - 손절가). 계산 불가 시 None."""
    if buy and target and stop:
        risk = buy - stop
        reward = target - buy
        if risk > 0:
            return round(reward / risk, 2)
    return None


def _broker_live_prices(tickers: list[str]) -> dict[str, float]:
    """브로커 보유 포지션의 라이브 현재가를 구한다(리스크 모니터 보유종목과 동일 소스).

    KIS 등 실거래 브로커는 잔고 조회 시 종목별 현재가(prpr)를 함께 반환한다. 보유 중인
    티커에 한해 그 값을 사용하면 장중 실시간 시세가 반영된다. 미보유 종목은 브로커가
    시세를 주지 않으므로 결과에서 빠진다(상위에서 일봉 종가로 폴백). 브로커 조회 실패는
    로깅 후 빈 딕셔너리로 흡수해 목록 조회가 죽지 않게 한다.
    """
    target = {t for t in tickers if t}
    if not target:
        return {}
    try:
        broker = get_broker()
        fetch = getattr(broker, "_fetch_positions_and_balance", None)
        if callable(fetch):
            position_map, _balance = fetch()
        else:
            position_map = {
                ticker: broker.get_position(ticker)
                for ticker in target
            }
    except (BrokerAdapterError, NotImplementedError, ValueError, requests.RequestException) as exc:
        logger.warning("워치리스트 브로커 현재가 조회 실패: %s", exc)
        return {}
    prices: dict[str, float] = {}
    for ticker, position in position_map.items():
        if ticker not in target or position is None:
            continue
        price = position.current_price
        if price is not None and price > 0:
            prices[ticker] = float(price)
    return prices


def _live_quote_prices(tickers: list[str]) -> dict[str, float]:
    """미보유 워치 종목의 실시간 현재가를 브로커 시세 조회로 구한다.

    보유 종목 시세만 주는 잔고 조회(`_broker_live_prices`)와 달리 임의 종목의 현재가를
    얻는다. 시세 API가 없는 브로커(mock)는 빈 딕셔너리를 반환한다. 조회 실패는 로깅 후
    흡수해 목록 조회가 죽지 않게 한다(상위에서 일봉 종가 폴백).
    """
    target = [t for t in tickers if t]
    if not target:
        return {}
    try:
        return get_broker().get_current_prices(target)
    except (BrokerAdapterError, NotImplementedError, ValueError, requests.RequestException) as exc:
        logger.warning("워치리스트 실시간 시세 조회 실패: %s", exc)
        return {}


def _current_prices(db: Session, tickers: list[str]) -> dict[str, float]:
    """워치리스트 종목의 현재가를 구한다.

    보유(BOUGHT) 종목은 리스크 모니터 보유종목과 동일하게 브로커 잔고의 라이브 현재가를
    쓰고(`_broker_live_prices`), 미보유 종목은 브로커 실시간 시세 조회(`_live_quote_prices`)로
    폴백한다. 시세 조회까지 실패한 종목만 historical_ohlcv 티커별 최신 date 종가로 최종
    폴백한다(일별 수집 16:40 KST 시점 값). 어느 소스에도 없으면 생략(current_price=None).
    """
    unique = list({t for t in tickers if t})
    if not unique:
        return {}
    latest = (
        db.query(
            HistoricalOHLCV.ticker.label("ticker"),
            func.max(HistoricalOHLCV.date).label("d"),
        )
        .filter(HistoricalOHLCV.ticker.in_(unique))
        .group_by(HistoricalOHLCV.ticker)
        .subquery()
    )
    rows = (
        db.query(HistoricalOHLCV.ticker, HistoricalOHLCV.close)
        .join(
            latest,
            (HistoricalOHLCV.ticker == latest.c.ticker)
            & (HistoricalOHLCV.date == latest.c.d),
        )
        .all()
    )
    prices = {ticker: float(close) for ticker, close in rows if close and close > 0}
    # 보유 종목은 잔고의 라이브 현재가로 덮어쓴다(장중 실시간 반영).
    held = _broker_live_prices(unique)
    prices.update(held)
    # 미보유 종목은 실시간 시세 조회로 일봉 종가를 덮어쓴다.
    prices.update(_live_quote_prices([t for t in unique if t not in held]))
    return prices


def _fill_prices(db: Session, picks: list[AnalysisPick]) -> dict[int, float]:
    """진입 주문이 있는 픽의 실 진입 체결가를 구한다(pick.id → 체결가).

    pick.entry_order_id 로 order_log 를 단일 배치 조회해, 리스크 모니터 보유종목과 동일하게
    실제 체결가(fill_price)를 우선 쓰고 없으면 계획가(order_price)로 폴백한다. 미체결(WATCH/
    ARMED)이나 진입 주문이 없는 픽은 결과에서 빠진다(응답에서 fill_price=None). 외부 브로커
    호출 없이 DB 쿼리만으로 처리한다.
    """
    order_ids = {p.entry_order_id for p in picks if p.entry_order_id}
    if not order_ids:
        return {}
    rows = (
        db.query(OrderLog.order_id, OrderLog.fill_price, OrderLog.order_price)
        .filter(OrderLog.order_id.in_(order_ids))
        .all()
    )
    by_order: dict[str, float] = {}
    for order_id, fill_price, order_price in rows:
        price = fill_price if fill_price and fill_price > 0 else order_price
        if price and price > 0:
            by_order[order_id] = float(round(price))  # 체결가는 원 단위 정수로 표시
    return {
        p.id: by_order[p.entry_order_id]
        for p in picks
        if p.entry_order_id and p.entry_order_id in by_order
    }


def _to_item(
    p: AnalysisPick,
    current_price: float | None = None,
    fill_price: float | None = None,
    cutoff: datetime.date | None = None,
) -> AnalysisPickItem:
    """ORM 모델을 응답 스키마로 변환한다.

    :param cutoff: 신선도 기준일. 목록 조회는 한 번만 계산해 전 행에 넘긴다.
        생략하면 여기서 계산한다 — 기본값을 "신선"으로 두면 한 달 된 픽을 PATCH 했을 때
        `data_stale=False` 라는 거짓이 응답에 실린다(단건 변경 응답이 호출부 5곳 중 4곳).
    """
    settings = get_settings()
    if cutoff is None:
        cutoff = pick_cutoff_date(settings)
    stale = is_pick_stale(p, cutoff)
    legs = sorted(p.legs, key=lambda leg: leg.sequence)
    split = p.trade_mode == "split" and bool(legs)
    leg_items = [
        AnalysisPickLegItem(
            id=leg.id,
            sequence=leg.sequence,
            entry_price=leg.entry_price,
            weight_pct=leg.weight_pct,
            planned_qty=leg.planned_qty,
            filled_qty=leg.filled_qty,
            remaining_qty=max(leg.planned_qty - leg.filled_qty, 0),
            fill_price=leg.fill_price,
            order_id=leg.order_id,
            status=leg.status,
        )
        for leg in legs
    ]
    if split:
        filled_legs = sum(leg.status == "FILLED" for leg in legs)
        next_leg = next(
            (leg for leg in legs if leg.status not in ("FILLED", "CANCELLED")),
            None,
        )
        priced_fills = [leg for leg in legs if leg.fill_price and leg.filled_qty > 0]
        filled_qty = sum(leg.filled_qty for leg in priced_fills)
        split_fill_price = (
            round(sum(leg.fill_price * leg.filled_qty for leg in priced_fills) / filled_qty)
            if filled_qty else None
        )
        effective_fill_price = split_fill_price or fill_price
        total_legs = len(legs)
        next_entry_price = next_leg.entry_price if next_leg and not p.entries_cancelled else None
        qty = sum(leg.planned_qty for leg in legs)
        planned_entry = legs[0].entry_price
    else:
        filled_legs = 1 if p.state in ("BOUGHT", "CLOSED") else 0
        effective_fill_price = fill_price
        total_legs = 1
        next_entry_price = p.buy_price if filled_legs == 0 and not p.entries_cancelled else None
        qty = p.qty
        planned_entry = p.buy_price
    return AnalysisPickItem(
        id=p.id,
        ref_date=p.ref_date.isoformat(),
        ticker=p.ticker,
        name=p.name,
        market=p.market,
        current_price=current_price,
        source=p.source,
        buy_price=p.buy_price,
        fill_price=effective_fill_price,
        target_price=p.target_price,
        stop_price=p.stop_price,
        qty=qty,
        trade_mode="split" if split else "single",
        total_budget=p.total_budget,
        entries_cancelled=p.entries_cancelled,
        exit_pending_reason=p.exit_pending_reason,
        legs=leg_items,
        filled_legs=filled_legs,
        total_legs=total_legs,
        next_entry_price=next_entry_price,
        rationale=p.rationale,
        regime=p.regime,
        strategy_context=p.strategy_context,
        strategy_trade_enabled=p.strategy_trade_enabled,
        state=p.state,
        entry_order_id=p.entry_order_id,
        exit_order_id=p.exit_order_id,
        exit_reason=p.exit_reason,
        last_action_at=p.last_action_at.isoformat() if p.last_action_at else None,
        # 손익비는 체결가 우선(실제 진입가) — 미체결이면 계획 매수가 기준.
        rr_ratio=_rr_ratio(effective_fill_price or planned_entry, p.target_price, p.stop_price),
        created_at=p.created_at.isoformat() if p.created_at else "",
        data_stale=stale,
        stale_reason=pick_stale_reason(p, cutoff),
        age_trading_days=pick_age_trading_days(p, settings=settings) if stale else None,
    )


@router.get("", response_model=AnalysisPicksResponse)
def list_picks(
    request: Request,
    state: str | None = Query(default=None),
    source: str | None = Query(default=None),
    db: Session = DbDep,
) -> AnalysisPicksResponse:
    """워치리스트 픽 목록을 최신순으로 반환한다(상태/소스 필터).

    일반 사용자에게는 자기 픽만 보인다. 소유자가 없는 행(`NULL`)은 운영자 픽이며
    자동매매 대상은 지금도 그쪽 하나뿐이다.
    """
    identity = current_identity(request)
    q = db.query(AnalysisPick).options(selectinload(AnalysisPick.legs))
    if not identity.is_admin:
        owner = load_user(db, identity.username)
        q = q.filter(AnalysisPick.owner_user_id == (owner.id if owner else None))
    if state:
        q = q.filter(AnalysisPick.state == state)
    else:
        q = q.filter(AnalysisPick.state != "CLOSED")   # 완료(익절/손절)는 기본 목록에서 분리, ?state=CLOSED로만 조회
    if source:
        q = q.filter(AnalysisPick.source == source)
    rows = q.order_by(AnalysisPick.created_at.desc()).limit(500).all()
    prices = _current_prices(db, [r.ticker for r in rows])
    fills = _fill_prices(db, rows)
    # 오래된 픽도 목록에서 빼지 않는다 — 표시만 하고 보여준다. 숨기면 운영자가 삭제하려
    # 해도 보이지 않고, 오래된 BOUGHT 포지션이 통째로 사라진다.
    cutoff = pick_cutoff_date(get_settings())
    items = [_to_item(r, prices.get(r.ticker), fills.get(r.id), cutoff=cutoff) for r in rows]
    return AnalysisPicksResponse(
        total=len(items),
        picks=items,
        expected_ref_date=cutoff.isoformat(),
        stale_count=sum(1 for i in items if i.data_stale),
    )


@router.post("", response_model=AnalysisPicksResponse)
def create_picks(body: AnalysisPickBatchCreate, db: Session = DbDep) -> AnalysisPicksResponse:
    """픽을 단건/일괄 생성한다(항상 picks 배열)."""
    if not body.picks:
        raise HTTPException(status_code=400, detail="picks가 비어 있습니다.")

    created: list[AnalysisPick] = []
    for c in body.picks:
        ticker = (c.ticker or "").strip()
        if not ticker:
            continue
        try:
            ref = datetime.date.fromisoformat(c.ref_date) if c.ref_date else datetime.date.today()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"잘못된 ref_date: {c.ref_date}")
        pick = AnalysisPick(
            ref_date=ref,
            ticker=ticker,
            name=(c.name or ticker).strip(),
            market=c.market,
            source=c.source or "analyze",
            buy_price=c.buy_price,
            target_price=c.target_price,
            stop_price=c.stop_price,
            qty=c.qty,
            rationale=c.rationale,
            regime=c.regime,
            strategy_context=c.strategy_context,
        )
        db.add(pick)
        created.append(pick)

    if not created:
        raise HTTPException(status_code=400, detail="유효한 ticker가 없습니다.")

    db.commit()
    for pick in created:
        db.refresh(pick)
    return AnalysisPicksResponse(total=len(created), picks=[_to_item(p) for p in created])


@router.patch("/{pick_id}", response_model=AnalysisPickItem)
def update_pick(pick_id: int, body: AnalysisPickUpdate, db: Session = DbDep) -> AnalysisPickItem:
    """가격/메모를 부분 수정한다. 상태 전이는 arm/disarm 전용."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")

    data = body.model_dump(exclude_unset=True)
    # 무장/보유 중에는 가격·수량 변경 금지 (브래킷 정합성 보호 — 먼저 disarm 필요)
    price_fields = {"buy_price", "target_price", "stop_price", "qty"}
    if pick.state in ("ARMED", "BOUGHT") and price_fields & data.keys():
        raise HTTPException(status_code=409, detail="무장/보유 중에는 가격을 수정할 수 없습니다. 먼저 해제하세요.")
    for key, value in data.items():
        setattr(pick, key, value)

    db.commit()
    db.refresh(pick)
    return _to_item(pick)


@router.post("/{pick_id}/arm", response_model=AnalysisPickItem)
def arm_pick(pick_id: int, db: Session = DbDep) -> AnalysisPickItem:
    """픽을 진입 대기(ARMED) 상태로 무장한다. WATCH/CANCELLED에서만 허용."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")
    # 만료 검사를 상태 검사보다 먼저 한다 — 운영자가 "무장 불가 상태" 대신 진짜 사유를
    # 받아야 한다. 이 한 곳이 웹·모바일·텔레그램 인라인 버튼을 동시에 막는다(텔레그램은
    # 한 달 전 메시지의 버튼도 영구히 살아 있어서 서버 거부 외에 막을 방법이 없다).
    settings = get_settings()
    cutoff = pick_cutoff_date(settings)
    if is_pick_stale(pick, cutoff):
        age = pick_age_trading_days(pick, settings=settings)
        raise HTTPException(
            status_code=409,
            detail=(
                f"기준일 {pick.ref_date} 픽은 {age}거래일 지나 만료됐습니다. "
                f"({cutoff} 이후 기준일만 무장 가능) 재분석 후 가격을 갱신하세요."
            ),
        )
    if pick.state not in ("WATCH", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"무장 불가 상태: {pick.state}")
    duplicate = (
        db.query(AnalysisPick.id)
        .filter(
            AnalysisPick.id != pick.id,
            AnalysisPick.ticker == pick.ticker,
            AnalysisPick.state.in_(["ARMED", "BOUGHT"]),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="동일 종목의 활성 전략이 이미 있습니다.")
    b, t, s = pick.buy_price, pick.target_price, pick.stop_price
    if b is None or t is None or s is None:
        raise HTTPException(status_code=400, detail="매수가·목표가·손절가가 모두 있어야 무장할 수 있습니다.")
    if not (s < b < t):
        raise HTTPException(status_code=400, detail="가격 정합성 오류: 손절가 < 매수가 < 목표가 여야 합니다.")
    pick.strategy_trade_enabled = True
    pick.state = "ARMED"
    pick.entry_order_id = None
    pick.last_action_at = datetime.datetime.now(datetime.timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="동일 종목의 활성 전략이 이미 있습니다.",
        ) from exc
    db.refresh(pick)
    return _to_item(pick)


@router.post("/{pick_id}/disarm", response_model=AnalysisPickItem)
def disarm_pick(pick_id: int, db: Session = DbDep) -> AnalysisPickItem:
    """무장을 해제한다. 보유(BOUGHT) 중에는 거부(청산은 별도 승인 필요)."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")
    if pick.state == "BOUGHT":
        raise HTTPException(status_code=409, detail="보유 중인 포지션 — 청산은 주문 화면에서 별도 처리하세요.")
    # 미체결 진입 주문이 있으면 취소를 시도하고, 취소가 확인되지 않으면 거부한다.
    # (라이브 주문이 살아있는 채로 entry_order_id를 비우면 체결 시 추적 불가 — 고아 포지션)
    if pick.entry_order_id:
        # 잔량 취소를 먼저 시도한다 — 체결 여부를 판정하는 동안 추가 체결이 쌓이는 것을 막는다.
        try:
            from maps.execution.broker_adapter import get_broker
            cancelled = bool(get_broker(get_settings().maps_broker_mode).cancel_order(pick.entry_order_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("disarm 진입주문 취소 실패 [%s %s]: %s", pick.ticker, pick.entry_order_id, exc)
            cancelled = False

        # 취소 성공 여부보다 "이미 체결된 물량"을 먼저 본다. 취소는 잔량에만 걸리므로
        # 부분 체결된 주식은 취소가 성공해도 그대로 남는다. 이때 entry_order_id를 지우면
        # 브래킷도 %/ATR 손절도 관리하지 않는 고아 포지션이 된다(2026-07-30 실제 발생:
        # 1,253주 주문 중 21주 체결 후 해제 → 손절·익절 없이 방치).
        entry_log = (
            db.query(OrderLog).filter(OrderLog.order_id == pick.entry_order_id).first()
        )
        filled_qty = int(entry_log.fill_qty or 0) if entry_log is not None else 0
        # fill_qty 는 브로커 동기화에 의존해 늦게 채워질 수 있다. 수량을 모르더라도
        # 체결 상태면 보유로 간주한다(과소평가보다 과대평가가 안전하다).
        has_fill = filled_qty > 0 or (
            entry_log is not None and entry_log.status in ("filled", "partially_filled")
        )
        if has_fill:
            # 해제하지 않고 BOUGHT 로 올린다. 브래킷이 목표/손절을 계속 관리하게 해야
            # 체결분이 추적 밖으로 벗어나지 않는다. 청산은 사용자 승인이 필요하므로
            # 여기서 자동으로 팔지는 않는다.
            pick.state = "BOUGHT"
            pick.last_action_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
            logger.warning(
                "disarm 거부 — 진입 주문 부분 체결 [%s %s] filled=%s cancelled=%s → BOUGHT 전이",
                pick.ticker, pick.entry_order_id, filled_qty or "미상", cancelled,
            )
            qty_text = f"{filled_qty}주" if filled_qty > 0 else "수량 미상"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"진입 주문이 이미 체결({qty_text})돼 해제할 수 없습니다. "
                    f"잔량은 {'취소했습니다' if cancelled else '취소를 확인하지 못했습니다'}. "
                    "보유분은 브래킷이 계속 관리합니다 — 청산은 주문 화면에서 처리하세요."
                ),
            )
        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail="진입 주문 취소를 확인하지 못했습니다. 주문 화면에서 처리 후 다시 시도하세요.",
            )
    pick.strategy_trade_enabled = False
    pick.state = "WATCH"
    pick.entry_order_id = None
    pick.last_action_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    db.refresh(pick)
    return _to_item(pick)


@router.post("/{pick_id}/stop-entries", response_model=AnalysisPickItem)
def stop_split_entries(pick_id: int, db: Session = DbDep) -> AnalysisPickItem:
    """Stop future split buys while preserving exits for any held quantity."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="분석 종목을 찾을 수 없습니다.")
    if pick.trade_mode != "split" or not pick.legs:
        raise HTTPException(status_code=409, detail="분할매매 계획만 진입을 중지할 수 있습니다.")
    if pick.state == "CLOSED":
        raise HTTPException(status_code=409, detail="이미 종료된 계획입니다.")

    broker = get_broker(get_settings().maps_broker_mode)
    order_manager = OrderManager(
        broker=broker,
        risk=RiskManager(broker, db),
        db=db,
    )
    held_qty = 0
    position = broker.get_position(pick.ticker)
    if position is not None and position.quantity > 0:
        held_qty = position.quantity

    live_statuses = {"pending", "partially_filled"}
    for leg in sorted(pick.legs, key=lambda item: item.sequence):
        row = (
            db.query(OrderLog).filter(OrderLog.order_id == leg.order_id).first()
            if leg.order_id
            else None
        )
        if row is not None:
            reported = max(int(row.fill_qty or 0), 0)
            delta = max(reported - int(leg.current_order_fill_qty or 0), 0)
            if delta:
                old_qty = int(leg.filled_qty or 0)
                new_qty = min(old_qty + delta, leg.planned_qty)
                applied = new_qty - old_qty
                if applied > 0:
                    fill_price = float(row.fill_price or row.order_price or leg.entry_price)
                    leg.fill_price = (
                        ((leg.fill_price or 0.0) * old_qty + fill_price * applied) / new_qty
                    )
                    leg.filled_qty = new_qty
                leg.current_order_fill_qty = reported
            if (row.status or "").lower() in live_statuses:
                try:
                    cancelled = bool(broker.cancel_order(leg.order_id))
                except (NotImplementedError, BrokerAdapterError):
                    cancelled = False
                if not cancelled:
                    raise HTTPException(
                        status_code=409,
                        detail="현재 분할 진입 주문의 취소를 확인하지 못했습니다.",
                    )
                try:
                    order_manager.sync_broker_state()
                except (NotImplementedError, BrokerAdapterError) as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="취소 후 최종 체결을 동기화하지 못했습니다. 잠시 후 다시 시도하세요.",
                    ) from exc
                # 취소 응답과 동시에 들어온 마지막 체결을 다른 세션/브로커 sync가
                # 기록했을 수 있으므로 캐시를 버리고 감사 행을 다시 읽는다.
                db.expire(row)
                row = db.query(OrderLog).filter(OrderLog.order_id == leg.order_id).one()
                final_reported = max(int(row.fill_qty or 0), 0)
                final_delta = max(final_reported - int(leg.current_order_fill_qty or 0), 0)
                if final_delta:
                    old_qty = int(leg.filled_qty or 0)
                    new_qty = min(old_qty + final_delta, leg.planned_qty)
                    applied = new_qty - old_qty
                    if applied > 0:
                        fill_price = float(row.fill_price or row.order_price or leg.entry_price)
                        leg.fill_price = (
                            ((leg.fill_price or 0.0) * old_qty + fill_price * applied) / new_qty
                        )
                        leg.filled_qty = new_qty
                    leg.current_order_fill_qty = final_reported
                if (row.status or "").lower() in live_statuses:
                    row.status = "cancelled"
        if leg.filled_qty >= leg.planned_qty:
            leg.status = "FILLED"
        else:
            leg.status = "CANCELLED"
        leg.order_id = None
        leg.current_order_fill_qty = 0

    position = broker.get_position(pick.ticker)
    if position is not None and position.quantity > 0:
        held_qty = max(held_qty, position.quantity)
    held_qty = max(held_qty, sum(int(leg.filled_qty or 0) for leg in pick.legs))

    pick.entries_cancelled = True
    pick.entry_order_id = None
    pick.last_action_at = datetime.datetime.now(datetime.timezone.utc)
    if held_qty > 0:
        pick.state = "BOUGHT"
        pick.strategy_trade_enabled = True
    else:
        pick.state = "WATCH"
        pick.strategy_trade_enabled = False
    db.commit()
    db.refresh(pick)
    return _to_item(pick)


@router.delete("/{pick_id}")
def delete_pick(pick_id: int, db: Session = DbDep) -> dict[str, object]:
    """픽을 삭제한다."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")
    db.delete(pick)
    db.commit()
    return {"status": "deleted", "id": pick_id}
