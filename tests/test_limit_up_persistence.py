"""Persistence invariants for upper-limit sessions and commands."""

from __future__ import annotations

import datetime as dt

from maps.common.models import LimitUpEvent, LimitUpOrderLeg, LimitUpTape
from maps.limit_up.domain import LimitUpState
from maps.limit_up.feed import TapeSnapshot
from maps.limit_up.repository import LimitUpRepository


def test_session_identity_is_unique_per_trading_day_and_ticker(db) -> None:
    """A restart must reuse, not duplicate, the same daily ticker session."""
    repo = LimitUpRepository(db)

    first = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )
    second = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        total_listed_shares=10_000_000,
    )

    assert first.id == second.id
    assert first.total_listed_shares == 10_000_000


def test_transition_increments_version_and_deduplicates_same_action(db) -> None:
    """A repeated callback must not create a second broker command intent."""
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
    )

    first = repo.transition(
        session,
        state=LimitUpState.NET_OPEN,
        action="fire_net",
        payload={"turnover": 50_000_000_000},
    )
    duplicate = repo.append_event(
        session,
        action="fire_net",
        state_version=session.state_version,
        payload={"turnover": 50_000_000_000},
    )

    assert session.state == LimitUpState.NET_OPEN.value
    assert session.state_version == 1
    assert first.id == duplicate.id
    assert db.query(LimitUpEvent).count() == 1


def test_order_leg_upsert_keeps_one_s_and_one_a_record(db) -> None:
    """Recovery must update the original leg instead of inventing a third order."""
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
    )

    leg = repo.upsert_leg(session, name="S", price=98_800, quantity=12)
    same = repo.upsert_leg(
        session,
        name="S",
        price=98_800,
        quantity=12,
        broker_order_id="12345",
        status="pending",
    )

    assert leg.id == same.id
    assert same.broker_order_id == "12345"
    assert db.query(LimitUpOrderLeg).count() == 1


def test_forced_transition_tape_is_persisted_as_one_bounded_snapshot(db) -> None:
    """Post-mortem replay must retain evidence around every critical transition."""
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
    )

    row = repo.persist_tape(
        session,
        TapeSnapshot(
            ticker="005930",
            transition="FIRST_FILL",
            payload=({"at": 1.0, "price": 99_000}, {"at": 2.0, "price": 100_000}),
        ),
    )

    assert row.transition == "FIRST_FILL"
    assert row.started_at_monotonic == 1.0
    assert row.ended_at_monotonic == 2.0
    assert db.query(LimitUpTape).count() == 1
