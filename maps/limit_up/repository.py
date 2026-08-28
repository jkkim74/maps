"""Small transactional repository for upper-limit V1 recovery state."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.common.models import LimitUpEvent, LimitUpOrderLeg, LimitUpSession, LimitUpTape
from maps.limit_up.domain import LimitUpState
from maps.limit_up.feed import TapeSnapshot


class LimitUpRepository:
    """Persist sessions, fixed legs, and idempotent transition events."""

    def __init__(self, db: Session) -> None:
        """Bind the repository to the caller-owned transaction."""
        self.db = db

    def create_or_get_session(
        self,
        *,
        ref_date: dt.date,
        ticker: str,
        market: str,
        upper_limit_price: int,
        trigger_price: int,
        total_listed_shares: int = 0,
    ) -> LimitUpSession:
        """Return the unique daily ticker session, creating it when absent."""
        existing = (
            self.db.query(LimitUpSession)
            .filter(LimitUpSession.ref_date == ref_date)
            .filter(LimitUpSession.ticker == ticker)
            .one_or_none()
        )
        if existing is not None:
            if total_listed_shares > 0:
                existing.total_listed_shares = total_listed_shares
            return existing
        row = LimitUpSession(
            ref_date=ref_date,
            ticker=ticker,
            market=market,
            upper_limit_price=upper_limit_price,
            trigger_price=trigger_price,
            total_listed_shares=total_listed_shares,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def event_exists(
        self,
        session: LimitUpSession,
        *,
        action: str,
        state_version: int,
        leg: str | None = None,
    ) -> bool:
        """Return whether a durable side-effect intent already exists."""
        key = f"{session.id}:{action}:{leg or '-'}:{state_version}"
        return (
            self.db.query(LimitUpEvent.id)
            .filter(LimitUpEvent.idempotency_key == key)
            .first()
            is not None
        )

    def transition(
        self,
        session: LimitUpSession,
        *,
        state: LimitUpState,
        action: str,
        payload: dict | None = None,
        leg: str | None = None,
    ) -> LimitUpEvent:
        """Advance state/version and append its durable command intent."""
        session.state_version += 1
        session.state = state.value
        event = self.append_event(
            session,
            action=action,
            state_version=session.state_version,
            payload=payload,
            leg=leg,
        )
        self.db.flush()
        return event

    def append_event(
        self,
        session: LimitUpSession,
        *,
        action: str,
        state_version: int,
        payload: dict | None = None,
        leg: str | None = None,
    ) -> LimitUpEvent:
        """Append an event once for session/action/leg/state-version."""
        key = f"{session.id}:{action}:{leg or '-'}:{state_version}"
        existing = (
            self.db.query(LimitUpEvent)
            .filter(LimitUpEvent.idempotency_key == key)
            .one_or_none()
        )
        if existing is not None:
            return existing
        row = LimitUpEvent(
            session_id=session.id,
            state_version=state_version,
            action=action,
            leg=leg,
            idempotency_key=key,
            payload=payload,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def upsert_leg(
        self,
        session: LimitUpSession,
        *,
        name: str,
        price: int,
        quantity: int,
        broker_order_id: str | None = None,
        status: str | None = None,
    ) -> LimitUpOrderLeg:
        """Create or update the unique fixed S/A leg."""
        if name not in {"S", "A"}:
            raise ValueError("leg name must be S or A")
        row = (
            self.db.query(LimitUpOrderLeg)
            .filter(LimitUpOrderLeg.session_id == session.id)
            .filter(LimitUpOrderLeg.name == name)
            .one_or_none()
        )
        if row is None:
            row = LimitUpOrderLeg(
                session_id=session.id,
                name=name,
                price=price,
                quantity=quantity,
            )
            self.db.add(row)
        else:
            row.price = price
            row.quantity = quantity
        if broker_order_id is not None:
            row.broker_order_id = broker_order_id
        if status is not None:
            row.status = status
        self.db.flush()
        return row

    def persist_tape(
        self, session: LimitUpSession, snapshot: TapeSnapshot
    ) -> LimitUpTape:
        """Persist one copied ring-buffer snapshot outside the feed callback."""
        payload = [dict(item) for item in snapshot.payload]
        row = LimitUpTape(
            session_id=session.id,
            transition=snapshot.transition,
            started_at_monotonic=(float(payload[0]["at"]) if payload else None),
            ended_at_monotonic=(float(payload[-1]["at"]) if payload else None),
            payload=payload,
        )
        self.db.add(row)
        self.db.flush()
        return row
