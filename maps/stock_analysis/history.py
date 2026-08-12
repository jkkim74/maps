"""종목분석 원본 이력 저장과 현재가 오버레이 갱신."""

from __future__ import annotations

import datetime
import math
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from maps.api.schemas import StockAnalysisPriceOverlay
from maps.common import db as db_module
from maps.common.models import HistoricalOHLCV, StockAnalysisHistory
from maps.execution.broker_adapter import get_broker


class CurrentPriceUnavailable(RuntimeError):
    """브로커와 저장된 일봉 모두 현재가를 제공하지 못했다."""


def save_analysis_history(
    db: Session,
    *,
    result: Mapping[str, Any],
    narrative: str,
    trade_plan: Mapping[str, Any],
    owner_user_id: int | None = None,
) -> StockAnalysisHistory:
    """완료된 분석을 중복 제거 없이 독립 이력으로 저장한다.

    `owner_user_id` 가 None 이면 운영자(시스템) 소유로 남는다 — 일반 사용자에게는
    보이지 않고 관리자에게만 보인다.
    """
    technical = result.get("기술적분석") or {}
    row = StockAnalysisHistory(
        owner_user_id=owner_user_id,
        ticker=str(result.get("종목코드") or "").strip(),
        name=str(result.get("종목명") or result.get("종목코드") or "").strip(),
        market=result.get("시장"),
        ref_date=datetime.date.fromisoformat(str(technical["기준일"])),
        snapshot=dict(result),
        narrative=narrative,
        trade_plan=dict(trade_plan),
        recommendation=trade_plan.get("recommendation"),
        analyzed_price=technical.get("현재가"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_analysis_history_with_new_session(
    result: Mapping[str, Any],
    narrative: str,
    trade_plan: Mapping[str, Any],
    owner_user_id: int | None = None,
) -> int:
    """작업 스레드에서 전용 세션으로 분석 이력 한 건을 저장한다."""
    with db_module.SessionLocal() as db:
        return save_analysis_history(
            db,
            result=result,
            narrative=narrative,
            trade_plan=trade_plan,
            owner_user_id=owner_user_id,
        ).id


def _positive_price(value: Any) -> float | None:
    """유한한 양수 가격만 반환한다."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _plan_distances(
    trade_plan: Mapping[str, Any], current_price: float
) -> dict[str, dict[str, float]]:
    """현재가에서 저장된 구조화 매매 가격까지의 거리를 계산한다."""
    distances: dict[str, dict[str, float]] = {}
    entries = trade_plan.get("entries") or []
    levels = {
        **{f"entry_{index}": price for index, price in enumerate(entries[:3], 1)},
        "target": trade_plan.get("target"),
        "stop": trade_plan.get("stop"),
    }
    for key, raw_price in levels.items():
        price = _positive_price(raw_price)
        if price is None:
            continue
        amount = price - current_price
        distances[key] = {
            "amount": amount,
            "pct": round(amount / current_price * 100.0, 2),
        }
    return distances


def refresh_analysis_price(
    db: Session, history: StockAnalysisHistory
) -> StockAnalysisPriceOverlay:
    """분석 원본은 유지하고 현재가 관련 열만 갱신한다."""
    rows = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == history.ticker)
        .order_by(HistoricalOHLCV.date.desc())
        .limit(2)
        .all()
    )
    try:
        broker_price = _positive_price(
            get_broker().get_current_prices([history.ticker]).get(history.ticker)
        )
    except Exception:
        broker_price = None

    today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
    if broker_price is not None:
        current_price = broker_price
        if rows and rows[0].date == today:
            reference_close = _positive_price(rows[1].close) if len(rows) > 1 else None
        else:
            reference_close = _positive_price(rows[0].close) if rows else None
        source = "broker"
    elif rows:
        current_price = _positive_price(rows[0].close)
        reference_close = _positive_price(rows[1].close) if len(rows) > 1 else None
        source = "historical_ohlcv"
    else:
        current_price = None
        reference_close = None
        source = ""

    if current_price is None:
        raise CurrentPriceUnavailable(f"{history.ticker} 현재가를 확인할 수 없습니다.")

    refreshed_at = datetime.datetime.now(datetime.timezone.utc)
    history.latest_price = current_price
    history.latest_reference_close = reference_close
    history.latest_price_source = source
    history.price_refreshed_at = refreshed_at
    db.commit()

    change_amount = (
        current_price - reference_close if reference_close is not None else None
    )
    change_pct = (
        round(change_amount / reference_close * 100.0, 2)
        if change_amount is not None and reference_close
        else None
    )
    return StockAnalysisPriceOverlay(
        history_id=history.id,
        current_price=current_price,
        reference_close=reference_close,
        change_amount=change_amount,
        change_pct=change_pct,
        source=source,
        refreshed_at=refreshed_at,
        plan_distances=_plan_distances(history.trade_plan, current_price),
    )
