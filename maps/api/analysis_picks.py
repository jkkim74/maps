"""SCR-19 분석 워치리스트 API.

`/analyze` 등으로 선정한 종목을 자동주문 파이프라인(candidate_snapshot)과 분리해
보관한다. 종목별 매수가/목표가/손절가와 근거를 영속화하며, 화면에서 종목을 클릭하면
종합분석 딥다이브를 재실행한다(프런트엔드 `openStockAnalysisModal`).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from maps.api.deps import DbDep
from maps.api.schemas import (
    AnalysisPickBatchCreate,
    AnalysisPickItem,
    AnalysisPicksResponse,
    AnalysisPickUpdate,
)
from maps.common.models import AnalysisPick

router = APIRouter(prefix="/api/v1/analysis-picks", tags=["SCR-19 Analysis Picks"])

_VALID_STATES = {"WATCH", "ARMED", "BOUGHT", "CLOSED", "CANCELLED"}


def _rr_ratio(buy: float | None, target: float | None, stop: float | None) -> float | None:
    """손익비 = (목표가 - 매수가) / (매수가 - 손절가). 계산 불가 시 None."""
    if buy and target and stop:
        risk = buy - stop
        reward = target - buy
        if risk > 0:
            return round(reward / risk, 2)
    return None


def _to_item(p: AnalysisPick) -> AnalysisPickItem:
    """ORM 모델을 응답 스키마로 변환한다."""
    return AnalysisPickItem(
        id=p.id,
        ref_date=p.ref_date.isoformat(),
        ticker=p.ticker,
        name=p.name,
        market=p.market,
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
    return AnalysisPicksResponse(total=len(rows), picks=[_to_item(r) for r in rows])


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
    """가격/전략매매 토글/상태를 부분 수정한다."""
    pick = db.get(AnalysisPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="픽을 찾을 수 없습니다.")

    data = body.model_dump(exclude_unset=True)
    if "state" in data and data["state"] not in _VALID_STATES:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 state: {data['state']}")
    for key, value in data.items():
        setattr(pick, key, value)

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
