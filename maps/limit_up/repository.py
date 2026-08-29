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

    def realized_pnl_total(self, ref_date: dt.date) -> float:
        """Return P/L **booked on** ``ref_date``, across sessions of any entry day.

        Not the sum of sessions that *started* that day: an overnight carry exits
        the next morning, and that loss belongs to the day the account took it —
        the day whose stop must react to it.

        The daily guard is rebuilt from this sum instead of an in-memory tally so
        it survives a process restart. ``ref_date`` is a KST trading date, never
        derived from the UTC-naive ``created_at``.
        """
        key = ref_date.isoformat()
        rows = (
            self.db.query(LimitUpSession.realized_pnl_by_date)
            .filter(LimitUpSession.ref_date <= ref_date)
            .filter(LimitUpSession.realized_pnl_by_date.isnot(None))
            .all()
        )
        return float(sum((row[0] or {}).get(key, 0.0) for row in rows))

    def guard_state(self, ref_date: dt.date) -> tuple[int, int, bool]:
        """Return today's persisted attempt count, pattern failures, and halt flag.

        A restart that starts these at zero hands back limits the day already
        spent. Sessions are the durable record of both.

        Args:
            ref_date: KST trading date.

        Returns:
            ``(attempts, pattern_failures, market_halted)``.
        """
        rows = (
            self.db.query(
                LimitUpSession.net_fired_at,
                LimitUpSession.pattern_failure_counted,
                LimitUpSession.end_reason,
            )
            .filter(LimitUpSession.ref_date == ref_date)
            .all()
        )
        attempts = sum(1 for fired, _, _ in rows if fired is not None)
        failures = sum(1 for _, counted, _ in rows if counted)
        halted = any(reason == "market_halt" for _, _, reason in rows)
        return attempts, failures, halted

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
