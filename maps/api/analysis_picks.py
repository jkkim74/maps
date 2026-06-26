"""SCR-19 분석 워치리스트 API.

`/analyze` 등으로 선정한 종목을 자동주문 파이프라인(candidate_snapshot)과 분리해
보관한다. 종목별 매수가/목표가/손절가와 근거를 영속화하며, 화면에서 종목을 클릭하면
종합분석 딥다이브를 재실행한다(프런트엔드 `openStockAnalysisModal`).
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import DbDep
from maps.api.schemas import (
    AnalysisPickBatchCreate,
    AnalysisPickItem,
    AnalysisPicksResponse,
    AnalysisPickUpdate,
)
from maps.common.models import AnalysisPick, HistoricalOHLCV
from maps.common.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analysis-picks", tags=["SCR-19 Analysis Picks"])


def _rr_ratio(buy: float | None, target: float | None, stop: float | None) -> float | None:
    """손익비 = (목표가 - 매수가) / (매수가 - 손절가). 계산 불가 시 None."""
    if buy and target and stop:
        risk = buy - stop
        reward = target - buy
        if risk > 0:
            return round(reward / risk, 2)
    return None


def _current_prices(db: Session, tickers: list[str]) -> dict[str, float]:
    """워치리스트 종목의 현재가(최신 일봉 종가 기준)를 구한다.

    historical_ohlcv에서 티커별 최신 date의 종가를 반환한다. 장중 실시간 틱이 아니라
    가장 최근 종가이며(매수가/목표가 대비 참고용), 일별 수집(16:10 KST) 이후엔 당일 종가가
    된다. DB에 시세가 없는 티커는 생략한다(응답에서 current_price=None). 외부 네트워크 호출
    없이 단일 그룹 쿼리로 처리해 목록 조회를 가볍게 유지한다.
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
    return {ticker: float(close) for ticker, close in rows if close and close > 0}


def _to_item(p: AnalysisPick, current_price: float | None = None) -> AnalysisPickItem:
    """ORM 모델을 응답 스키마로 변환한다."""
    return AnalysisPickItem(
        id=p.id,
        ref_date=p.ref_date.isoformat(),
        ticker=p.ticker,
        name=p.name,
        market=p.market,
        current_price=current_price,
        source=p.source,
        buy_price=p.buy_price,
        target_price=p.target_price,
        stop_price=p.stop_price,
        qty=p.qty,
        rationale=p.rationale,
        regime=p.regime,
        strategy_context=p.strategy_context,
        strategy_trade_enabled=p.strategy_trade_enabled,
        state=p.state,
        entry_order_id=p.entry_order_id,
        exit_order_id=p.exit_order_id,
        last_action_at=p.last_action_at.isoformat() if p.last_action_at else None,
        rr_ratio=_rr_ratio(p.buy_price, p.target_price, p.stop_price),
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


@router.get("", response_model=AnalysisPicksResponse)
def list_picks(
    state: str | None = Query(default=None),
    source: str | None = Query(default=None),
    db: Session = DbDep,
) -> AnalysisPicksResponse:
    """워치리스트 픽 목록을 최신순으로 반환한다(상태/소스 필터)."""
    q = db.query(AnalysisPick)
    if state:
        q = q.filter(AnalysisPick.state == state)
    if source:
        q = q.filter(AnalysisPick.source == source)
    rows = q.order_by(AnalysisPick.created_at.desc()).limit(500).all()
    prices = _current_prices(db, [r.ticker for r in rows])
    return AnalysisPicksResponse(
        total=len(rows),
        picks=[_to_item(r, prices.get(r.ticker)) for r in rows],
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
    if pick.state not in ("WATCH", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"무장 불가 상태: {pick.state}")
    b, t, s = pick.buy_price, pick.target_price, pick.stop_price
    if b is None or t is None or s is None:
        raise HTTPException(status_code=400, detail="매수가·목표가·손절가가 모두 있어야 무장할 수 있습니다.")
    if not (s < b < t):
        raise HTTPException(status_code=400, detail="가격 정합성 오류: 손절가 < 매수가 < 목표가 여야 합니다.")
    pick.strategy_trade_enabled = True
    pick.state = "ARMED"
    pick.entry_order_id = None
    pick.last_action_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
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
        try:
            from maps.execution.broker_adapter import get_broker
            cancelled = bool(get_broker(get_settings().maps_broker_mode).cancel_order(pick.entry_order_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("disarm 진입주문 취소 실패 [%s %s]: %s", pick.ticker, pick.entry_order_id, exc)
            cancelled = False
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


@router.delete("/{pick_id}")
def delete_pick(pick_id: int, db: Session = DbDep) -> dict[str, object]:
    """픽을 삭제한다."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")
    db.delete(pick)
    db.commit()
    return {"status": "deleted", "id": pick_id}
