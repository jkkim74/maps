"""Account-locked Fujimoto control, order ownership and restart recovery.

Controls are immutable evidence so stopping entries never rewrites funded configs
or drops a pending reservation. Only OrderManager can communicate an order.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import logging

from sqlalchemy.orm import Session

from maps.common.exceptions import DataQualityError, ExecutionBlockedError
from maps.common.models import (FujimotoConfig, FujimotoCycle, FujimotoEvidence, FujimotoFill,
                                FujimotoOrder, OrderIntent, OrderLog, ValidationRun)
from maps.common.settings import get_settings
from maps.execution.broker_adapter import Order, OrderSide, OrderType, raw_broker_order_id
from maps.execution.broker_adapter import PendingOrder
from maps.execution.safety import ExecutionContext, account_execution_lock, account_key, utcnow
from maps.execution.reconciliation import ACTIVE
from maps.fujimoto.domain import Mode, RuleEvidence, evaluate
from maps.fujimoto.repository import (FujimotoRepository, AccountLimits, FillEvent,
                                     TERMINAL, fingerprint, json_data, money, utc_naive)
from maps.fujimoto.replay import next_session

KST = ZoneInfo("Asia/Seoul")
STRATEGIES = {mode.strategy_id for mode in Mode}
logger = logging.getLogger(__name__)


def require_paper_eligibility(db: Session, strategy_id: str, settings) -> str:
    """Existing mock promotion and fresh measured gates, without live track-record circularity."""
    import math
    from maps.common.models import PromotionHistory
    from maps.common.constants import WEIGHT_PRESETS, TRADEABILITY_THRESHOLDS
    from maps.promotion.evidence import evidence_metrics, hard_gate_errors
    stage = db.query(PromotionHistory).filter_by(strategy_id=strategy_id, passed=True).order_by(
        PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc()).first()
    if stage is None or stage.to_stage not in {"mock_candidate", "live_candidate", "live"}:
        raise ExecutionBlockedError("strategy_not_mock_eligible")
    metrics = dict(evidence_metrics(db, strategy_id, settings))
    errors = [e for e in metrics.get("evidence_errors", []) if e != "insufficient_completed_trades_or_track_record"]
    metrics.update(evidence_errors=errors, evidence_valid=not errors)
    errors = hard_gate_errors(metrics)
    score = sum(float(metrics.get(k, 0)) * w for k, w in WEIGHT_PRESETS["balanced"].items()) * 100
    if errors or not math.isfinite(score) or score < TRADEABILITY_THRESHOLDS["mock_candidate"]:
        raise ExecutionBlockedError("paper_measured_gate:" + ";".join(errors or ["tradeability_below_60"]))
    return metrics["validation_run_id"]


def unbound_reservations(db: Session, key: str, *, exclude_event: str = "") -> list:
    """Expose committed pre-intent BUY reservations to every strategy's risk path."""
    repo, pending = FujimotoRepository(db), []
    for order in repo.orders(key):
        if (order.intent_id is None and order.status not in TERMINAL
                and order.decision["action"] == "buy" and exclude_event != f"fujimoto:{order.id}"):
            # An intent may have committed immediately before the binding crash.
            if db.query(OrderIntent.id).filter_by(account_key=key, event_key=f"fujimoto:{order.id}").first():
                continue
            cycle = db.get(FujimotoCycle, order.cycle_id)
            pending.append(PendingOrder(f"fujimoto:{order.id}", cycle.ticker, OrderSide.BUY,
                order.quantity, order.quantity - order.filled_quantity,
                float(order.limit_price) * (1 + order.fee_rate)))
    return pending


def reserved_exposure(db: Session, key: str, opens: list) -> tuple[Decimal, dict]:
    """Union broker opens, durable intents and unbound reservations exactly once."""
    rows = {}
    for order in opens:
        if order.side == OrderSide.BUY:
            rows[raw_broker_order_id(order.order_id)] = (order.ticker,
                money(order.order_price or 0) * order.remaining_quantity)
    for intent in db.query(OrderIntent).filter(OrderIntent.account_key == key,
            OrderIntent.status.in_(ACTIVE)):
        if intent.side == "buy":
            identity = raw_broker_order_id(intent.broker_order_id) if intent.broker_order_id else intent.id
            rows[identity] = (intent.ticker, max(rows.get(identity, (None, 0))[1], intent.reserved_amount))
    repo = FujimotoRepository(db)
    for order in repo.orders(key):
        cycle = db.get(FujimotoCycle, order.cycle_id)
        if order.decision["action"] == "buy" and order.status not in TERMINAL:
            intent = db.get(OrderIntent, order.intent_id) if order.intent_id else None
            identity = (raw_broker_order_id(intent.broker_order_id) if intent and intent.broker_order_id
                        else order.intent_id or f"fujimoto:{order.id}")
            amount = money(order.limit_price) * (order.quantity - order.filled_quantity) * (1 + money(order.fee_rate))
            rows[identity] = (cycle.ticker, max(rows.get(identity, (None, 0))[1], amount))
    by_ticker = {}
    for ticker, amount in rows.values():
        by_ticker[ticker] = by_ticker.get(ticker, Decimal(0)) + amount
    return sum(by_ticker.values(), Decimal(0)), by_ticker


class FujimotoService:
    """Durable service; caller session is never shared concurrently."""
    def __init__(self, db: Session, manager=None, *, settings=None):
        self.db, self.repo, self.manager = db, FujimotoRepository(db), manager
        self.settings = settings or get_settings()
        from maps.fujimoto.feed import FujimotoFeed
        self.feed = FujimotoFeed(db, account_key(self.settings))
        self._last_sync = None

    def owner(self, key: str, user_id: int | None) -> list:
        """Authorize the configured account owner, including read paths."""
        configs = self.repo.configurations(key)
        if any(c.owner_user_id != user_id for c in configs):
            raise ExecutionBlockedError("fujimoto_owner_mismatch")
        return configs

    def current_configs(self, key: str) -> dict:
        """Resolve prospective settings while cycles retain frozen budgets."""
        return {c.mode: c for c in self.repo.configurations(key)}

    def control(self, key: str) -> dict:
        """Latest append-only account switch; absence grants no permission."""
        rows = self.repo.evidence_as_of("control", "*", utcnow(), account_key=key)
        return ({**rows[-1].payload, "control_id": rows[-1].id} if rows else
            {"execution_mode": "observe", "entries_enabled": False, "sell_consent": False})

    def _control(self, key: str, payload: dict) -> dict:
        now = utcnow()
        row = self.repo.record_evidence("control", "*", now, now,
            {k: v for k, v in payload.items() if k != "control_id"}, account_key=key)
        self.db.commit()
        return {**row.payload, "control_id": row.id}

    def configure(self, key: str, owner: int | None, budget: float, *,
                  deposit: float = 0, with_orderbook: bool = True) -> dict:
        """Explicit dedication, split equally; changes stop new entries."""
        with account_execution_lock(key):
            self.owner(key, owner)
            if not isinstance(with_orderbook, bool):
                raise ExecutionBlockedError("invalid_orderbook_setting")
            for mode in Mode:
                self.repo.configure(key, owner, mode, budget / 2, deposit=deposit / 2,
                    settings={"execution_mode": "observe", "with_orderbook": with_orderbook})
            old = self.control(key)
            return self._control(key, {**old, "entries_enabled": False, "owner_user_id": owner})

    def stop(self, key: str, owner: int | None) -> dict:
        """Stop entries immediately, preserving pending orders and approved exits."""
        with account_execution_lock(key):
            self.owner(key, owner)
            return self._control(key, {**self.control(key), "entries_enabled": False,
                                      "owner_user_id": owner})

    def observe(self, key: str, owner: int | None) -> dict:
        """Observe new candidates while retaining previously approved exit control."""
        return self.stop(key, owner)

    def validation_gate(self, key: str, replay_id: int, *, recompute: bool = False,
                        binding: dict | None = None) -> dict:
        """Recompute measured combined evidence and require both existing promotions."""
        from maps.fujimoto.validation import validation
        from maps.fujimoto.replay import code_fingerprint
        from maps.promotion.evidence import require_live_eligibility
        configs = self.current_configs(key)
        if set(configs) != {"safe", "original"}:
            raise ExecutionBlockedError("dedicated_budget_required")
        book = configs["safe"].settings.get("with_orderbook", True)
        if not isinstance(book, bool) or configs["original"].settings.get("with_orderbook", True) != book:
            raise ExecutionBlockedError("configuration_variant_mismatch")
        evidence = self.db.query(FujimotoEvidence.id, FujimotoEvidence.kind,
            FujimotoEvidence.account_key, FujimotoEvidence.fingerprint).filter_by(id=replay_id).first()
        if evidence is None or evidence.kind != "replay" or evidence.account_key != key:
            raise ExecutionBlockedError("combined_account_evidence_missing")
        expected_execution = {"fee_rate": .00015, "tax_rate": .002, "slippage": .001,
            "budget": float(sum((c.budget for c in configs.values()), Decimal(0))),
            "account_ticker_limit": self.settings.max_single_exposure,
            "minimum_cash_fraction": max(.325, self.settings.maps_min_cash_ratio_weak,
                self.settings.maps_min_cash_ratio_mixed, self.settings.maps_min_cash_ratio_strong)}
        params = {"with_orderbook": book, "account_mdd_limit": self.settings.maps_account_mdd_limit}
        code_hash = code_fingerprint()
        if recompute:
            payload = self.db.get(FujimotoEvidence, replay_id).payload
            if any(payload["inputs"].get(k) != v for k, v in expected_execution.items()):
                raise ExecutionBlockedError("validation_execution_parameters_mismatch")
            if evidence.fingerprint != fingerprint(payload) or payload["report"]["baseline"]["code_hash"] != code_hash:
                raise ExecutionBlockedError("validation_data_or_code_changed")
            result = validation(self.repo, replay_id, **params)
            if result.status != "passed":
                raise ExecutionBlockedError("combined_validation_" + result.status + ":" + ";".join(result.reasons))
            combined_fingerprint = result.fingerprint
        else:
            control = binding if binding is not None else self.control(key)
            if (control.get("replay_id") != replay_id or control.get("execution_params") != params
                    or control.get("runtime_parameters") != expected_execution):
                raise ExecutionBlockedError("current_measured_activation_required")
            if control.get("code_hash") != code_hash or control.get("replay_fingerprint") != evidence.fingerprint:
                raise ExecutionBlockedError("validation_data_or_code_changed")
            combined_fingerprint = control.get("fingerprint")
        runs = {}
        for mode in Mode:
            run_id = (require_paper_eligibility(self.db, mode.strategy_id, self.settings)
                      if self.settings.is_paper_account else
                      require_live_eligibility(self.db, mode.strategy_id, self.settings))
            run = self.db.get(ValidationRun, run_id)
            expected = {"account_key": key, "fujimoto_replay_id": replay_id,
                "combined_fingerprint": combined_fingerprint, "combined_status": "passed",
                "with_orderbook": book, "account_mdd_limit": self.settings.maps_account_mdd_limit,
                "execution_params_hash": fingerprint(params),
                "variant": "with_orderbook" if book else "without_orderbook"}
            if run is None or any(run.manifest.get(k) != v for k, v in expected.items()):
                raise ExecutionBlockedError("combined_promotion_binding_mismatch")
            runs[mode.value] = run_id
        return {"replay_id": replay_id, "fingerprint": combined_fingerprint, "execution_params": params,
                "replay_fingerprint": evidence.fingerprint, "code_hash": code_hash,
                "runtime_parameters": expected_execution,
                "validation_runs": runs, "config_ids": {m: c.id for m, c in configs.items()}}

    def activate(self, key: str, owner: int | None, *, execution_mode: str,
                 replay_id: int, sell_consent: bool) -> dict:
        """Explicit approval never bypasses account, measured or promotion gates."""
        self.owner(key, owner)
        if execution_mode == "paper" and not self.settings.is_paper_account:
            raise ExecutionBlockedError("paper_account_required")
        from maps.execution.safety import require_execution_enabled
        require_execution_enabled(self.settings)
        # Research can be expensive; never hold the shared account lock/pump.
        gate = self.validation_gate(key, replay_id, recompute=True)
        with account_execution_lock(key):
            self.owner(key, owner)
            require_execution_enabled(self.settings)
            if key != account_key(self.settings):
                raise ExecutionBlockedError("account_identity_mismatch")
            if execution_mode == "paper" and not self.settings.is_paper_account:
                raise ExecutionBlockedError("paper_account_required")
            if execution_mode == "live" and self.settings.is_paper_account:
                raise ExecutionBlockedError("live_account_required")
            if execution_mode not in {"paper", "live"} or sell_consent is not True:
                raise ExecutionBlockedError("explicit_strategy_sell_consent_required")
            if not self.settings.maps_fujimoto_enabled:
                raise ExecutionBlockedError("fujimoto_disabled")
            if gate["config_ids"] != {m: c.id for m, c in self.current_configs(key).items()}:
                raise ExecutionBlockedError("configuration_changed_during_validation")
            if self.validation_gate(key, replay_id, binding=gate) != gate:
                raise ExecutionBlockedError("validation_changed_during_activation")
            return self._control(key, {**gate, "execution_mode": execution_mode,
                "entries_enabled": True, "sell_consent": True, "owner_user_id": owner,
                "consent_scope": "new_fujimoto_acquisitions_only"})

    def expire_empty_cycles(self, key: str, today: date) -> None:
        """Release unfunded watch slots without expiring pending or UNKNOWN orders."""
        for cycle in self.repo.cycles(key):
            state = self.repo.state(cycle.id)
            if not state.quantity and not state.pending_order and not state.buy_stage:
                if (utc_naive(cycle.created_at) + timedelta(hours=9)).date() < today:
                    now = utcnow()
                    rows = self.repo.evidence_as_of("cycle_expired", cycle.ticker, now, account_key=key)
                    if not any(r.payload.get("cycle_id") == cycle.id for r in rows):
                        self.repo.record_evidence("cycle_expired", cycle.ticker, now, now,
                            {"cycle_id": cycle.id}, account_key=key)
        self.db.commit()

    def unresolved_costs(self, key: str) -> list[int]:
        """Unknown cost is distinct from an actual confirmed zero fee/tax."""
        result = []
        for order in self.repo.orders(key):
            if not order.filled_quantity:
                continue
            rows = self.repo.evidence_as_of("order_cost", str(order.id), utcnow(), account_key=key)
            if not rows or rows[-1].payload.get("complete") is not True:
                result.append(order.id)
        return result

    def subscriptions(self) -> tuple[list, list, list]:
        """Held and unresolved tickers precede the latest actual candidate ranking."""
        key = account_key(self.settings)
        cycles = self.repo.cycles(key)
        held = [c.ticker for c in cycles if self.repo.state(c.id).quantity]
        pending = [c.ticker for c in cycles if self.repo.state(c.id).pending_order]
        from maps.common.models import AccountObservation
        snapshot = self.db.query(AccountObservation).filter_by(account_key=key).order_by(AccountObservation.id.desc()).first()
        if snapshot:
            held.extend(t for t, p in snapshot.evidence.get("positions", {}).items() if p.get("qty", 0) > 0)
        pending.extend(i.ticker for i in self.db.query(OrderIntent).filter(
            OrderIntent.account_key == key, OrderIntent.status.in_(ACTIVE)))
        screens = self.repo.evidence_as_of("screen", "*", utcnow())
        candidates = screens[-1].payload.get("ranked", []) if screens else []
        return held, pending, candidates

    def tick(self, *, now: datetime) -> None:
        """Reconcile approved activity and cancel expired entries without blind expiry."""
        key = account_key(self.settings)
        with account_execution_lock(key):
            control = self.control(key)
            if self.manager is None or control["execution_mode"] == "observe":
                return
            if self._last_sync is not None and (utc_naive(now) - self._last_sync).total_seconds() < 15:
                return
            self._last_sync = utc_naive(now)
            self.manager.sync_broker_state()
            self.db.expire_all()
            self.recover_links(key)
            for order in self.repo.orders(key):
                if order.status in TERMINAL or order.decision["action"] != "buy":
                    continue
                expired = order.signal_date and next_session(order.signal_date, tuple(self.settings.krx_closed_dates)) < now.astimezone(KST).date()
                if (expired or not control.get("entries_enabled")) and order.broker_order_id and order.status not in {"UNKNOWN", "CANCEL_REQUESTED"}:
                    order.status = "CANCEL_REQUESTED"
                    self.db.commit()
                    self.manager.cancel(order.broker_order_id)
                    self.db.expire_all()
            self.expire_empty_cycles(key, now.astimezone(KST).date())

    def on_quote(self, quote, *, now: datetime | None = None) -> None:
        """Persist shared quote then evaluate confirmed owned cycles and actual candidates."""
        now = now or datetime.now(timezone.utc)
        key = account_key(self.settings)
        with account_execution_lock(key):
            self.db.expire_all()
            self.feed.on_quote(quote, now=now)
            self.db.commit()
            control = self.control(key)
            if control["execution_mode"] == "observe" or self.manager is None:
                return
            wall = now.astimezone(KST)
            if not time(9) <= wall.time().replace(tzinfo=None) < time(15, 20):
                return
            candidates = self.repo.evidence_as_of("candidate", quote.ticker, now)
            source = candidates[-1] if candidates else None
            if source:
                data = dict(source.payload["rule"])
                data["as_of"] = date.fromisoformat(data["as_of"])
                data["blocking_reasons"] = tuple(data.get("blocking_reasons", ()))
                evidence = RuleEvidence(**data)
            else:
                evidence = RuleEvidence(wall.date(), None, blocking_reasons=("candidate_missing",))
            from maps.fujimoto.sources import current_financial_status
            evidence = replace(evidence, financial_status=current_financial_status(self.db, quote.ticker, wall.date()))
            current = self.current_configs(key)
            cycles = [c for c in self.repo.cycles(key) if c.ticker == quote.ticker
                      and (self.repo.state(c.id).quantity or self.repo.state(c.id).pending_order
                           or not self.repo.state(c.id).buy_stage)]
            expired_ids = {r.payload.get("cycle_id") for r in self.db.query(FujimotoEvidence).filter_by(
                kind="cycle_expired", account_key=key)}
            cycles = [c for c in cycles if c.id not in expired_ids]
            if control.get("entries_enabled") and source:
                for mode in Mode:
                    if mode.value not in current or any(c.mode == mode.value for c in cycles):
                        continue
                    from maps.fujimoto.domain import CycleState
                    if evaluate(mode, evidence, CycleState()).action == "buy" and next_session(evidence.as_of,
                            tuple(self.settings.krx_closed_dates)) == wall.date():
                        try:
                            cycles.append(self.repo.create_cycle(current[mode.value].id, quote.ticker))
                        except DataQualityError as exc:
                            self._blocked(key, quote.ticker, str(exc), now)
                self.db.commit()
            cost_gaps = self.unresolved_costs(key)
            for cycle in cycles:
                state = self.repo.state(cycle.id)
                complete = not any(o.id in cost_gaps for o in self.repo.orders(key) if o.cycle_id == cycle.id)
                bid, profit = self.feed.on_quote(quote, now=now, cycle_id=cycle.id,
                    cost_basis=float(cycle.cost_basis), quantity=state.quantity,
                    costs_complete=complete, persist=False)
                live = replace(evidence, live_price=bid, orderbook_take_profit=bool(profit and
                    current.get(cycle.mode) and current[cycle.mode].settings.get("with_orderbook", True)))
                if bid is None:
                    continue
                decision = evaluate(Mode(cycle.mode), live, state)
                if state.pending_order:
                    emergency = evaluate(Mode(cycle.mode), live, replace(state, pending_order=False))
                    if emergency.action == "sell" and emergency.timing in {"intraday", "first_available"}:
                        for order in self.repo.orders(key):
                            if order.cycle_id == cycle.id and order.status not in TERMINAL and order.decision["action"] == "buy":
                                if order.broker_order_id and order.status not in {"UNKNOWN", "CANCEL_REQUESTED"}:
                                    order.status = "CANCEL_REQUESTED"
                                    self.db.commit()
                                    self.manager.cancel(order.broker_order_id)
                    continue
                if decision.action == "hold":
                    if source:
                        self.repo.record_decision(cycle.id, source.id, decision)
                    continue
                if decision.timing == "next_session" and next_session(evidence.as_of,
                        tuple(self.settings.krx_closed_dates)) != wall.date():
                    continue
                if decision.action == "buy" and (not control.get("entries_enabled") or cost_gaps):
                    continue
                try:
                    self._submit_decision(cycle, live, decision, bid, source, now)
                except (ExecutionBlockedError, DataQualityError) as exc:
                    self.db.rollback()
                    self._blocked(key, cycle.ticker, str(exc), now)
            self.db.commit()

    def _blocked(self, key: str, ticker: str, reason: str, now: datetime) -> None:
        """Expose actionable execution blocks without modifying order truth."""
        previous = self.repo.evidence_as_of("block", ticker, now, account_key=key)
        if not previous or previous[-1].payload.get("reason") != reason:
            self.repo.record_evidence("block", ticker, now, now, {"reason": reason}, account_key=key)
            self.db.commit()

    def _submit_decision(self, cycle, evidence, decision, bid, source, now) -> None:
        """Commit reservation under account lock before OrderManager's separate session."""
        key = cycle.account_key
        stop = None
        if decision.action == "buy":
            self.validation_gate(key, self.control(key)["replay_id"])
            snapshot = self.manager._broker.get_execution_snapshot()
            age = (datetime.now(timezone.utc) - snapshot.as_of.astimezone(timezone.utc)).total_seconds()
            if not 0 <= age <= self.settings.maps_execution_snapshot_max_age_seconds:
                raise ExecutionBlockedError("account_snapshot_stale")
            opens = self.manager._broker.get_open_orders()
            reserved, by_ticker = reserved_exposure(self.db, key, opens)
            marks = {t: p.current_price for t, p in snapshot.positions.items() if p.current_price is not None}
            position = snapshot.positions.get(cycle.ticker)
            limits = AccountLimits(snapshot.balance.total_value, snapshot.balance.cash,
                (position.market_value if position else 0) + float(by_ticker.get(cycle.ticker, 0)),
                float(reserved), self.settings.max_single_exposure,
                max(.325, self.settings.maps_min_cash_ratio_weak,
                    self.settings.maps_min_cash_ratio_mixed, self.settings.maps_min_cash_ratio_strong))
            plan = self.repo.plan_buy(cycle.id, decision, limits, marks, atr14=evidence.atr14)
            if not plan.quantity:
                raise ExecutionBlockedError(plan.reason)
            quantity, price, stop = plan.quantity, plan.limit_price, plan.stop_price
        else:
            from maps.market.trading_rules import round_down_krx_price
            quantity, price = decision.sell_quantity, round_down_krx_price(bid)
        actual = self.repo.record_evidence("execution_decision", cycle.ticker, now, now,
            {"rule": evidence, "decision": decision, "candidate_id": source.id if source else None}, account_key=key)
        reservation = self.repo.reserve_order(cycle.id, decision, actual.id, quantity, price,
            signal_date=evidence.as_of, stop_price=stop)
        self.db.commit()
        order = Order(Mode(cycle.mode).strategy_id, cycle.ticker,
            OrderSide.BUY if decision.action == "buy" else OrderSide.SELL,
            OrderType.LIMIT, quantity, limit_price=float(price), current_price=bid, atr14=evidence.atr14)
        context = ExecutionContext(f"fujimoto:{reservation.id}", source="fujimoto", source_id=cycle.id,
            valid_until=utc_naive(now) + timedelta(seconds=3))
        try:
            if decision.action == "buy":
                self.manager.submit(order, context=context)
            else:
                self.manager.submit_exit(order, context=context, exit_reason=decision.reason)
        finally:
            self.db.expire_all()
            self.recover_links(key)

    def recover_links(self, key: str) -> None:
        """Recover reservation/intent crash boundaries without any broker resend."""
        for reservation in self.repo.orders(key):
            intent = self.db.query(OrderIntent).filter_by(account_key=key,
                event_key=f"fujimoto:{reservation.id}").one_or_none()
            if intent is not None:
                self.repo.bind_intent(reservation.id, intent.id, intent.broker_order_id)
                if reservation.status not in TERMINAL and intent.status in {"SENDING", "UNKNOWN"}:
                    reservation.status = "UNKNOWN"
                elif reservation.status not in TERMINAL and intent.cancel_requested:
                    reservation.status = "CANCEL_REQUESTED"
                elif reservation.status not in TERMINAL and intent.status == "REJECTED" and not intent.filled_quantity:
                    self.repo.apply_fill(FillEvent(reservation.id, key, intent.id, 0, 0, 0, 0,
                                                  "REJECTED", datetime.now(KST).date()))
            elif reservation.status == "RESERVED":
                # Under account lock: no intent means no send ever began. Do not
                # replay an old decision after restart; release only this boundary.
                self.repo.apply_fill(FillEvent(reservation.id, key, None, 0, 0, 0, 0,
                                              "EXPIRED", datetime.now(KST).date()))
        self.db.commit()

    def settle_costs(self, key: str, owner: int | None, order_id: int, *, gross: float,
                     fees: float, tax: float, evidence: dict) -> None:
        """Audit exact per-order settlement; late ambiguous reconstruction stays blocked."""
        with account_execution_lock(key):
            self.owner(key, owner)
            order = self.db.get(FujimotoOrder, order_id)
            if order is None or order.account_key != key or order.status not in TERMINAL:
                raise ExecutionBlockedError("cost_order_identity_or_terminal_required")
            if (evidence.get("broker_order_id") != order.broker_order_id
                    or not evidence.get("source_url") or not evidence.get("document_hash")):
                raise ExecutionBlockedError("exact_order_cost_provenance_required")
            event = FillEvent(order.id, key, order.intent_id, order.filled_quantity,
                gross, fees, tax, order.status, datetime.now(KST).date())
            try:
                self.repo.apply_fill(event)
            except DataQualityError as exc:
                if str(exc) != "late_cost_correction_requires_reconstruction":
                    raise
                # Strict audited settlement: unchanged actual gross/quantity, no
                # later BUY, only increasing exact costs. SELL-only history gives
                # an unambiguous remaining/realized allocation of this fee delta.
                later = [o for o in self.repo.orders(key) if o.cycle_id == order.cycle_id and o.id > order.id]
                if (money(gross) != order.gross or money(fees) < order.fees or money(tax) < order.tax
                        or any(o.decision["action"] == "buy" for o in later)):
                    raise
                history = self.db.query(FujimotoFill).filter_by(order_id=order.id).order_by(FujimotoFill.id).all()
                if not history:
                    raise
                quantity_after_buy = max(row.resulting_state["quantity"] for row in history)
                cycle = self.db.get(FujimotoCycle, order.cycle_id)
                quantity_now = self.repo.state(cycle.id).quantity
                if not 0 <= quantity_now <= quantity_after_buy or quantity_after_buy == 0:
                    raise
                delta = money(fees) + money(tax) - order.fees - order.tax
                remaining = delta * quantity_now / quantity_after_buy
                cycle.cost_basis += remaining
                cycle.realized_pnl -= delta - remaining
                order.fees, order.tax = money(fees), money(tax)
                self.db.add(FujimotoFill(order_id=order.id, fingerprint=fingerprint(event),
                    payload={**json_data(event), "settlement": "audited_sell_only_cost_correction",
                             "quantity_after_buy": quantity_after_buy}, resulting_state=cycle.state))
            now = utcnow()
            self.repo.record_evidence("order_cost", str(order.id), now, now,
                {"complete": True, "gross": gross, "fees": fees, "tax": tax,
                 "source": "operator_exact_order_evidence", "evidence": evidence, "owner_user_id": owner},
                account_key=key)
            self.db.commit()


def apply_broker_result(db: Session, intent: OrderIntent, log: OrderLog, result) -> None:
    """Shared reconciliation hook: exact intent linkage, quantity truth, explicit cost gaps."""
    repo = FujimotoRepository(db)
    if not intent.event_key.startswith("fujimoto:") or not intent.event_key.split(":")[-1].isdigit():
        raise ExecutionBlockedError("fujimoto_orphan_intent")
    reservation = db.query(FujimotoOrder).filter_by(account_key=intent.account_key,
        cycle_id=intent.request.get("source_id")).filter(
            FujimotoOrder.id == int(intent.event_key.split(":")[-1])).one_or_none()
    if reservation is None or intent.event_key != f"fujimoto:{reservation.id}":
        raise ExecutionBlockedError("fujimoto_orphan_intent")
    repo.bind_intent(reservation.id, intent.id, intent.broker_order_id)
    complete = result.costs_complete is True and result.tax is not None and result.cumulative_gross is not None
    gross = result.cumulative_gross if result.cumulative_gross is not None else result.filled_quantity * result.avg_price
    # Provisional average-derived gross is labelled, never claimed as exact fees.
    # Later incomplete broker refreshes cannot erase an audited settlement.
    known = repo.evidence_as_of("order_cost", str(reservation.id), utcnow(), account_key=intent.account_key)
    preserve_settlement = (not complete and known and known[-1].payload.get("complete")
                           and reservation.filled_quantity == result.filled_quantity)
    if preserve_settlement:
        complete, gross = True, reservation.gross
        fees, tax = reservation.fees, reservation.tax
    else:
        fees, tax = (result.commission, result.tax) if complete else (reservation.fees, reservation.tax)
    status = {"ACKNOWLEDGED": "SUBMITTED", "PARTIALLY_FILLED": "PARTIAL"}.get(intent.status, intent.status)
    if intent.cancel_requested and status not in TERMINAL:
        status = "CANCEL_REQUESTED"
    stamp = result.filled_at or result.submitted_at
    fill_day = stamp.replace(tzinfo=KST).date() if stamp.tzinfo is None else stamp.astimezone(KST).date()
    repo.apply_fill(FillEvent(reservation.id, intent.account_key, intent.id,
        result.filled_quantity, gross, fees, tax, status, fill_day))
    payload = {"complete": complete, "gross": str(gross), "fees": str(fees) if complete else None,
        "tax": str(tax) if complete else None, "source": "broker_order_result",
        "gross_source": "reported_total" if result.cumulative_gross is not None else "reported_average_provisional",
        "broker_order_id": intent.broker_order_id}
    if preserve_settlement:
        payload = known[-1].payload
    if not known or known[-1].payload != payload:
        now = utcnow()
        repo.record_evidence("order_cost", str(reservation.id), now, now, payload, account_key=intent.account_key)


def validate_source(db: Session, order: Order, context: ExecutionContext, settings) -> int:
    """Validate reservation identity and current permission for both entry and exit."""
    service = FujimotoService(db, settings=settings)
    key = account_key(settings)
    cycle = db.get(FujimotoCycle, context.source_id)
    if (cycle is None or cycle.account_key != key or cycle.ticker != order.ticker
            or Mode(cycle.mode).strategy_id != order.strategy_id):
        raise ExecutionBlockedError("fujimoto_cycle_identity_mismatch")
    rows = [r for r in service.repo.orders(key) if r.cycle_id == cycle.id
            and context.event_key == f"fujimoto:{r.id}"]
    if len(rows) != 1:
        raise ExecutionBlockedError("fujimoto_reservation_required")
    reservation = rows[0]
    if (reservation.quantity != order.quantity or reservation.decision["action"] != order.side.value
            or order.order_type != OrderType.LIMIT or money(order.limit_price) != reservation.limit_price
            or reservation.status in TERMINAL):
        raise ExecutionBlockedError("fujimoto_reservation_identity_mismatch")
    control = service.control(key)
    if (not settings.maps_fujimoto_enabled or control.get("execution_mode") not in {"paper", "live"}
            or control.get("sell_consent") is not True):
        raise ExecutionBlockedError("fujimoto_not_approved")
    if (control["execution_mode"] == "paper") != settings.is_paper_account:
        raise ExecutionBlockedError("fujimoto_environment_mismatch")
    if order.side == OrderSide.BUY:
        if not control.get("entries_enabled") or service.unresolved_costs(key):
            raise ExecutionBlockedError("fujimoto_entries_stopped_or_costs_unresolved")
        gate = service.validation_gate(key, control["replay_id"])
        if gate["config_ids"] != control.get("config_ids") or gate["fingerprint"] != control.get("fingerprint"):
            raise ExecutionBlockedError("fujimoto_activation_stale")
    else:
        acquired = db.query(OrderIntent).filter_by(account_key=key, strategy_id=order.strategy_id,
            ticker=order.ticker, side="buy").all()
        approved = []
        for intent in acquired:
            approval = db.get(FujimotoEvidence, intent.request.get("fujimoto_approval_id")) if intent.request.get("fujimoto_approval_id") else None
            if (intent.request.get("source") == "fujimoto" and intent.request.get("source_id") == cycle.id
                    and intent.filled_quantity and approval is not None and approval.kind == "control"
                    and approval.account_key == key and approval.payload.get("sell_consent") is True
                    and approval.payload.get("consent_scope") == "new_fujimoto_acquisitions_only"):
                approved.append(intent)
        if not approved:
            raise ExecutionBlockedError("fujimoto_acquisition_consent_missing")
        if order.quantity > service.repo.state(cycle.id).quantity:
            raise ExecutionBlockedError("sell_source_ownership_unverified")
    return control["control_id"]
