"""Broker command operations for the upper-limit V1 engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from maps.common.exceptions import BrokerAdapterError
from maps.common.models import LimitUpOrderLeg, LimitUpSession
from maps.execution.broker_adapter import (
    BrokerAdapter,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    raw_broker_order_id,
)
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import GridLeg, realized_pnl
from maps.limit_up.repository import LimitUpRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileResult:
    """Broker-authoritative session facts after reconciliation."""

    position_quantity: int
    open_order_ids: tuple[str, ...]
    filled_quantity: int


class LimitUpCommandWorker:
    """Broker-facing entry, cancellation, reconciliation, and exit operations."""

    def __init__(
        self,
        order_manager: OrderManager,
        broker: BrokerAdapter,
        repository: LimitUpRepository,
    ) -> None:
        """Bind existing execution and persistence boundaries.

        Ordering is not this class's job. Serialization and sell-before-buy
        priority live in ``KISIntradayRuntime._service_pump``, which is the only
        thing that actually owns the event loop.
        """
        self.order_manager = order_manager
        self.broker = broker
        self.repository = repository

    def legs(self, session: LimitUpSession) -> list[LimitUpOrderLeg]:
        """Return fixed legs in deterministic A/S order for audit displays."""
        return (
            self.repository.db.query(LimitUpOrderLeg)
            .filter(LimitUpOrderLeg.session_id == session.id)
            .order_by(LimitUpOrderLeg.name)
            .all()
        )

    def fire_grid(
        self,
        session: LimitUpSession,
        grid: tuple[GridLeg, GridLeg],
        *,
        daily_pnl_ratio: float = 0.0,
    ) -> ReconcileResult:
        """Persist then submit S and A back-to-back, unwinding a half-open grid."""
        for spec in grid:
            self.repository.upsert_leg(
                session, name=spec.name, price=spec.price, quantity=spec.quantity
            )
        self.repository.db.commit()

        ambiguous_intent = False
        for spec in grid:
            leg = self._leg(session, spec.name)
            if (
                not leg.broker_order_id
                and self.repository.event_exists(
                    session,
                    action="submit_buy",
                    state_version=session.state_version,
                    leg=spec.name,
                )
            ):
                leg.status = "reconciling"
                ambiguous_intent = True
        if ambiguous_intent:
            self.repository.db.commit()
            return self.reconcile(session)

        for spec in grid:
            leg = self._leg(session, spec.name)
            if leg.broker_order_id or leg.status not in {"created", "rejected"}:
                continue
            self.repository.append_event(
                session,
                action="submit_buy",
                state_version=session.state_version,
                leg=spec.name,
                payload={"price": spec.price, "quantity": spec.quantity},
            )
            self.repository.db.commit()
            order = Order(
                strategy_id=f"limit_up_v1:{spec.name}",
                ticker=session.ticker,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=spec.quantity,
                limit_price=spec.price,
                decision_context={
                    "limit_up_session_id": session.id,
                    "limit_up_leg": spec.name,
                },
            )
            try:
                result = self.order_manager.submit(
                    order,
                    daily_pnl=daily_pnl_ratio,
                    risk_strategy_id="limit_up_v1",
                )
            except Exception:
                leg.status = "rejected"
                self.repository.db.commit()
                return self.cancel_pending_buys(session)
            leg.broker_order_id = result.order_id
            leg.filled_quantity = result.filled_quantity
            leg.avg_fill_price = result.avg_price or None
            leg.status = result.status.value
            self.repository.db.commit()
        return self.reconcile(session)

    def cancel_pending_buys(self, session: LimitUpSession) -> ReconcileResult:
        """Cancel all known buy legs and reconcile every ambiguous response."""
        ambiguous = False
        for leg in self.legs(session):
            if not leg.broker_order_id or leg.status not in {
                "created",
                "pending",
                "partially_filled",
                "reconciling",
            }:
                continue
            self.repository.append_event(
                session,
                action="cancel_buy",
                state_version=session.state_version,
                leg=leg.name,
                payload={"broker_order_id": leg.broker_order_id},
            )
            self.repository.db.commit()
            try:
                self.order_manager.cancel(leg.broker_order_id)
            except BrokerAdapterError:
                ambiguous = True
                leg.status = "reconciling"
            else:
                leg.status = "cancelled"
            self.repository.db.commit()
        result = self.reconcile(session)
        if ambiguous:
            return result
        return result

    def reconcile(self, session: LimitUpSession) -> ReconcileResult:
        """Apply daily fills, then open orders, then the actual broker holding."""
        daily_results = self.broker.get_daily_order_results()
        result_by_id = {
            raw_broker_order_id(result.order_id): result for result in daily_results
        }
        open_orders = self.broker.get_open_orders()
        open_by_id = {
            raw_broker_order_id(order.order_id): order for order in open_orders
        }
        for leg in self.legs(session):
            if not leg.broker_order_id:
                continue
            raw_id = raw_broker_order_id(leg.broker_order_id)
            result = result_by_id.get(raw_id)
            if result is not None:
                leg.filled_quantity = result.filled_quantity
                leg.avg_fill_price = result.avg_price or leg.avg_fill_price
                leg.status = result.status.value
                continue
            pending = open_by_id.get(raw_id)
            if pending is not None:
                leg.filled_quantity = max(0, pending.quantity - pending.remaining_quantity)
                leg.status = (
                    OrderStatus.PARTIALLY_FILLED.value
                    if leg.filled_quantity
                    else OrderStatus.PENDING.value
                )
        self._settle_realized_pnl(session, result_by_id)
        position = self.broker.get_position(session.ticker)
        self.repository.db.commit()
        return ReconcileResult(
            position_quantity=position.quantity if position else 0,
            open_order_ids=tuple(sorted(open_by_id)),
            filled_quantity=sum(leg.filled_quantity for leg in self.legs(session)),
        )

    def sell_actual_position(
        self, session: LimitUpSession, *, reason: str
    ) -> ReconcileResult:
        """Reconcile actual quantity and submit one market exit for that remainder."""
        position = self.broker.get_position(session.ticker)
        if position is None or position.quantity <= 0:
            return self.reconcile(session)
        self.repository.append_event(
            session,
            action="market_sell",
            state_version=session.state_version,
            payload={"quantity": position.quantity, "reason": reason},
        )
        self.repository.db.commit()
        order = Order(
            strategy_id="limit_up_v1:exit",
            ticker=session.ticker,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=position.quantity,
            current_price=position.current_price or position.avg_price,
            decision_context={"limit_up_session_id": session.id, "reason": reason},
        )
        result = self.order_manager.submit_exit(order, exit_reason=reason)
        self._record_exit_order(session, result.order_id)
        self.repository.db.commit()
        return self.reconcile(session)

    def _record_exit_order(self, session: LimitUpSession, order_id: str) -> None:
        """Append one exit order id to the session ledger.

        A session can exit more than once — an EOD trim, then the remainder — and
        the broker's daily results carry no strategy id (KIS returns an empty
        one), so order ids are the only way back to our own sells.
        """
        existing = [item for item in (session.exit_order_ids or "").split(",") if item]
        if order_id in existing:
            return
        existing.append(order_id)
        session.exit_order_ids = ",".join(existing)

    def _settle_realized_pnl(
        self, session: LimitUpSession, result_by_id: dict[str, OrderResult]
    ) -> None:
        """Book each filled exit against the day it actually settled on.

        An overnight carry exits the *next* morning, so charging that loss to the
        entry day would hide it from the day the account really lost the money —
        the day whose stop is supposed to react.

        Only days present in this snapshot are rewritten. The broker returns
        same-day orders only, so yesterday's trim would otherwise vanish from the
        ledger the moment today's exit fills.

        Reuses the caller's daily-results snapshot, so settlement costs no extra
        broker call. A day with no filled exit is left absent, not zeroed — an
        unpriced exit is unknown.
        """
        legs = self.legs(session)
        buy_quantity = sum(leg.filled_quantity for leg in legs)
        buy_amount = sum(
            leg.filled_quantity * (leg.avg_fill_price or leg.price) for leg in legs
        )
        if buy_quantity <= 0:
            return

        by_day: dict[str, tuple[int, float]] = {}
        for order_id in (session.exit_order_ids or "").split(","):
            if not order_id:
                continue
            result = result_by_id.get(raw_broker_order_id(order_id))
            if result is None or result.filled_quantity <= 0:
                continue
            day = (result.filled_at or result.submitted_at).date().isoformat()
            quantity, amount = by_day.get(day, (0, 0.0))
            by_day[day] = (
                quantity + result.filled_quantity,
                amount + result.filled_quantity * result.avg_price,
            )
        if not by_day:
            return

        ledger = dict(session.realized_pnl_by_date or {})
        for day, (quantity, amount) in by_day.items():
            ledger[day] = realized_pnl(
                buy_amount=buy_amount,
                buy_quantity=buy_quantity,
                sell_amount=amount,
                sell_quantity=quantity,
            )
        session.realized_pnl_by_date = ledger
        # 총합은 원장에서 파생된다 — 두 곳에서 따로 계산하면 조용히 어긋난다.
        session.realized_pnl = sum(ledger.values())

    def cancel_open_exits(self, session: LimitUpSession) -> int:
        """Cancel every still-open exit order and return how many were pulled.

        Must run before any follow-up sell: an unfilled trim limit order still
        reserves shares, so selling the whole position on top of it would submit
        more shares than are held.
        """
        recorded = {
            raw_broker_order_id(item)
            for item in (session.exit_order_ids or "").split(",")
            if item
        }
        cancelled = 0
        for order in self.broker.get_open_orders():
            if order.ticker != session.ticker or order.side is not OrderSide.SELL:
                continue
            raw_id = raw_broker_order_id(order.order_id)
            # 브로커에는 접수됐는데 주문 ID 를 저장하기 전에 죽으면 exit_order_ids 에
            # 없다. 원장만 믿고 취소하면 그 매도가 살아 있는 채로 전량매도를 덧대어
            # 보유보다 많이 팔게 된다. 티커의 열린 매도는 전부 취소 대상이다.
            if raw_id not in recorded:
                logger.warning(
                    "기록되지 않은 매도 주문 발견 [%s] id=%s — 중복 매도를 막기 위해 취소",
                    session.ticker, order.order_id,
                )
            try:
                self.order_manager.cancel(order.order_id)
            except BrokerAdapterError:
                continue
            self._record_exit_order(session, order.order_id)
            cancelled += 1
        self.repository.db.commit()
        return cancelled

    def sell_overnight_excess(
        self, session: LimitUpSession, *, quantity: int, price: int
    ) -> ReconcileResult:
        """Submit one limit sell for shares that exceed the overnight budget.

        Priced at the upper limit because the overnight test itself requires a
        bid wall of >=1% of listed shares, so a limit sell into it fills. A market
        sell would instead walk the book down and give up the locked close.
        """
        position = self.broker.get_position(session.ticker)
        if quantity <= 0 or position is None or position.quantity <= 0:
            return self.reconcile(session)
        quantity = min(quantity, position.quantity)
        # The caller's state transition already owns the "overnight_trim" key at
        # this state_version, so the submit intent needs its own action name.
        if self.repository.event_exists(
            session, action="overnight_trim_sell", state_version=session.state_version
        ):
            return self.reconcile(session)
        self.repository.append_event(
            session,
            action="overnight_trim_sell",
            state_version=session.state_version,
            payload={"quantity": quantity, "price": price},
        )
        self.repository.db.commit()
        order = Order(
            strategy_id="limit_up_v1:exit",
            ticker=session.ticker,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            limit_price=price,
            current_price=position.current_price or position.avg_price,
            decision_context={
                "limit_up_session_id": session.id,
                "reason": "overnight_cap",
            },
        )
        result = self.order_manager.submit_exit(order, exit_reason="overnight_cap")
        self._record_exit_order(session, result.order_id)
        self.repository.db.commit()
        return self.reconcile(session)

    def _leg(self, session: LimitUpSession, name: str) -> LimitUpOrderLeg:
        """Return one required persisted leg."""
        return (
            self.repository.db.query(LimitUpOrderLeg)
            .filter(LimitUpOrderLeg.session_id == session.id)
            .filter(LimitUpOrderLeg.name == name)
            .one()
        )
