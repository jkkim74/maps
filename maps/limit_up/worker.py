"""Broker command operations for the upper-limit V1 engine."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import or_

from maps.common.exceptions import BrokerAdapterError
from maps.common.models import LimitUpOrderLeg, LimitUpSession, OrderLog
from maps.common.settings import get_settings
from maps.execution.broker_adapter import (
    BrokerAdapter,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    order_log_id,
    raw_broker_order_id,
)
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import (
    EXIT_STRATEGY_IDS,
    GridLeg,
    exit_audit_code,
    exit_strategy_id,
    realized_pnl,
)
from maps.limit_up.repository import LimitUpRepository


logger = logging.getLogger(__name__)
_KST = dt.timezone(dt.timedelta(hours=9))


@dataclass(frozen=True)
class CancelExitsResult:
    """Outcome of pulling a session's open exit orders."""

    cancelled: int
    stranded: int

    @property
    def is_clear(self) -> bool:
        """Return whether no open exit is left to collide with a new order."""
        return self.stranded == 0


@dataclass(frozen=True)
class ReconcileResult:
    """Broker-authoritative session facts after reconciliation.

    Attributes:
        position_quantity: **Account-wide** holding for the ticker. On a shared
            account this includes other strategies' shares — never treat it as
            this session's position.
        open_buy_order_ids: This session's still-open entry ids.
        open_exit_order_ids: This session's still-open exit ids.
        bought_quantity: Cumulative fills on this session's buy legs.
        exited_quantity: Cumulative fills on this session's exit ledger.
    """

    position_quantity: int
    open_buy_order_ids: tuple[str, ...]
    open_exit_order_ids: tuple[str, ...]
    bought_quantity: int
    exited_quantity: int

    @property
    def open_order_ids(self) -> tuple[str, ...]:
        """Return every session-owned open order."""
        return tuple(sorted({*self.open_buy_order_ids, *self.open_exit_order_ids}))

    @property
    def filled_quantity(self) -> int:
        """Compatibility alias for gross buy fills."""
        return self.bought_quantity

    @property
    def remaining_quantity(self) -> int:
        """Return bought shares less confirmed session exits."""
        return max(0, self.bought_quantity - self.exited_quantity)

    @property
    def owned_quantity(self) -> int:
        """Return shares this session actually holds.

        The account can hold more (another strategy) or fewer (someone sold
        outside the engine); both directions matter, so take the smaller.
        """
        return max(0, min(self.position_quantity, self.remaining_quantity))


@dataclass(frozen=True)
class CancelBuysResult:
    """Broker-verified result of cancelling a session's entry orders."""

    reconciliation: ReconcileResult
    stranded_buy_ids: tuple[str, ...]

    @property
    def is_clear(self) -> bool:
        """Return whether broker truth shows no entry order still open."""
        return not self.stranded_buy_ids

    @property
    def position_quantity(self) -> int:
        """Forward the account diagnostic for compatibility."""
        return self.reconciliation.position_quantity

    @property
    def open_order_ids(self) -> tuple[str, ...]:
        """Forward all open session orders for compatibility."""
        return self.reconciliation.open_order_ids

    @property
    def owned_quantity(self) -> int:
        """Forward the session-owned quantity."""
        return self.reconciliation.owned_quantity


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
                return self.cancel_pending_buys(session).reconciliation
            leg.broker_order_id = result.order_id
            leg.filled_quantity = result.filled_quantity
            leg.avg_fill_price = result.avg_price or None
            leg.status = result.status.value
            self.repository.db.commit()
        return self.reconcile(session)

    def cancel_pending_buys(self, session: LimitUpSession) -> CancelBuysResult:
        """Cancel known buy legs and verify the result against broker truth."""
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
                cancelled = self.order_manager.cancel(leg.broker_order_id)
            except Exception:
                leg.status = "reconciling"
                logger.exception(
                    "매수 취소 예외 [%s] id=%s", session.ticker, leg.broker_order_id
                )
            else:
                leg.status = "cancelled" if cancelled else "reconciling"
            self.repository.db.commit()
        # 호출 결과가 False이거나 예외여도 최종 판단은 브로커 미체결 목록으로 한다.
        # 남은 매수 ID를 반환해야 호출부가 같은 명령 묶음의 매도를 중단할 수 있다.
        result = self.reconcile(session)
        return CancelBuysResult(
            reconciliation=result,
            stranded_buy_ids=result.open_buy_order_ids,
        )

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
        self._settle_exit_ledger(session, result_by_id)
        position = self.broker.get_position(session.ticker)
        # 이 세션이 낸 주문만 열린 주문으로 센다. 같은 종목의 다른 전략 주문이나 우리
        # 매수 주문까지 세면 정체된 손절의 재제출이 영영 막힌다.
        buy_order_ids = {
            raw_broker_order_id(leg.broker_order_id)
            for leg in self.legs(session)
            if leg.broker_order_id
        }
        exit_order_ids = {
            raw_broker_order_id(item)
            for item in (session.exit_order_ids or "").split(",")
            if item
        }
        bought = self.repository.bought_quantity(session)
        exited = self.repository.exited_quantity(session)
        self.repository.db.commit()
        return ReconcileResult(
            position_quantity=position.quantity if position else 0,
            # 이 세션의 티커로 좁힌다. 계좌 전체를 담으면 무관한 주문 하나 때문에
            # tick() 의 "열린 주문이 없다" 재제출 조건이 영영 성립하지 않는다.
            open_buy_order_ids=tuple(sorted(buy_order_ids & open_by_id.keys())),
            open_exit_order_ids=tuple(sorted(exit_order_ids & open_by_id.keys())),
            bought_quantity=bought,
            exited_quantity=exited,
        )

    def sell_actual_position(
        self, session: LimitUpSession, *, reason: str, owned_quantity: int | None = None
    ) -> ReconcileResult:
        """Submit one market exit for **this session's** remaining shares.

        ``get_position()`` reports the whole account. On a shared account that
        includes other strategies' holdings in the same ticker, and selling that
        number liquidates them too. ``sell_overnight_excess`` already caps with
        ``min(...)``; this path must do the same.

        Args:
            session: Session being exited.
            reason: Exit reason — also selects the order's strategy id.
            owned_quantity: Shares this session holds. ``None`` falls back to the
                session's filled buy legs, never to the account total.

        Returns:
            Broker-authoritative state after the submission.
        """
        if owned_quantity is None:
            # legs 가 아직 브로커 체결을 반영하지 않았을 수 있다. 소유 수량을 legs 에서
            # 유추할 거라면 먼저 정본을 당겨와야 한다.
            self.reconcile(session)
        position = self.broker.get_position(session.ticker)
        if position is None or position.quantity <= 0:
            return self.reconcile(session)
        owned = (
            owned_quantity
            if owned_quantity is not None
            else self.repository.remaining_quantity(session)
        )
        quantity = min(position.quantity, owned) if owned > 0 else 0
        if quantity <= 0:
            logger.warning(
                "청산 건너뜀 [%s] — 세션 소유분 0 (계좌 보유 %s 주는 다른 전략 것이다)",
                session.ticker, position.quantity,
            )
            return self.reconcile(session)
        self.repository.append_event(
            session,
            action="market_sell",
            state_version=session.state_version,
            payload={"quantity": quantity, "reason": reason},
        )
        self.repository.db.commit()
        order = Order(
            strategy_id=exit_strategy_id(reason),
            ticker=session.ticker,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=quantity,
            current_price=position.current_price or position.avg_price,
            decision_context={"limit_up_session_id": session.id, "reason": reason},
        )
        result = self.order_manager.submit_exit(order, exit_reason=exit_audit_code(reason))
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

    def _settle_exit_ledger(
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
            stamp = result.filled_at or result.submitted_at
            day = (
                stamp.astimezone(_KST).date().isoformat()
                if stamp.tzinfo is not None
                else stamp.date().isoformat()
            )
            quantity, amount = by_day.get(day, (0, 0.0))
            by_day[day] = (quantity + result.filled_quantity, amount)
            if result.avg_price <= 0:
                # 방금 보고된 체결은 평균가가 비어 올 수 있다. 0 으로 곱하면 진입금액
                # 전액이 손실로 기록돼 일일 중단선이 걸리고 오버나이트 예산이 0 이 된다.
                # 이 함수의 계약대로 "가격을 모르는 체결" 은 장부에 올리지 않는다.
                logger.warning(
                    "체결 평균가 미확정 [%s] id=%s qty=%s — 손익 반영을 미룬다",
                    session.ticker, order_id, result.filled_quantity,
                )
                continue
            by_day[day] = (
                by_day[day][0],
                by_day[day][1] + result.filled_quantity * result.avg_price,
            )
        if not by_day:
            return

        ledger = dict(session.realized_pnl_by_date or {})
        pnl_updated = False
        for day, (quantity, amount) in by_day.items():
            self.repository.record_exit_quantity(
                session, ref_date=dt.date.fromisoformat(day), quantity=quantity
            )
            if amount <= 0:
                continue
            ledger[day] = realized_pnl(
                buy_amount=buy_amount,
                buy_quantity=buy_quantity,
                sell_amount=amount,
                sell_quantity=quantity,
            )
            pnl_updated = True
        if not pnl_updated:
            return
        session.realized_pnl_by_date = ledger
        # 총합은 원장에서 파생된다 — 두 곳에서 따로 계산하면 조용히 어긋난다.
        session.realized_pnl = sum(ledger.values())

    def cancel_open_exits(self, session: LimitUpSession) -> "CancelExitsResult":
        """Cancel our still-open exits and report anything left standing.

        Must run before any follow-up sell: an unfilled exit still reserves
        shares, so selling the whole position on top of it would submit more
        shares than are held. When a cancel fails the caller must **not** send
        that follow-up — ``stranded`` is what tells it so.

        Args:
            session: Session whose exits should be pulled.

        Returns:
            Counts of orders cancelled and orders still open.
        """
        recorded = {
            raw_broker_order_id(item)
            for item in (session.exit_order_ids or "").split(",")
            if item
        }
        cancelled = 0
        stranded = 0
        for order in self.broker.get_open_orders():
            if order.ticker != session.ticker or order.side is not OrderSide.SELL:
                continue
            raw_id = raw_broker_order_id(order.order_id)
            owner = self._order_owner(order.order_id)
            if raw_id not in recorded and owner is not None and not owner.startswith(
                "limit_up_v1"
            ):
                # 공유 계좌다. 다른 전략이나 사람이 낸 매도를 취소하면 그쪽 로직이 깨진다.
                continue
            if raw_id not in recorded:
                # 브로커는 접수했는데 주문 ID 를 저장하기 전에 죽은 우리 매도이거나,
                # 출처를 모르는 주문이다. 살아 있는 채로 전량매도를 덧대면 보유보다
                # 많이 팔게 되므로 취소를 시도하되, 실패하면 새 매도를 내지 않는다.
                logger.warning(
                    "출처가 확인되지 않은 열린 매도 [%s] id=%s owner=%s",
                    session.ticker, order.order_id, owner,
                )
            try:
                if not self.order_manager.cancel(order.order_id):
                    stranded += 1
                    continue
            except BrokerAdapterError:
                logger.exception("매도 취소 실패 [%s] id=%s", session.ticker, order.order_id)
                stranded += 1
                continue
            self._record_exit_order(session, order.order_id)
            cancelled += 1
        self.repository.db.commit()
        return CancelExitsResult(cancelled=cancelled, stranded=stranded)

    def _order_owner(self, order_id: str) -> str | None:
        """Return the strategy that placed one order, per the audit log.

        The broker's open-order view carries no strategy id, so ``order_log`` is
        the only way to tell our sells from a shared account's other traffic.
        """
        # order_log 는 감사 ID(kis:...)로 저장되고 브로커의 열린 주문 목록은 원주문 ID 만
        # 준다. 정규화하지 않으면 KIS 에서 **항상 None** 이 되어 소유권 가드가 구조적으로
        # 죽는다 — 공유 계좌에서 남의 매도를 취소하게 된다.
        settings = get_settings()
        raw_id = raw_broker_order_id(order_id)
        # ODNO 재사용 때문에 접미사 매칭은 과거 주문을 집을 수 있다 — 계좌·날짜까지 맞춘다.
        same_day_audit_id = order_log_id(
            raw_id,
            broker=settings.maps_broker_mode,
            account_no=settings.kis_account_no,
            submitted_at=dt.datetime.now(_KST),
        )
        row = (
            self.repository.db.query(OrderLog.strategy_id)
            .filter(or_(
                OrderLog.order_id == order_id,
                OrderLog.order_id == same_day_audit_id,
            ))
            .first()
        )
        return str(row[0]) if row else None

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
            strategy_id=EXIT_STRATEGY_IDS["trim"],
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
        result = self.order_manager.submit_exit(
            order, exit_reason=exit_audit_code("overnight_cap")
        )
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
