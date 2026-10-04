"""주문 관리자 — 신호를 주문으로 변환하고 감사 로그를 기록한다."""

from __future__ import annotations

import logging
import uuid
import zoneinfo
from dataclasses import replace
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from maps.common.exceptions import (
    BrokerOrderUnknownError, BrokerOrderRejectedError, ExecutionBlockedError,
    ResearchStrategyError,
)
from maps.common.models import (
    AnalysisPick, CandidateSnapshot, ExecutionAccountState, LimitUpSession, OrderIntent, OrderLog,
)
from maps.common.settings import get_settings
from maps.execution.broker_adapter import (
    CancelResult, OrderResult, OrderSide, OrderStatus, PendingOrder, raw_broker_order_id,
)
from maps.execution.reconciliation import ACTIVE, TERMINAL, apply_result, audit_id, event, money, reconcile
from maps.execution.safety import (
    ExecutionContext, account_execution_lock, account_key,
    execution_environment, require_execution_enabled, utcnow,
)
from maps.ops.notifications import SlackNotifier

logger = logging.getLogger(__name__)

_KST = zoneinfo.ZoneInfo("Asia/Seoul")


def kst_day_bounds_utc(ref_date: date) -> tuple[datetime, datetime]:
    """KST 하루(ref_date 00:00~24:00)를 order_log.created_at 과 같은 UTC naive 구간으로 반환한다.

    created_at 은 UTC 로 저장되는데 08:55 KST 주문은 UTC 로 **전일 23:55** 다.
    naive 한 date.today() 로 경계를 잡으면 스케줄러가 낸 매수가 통째로 조회 범위
    밖으로 빠져 영영 pending 으로 남는다(2026-07-27 475150 사례). order_log 를
    거래일 기준으로 조회하는 코드는 전부 이 함수를 거쳐야 한다.
    """
    start = datetime.combine(ref_date, dt_time.min) - timedelta(hours=9)
    return start, start + timedelta(days=1)


def _order_log_mode() -> str:
    """order_log.mode 라벨 — 실제 돈이 오간 주문만 'live'.

    이전에는 maps_live_trading_enabled 만 봐서 KIS 모의투자(paper) 체결까지
    'live' 로 기록됐다. 계좌 종류(is_paper_account)를 함께 봐야 감사 로그가
    실거래와 모의를 구분한다.
    """
    settings = get_settings()
    if settings.maps_live_trading_enabled and not settings.is_paper_account:
        return "live"
    return "mock"


class OrderManager:
    """Account-scoped durable order submission. Broker timeout is not rejection."""

    def __init__(self, broker, risk, db, research_strategies=None, notifier=None, settings=None):
        self._broker, self._risk, self._db = broker, risk, db
        self._settings = settings or get_settings()
        self._notifier = notifier or SlackNotifier()
        self._research = set(research_strategies or ())

    def _session(self):
        return Session(bind=self._db.get_bind(), expire_on_commit=False)

    def submit(self, order, daily_pnl=None, *, risk_strategy_id=None, context=None):
        return self._submit(order, context=context, check_entry_risk=True,
            risk_strategy_id=risk_strategy_id)

    def submit_exit(self, order, *, exit_reason=None, context=None):
        if order.side != OrderSide.SELL:
            raise ValueError("submit_exit only accepts sell orders")
        return self._submit(order, context=context, check_entry_risk=False, exit_reason=exit_reason)

    def _context(self, order, context):
        if context is None:
            candidate = (order.decision_context or {}).get("candidate", {}).get("snapshot_id")
            if candidate:
                context = ExecutionContext(f"candidate:{candidate}:entry", source_id=candidate)
            elif self._settings.maps_broker_mode == "mock":
                context = ExecutionContext(f"mock:{datetime.now(_KST).date()}:{order.strategy_id}:{order.ticker}:{order.side.value}", source="mock")
            else:
                raise ExecutionBlockedError("execution_context_missing")
        if not context.event_key or len(context.event_key) > 128:
            raise ExecutionBlockedError("invalid_event_key")
        return context

    def _entry_policy(self, db, order, context):
        if context.source == "analysis_pick":
            from maps.ops.pick_freshness import is_pick_stale, pick_cutoff_date
            pick = db.get(AnalysisPick, context.source_id)
            if (not self._settings.maps_strategy_trade_enabled or pick is None
                    or pick.ticker != order.ticker or pick.state not in ("ARMED", "BOUGHT")
                    or pick.entries_cancelled or pick.exit_pending_reason
                    or is_pick_stale(pick, pick_cutoff_date(self._settings))):
                raise ExecutionBlockedError("analysis_pick_not_armed")
            return None
        if context.source == "limit_up":
            session = db.get(LimitUpSession, context.source_id)
            if (not self._settings.maps_limit_up_enabled or self._settings.maps_limit_up_mode != "automatic"
                    or session is None or session.ticker != order.ticker
                    or session.execution_mode != "automatic"):
                raise ExecutionBlockedError("limit_up_not_automatic")
            return None
        if context.source not in ("catalog", "mock"):
            raise ExecutionBlockedError("entry_policy_unknown")
        if context.source == "mock" and self._settings.maps_broker_mode != "mock":
            raise ExecutionBlockedError("mock_policy_on_live_broker")
        if context.source_id is not None:
            candidate = db.get(CandidateSnapshot, context.source_id)
            if candidate is None or candidate.strategy_id != order.strategy_id or candidate.ticker != order.ticker:
                raise ExecutionBlockedError("candidate_identity_mismatch")
        if not self._settings.is_paper_account:
            from maps.promotion.evidence import require_live_eligibility
            return require_live_eligibility(db, order.strategy_id, self._settings)
        return None

    def _submit(self, order, *, context, check_entry_risk, exit_reason=None, risk_strategy_id=None):
        require_execution_enabled(self._settings)
        if order.strategy_id in self._research:
            raise ResearchStrategyError(order.strategy_id, "research")
        if isinstance(order.quantity, bool) or not isinstance(order.quantity, int) or order.quantity <= 0:
            raise ExecutionBlockedError("invalid_order_quantity")
        context = self._context(order, context)
        key = account_key(self._settings)
        with account_execution_lock(key), self._session() as db:
            existing = db.query(OrderIntent).filter_by(account_key=key, event_key=context.event_key).first()
            if existing:
                request = existing.request
                if (existing.ticker != order.ticker or existing.side != order.side.value
                        or existing.strategy_id != order.strategy_id
                        or request.get("source") != context.source
                        or request.get("source_id") != context.source_id
                        or existing.quantity != order.quantity or request["order_type"] != order.order_type.value
                        or request.get("limit_price") != order.limit_price):
                    raise ExecutionBlockedError("idempotency_payload_conflict")
                if existing.broker_order_id:
                    log = db.query(OrderLog).filter_by(intent_id=existing.id).one()
                    return OrderResult(order_id=log.order_id, strategy_id=log.strategy_id,
                        ticker=log.ticker, side=OrderSide(log.side), status=OrderStatus(log.status),
                        filled_quantity=log.fill_qty, avg_price=log.fill_price or 0)
                if existing.status != "PREPARED":
                    raise BrokerOrderUnknownError(f"Order intent {existing.id}: {existing.status}; not resent")
            sync, snapshot, opens = reconcile(db, self._broker, self._settings)
            if order.side == OrderSide.BUY and not sync["complete"]:
                raise ExecutionBlockedError(";".join(sync["block_reasons"]))
            if snapshot is None:
                raise ExecutionBlockedError("position_snapshot_unavailable")
            validation_id = self._entry_policy(db, order, context) if order.side == OrderSide.BUY else None
            pending = db.query(OrderIntent).filter(OrderIntent.account_key == key, OrderIntent.status.in_(ACTIVE)).all()
            if any(i.ticker == order.ticker and i.id != getattr(existing, "id", None)
                    and i.status in ("SENDING", "UNKNOWN") for i in pending):
                raise ExecutionBlockedError("ticker_order_outcome_unknown")
            now = utcnow()
            expires = (existing.valid_until if existing else context.valid_until) or now + timedelta(
                seconds=self._settings.maps_execution_quote_max_age_seconds)
            if expires.tzinfo is not None:
                expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
            if expires <= now:
                raise ExecutionBlockedError("intent_expired")
            bound = money(order.limit_price or order.current_price or 0)
            if order.side == OrderSide.BUY:
                power = self._broker.get_buying_power(order)
                age = (utcnow() - power.as_of.astimezone(timezone.utc).replace(tzinfo=None)).total_seconds()
                if age < -5 or age > self._settings.maps_execution_quote_max_age_seconds:
                    raise ExecutionBlockedError("buying_power_stale")
                bound = money(power.price_bound)
                if order.limit_price is not None:
                    bound = max(bound, money(order.limit_price))
                if bound <= 0 or order.quantity > power.quantity:
                    raise ExecutionBlockedError("insufficient_buying_power")
                # Open broker reservations are already reflected in buying power.
                open_ids = {raw_broker_order_id(o.order_id) for o in opens}
                extra = sum((i.reserved_amount for i in pending if i.side == "buy"
                    and i.id != getattr(existing, "id", None)
                    and (not i.broker_order_id or raw_broker_order_id(i.broker_order_id) not in open_ids)), Decimal(0))
                if money(order.quantity) * bound > money(power.amount) - extra:
                    raise ExecutionBlockedError("cash_reserved")
                state = db.get(ExecutionAccountState, key)
                self._risk._db.expire_all()
                risk_order = replace(order, limit_price=float(bound), current_price=float(bound))
                risk_pending = list(opens)
                for pending_intent in pending:
                    if (pending_intent.side == "buy" and pending_intent.id != getattr(existing, "id", None)
                            and (not pending_intent.broker_order_id or raw_broker_order_id(pending_intent.broker_order_id) not in open_ids)):
                        risk_pending.append(PendingOrder(pending_intent.id, pending_intent.ticker, OrderSide.BUY,
                            pending_intent.quantity, pending_intent.quantity - pending_intent.filled_quantity,
                            float(pending_intent.request["price_bound"])))
                self._risk.check_before_order(risk_order, replace(snapshot.balance, cash=float(money(power.amount) - extra)),
                    float(state.daily_return), risk_strategy_id=risk_strategy_id,
                    positions=snapshot.positions, pending_orders=risk_pending)
            else:
                position = snapshot.positions.get(order.ticker)
                if position is None:
                    raise ExecutionBlockedError("position_missing")
                available = position.sellable_quantity if position.sellable_quantity is not None else position.quantity
                open_sell_ids = {raw_broker_order_id(o.order_id) for o in opens if o.side == OrderSide.SELL}
                reserved = sum(i.reserved_quantity for i in pending if i.side == "sell" and i.ticker == order.ticker
                    and i.id != getattr(existing, "id", None)
                    and (not i.broker_order_id or raw_broker_order_id(i.broker_order_id) not in open_sell_ids))
                if position.sellable_quantity is None:
                    reserved += sum(o.remaining_quantity for o in opens if o.side == OrderSide.SELL and o.ticker == order.ticker)
                if order.quantity > available - reserved:
                    raise ExecutionBlockedError("sell_quantity_reserved")
                if context.source != "mock":
                    owned = self._owned_quantity(db, key, order, context)
                    if order.quantity > owned:
                        raise ExecutionBlockedError("sell_source_ownership_unverified")
            require_execution_enabled(self._settings)
            from maps.promotion.evidence import strategy_fingerprint
            code_hash, params_hash = strategy_fingerprint(order.strategy_id)
            intent = existing or OrderIntent(id=str(uuid.uuid4()), account_key=key,
                environment=execution_environment(self._settings), event_key=context.event_key,
                strategy_id=order.strategy_id, ticker=order.ticker, side=order.side.value,
                status="PREPARED", quantity=order.quantity, filled_quantity=0,
                request={"order_type": order.order_type.value, "limit_price": order.limit_price,
                    "code_hash": code_hash, "params_hash": params_hash,
                    "current_price": order.current_price, "price_bound": str(bound),
                    "source": context.source, "source_id": context.source_id,
                    "exit_reason": exit_reason, "atr14": order.atr14, "decision_context": order.decision_context},
                validation_run_id=validation_id, valid_until=expires, created_at=now, updated_at=now,
                reserved_amount=money(order.quantity) * bound if order.side == OrderSide.BUY else 0,
                reserved_quantity=order.quantity if order.side == OrderSide.SELL else 0)
            if existing:
                intent.request = {**intent.request, "price_bound": str(bound)}
                intent.reserved_amount = money(order.quantity) * bound if order.side == OrderSide.BUY else 0
                intent.reserved_quantity = order.quantity if order.side == OrderSide.SELL else 0
                intent.version += 1
            db.add(intent)
            db.commit()
            intent.status = "SENDING"
            intent.updated_at = utcnow()
            db.commit()
            try:
                require_execution_enabled(self._settings)
                if utcnow() >= expires:
                    raise ExecutionBlockedError("intent_expired")
                snapshot_age = (datetime.now(timezone.utc) - snapshot.as_of.astimezone(timezone.utc)).total_seconds()
                if snapshot_age > self._settings.maps_execution_snapshot_max_age_seconds:
                    raise ExecutionBlockedError("account_snapshot_stale")
                result = self._broker.place_order(order)
            except BrokerOrderRejectedError:
                intent.status = "REJECTED"
                intent.reserved_amount = 0
                intent.reserved_quantity = 0
                intent.version += 1
                db.add(OrderLog(order_id=f"rejected:{intent.id}", intent_id=intent.id, account_key=key,
                    environment=intent.environment, strategy_id=order.strategy_id, ticker=order.ticker,
                    side=order.side.value, qty=order.quantity, fill_qty=0, status="rejected",
                    broker=self._settings.maps_broker_mode, mode="mock" if self._settings.is_paper_account else "live"))
                event(db, key, "order_rejected", {"intent_id": intent.id})
                db.commit()
                self._risk.on_order_failure(risk_strategy_id or order.strategy_id, reason="broker rejected",
                    order_id=f"rejected:{intent.id}")
                raise
            except ExecutionBlockedError:
                intent.status = "PREPARED"
                db.commit()
                raise
            except Exception as exc:
                # A generic transport error does not prove non-acceptance.
                intent.status = "UNKNOWN"
                intent.updated_at = utcnow()
                intent.version += 1
                event(db, key, "order_outcome_unknown", {"intent_id": intent.id, "exception": type(exc).__name__})
                db.commit()
                raise BrokerOrderUnknownError(f"Order intent {intent.id}: outcome unknown; not resent") from exc
            try:
                if not result.order_id:
                    raise ExecutionBlockedError("broker_order_id_missing")
                result = replace(result, order_id=audit_id(result, self._settings))
                intent.broker_order_id = result.order_id
                row = OrderLog(order_id=result.order_id, intent_id=intent.id, account_key=key,
                    environment=execution_environment(self._settings), strategy_id=order.strategy_id,
                    ticker=order.ticker, side=order.side.value, qty=order.quantity, fill_qty=0,
                    status="pending", order_price=order.limit_price or order.current_price,
                    mode="mock" if self._settings.is_paper_account else "live", broker=self._settings.maps_broker_mode,
                    exit_reason=exit_reason, atr14=order.atr14, decision_context=order.decision_context)
                row.code_hash, row.params_hash = intent.request["code_hash"], intent.request["params_hash"]
                db.add(row)
                db.flush()
                apply_result(db, row, result)
                db.commit()
            except Exception:
                db.rollback()
                # Durable SENDING is deliberately retained for restart recovery.
                raise
            if result.status == OrderStatus.REJECTED:
                self._risk.on_order_failure(risk_strategy_id or order.strategy_id, reason="broker rejected",
                    order_id=result.order_id)
            else:
                self._risk.on_order_success(risk_strategy_id or order.strategy_id)
            return result

    def _owned_quantity(self, db, key, order, context):
        if context.source == "catalog":
            entry = db.get(OrderLog, context.source_id)
            if (entry is None or entry.account_key != key or entry.ticker != order.ticker
                    or entry.strategy_id != order.strategy_id or entry.side != "buy"):
                return 0
            rows = db.query(OrderLog).filter_by(account_key=key, ticker=order.ticker,
                strategy_id=order.strategy_id).all()
            return max(sum((r.fill_qty or 0) * (1 if r.side == "buy" else -1) for r in rows), 0)
        rows = db.query(OrderLog, OrderIntent).join(OrderIntent, OrderLog.intent_id == OrderIntent.id).filter(
            OrderLog.account_key == key, OrderLog.ticker == order.ticker, OrderLog.fill_qty > 0).all()
        owned = {row.order_id: row for row, intent in rows if intent.request.get("source") == context.source
                 and intent.request.get("source_id") == context.source_id}
        # Explicit source links also support manually verified legacy audit rows.
        linked = set()
        if context.source == "analysis_pick":
            source = db.get(AnalysisPick, context.source_id)
            if source is None or source.ticker != order.ticker:
                return 0
            linked.update([source.entry_order_id, source.exit_order_id])
            linked.update(leg.order_id for leg in source.legs)
        elif context.source == "limit_up":
            from maps.common.models import LimitUpOrderLeg
            source = db.get(LimitUpSession, context.source_id)
            if source is None or source.ticker != order.ticker:
                return 0
            linked.update((source.exit_order_ids or "").split(","))
            linked.update(leg.broker_order_id for leg in db.query(LimitUpOrderLeg).filter_by(session_id=source.id))
        else:
            return 0
        for row in db.query(OrderLog).filter(OrderLog.account_key == key, OrderLog.order_id.in_(linked - {None, ""})):
            owned[row.order_id] = row
        return max(sum((r.fill_qty or 0) * (1 if r.side == "buy" else -1) for r in owned.values()), 0)

    def sync_broker_state(self):
        key = account_key(self._settings)
        with account_execution_lock(key), self._session() as db:
            summary, _, _ = reconcile(db, self._broker, self._settings)
            from maps.ops.safety_notifications import deliver_safety_events
            deliver_safety_events(db, self._notifier, key)
            return summary

    def cancel(self, order_id):
        require_execution_enabled(self._settings)
        key = account_key(self._settings)
        with account_execution_lock(key), self._session() as db:
            row = db.query(OrderLog).filter_by(order_id=order_id, account_key=key).first()
            if row is None:
                candidates = db.query(OrderLog).filter(OrderLog.account_key == key,
                    OrderLog.status.in_(("pending", "partially_filled"))).all()
                matches = [r for r in candidates if raw_broker_order_id(r.order_id) == order_id]
                row = matches[0] if len(matches) == 1 else None
            if row is None:
                raise ExecutionBlockedError("cancel_order_identity_unverified")
            order_id = row.order_id
            if row.status in TERMINAL:
                return CancelResult(False, row.status == "cancelled")
            if row.intent_id:
                intent = db.get(OrderIntent, row.intent_id)
                intent.cancel_requested = True
                intent.version += 1
            event(db, key, "cancel_requested", {"order_id": order_id})
            db.commit()
            accepted = bool(self._broker.cancel_order(order_id))
            reconcile(db, self._broker, self._settings)
            db.refresh(row)
            return CancelResult(accepted, row.status == "cancelled")

    def expire_pending_orders(self, *, before=None):
        # Elapsed time is not evidence of cancellation or rejection.
        return 0

    def eod_cleanup(self):
        require_execution_enabled(self._settings)
        key = account_key(self._settings)
        with account_execution_lock(key), self._session() as db:
            ids = [row.order_id for row in db.query(OrderLog).filter(
                OrderLog.account_key == key, OrderLog.intent_id.isnot(None),
                OrderLog.status.in_(("pending", "partially_filled")))]
            for order_id in ids:
                self.cancel(order_id)

    def block_strategy(self, strategy_id):
        self._research.add(strategy_id)

    def unblock_strategy(self, strategy_id):
        self._research.discard(strategy_id)
