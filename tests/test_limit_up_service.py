"""End-to-end V1 service behavior with a deterministic broker boundary."""

from __future__ import annotations

import datetime as dt

import pytest

from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    PendingOrder,
    Position,
)
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import LimitUpConfig, LimitUpState
from maps.limit_up.feed import FeedQuote, FeedTrade
from maps.limit_up.repository import LimitUpRepository
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import LimitUpSession, OrderLog, PortfolioSnapshot
from maps.limit_up.service import Candidate, LimitUpMode, LimitUpService
from maps.limit_up.worker import LimitUpCommandWorker
from maps.risk.manager import RiskManager


KST = dt.timezone(dt.timedelta(hours=9))


class ServiceBroker(BrokerAdapter):
    """Pending-order broker that exposes actual orders and positions."""

    def __init__(self) -> None:
        """Create a flat 20m KRW paper account."""
        self.orders: list[tuple[str, Order]] = []
        self.cancelled: set[str] = set()
        self.positions: dict[str, Position] = {}

    def place_order(self, order: Order) -> OrderResult:
        """Accept every submitted order as pending."""
        order_id = str(2000 + len(self.orders) + 1)
        self.orders.append((order_id, order))
        return OrderResult(
            order_id=order_id,
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Remove one order from the open-order view."""
        self.cancelled.add(order_id)
        return True

    def get_position(self, ticker: str) -> Position | None:
        """Return one actual holding."""
        return self.positions.get(ticker)

    def get_positions(self) -> dict[str, int]:
        """Return all actual broker holdings."""
        return {ticker: position.quantity for ticker, position in self.positions.items()}

    def get_account_balance(self) -> AccountBalance:
        """Return fixed cash and marked position value."""
        value = sum(position.market_value for position in self.positions.values())
        return AccountBalance(20_000_000, value, 20_000_000 + value)

    def is_market_open(self) -> bool:
        """Keep service tests in the regular session."""
        return True

    def get_open_orders(self) -> list[PendingOrder]:
        """Return accepted orders that were not cancelled."""
        return [
            PendingOrder(
                order_id=order_id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                remaining_quantity=order.quantity,
                order_price=order.limit_price,
            )
            for order_id, order in self.orders
            if order_id not in self.cancelled
        ]

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return no fills until a test scripts an actual position."""
        return []


def _candidate() -> Candidate:
    return Candidate(
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        total_listed_shares=10_000_000,
        current_price=96_000,
        change_rate=25.0,
    )


def _service(db, mode: LimitUpMode, broker: ServiceBroker | None = None) -> LimitUpService:
    repo = LimitUpRepository(db)
    worker = None
    if broker is not None:
        manager = OrderManager(broker, RiskManager(broker, db), db)
        worker = LimitUpCommandWorker(manager, broker, repo)
    return LimitUpService(
        mode=mode,
        config=LimitUpConfig(),
        repository=repo,
        worker=worker,
    )


def _seed_owned(service, ticker: str, quantity: int, price: int = 98_800) -> None:
    """Record filled buy legs so the session actually owns what it claims to.

    Session ownership comes from its own order legs, not from the account
    position — the account can hold another strategy's shares in the same ticker.
    """
    session = service._sessions[ticker]
    for name, qty in (("S", quantity - quantity // 3), ("A", quantity // 3)):
        leg = service.repository.upsert_leg(
            session, name=name, price=price, quantity=qty
        )
        leg.filled_quantity = qty
        leg.avg_fill_price = float(price)
    service.repository.db.commit()


def _trade(at: float, price: int) -> FeedTrade:
    return FeedTrade(
        ticker="005930",
        price=price,
        cumulative_turnover_krw=50_000_000_000,
        execution_strength=151.0,
        buy_initiated=True,
        received_at=at,
    )


def test_automatic_trigger_submits_exactly_two_grid_orders(db) -> None:
    """One valid upward cross must produce one durable S/A net, never duplicates."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    assert service.watch_candidate(_candidate(), now_kst=now)

    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert [order.strategy_id for _, order in broker.orders] == [
        "limit_up_v1:S",
        "limit_up_v1:A",
    ]
    assert service.status()["attempts"] == 1


def test_recommend_only_uses_same_fill_and_lock_state_machine_without_orders(db) -> None:
    """Shadow validation must exercise lifecycle logic without touching a broker."""
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_trade(_trade(3.0, 98_800), now_kst=now)
    service.on_quote(
        FeedQuote("005930", 100_000, 0, 100_000, 100_000, 4.0),
        now_kst=now,
    )
    service.tick(now_monotonic=14.0)

    assert service.machine("005930").state is LimitUpState.LOCKED
    assert service.status()["pattern_failures"] == 0


def test_kosdaq_halt_cancels_pending_grid_and_never_reopens(db) -> None:
    """The intraday market latch must pull buys and stay off after recovery."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_kosdaq(value=1_000.0, at=3.0)
    service.on_kosdaq(value=985.0, at=4.0)
    service.on_kosdaq(value=1_010.0, at=5.0)

    assert broker.cancelled == {"2001", "2002"}
    assert service.status()["entry_halted"] is True


def test_eod_review_holds_only_when_both_fresh_bid_tests_pass(db) -> None:
    """A locked recommendation may cross overnight only on a strict fresh PASS."""
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=1)
    machine.on_quote(
        event=service.quote_event("005930", price=100_000, ask_qty=0, at=3.0)
    )
    machine.on_timer(13.0)

    decision = service.review_eod(
        "005930",
        best_bid_price=100_000,
        best_bid_qty=100_000,
        quote_fresh=True,
        shares_fresh=True,
    )

    assert decision == "hold"
    assert machine.state is LimitUpState.OVERNIGHT


_order_log_seq = iter(range(1, 10_000))


def _seed_order_log(
    db,
    ticker: str,
    *,
    strategy_id: str,
    side: str,
    fill_qty: int,
    status: str = "filled",
    broker: str = "kis",
) -> None:
    """Insert one audit row the way OrderManager records a submitted order."""
    seq = next(_order_log_seq)
    db.add(
        OrderLog(
            order_id=f"{broker}:test:20260828:{seq:010d}",
            strategy_id=strategy_id,
            ticker=ticker,
            side=side,
            qty=max(fill_qty, 1),
            fill_qty=fill_qty,
            status=status,
            broker=broker,
        )
    )
    db.commit()


def test_recovery_unknown_broker_position_enters_manual_lock(db) -> None:
    """A limit-up traced position with no V1 session must lock, never auto-sell."""
    broker = ServiceBroker()
    broker.positions["000660"] = Position("000660", 3, 200_000)
    # 이 테스트는 mock 모드(ServiceBroker)라 흔적도 mock 레인에 남는다.
    _seed_order_log(
        db, "000660", strategy_id="limit_up_v1:S", side="buy", fill_qty=3, broker="mock"
    )
    service = _service(db, LimitUpMode.AUTOMATIC, broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is True
    assert service.status()["unknown_positions"] == ["000660"]
    assert broker.orders == []


def test_automatic_recover_ignores_foreign_holdings_without_limit_up_trace(db) -> None:
    """A holding another strategy bought is not this engine's orphan to lock on."""
    broker = ServiceBroker()
    broker.positions["000660"] = Position("000660", 3, 200_000)
    _seed_order_log(
        db, "000660", strategy_id="donchian_v2", side="buy", fill_qty=3, broker="mock"
    )
    service = _service(db, LimitUpMode.AUTOMATIC, broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is False
    assert service.status()["unknown_positions"] == []
    assert broker.orders == []


def test_recommend_only_recover_ignores_foreign_holdings_on_shared_account(
    db, kis_like_broker
) -> None:
    """2026-08-31 운영 사고 재현: 공유 계좌의 타 전략 보유로 기동이 잠기면 안 된다."""
    kis_like_broker.seed_foreign_position("006800", 10, 5_000)
    kis_like_broker.seed_foreign_position("051900", 4, 300_000)
    _seed_order_log(db, "006800", strategy_id="strategy_trade", side="buy", fill_qty=10)
    _seed_order_log(db, "051900", strategy_id="ath_breakout_v1", side="buy", fill_qty=4)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY, kis_like_broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is False
    assert service.status()["unknown_positions"] == []
    assert kis_like_broker.submitted == []
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    assert service.watch_candidate(_candidate(), now_kst=now) is True


def test_recover_locks_orphan_holding_with_limit_up_buy_trace(db, kis_like_broker) -> None:
    """A holding our own buy lane produced must lock when its session is gone."""
    kis_like_broker.seed_position("005930", 3, 98_800)
    _seed_order_log(db, "005930", strategy_id="limit_up_v1:S", side="buy", fill_qty=3)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY, kis_like_broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is True
    assert service.status()["unknown_positions"] == ["005930"]
    assert kis_like_broker.submitted == []


def test_recover_ignores_ticker_whose_limit_up_trace_netted_to_zero(
    db, kis_like_broker
) -> None:
    """A fully exited limit-up ticker rebought by another strategy is not ours."""
    _seed_order_log(db, "005930", strategy_id="limit_up_v1:S", side="buy", fill_qty=3)
    _seed_order_log(
        db, "005930", strategy_id="limit_up_v1:exit:stop", side="sell", fill_qty=3
    )
    kis_like_broker.seed_foreign_position("005930", 5, 70_000)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY, kis_like_broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is False
    assert service.status()["unknown_positions"] == []


def test_recover_locks_on_unsettled_limit_up_buy_order(db, kis_like_broker) -> None:
    """A pending limit-up buy may have filled unrecorded — fail closed and lock."""
    kis_like_broker.seed_position("005930", 3, 98_800)
    _seed_order_log(
        db,
        "005930",
        strategy_id="limit_up_v1:A",
        side="buy",
        fill_qty=0,
        status="pending",
    )
    service = _service(db, LimitUpMode.RECOMMEND_ONLY, kis_like_broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is True
    assert service.status()["unknown_positions"] == ["005930"]


def test_recover_ignores_mock_mode_limit_up_traces(db, kis_like_broker) -> None:
    """Mock-mode experiment leftovers must not lock a kis-mode recovery."""
    _seed_order_log(
        db, "005930", strategy_id="limit_up_v1:S", side="buy", fill_qty=3, broker="mock"
    )
    kis_like_broker.seed_foreign_position("005930", 3, 70_000)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY, kis_like_broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is False
    assert service.status()["unknown_positions"] == []


def test_recovery_restores_first_fill_deadline_and_eod_share_facts(db) -> None:
    """A restart must preserve the original 180-second clock and EOD inputs."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    row = service._sessions["005930"]
    row.state = LimitUpState.FILLED_WAIT_LOCK.value
    row.first_fill_at = dt.datetime(2026, 8, 28, 1, 0, 0)
    service.repository.db.commit()
    broker.positions["005930"] = Position("005930", 3, 98_800)

    restarted = _service(db, LimitUpMode.AUTOMATIC, broker)
    restarted.recover(
        ref_date=dt.date(2026, 8, 28),
        now_monotonic=1_000.0,
        now_kst=dt.datetime(2026, 8, 28, 10, 2, 0, tzinfo=KST),
    )

    machine = restarted.machine("005930")
    assert machine.first_fill_at == 880.0
    assert restarted._candidates["005930"].total_listed_shares == 10_000_000


def test_feed_disconnect_pulls_unfilled_net_but_keeps_held_position_protected(db) -> None:
    """Loss of real-time truth must close entries without dumping a refuge leader."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_feed_disconnect(at=3.0, now_kst=now)

    assert broker.cancelled == {"2001", "2002"}
    assert "feed_disconnected" in service.status()["halted_reasons"]

    held_broker = ServiceBroker()
    row = service._sessions["005930"]
    row.state = LimitUpState.LOCKED.value
    service.repository.db.commit()
    held_broker.positions["005930"] = Position("005930", 3, 98_800)
    held = _service(db, LimitUpMode.AUTOMATIC, held_broker)
    held.recover(ref_date=now.date(), now_monotonic=10.0, now_kst=now)
    held.on_feed_disconnect(at=11.0, now_kst=now)

    assert held_broker.orders == []


def test_rest_fallback_price_can_fire_hard_stop_but_never_entry(db) -> None:
    """REST degradation mode is protection-only and may still liquidate a break."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.state = LimitUpState.LOCKED
    machine.filled_quantity = 3
    broker.positions["005930"] = Position("005930", 3, 98_800)

    service.on_fallback_price("005930", price=94_900, at=20.0, now_kst=now)

    assert broker.orders[-1][1].side is OrderSide.SELL
    assert service.status()["pattern_failures"] == 1


def test_realized_daily_loss_limit_blocks_new_entries(db) -> None:
    """The confirmed -300,000 KRW stop must latch from persisted P/L, not memory.

    Regression: DailyGuard.update_daily_pnl had no caller, so the only
    money-based guard was dead while the count-based ones ran.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    closed = service.repository.create_or_get_session(
        ref_date=now.date(),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    closed.realized_pnl_by_date = {"2026-08-28": -300_000.0}
    db.commit()

    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert broker.orders == []
    assert service.machine("005930").state is LimitUpState.CLOSED
    status = service.status()
    assert status["daily_pnl"] == -300_000.0
    assert "daily_loss" in status["halted_reasons"]


def test_profitable_day_does_not_latch_the_daily_loss_stop(db) -> None:
    """Only realized losses close the gate; a green day must still trade."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    won = service.repository.create_or_get_session(
        ref_date=now.date(),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    won.realized_pnl_by_date = {"2026-08-28": 400_000.0}
    db.commit()

    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert len(broker.orders) == 2
    assert service.status()["daily_pnl"] == 400_000.0


def _overnight_service(db, held: int) -> tuple[ServiceBroker, LimitUpService]:
    """Drive one session to a locked overnight hold with `held` shares."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    broker.positions["005930"] = Position("005930", held, 98_000.0)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=held)
    _seed_owned(service, "005930", held)
    machine.on_quote(
        event=service.quote_event("005930", price=100_000, ask_qty=0, at=3.0)
    )
    machine.on_timer(13.0)
    service.review_eod(
        "005930",
        best_bid_price=100_000,
        best_bid_qty=100_000,
        quote_fresh=True,
        shares_fresh=True,
    )
    assert machine.state is LimitUpState.OVERNIGHT
    return broker, service


def test_overnight_cap_trims_the_excess_at_the_upper_limit_price(db) -> None:
    """40 shares x 100,000 = 4,000,000 exposure would breach the -1,000,000 cap."""
    broker, service = _overnight_service(db, held=40)

    submitted = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))

    # budget 3,333,333 / 100,000 = 33 shares allowed, 7 must go
    assert submitted == {"005930": 7}
    assert service.machine("005930").state is LimitUpState.EOD_TRIM
    _, sell = broker.orders[-1]
    assert (sell.side, sell.quantity, sell.limit_price) == (OrderSide.SELL, 7, 100_000)
    assert 33 * 100_000 * 0.30 <= 1_000_000


def test_position_inside_the_budget_is_never_trimmed(db) -> None:
    """The cap must not touch a carry that is already small enough."""
    broker, service = _overnight_service(db, held=30)

    assert service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28)) == {}
    assert service.machine("005930").state is LimitUpState.OVERNIGHT
    assert all(order.side is OrderSide.BUY for _, order in broker.orders)


def test_realized_loss_shrinks_the_overnight_carry(db) -> None:
    """A -300,000 KRW day leaves only 2,333,333 KRW of overnight room."""
    broker, service = _overnight_service(db, held=40)
    spent = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    spent.realized_pnl_by_date = {"2026-08-28": -300_000.0}
    db.commit()

    submitted = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))

    # 2,333,333 / 100,000 = 23 shares; the loss already spent 300,000 of the cap
    assert submitted == {"005930": 17}
    assert 300_000 + 23 * 100_000 * 0.30 <= 1_000_000


def test_filled_trim_returns_the_session_to_overnight(db) -> None:
    """Once the excess is gone the session may cross the night as planned."""
    broker, service = _overnight_service(db, held=40)
    service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))
    broker.positions["005930"] = Position("005930", 33, 98_000.0)

    restored = service.confirm_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert restored == ["005930"]
    assert service.machine("005930").state is LimitUpState.OVERNIGHT


def test_unfilled_trim_gives_up_the_carry_instead_of_breaching_the_cap(db) -> None:
    """Fail closed: an untrimmable position must not go overnight at all."""
    broker, service = _overnight_service(db, held=40)
    service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))
    trim_order_id = broker.orders[-1][0]

    assert service.confirm_overnight_cap(ref_date=dt.date(2026, 8, 28)) == []
    liquidated = service.force_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert liquidated == ["005930"]
    assert trim_order_id in broker.cancelled
    _, final = broker.orders[-1]
    assert (final.side, final.quantity) == (OrderSide.SELL, 40)
    assert service._sessions["005930"].end_reason == "overnight_cap_unfilled"


def test_account_position_without_session_fill_is_not_adopted(db) -> None:
    """Closing as unfilled while shares exist drops them out of every guard.

    A cancel racing a fill leaves real stock behind a CLOSED session — no hard
    stop, no REST fallback, no EOD review would ever look at it again.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)
    machine = service.machine("005930")
    assert machine.state is LimitUpState.NET_OPEN

    # the broker reports a position that landed just before the cancel
    broker.positions["005930"] = Position("005930", 12, 98_800.0)
    service.tick(now_monotonic=200.0, now_kst=now)  # no_fill_timeout -> CANCEL_BUYS

    assert machine.state is LimitUpState.CLOSED
    assert machine.filled_quantity == 0


def test_completed_exit_leaves_reconciling(db) -> None:
    """A session stuck in RECONCILING holds a slot and never books its P/L."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    _seed_owned(service, "005930", 20)
    broker.positions["005930"] = Position("005930", 20, 98_000.0)

    # hard stop submits the exit, but the broker has not filled it yet
    service.on_trade(_trade(3.0, 90_000), now_kst=now)
    assert machine.state is LimitUpState.RECONCILING

    # the fill lands later; the periodic reconcile must notice and close out
    broker.positions.pop("005930")
    service.tick(now_monotonic=100.0, now_kst=now)

    assert machine.state is LimitUpState.CLOSED
    assert service._active_count() == 0


def test_missed_eod_review_liquidates_instead_of_carrying_overnight(db) -> None:
    """Restarting through 15:18-15:20 must not hand an unreviewed position to tomorrow."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    broker.positions["005930"] = Position("005930", 40, 98_000.0)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=40)
    machine.state = LimitUpState.LOCKED  # 15:18 review never ran

    # 15:25 sees nothing to confirm, because the cap never trimmed anything
    assert service.confirm_overnight_cap(ref_date=dt.date(2026, 8, 28)) == []
    liquidated = service.force_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert liquidated == ["005930"]
    assert service._sessions["005930"].end_reason == "eod_review_missed"
    _, sell = broker.orders[-1]
    assert (sell.side, sell.quantity) == (OrderSide.SELL, 40)


def test_recovery_reaches_yesterdays_overnight_position(db) -> None:
    """A morning restart must not orphan the carry before the 30s exit window."""
    broker = ServiceBroker()
    broker.positions["005930"] = Position("005930", 20, 98_000.0)
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    carried = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    carried.state = LimitUpState.OVERNIGHT.value
    db.commit()

    # restarted the next morning
    service.recover(ref_date=dt.date(2026, 8, 31), now_monotonic=100.0)

    assert service.unknown_positions == []
    assert service.status()["manual_lock"] is False
    assert service.overnight_tickers() == ["005930"]


def test_failed_after_hours_escape_still_exits_at_the_next_open(db) -> None:
    """An unfilled after-hours order holds shares; leaving it out strands them a day."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.on_fill(at=1.0, cumulative_quantity=20)
    machine.state = LimitUpState.AFTER_HOURS_EXIT

    assert service.overnight_tickers() == ["005930"]


def test_restart_does_not_hand_back_attempts_the_day_already_spent(db) -> None:
    """Starting counters at zero mid-session lets the engine trade past its limits."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    ref_date = dt.date(2026, 8, 28)
    service.repository.save_guard(
        ref_date,
        attempts=5,
        pattern_failures=0,
        kosdaq_high=None,
        halted_reasons={"max_attempts"},
    )
    db.commit()

    restarted = _service(db, LimitUpMode.AUTOMATIC, broker)
    restarted._refresh_daily_pnl(ref_date)

    assert restarted.guard.attempts == 5
    assert "max_attempts" in restarted.guard.halted_reasons
    assert not restarted.guard.can_enter(active_sessions=0)


def test_kosdaq_latch_survives_a_restart_with_no_session_to_infer_it_from(db) -> None:
    """The halt can fire with nothing open, leaving no session trace to rebuild from.

    Inferring the latch from session end_reasons quietly released it in exactly
    that case — the one where no order existed to cancel.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    ref_date = dt.date(2026, 8, 28)
    at_kst = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    # 실제 기동 순서: 지수 관측이 첫 진입 심사보다 먼저 온다
    service.on_kosdaq(value=1_000.0, at=1.0, now_kst=at_kst)
    service.on_kosdaq(value=980.0, at=2.0, now_kst=at_kst)  # -2% latches
    assert "kosdaq_drawdown" in service.guard.halted_reasons
    assert service.repository.db.query(LimitUpSession).count() == 0

    restarted = _service(db, LimitUpMode.AUTOMATIC, broker)
    restarted._refresh_daily_pnl(ref_date)

    assert "kosdaq_drawdown" in restarted.guard.halted_reasons
    assert not restarted.guard.can_enter(active_sessions=0)


def test_restart_keeps_the_kosdaq_high_it_had_already_seen(db) -> None:
    """Rebuilding the high from the post-restart tape starts from a fallen price."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    ref_date = dt.date(2026, 8, 28)
    at_kst = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.on_kosdaq(value=1_000.0, at=1.0, now_kst=at_kst)

    restarted = _service(db, LimitUpMode.AUTOMATIC, broker)
    # one tick, already 1.6% below the pre-restart high — no _refresh_daily_pnl
    # first, because a real restart observes the index before any entry verdict
    restarted.on_kosdaq(value=984.0, at=3.0, now_kst=at_kst)

    assert restarted.guard.kosdaq_high == 1_000.0
    assert "kosdaq_drawdown" in restarted.guard.halted_reasons


def test_overnight_loss_lands_on_the_day_the_account_took_it(db) -> None:
    """Charging an overnight exit to the entry day hides it from the stop that matters."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    carried = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    # trimmed on the entry day, the rest sold at the next open
    carried.realized_pnl_by_date = {
        "2026-08-28": -50_000.0,
        "2026-08-31": -400_000.0,
    }
    db.commit()

    assert service.repository.realized_pnl_total(dt.date(2026, 8, 28)) == -50_000.0
    assert service.repository.realized_pnl_total(dt.date(2026, 8, 31)) == -400_000.0


def test_guard_rolls_over_at_the_date_boundary(db) -> None:
    """Yesterday's latch must not keep today's engine from trading."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    service._refresh_daily_pnl(dt.date(2026, 8, 28))
    service.guard.halted_reasons.add("feed_disconnected")
    assert not service.guard.can_enter(active_sessions=0)

    service._refresh_daily_pnl(dt.date(2026, 8, 31))

    assert service.guard.ref_date == dt.date(2026, 8, 31)
    assert service.guard.can_enter(active_sessions=0)


def test_recovery_restores_the_no_fill_timeout_clock(db) -> None:
    """Without net_fired_at an unfilled buy survives its 180s timeout forever."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    row.state = LimitUpState.NET_OPEN.value
    row.net_fired_at = dt.datetime(2026, 8, 28, 1, 0)  # UTC-naive, 200s before wall
    db.commit()

    wall = dt.datetime(2026, 8, 28, 10, 3, 20, tzinfo=KST)  # 01:03:20 UTC
    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=1_000.0, now_kst=wall)

    machine = service.machine("005930")
    assert machine.net_fired_at is not None
    # the timeout is already due, so the very next timer cancels the net
    service.tick(now_monotonic=1_000.0, now_kst=wall)
    assert machine.state is LimitUpState.CLOSED


def test_recovery_resubmits_an_exit_interrupted_before_it_was_sent(db) -> None:
    """Dying between 'decided to sell' and 'sent it' must not strand the position."""
    broker = ServiceBroker()
    broker.positions["005930"] = Position("005930", 20, 98_000.0)
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    row.state = LimitUpState.RECONCILING.value
    row.end_reason = "hard_stop"
    # 세션 소유분은 자기 레그 체결에서 나온다 — 계좌 보유가 아니다
    for name, qty in (("S", 12), ("A", 8)):
        leg = service.repository.upsert_leg(row, name=name, price=98_800, quantity=qty)
        leg.filled_quantity = qty
        leg.avg_fill_price = 98_800.0
    db.commit()

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert len(broker.orders) == 1
    _, sell = broker.orders[0]
    assert (sell.side, sell.quantity) == (OrderSide.SELL, 20)


def test_a_carry_confirmed_today_is_not_sold_at_todays_open(db) -> None:
    """15:18 turns a session OVERNIGHT while the market is still open.

    Regression: once the next-open exit became a deadline instead of a window,
    the same daily-actions pass sold the carry minutes after confirming it,
    destroying the strategy's entire reason for holding overnight.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    today = dt.date(2026, 8, 28)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.on_fill(at=1.0, cumulative_quantity=20)
    machine.state = LimitUpState.OVERNIGHT

    # during the 15:18-15:35 tail of the entry day
    assert service.overnight_tickers(before=today) == []
    # the next trading day it is due
    assert service.overnight_tickers(before=dt.date(2026, 8, 31)) == ["005930"]
    # unfiltered still reports it, for status displays
    assert service.overnight_tickers() == ["005930"]


def test_emergency_off_blocks_entries_but_still_sells_what_is_held(db) -> None:
    """The worst failure is a kill switch that makes the system *think* it exited.

    Regression: emergency_off set mode=OFF, and every protective path checked
    mode, so a held position was marked CLOSED without an order ever reaching
    the broker.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    _seed_owned(service, "005930", 20)
    broker.positions["005930"] = Position("005930", 20, 98_000.0)

    service.emergency_off()

    assert service.mode is LimitUpMode.OFF
    assert not service.guard.can_enter(active_sessions=0)
    assert service.exits_are_live()  # selling must still work

    # a hard stop after the kill switch must place a real order
    service.on_trade(_trade(3.0, 90_000), now_kst=now)

    sells = [order for _, order in broker.orders if order.side is OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 20


def test_recommend_only_never_places_exits_even_though_the_gate_is_shared(db) -> None:
    """exits_are_live must not accidentally open orders in signals-only mode."""
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)

    assert not service.exits_are_live()


def test_a_failed_stop_loss_order_does_not_strand_the_position(db) -> None:
    """One broker error used to leave real shares outside every guard.

    The machine latches RECONCILING before the order goes out and suppresses
    repeat exits from there, so nothing retried it.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    _seed_owned(service, "005930", 20)
    broker.positions["005930"] = Position("005930", 20, 98_000.0)

    # the protective sell blows up mid-flight
    def _explode(order):
        raise BrokerAdapterError("broker rejected the exit")

    original = broker.place_order
    broker.place_order = _explode
    try:
        service.on_trade(_trade(3.0, 90_000), now_kst=now)
    except BrokerAdapterError:
        pass
    broker.place_order = original
    assert machine.state is LimitUpState.RECONCILING
    assert broker.orders == []

    # the periodic tick must notice the position has no order behind it
    service.tick(now_monotonic=500.0, now_kst=now)

    sells = [order for _, order in broker.orders if order.side is OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 20


def test_recommendation_hard_stop_closes_paper_ledger_across_restart(db) -> None:
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 98_000), now_kst=now)
    assert service.machine("005930").filled_quantity == 12

    service.on_trade(_trade(4.0, 90_000), now_kst=now)

    session = service._sessions["005930"]
    assert session.state == LimitUpState.CLOSED.value
    assert service.repository.remaining_quantity(session) == 0
    assert session.exit_quantity_by_date == {"2026-08-28": 12}
    restarted = _service(db, LimitUpMode.AUTOMATIC, ServiceBroker())
    restarted.recover(ref_date=now.date(), now_monotonic=100.0, now_kst=now)
    assert restarted.watched_tickers() == ()


def test_live_session_stays_sellable_after_mode_downgrade(db) -> None:
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20)
    _seed_owned(service, "005930", 20)
    broker.positions["005930"] = Position("005930", 20, 98_000.0)

    service.set_mode(LimitUpMode.RECOMMEND_ONLY)
    service.on_trade(_trade(3.0, 90_000), now_kst=now)

    sells = [order for _, order in broker.orders if order.side is OrderSide.SELL]
    assert [order.quantity for order in sells] == [20]


def test_paper_session_does_not_dilute_live_overnight_allowance(db) -> None:
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 14, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    live = service.machine("005930")
    live.state = LimitUpState.OVERNIGHT
    live.filled_quantity = 40
    _seed_owned(service, "005930", 40)
    broker.positions["005930"] = Position("005930", 40, 98_000.0)

    service.set_mode(LimitUpMode.RECOMMEND_ONLY)
    service.watch_candidate(Candidate(
        ticker="000660", market="KOSPI", upper_limit_price=100_000,
        total_listed_shares=10_000_000, current_price=96_000, change_rate=25.0,
    ), now_kst=now)
    paper = service.machine("000660")
    paper.state = LimitUpState.OVERNIGHT
    paper.filled_quantity = 40
    _seed_owned(service, "000660", 40)

    allowances = service.overnight_allowances(now.date())

    assert allowances == {"005930": 33, "000660": 33}


def test_unknown_recovered_session_locks_without_selling(db) -> None:
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28), ticker="005930", market="KOSPI",
        upper_limit_price=100_000, trigger_price=99_700, execution_mode="unknown",
    )
    row.state = LimitUpState.OVERNIGHT.value
    leg = service.repository.upsert_leg(row, name="S", price=98_800, quantity=10)
    leg.filled_quantity = 10
    broker.positions["005930"] = Position("005930", 10, 98_800.0)
    db.commit()

    service.recover(ref_date=row.ref_date, now_monotonic=100.0)

    assert service.manual_lock
    assert service.unknown_positions == ["005930"]
    assert [order for _, order in broker.orders if order.side is OrderSide.SELL] == []


@pytest.mark.parametrize(
    "cancel_result",
    [False, BrokerAdapterError("cancel failed"), RuntimeError("timeout")],
)
def test_protective_sell_waits_for_verified_buy_cancels(db, cancel_result) -> None:
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    machine = service.machine("005930")
    machine.on_fill(at=2.5, cumulative_quantity=20)
    _seed_owned(service, "005930", 20)
    broker.positions["005930"] = Position("005930", 20, 98_800.0)

    def cancel(order_id: str) -> bool:
        if isinstance(cancel_result, Exception):
            raise cancel_result
        return cancel_result

    broker.cancel_order = cancel  # type: ignore[method-assign]
    service.on_trade(_trade(3.0, 90_000), now_kst=now)

    assert service.manual_lock
    assert [order for _, order in broker.orders if order.side is OrderSide.SELL] == []


def test_account_daily_loss_is_passed_to_entry_risk_gate(db) -> None:
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    today = dt.date(2026, 8, 28)
    db.add_all([
        PortfolioSnapshot(
            ref_date=dt.date(2026, 8, 27), source="broker", total_assets=100
        ),
        PortfolioSnapshot(ref_date=today, source="broker", total_assets=98.5),
    ])
    db.commit()
    now = dt.datetime.combine(today, dt.time(10), tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    assert broker.orders == []


def test_recover_does_not_lock_on_a_watch_only_row_of_unknown_provenance(db) -> None:
    """A never-fired watching row is not exposure; locking on it re-locks every boot."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    row = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28), ticker="005930", market="KOSPI",
        upper_limit_price=100_000, trigger_price=99_700, execution_mode="unknown",
    )
    assert row.state == LimitUpState.WATCHING.value
    db.commit()

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is False


def test_watch_and_trigger_are_announced_to_the_operator(db, monkeypatch) -> None:
    """추천 모드의 신호는 알림 말고는 사람에게 닿는 경로가 없다.

    화면은 사람이 열어야 보이고, `recommend_only` 는 주문을 내지 않아 체결 통보도
    없다. 감시 등록과 트리거가 조용하면 그날의 추천은 아무도 모른 채 지나간다.
    """
    from maps.limit_up import notify

    sent: list[str] = []
    monkeypatch.setattr(notify, "push", sent.append)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)

    assert service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert "상한가 감시" in sent[0]
    trigger = next(text for text in sent if "상한가 트리거" in text)
    assert "005930" in trigger
    # 그리드가 붙지 않으면 "무엇을 얼마에 사라" 가 빠져 추천이 실행 불가능해진다.
    assert "주 @" in trigger
