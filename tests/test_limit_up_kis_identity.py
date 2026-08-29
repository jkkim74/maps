"""Failures that only appear once broker ids and audit ids differ.

Every test here passes against ``MockBroker`` — which returns one string for
both — and fails against a KIS-shaped broker. That gap is why three separate P0
defects reached production review instead of a red test.
"""

from __future__ import annotations

import datetime as dt

import pytest

from maps.common.models import LimitUpSession, OrderLog
from maps.execution.broker_adapter import OrderSide, OrderStatus, raw_broker_order_id
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import LimitUpConfig, LimitUpState, build_grid
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.service import Candidate, LimitUpMode, LimitUpService
from maps.limit_up.worker import LimitUpCommandWorker
from maps.risk.manager import RiskManager


KST = dt.timezone(dt.timedelta(hours=9))


def _worker(db, broker) -> tuple[LimitUpCommandWorker, LimitUpSession]:
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    db.commit()
    manager = OrderManager(broker, RiskManager(broker, db), db)
    return LimitUpCommandWorker(manager, broker, repo), session


def _service(db, broker, mode=LimitUpMode.AUTOMATIC) -> LimitUpService:
    repo = LimitUpRepository(db)
    manager = OrderManager(broker, RiskManager(broker, db), db)
    return LimitUpService(
        mode=mode,
        config=LimitUpConfig(),
        repository=repo,
        worker=LimitUpCommandWorker(manager, broker, repo),
    )


def test_audit_and_broker_ids_actually_differ(db, kis_like_broker) -> None:
    """Guard on the double itself — if these ever match, every test below is vacuous."""
    worker, session = _worker(db, kis_like_broker)
    kis_like_broker.seed_position("005930", 20, 98_000.0)

    worker.sell_actual_position(session, reason="hard_stop", owned_quantity=20)

    audit_id = (session.exit_order_ids or "").split(",")[-1]
    raw_id = kis_like_broker.last_raw_order_id()
    assert audit_id.startswith("kis:")
    assert audit_id != raw_id
    assert raw_broker_order_id(audit_id) == raw_id


def test_a_second_exit_on_the_same_day_is_not_blocked_as_a_duplicate(db, kis_like_broker) -> None:
    """15:18 trim then 15:28 forced liquidation — the cap depends on both landing.

    All exits share ``strategy_id="limit_up_v1:exit"``, and the duplicate guard
    rejects a same-day repeat of strategy+ticker+side including **filled** ones,
    so the second exit never reaches the broker and the shares stay in the account.
    """
    worker, session = _worker(db, kis_like_broker)
    kis_like_broker.seed_position("005930", 100, 98_000.0)

    # 15:18 trim: sell the excess and let it fill
    worker.sell_overnight_excess(session, quantity=40, price=100_000)
    kis_like_broker.fill(kis_like_broker.last_raw_order_id(), quantity=40, price=100_000.0)

    # 15:28 the trim could not bring us inside the cap, so give up the carry
    worker.sell_actual_position(
        session, reason="overnight_cap_unfilled", owned_quantity=60
    )

    sells = [o for o in kis_like_broker.submitted if o.side is OrderSide.SELL]
    assert len(sells) == 2, "두 번째 청산이 중복 가드에 막혔다"
    assert sells[0].quantity == 40  # trim
    assert sells[1].quantity == 60  # remainder
    # 사유가 다르면 전략 ID 도 달라야 중복 가드가 서로를 막지 않는다
    assert sells[0].strategy_id != sells[1].strategy_id


def test_cancelling_an_exit_lets_the_next_one_through(db, kis_like_broker) -> None:
    """``cancel()`` must settle the audit row, or the follow-up sell is refused.

    The audit row is keyed by the audit id while the cancel is issued with the
    broker id, so the row stays PENDING and blocks the next exit.
    """
    worker, session = _worker(db, kis_like_broker)
    kis_like_broker.seed_position("005930", 100, 98_000.0)
    worker.sell_overnight_excess(session, quantity=40, price=100_000)
    audit_id = (session.exit_order_ids or "").split(",")[-1]

    worker.cancel_open_exits(session)

    row = db.query(OrderLog).filter(OrderLog.order_id == audit_id).one()
    assert row.status == OrderStatus.CANCELLED.value


def test_another_strategys_sell_survives_our_cancel(db, kis_like_broker) -> None:
    """The ownership guard reads ``order_log`` by broker id, so it never matches.

    On a shared account that means a protective path cancels someone else's
    working sell while logging only a warning.
    """
    worker, session = _worker(db, kis_like_broker)
    kis_like_broker.seed_position("005930", 20, 98_000.0)
    foreign_id = kis_like_broker.seed_foreign_order(
        "005930", side=OrderSide.SELL, quantity=300
    )
    from maps.execution.broker_adapter import order_log_id

    # 다른 전략도 감사 ID 로 기록된다 — order_log 는 언제나 감사 ID 다
    db.add(
        OrderLog(
            # 오늘 낸 주문이어야 소유자로 인식된다 — ODNO 는 거래일마다 재사용되므로
            # 조회가 계좌·날짜까지 맞춘다
            order_id=order_log_id(
                foreign_id,
                broker="kis",
                account_no="50200591-01",
                submitted_at=dt.datetime.now(),
            ),
            strategy_id="pullback_v3",
            ticker="005930",
            side="sell",
            qty=300,
            status="pending",
            broker="kis",
            mode="mock",
        )
    )
    db.commit()

    worker.cancel_open_exits(session)

    assert foreign_id not in kis_like_broker.cancelled


def test_exit_sells_only_this_sessions_shares(db, kis_like_broker) -> None:
    """A shared account must not have another strategy's holding liquidated."""
    broker = kis_like_broker
    service = _service(db, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(
        Candidate(
            ticker="005930",
            market="KOSPI",
            upper_limit_price=100_000,
            total_listed_shares=10_000_000,
            current_price=96_000,
            change_rate=25.0,
        ),
        now_kst=now,
    )
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    broker.seed_position("005930", 20, 98_000.0)
    broker.seed_foreign_position("005930", 300, 70_000.0)  # pullback_v3 holds these
    assert broker.get_position("005930").quantity == 320

    # 하드스톱을 서비스 경로로 유발한다 — worker 를 직접 부르면 서비스가 올바른
    # 소유 수량을 넘기는지는 검증되지 않는다
    from maps.limit_up.feed import FeedTrade

    service.on_trade(
        FeedTrade(
            ticker="005930",
            price=90_000,
            cumulative_turnover_krw=50_000_000_000,
            execution_strength=151.0,
            buy_initiated=True,
            received_at=3.0,
        ),
        now_kst=now,
    )

    sells = [o for o in broker.submitted if o.side is OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 20, "다른 전략의 300주까지 팔았다"


def test_restart_does_not_fake_a_hard_stop_on_the_first_quote(db, kis_like_broker) -> None:
    """``recover()`` leaves ``_last_prices`` empty, so the first quote reads 0.

    ``0 < upper_limit * 0.95`` is true, so a healthy overnight carry is dumped at
    market the moment the engine reconnects.
    """
    broker = kis_like_broker
    broker.seed_position("005930", 20, 98_000.0)
    broker.set_price("005930", 100_000)
    service = _service(db, broker)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    row.state = LimitUpState.OVERNIGHT.value
    db.commit()
    now = dt.datetime(2026, 8, 31, 9, 5, tzinfo=KST)
    service.recover(ref_date=dt.date(2026, 8, 31), now_monotonic=100.0, now_kst=now)

    from maps.limit_up.feed import FeedQuote

    # a perfectly normal book snapshot, nothing wrong with it
    service.on_quote(
        FeedQuote("005930", 100_000, 500, 99_950, 400, 101.0), now_kst=now
    )

    assert service.machine("005930").state is LimitUpState.OVERNIGHT
    assert [o for o in broker.submitted if o.side is OrderSide.SELL] == []


def test_emergency_off_still_retries_a_stuck_exit(db, kis_like_broker) -> None:
    """The kill switch must not disable the retry that rescues a failed stop.

    ``tick()`` gated its reconcile on ``mode is AUTOMATIC``, so pulling the
    switch after a failed protective sell left the shares with no order behind
    them and nothing looking at them.
    """
    broker = kis_like_broker
    service = _service(db, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(
        Candidate(
            ticker="005930",
            market="KOSPI",
            upper_limit_price=100_000,
            total_listed_shares=10_000_000,
            current_price=96_000,
            change_rate=25.0,
        ),
        now_kst=now,
    )
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    machine.state = LimitUpState.RECONCILING  # protective sell blew up mid-flight
    broker.seed_position("005930", 20, 98_000.0)

    service.emergency_off()
    service.tick(now_monotonic=500.0, now_kst=now)

    sells = [o for o in broker.submitted if o.side is OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 20


def test_a_held_session_is_not_closed_when_orders_are_impossible(db, kis_like_broker) -> None:
    """recommend_only after a restart must not silently abandon a real carry.

    Marking it CLOSED removes it from recover(), the after-hours watch and the
    forced liquidation in one move.
    """
    broker = kis_like_broker
    broker.seed_position("005930", 20, 98_000.0)
    service = _service(db, broker, mode=LimitUpMode.RECOMMEND_ONLY)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    row.state = LimitUpState.OVERNIGHT.value
    db.commit()
    now = dt.datetime(2026, 8, 31, 9, 5, tzinfo=KST)
    service.recover(ref_date=dt.date(2026, 8, 31), now_monotonic=100.0, now_kst=now)
    service.machine("005930").filled_quantity = 20

    service.sell_next_open("005930")

    assert service._sessions["005930"].state != LimitUpState.CLOSED.value
    assert service.status()["manual_lock"] is True
    assert "005930" in service.status()["unknown_positions"]


def test_a_feed_blip_does_not_block_the_rest_of_the_day(db, kis_like_broker) -> None:
    """Nothing released this latch, and persisting it made a blip permanent."""
    service = _service(db, kis_like_broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service._refresh_daily_pnl(now.date())

    service.on_feed_disconnect(at=1.0, now_kst=now)
    assert not service.guard.can_enter(active_sessions=0)

    service.on_feed_reconnect()

    assert service.guard.can_enter(active_sessions=0)
    # and a restart must not resurrect it
    restarted = _service(db, kis_like_broker)
    restarted._refresh_daily_pnl(now.date())
    assert "feed_disconnected" not in restarted.guard.halted_reasons


def test_repeating_the_eod_cap_does_not_resubmit_the_same_trim(db, kis_like_broker) -> None:
    """The cap now runs every pass, so it must be idempotent for real.

    Re-transitioning an EOD_TRIM session bumps state_version, which mints a fresh
    idempotency key and sends the trim again — the duplicate then blocks the
    15:28 fail-closed liquidation that the whole cap exists to guarantee.
    """
    broker = kis_like_broker
    service = _service(db, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(
        Candidate(
            ticker="005930", market="KOSPI", upper_limit_price=100_000,
            total_listed_shares=10_000_000, current_price=96_000, change_rate=25.0,
        ),
        now_kst=now,
    )
    machine = service.machine("005930")
    machine.on_fill(at=1.0, cumulative_quantity=100)
    machine.state = LimitUpState.OVERNIGHT
    broker.seed_position("005930", 100, 98_000.0)
    session = service._sessions["005930"]
    for name, qty in (("S", 60), ("A", 40)):
        leg = service.repository.upsert_leg(session, name=name, price=98_800, quantity=qty)
        leg.filled_quantity = qty
        leg.avg_fill_price = 98_800.0
    db.commit()

    first = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))
    assert first  # 초과분이 있어 트림이 나갔다
    sells_after_first = len([o for o in broker.submitted if o.side is OrderSide.SELL])

    # 같은 회차 루프가 다시 돈다
    second = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert second == {}, "같은 트림을 다시 제출했다"
    assert len([o for o in broker.submitted if o.side is OrderSide.SELL]) == sells_after_first


def test_a_downgraded_engine_does_not_sell_paper_positions(db, kis_like_broker) -> None:
    """recommend_only fills are imaginary; selling them hits real account shares."""
    broker = kis_like_broker
    broker.seed_foreign_position("005930", 12, 70_000.0)  # 다른 전략의 실보유
    service = _service(db, broker, mode=LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(
        Candidate(
            ticker="005930", market="KOSPI", upper_limit_price=100_000,
            total_listed_shares=10_000_000, current_price=96_000, change_rate=25.0,
        ),
        now_kst=now,
    )
    # 추천 모드에서 가상 체결이 일어난다
    service.on_trade(_trade_at(1.0, 99_600), now_kst=now)
    service.on_trade(_trade_at(2.0, 99_700), now_kst=now)
    service.on_trade(_trade_at(3.0, 98_800), now_kst=now)
    service.set_mode(LimitUpMode.AUTOMATIC)  # 능력이 열려도 가상분은 팔면 안 된다

    service.on_trade(_trade_at(4.0, 90_000), now_kst=now)  # 하드스톱

    assert [o for o in broker.submitted if o.side is OrderSide.SELL] == []
    assert broker.get_position("005930").quantity == 12


def _trade_at(at: float, price: int):
    from maps.limit_up.feed import FeedTrade

    return FeedTrade(
        ticker="005930",
        price=price,
        cumulative_turnover_krw=50_000_000_000,
        execution_strength=151.0,
        buy_initiated=True,
        received_at=at,
    )
