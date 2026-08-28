"""Serialized broker command worker for the upper-limit V1 engine."""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from enum import Enum

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
from maps.limit_up.domain import GridLeg
from maps.limit_up.repository import LimitUpRepository


class WorkerTaskKind(str, Enum):
    """Serialized worker operations ordered by safety priority."""

    SELL_POSITION = "sell_position"
    CANCEL_BUYS = "cancel_buys"
    RECONCILE = "reconcile"
    FIRE_GRID = "fire_grid"


_PRIORITY = {
    WorkerTaskKind.SELL_POSITION: 0,
    WorkerTaskKind.CANCEL_BUYS: 10,
    WorkerTaskKind.RECONCILE: 20,
    WorkerTaskKind.FIRE_GRID: 30,
}


@dataclass(frozen=True)
class ReconcileResult:
    """Broker-authoritative session facts after reconciliation."""

    position_quantity: int
    open_order_ids: tuple[str, ...]
    filled_quantity: int


@dataclass(order=True)
class _QueuedTask:
    """Heap entry whose payload is excluded from ordering."""

    priority: int
    sequence: int
    kind: WorkerTaskKind = field(compare=False)
    session: LimitUpSession = field(compare=False)
    payload: object = field(compare=False, default=None)


class LimitUpCommandWorker:
    """One serialized queue for entry, cancellation, reconciliation, and exit."""

    def __init__(
        self,
        order_manager: OrderManager,
        broker: BrokerAdapter,
        repository: LimitUpRepository,
    ) -> None:
        """Bind existing execution and persistence boundaries."""
        self.order_manager = order_manager
        self.broker = broker
        self.repository = repository
        self._queue: list[_QueuedTask] = []
        self._sequence = itertools.count()

    def legs(self, session: LimitUpSession) -> list[LimitUpOrderLeg]:
        """Return fixed legs in deterministic A/S order for audit displays."""
        return (
            self.repository.db.query(LimitUpOrderLeg)
            .filter(LimitUpOrderLeg.session_id == session.id)
            .order_by(LimitUpOrderLeg.name)
            .all()
        )

    def enqueue_grid(
        self, session: LimitUpSession, grid: tuple[GridLeg, GridLeg]
    ) -> None:
        """Queue the two-leg entry behind all protective work."""
        self._push(WorkerTaskKind.FIRE_GRID, session, grid)

    def enqueue_sell(self, session: LimitUpSession, *, reason: str) -> None:
        """Queue a protective sell at the highest priority."""
        self._push(WorkerTaskKind.SELL_POSITION, session, reason)

    def enqueue_cancel(self, session: LimitUpSession) -> None:
        """Queue pending-buy cancellation ahead of entry work."""
        self._push(WorkerTaskKind.CANCEL_BUYS, session)

    def enqueue_reconcile(self, session: LimitUpSession) -> None:
        """Queue broker reconciliation."""
        self._push(WorkerTaskKind.RECONCILE, session)

    def run_next(self) -> WorkerTaskKind | None:
        """Execute one highest-priority task and return its kind."""
        if not self._queue:
            return None
        task = heapq.heappop(self._queue)
        if task.kind is WorkerTaskKind.SELL_POSITION:
            self.sell_actual_position(task.session, reason=str(task.payload))
        elif task.kind is WorkerTaskKind.CANCEL_BUYS:
            self.cancel_pending_buys(task.session)
        elif task.kind is WorkerTaskKind.RECONCILE:
            self.reconcile(task.session)
        else:
            self.fire_grid(task.session, task.payload)  # type: ignore[arg-type]
        return task.kind

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
        self.order_manager.submit_exit(order, exit_reason=reason)
        return self.reconcile(session)

    def _push(
        self,
        kind: WorkerTaskKind,
        session: LimitUpSession,
        payload: object = None,
    ) -> None:
        """Push one task using a stable FIFO sequence within each priority."""
        heapq.heappush(
            self._queue,
            _QueuedTask(_PRIORITY[kind], next(self._sequence), kind, session, payload),
        )

    def _leg(self, session: LimitUpSession, name: str) -> LimitUpOrderLeg:
        """Return one required persisted leg."""
        return (
            self.repository.db.query(LimitUpOrderLeg)
            .filter(LimitUpOrderLeg.session_id == session.id)
            .filter(LimitUpOrderLeg.name == name)
            .one()
        )
