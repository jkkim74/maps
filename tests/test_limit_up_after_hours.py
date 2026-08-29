"""After-hours collapse watch: judging, escaping, and refusing to guess."""

from __future__ import annotations

import datetime as dt
import logging

from maps.execution.broker_adapter import (
    AfterHoursQuote,
    Order,
    OrderSide,
    OrderType,
    PendingOrder,
    Position,
)
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.limit_up.after_hours import (
    AfterHoursVerdict,
    after_hours_exit_price,
    after_hours_verdict,
    run_after_hours_watch,
)
from maps.limit_up.domain import LimitUpState
from maps.limit_up.repository import LimitUpRepository
from maps.risk.manager import RiskManager


def _verdict(price: int, volume: int, previous: int | None = 0) -> AfterHoursVerdict:
    return after_hours_verdict(
        quote=AfterHoursQuote(price=price, cumulative_volume=volume),
        previous_volume=previous,
        close_price=100_000,
        drop_pct=0.02,
    )


def test_no_new_trade_never_reaches_the_price_comparison() -> None:
    """An untraded round reporting 0 would read as -100% and dump the position."""
    assert _verdict(price=0, volume=0) is AfterHoursVerdict.BAD_DATA
    assert _verdict(price=100_000, volume=0) is AfterHoursVerdict.NO_NEW_TRADE

    # a counter that did not advance means nothing traded this round
    assert _verdict(price=50_000, volume=1_000, previous=1_000) is AfterHoursVerdict.NO_NEW_TRADE
    assert _verdict(price=50_000, volume=1_000, previous=1_200) is AfterHoursVerdict.NO_NEW_TRADE


def test_collapse_trigger_includes_its_boundary() -> None:
    """Exactly -2% counts as a break; one tick above it does not."""
    assert _verdict(price=98_000, volume=500, previous=100) is AfterHoursVerdict.EXIT
    assert _verdict(price=97_000, volume=500, previous=100) is AfterHoursVerdict.EXIT
    assert _verdict(price=98_100, volume=500, previous=100) is AfterHoursVerdict.HOLD


def test_bad_price_data_is_reported_not_traded_on() -> None:
    """A zero or negative print is broken data, not a -100% move."""
    assert _verdict(price=0, volume=500, previous=100) is AfterHoursVerdict.BAD_DATA
    assert after_hours_verdict(
        quote=AfterHoursQuote(price=90_000, cumulative_volume=500),
        previous_volume=None,
        close_price=0,
        drop_pct=0.02,
    ) is AfterHoursVerdict.BAD_DATA


def test_exit_price_is_the_floor_rounded_down_to_a_valid_tick() -> None:
    """An unrounded price is rejected outright; rounding up prices us out."""
    # 39,450 -> 35,505 -> 50 KRW tick in the 30k band -> 35,500
    assert after_hours_exit_price(39_450) == 35_500
    assert after_hours_exit_price(100_000) == 90_000


def _carried_session(db, *, held: int = 20, close: int = 100_000):
    """Create one overnight session with a real broker position behind it."""
    broker = MockBroker(price_feed={"005930": close})
    broker.place_order(
        Order(
            strategy_id="limit_up_v1:S",
            ticker="005930",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=held,
            limit_price=close,
        )
    )
    assert broker.get_position("005930").quantity == held
    submitted: list[Order] = []
    placed = broker.place_order

    def _capture(order: Order):
        submitted.append(order)
        return placed(order)

    broker.place_order = _capture  # type: ignore[method-assign]
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=close,
        trigger_price=close - 300,
    )
    session.state = LimitUpState.OVERNIGHT.value
    db.commit()
    manager = OrderManager(broker, RiskManager(broker, db), db)
    return broker, session, manager, submitted


def test_collapse_submits_one_full_floor_priced_exit(db) -> None:
    """A break in after-hours makes the next-day gap-down near certain."""
    broker, session, manager, submitted = _carried_session(db)
    broker.set_price("005930", 97_000)
    broker.set_after_hours_volume("005930", 5_000)

    counters = run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )

    assert counters["exited"] == 1
    assert session.state == LimitUpState.AFTER_HOURS_EXIT.value
    assert session.end_reason == "after_hours_break_exit"
    assert len(submitted) == 1
    order = submitted[0]
    assert (order.side, order.order_type, order.quantity) == (
        OrderSide.SELL, OrderType.AFTER_HOURS_SINGLE, 20,
    )
    assert order.limit_price == 90_000


def test_holding_above_the_trigger_submits_nothing(db) -> None:
    """A carry that is still intact must be left alone."""
    broker, session, manager, submitted = _carried_session(db)
    broker.set_price("005930", 99_500)
    broker.set_after_hours_volume("005930", 5_000)

    counters = run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )

    assert counters["exited"] == 0
    assert session.state == LimitUpState.OVERNIGHT.value
    assert session.after_hours_volume == 5_000


def test_quote_failure_never_sells(db) -> None:
    """Being unable to judge is not the same as judging the position is bad."""
    broker, session, manager, submitted = _carried_session(db)

    def _boom(ticker: str):
        raise NotImplementedError("after-hours quote unsupported")

    broker.get_after_hours_quote = _boom  # type: ignore[method-assign]

    counters = run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )

    assert counters == {
        "watched": 1, "exited": 0, "no_trade": 0, "bad_data": 0, "errors": 1,
    }
    assert session.state == LimitUpState.OVERNIGHT.value


def test_final_round_records_without_selling(db) -> None:
    """18:00 has no round left to execute in; selling there is theatre."""
    broker, session, manager, submitted = _carried_session(db)
    broker.set_price("005930", 90_000)
    broker.set_after_hours_volume("005930", 9_000)

    counters = run_after_hours_watch(
        db, broker, manager,
        ref_date=dt.date(2026, 8, 28), drop_pct=0.02, final_round=True,
    )

    assert counters["exited"] == 0
    assert session.state == LimitUpState.OVERNIGHT.value
    assert session.after_hours_volume == 9_000


def _pin_position(broker: MockBroker, ticker: str, quantity: int) -> None:
    """Hold the position steady so an unfilled after-hours order can be simulated."""
    pinned = Position(
        ticker=ticker, quantity=quantity, avg_price=100_000.0, current_price=97_000.0
    )
    broker.get_position = lambda t: pinned if t == ticker else None  # type: ignore[method-assign]


def test_a_still_open_exit_is_not_submitted_twice(db) -> None:
    """The order carries across rounds; re-sending it would double the sell."""
    broker, session, manager, submitted = _carried_session(db)
    _pin_position(broker, "005930", 20)
    broker.set_price("005930", 97_000)
    broker.set_after_hours_volume("005930", 5_000)
    run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )
    assert len(submitted) == 1
    live = submitted[-1]
    broker.get_open_orders = lambda: [  # type: ignore[method-assign]
        PendingOrder(
            order_id=session.exit_order_ids.split(",")[-1],
            ticker=live.ticker,
            side=live.side,
            quantity=live.quantity,
            remaining_quantity=live.quantity,
            order_price=live.limit_price,
        )
    ]
    broker.set_after_hours_volume("005930", 6_000)

    run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )

    assert len(submitted) == 1


def test_a_vanished_exit_is_reported_loudly_instead_of_resubmitted(db, caplog) -> None:
    """Carry-over is unverified: a vanished order must not fail silently.

    Re-submitting would need the stale order_log row settled first, so until the
    venue behaviour is confirmed this stays a loud alert rather than a guess.
    """
    broker, session, manager, submitted = _carried_session(db)
    _pin_position(broker, "005930", 20)
    broker.set_price("005930", 97_000)
    broker.set_after_hours_volume("005930", 5_000)
    run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )
    assert len(submitted) == 1
    broker.get_open_orders = lambda: []  # type: ignore[method-assign]
    broker.set_after_hours_volume("005930", 6_000)

    with caplog.at_level(logging.ERROR):
        run_after_hours_watch(
            db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
        )

    assert len(submitted) == 1
    assert "시간외 탈출 주문이 사라졌는데" in caplog.text


def test_kis_style_audit_ids_are_normalized_before_the_open_order_check(db) -> None:
    """Audit IDs (kis:...) never equal raw broker IDs, so every round would misfire.

    MockBroker returns identical IDs for both, which is why the earlier tests
    could not catch this.
    """
    broker, session, manager, submitted = _carried_session(db)
    _pin_position(broker, "005930", 20)
    broker.set_price("005930", 97_000)
    broker.set_after_hours_volume("005930", 5_000)
    run_after_hours_watch(
        db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
    )
    assert len(submitted) == 1
    raw_id = session.exit_order_ids.split(",")[-1]
    # store the audit-ID form the KIS adapter would produce
    session.exit_order_ids = f"kis:abc123:20260828:{raw_id}"
    db.commit()
    broker.get_open_orders = lambda: [  # type: ignore[method-assign]
        PendingOrder(
            order_id=raw_id,
            ticker="005930",
            side=OrderSide.SELL,
            quantity=20,
            remaining_quantity=20,
            order_price=90_000,
        )
    ]
    broker.set_after_hours_volume("005930", 6_000)

    with caplog_error() as text:
        run_after_hours_watch(
            db, broker, manager, ref_date=dt.date(2026, 8, 28), drop_pct=0.02
        )

    assert len(submitted) == 1
    assert "사라졌는데" not in text()


import contextlib


@contextlib.contextmanager
def caplog_error():
    """Capture ERROR records without a pytest fixture dependency."""
    records: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Sink(level=logging.ERROR)
    logger = logging.getLogger("maps.limit_up.after_hours")
    logger.addHandler(handler)
    try:
        yield lambda: "\n".join(records)
    finally:
        logger.removeHandler(handler)
