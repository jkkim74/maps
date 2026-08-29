"""After-hours single-price collapse watch for overnight upper-limit sessions.

Runs 16:00-18:00 from the scheduler, not the intraday runtime: it needs only a
database read and a quote poll, so it must not depend on the asyncio feed
process being alive.

This is the *second* line of defence. It lowers the odds of eating a gap-down,
it does not bound the loss — the -1,000,000 KRW guarantee comes entirely from
the overnight sizing in :mod:`maps.limit_up.service`.
"""

from __future__ import annotations

import datetime as dt
import logging
from enum import Enum

from sqlalchemy.orm import Session

from maps.common.exceptions import BrokerAdapterError
from maps.common.models import LimitUpSession
from maps.execution.broker_adapter import (
    AfterHoursQuote,
    BrokerAdapter,
    Order,
    OrderSide,
    OrderType,
    raw_broker_order_id,
)
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import (
    AFTER_HOURS_FLOOR_RATIO,
    EXIT_STRATEGY_IDS,
    LimitUpState,
)
from maps.limit_up.repository import LimitUpRepository
from maps.market.trading_rules import round_down_krx_price


logger = logging.getLogger(__name__)

_WATCHED_STATES = (
    LimitUpState.OVERNIGHT.value,
    LimitUpState.AFTER_HOURS_EXIT.value,
)


class AfterHoursVerdict(str, Enum):
    """What one poll round concluded about a carried session."""

    EXIT = "exit"
    HOLD = "hold"
    NO_NEW_TRADE = "no_new_trade"
    BAD_DATA = "bad_data"


def after_hours_verdict(
    *,
    quote: AfterHoursQuote,
    previous_volume: int | None,
    close_price: int,
    drop_pct: float,
) -> AfterHoursVerdict:
    """Judge one poll round for one carried session.

    The volume gate runs **before** any price comparison. An upper-limit stock
    often has no after-hours sellers at all, and a venue reporting ``0`` there
    would read as a -100% collapse and sell the whole position on the first
    round — worse than fail-open, because it acts on nothing.

    Comparing against the *previous* cumulative volume rather than testing for
    zero keeps that gate correct whether the broker's counter covers the
    after-hours session alone or the whole day.

    Args:
        quote: This round's poll.
        previous_volume: Cumulative volume seen last round, ``None`` on first.
        close_price: Today's close, which for a locked session is the limit price.
        drop_pct: Fractional drop that counts as a collapse (e.g. ``0.02``).

    Returns:
        The verdict for this round.
    """
    if close_price <= 0 or quote.price <= 0 or quote.cumulative_volume < 0:
        return AfterHoursVerdict.BAD_DATA
    if previous_volume is None:
        # 첫 회차에는 비교 기준이 없다. KIS acml_vol 은 당일 전체 누적이라 항상 양수라서
        # "0 이면 스킵" 만으로는 거래량 가드가 성립하지 않는다 — 기준선만 잡고 판정은 미룬다.
        return AfterHoursVerdict.NO_NEW_TRADE
    if quote.cumulative_volume <= previous_volume:
        return AfterHoursVerdict.NO_NEW_TRADE
    if quote.price <= close_price * (1.0 - drop_pct):
        return AfterHoursVerdict.EXIT
    return AfterHoursVerdict.HOLD


def after_hours_exit_price(close_price: int) -> int:
    """Return the after-hours floor price, rounded down to a valid tick.

    Rounded **down** because this is a sell: an unrounded price is rejected
    outright, and rounding up would price us out of the round entirely.
    """
    return round_down_krx_price(close_price * AFTER_HOURS_FLOOR_RATIO)


def run_after_hours_watch(
    db: Session,
    broker: BrokerAdapter,
    order_manager: OrderManager,
    *,
    ref_date: dt.date,
    drop_pct: float,
    final_round: bool = False,
) -> dict[str, int]:
    """Poll every carried session once and escape the ones that are breaking.

    A poll failure never sells. Being unable to judge is not the same as
    judging that the position is bad, and force-selling a healthy carry on an
    API outage is the more expensive mistake.

    Args:
        db: Session-owning transaction.
        broker: Quote and position source.
        order_manager: Audited order boundary.
        ref_date: KST trading date of the carried sessions.
        drop_pct: Fractional drop that counts as a collapse.
        final_round: 18:00 sweep — records state, never judges or sells.

    Returns:
        Counters: ``watched``, ``exited``, ``no_trade``, ``bad_data``, ``errors``.
    """
    repository = LimitUpRepository(db)
    rows = (
        db.query(LimitUpSession)
        .filter(LimitUpSession.ref_date == ref_date)
        .filter(LimitUpSession.state.in_(_WATCHED_STATES))
        .all()
    )
    counters = {"watched": len(rows), "exited": 0, "no_trade": 0, "bad_data": 0, "errors": 0}
    if not rows:
        return counters

    for session in rows:
        try:
            quote = broker.get_after_hours_quote(session.ticker)
        except (BrokerAdapterError, NotImplementedError) as exc:
            counters["errors"] += 1
            logger.warning(
                "시간외 시세 조회 실패 [%s] — 판단 불가이므로 매도하지 않는다: %s",
                session.ticker,
                exc,
            )
            continue

        verdict = after_hours_verdict(
            quote=quote,
            previous_volume=session.after_hours_volume,
            close_price=session.upper_limit_price,
            drop_pct=drop_pct,
        )
        session.after_hours_volume = max(0, quote.cumulative_volume)

        if final_round or verdict is not AfterHoursVerdict.EXIT:
            if verdict is AfterHoursVerdict.NO_NEW_TRADE:
                counters["no_trade"] += 1
            elif verdict is AfterHoursVerdict.BAD_DATA:
                counters["bad_data"] += 1
                logger.warning(
                    "시간외 시세 이상 [%s]: price=%s volume=%s",
                    session.ticker, quote.price, quote.cumulative_volume,
                )
            continue

        try:
            if _submit_after_hours_exit(session, broker, order_manager, repository):
                counters["exited"] += 1
        except Exception:  # noqa: BLE001 - 한 종목 실패가 남은 캐리를 막으면 안 된다
            counters["errors"] += 1
            logger.exception("시간외 탈출 제출 실패 [%s]", session.ticker)

    db.commit()
    return counters


def _submit_after_hours_exit(
    session: LimitUpSession,
    broker: BrokerAdapter,
    order_manager: OrderManager,
    repository: LimitUpRepository,
) -> bool:
    """Submit one full after-hours exit, once.

    KRX is expected to carry an unfilled after-hours order across rounds until
    18:00, and it is already at the best possible price, so there is no
    price-chasing loop and no re-submission — which also means no path to
    double-selling on a misread response.

    🟡 That carry-over is **not verified against the live venue yet** (design doc
    §5). If orders turn out to die each round, this escape silently stops
    existing after the first try, so a vanished order with the position still
    held is logged loudly rather than papered over: re-submitting would first
    need the stale ``order_log`` row settled, and guessing at that before the
    venue behaviour is known buys complexity, not safety.
    """
    position = broker.get_position(session.ticker)
    if position is None or position.quantity <= 0:
        return False
    submitted = {item for item in (session.exit_order_ids or "").split(",") if item}
    if session.state == LimitUpState.AFTER_HOURS_EXIT.value and submitted:
        # 감사 ID(kis:...)와 브로커 원주문 ID 를 정규화 없이 비교하면 KIS 에서는 절대
        # 겹치지 않아 매 회차 오경보가 난다. worker.cancel_open_exits 와 같은 규칙을 쓴다.
        open_ids = {
            raw_broker_order_id(order.order_id) for order in broker.get_open_orders()
        }
        if not {raw_broker_order_id(item) for item in submitted} & open_ids:
            logger.error(
                "시간외 탈출 주문이 사라졌는데 보유가 남아 있다 [%s] qty=%s — "
                "회차 이월이 안 되는 것일 수 있다. 수동 확인 필요.",
                session.ticker, position.quantity,
            )
        return False

    price = after_hours_exit_price(session.upper_limit_price)
    repository.transition(
        session,
        state=LimitUpState.AFTER_HOURS_EXIT,
        action="after_hours_exit",
        payload={"quantity": position.quantity, "price": price},
    )
    repository.db.commit()
    order = Order(
        strategy_id=EXIT_STRATEGY_IDS["after_hours"],
        ticker=session.ticker,
        side=OrderSide.SELL,
        order_type=OrderType.AFTER_HOURS_SINGLE,
        quantity=position.quantity,
        limit_price=price,
        current_price=position.current_price or position.avg_price,
        decision_context={
            "limit_up_session_id": session.id,
            "reason": "after_hours_break_exit",
        },
    )
    result = order_manager.submit_exit(order, exit_reason="after_hours_break_exit")
    ledger = [item for item in (session.exit_order_ids or "").split(",") if item]
    if result.order_id not in ledger:
        ledger.append(result.order_id)
        session.exit_order_ids = ",".join(ledger)
    session.end_reason = "after_hours_break_exit"
    repository.db.commit()
    return True
