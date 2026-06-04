"""주문 관리자 — 신호를 주문으로 변환하고 감사 로그를 기록한다."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as dt_time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError, ResearchStrategyError
from maps.common.models import OrderLog
from maps.common.settings import get_settings
from maps.execution.broker_adapter import BrokerAdapter, Order, OrderResult, OrderSide, OrderStatus
from maps.ops.notifications import SlackNotifier
from maps.risk.manager import RiskManager

logger = logging.getLogger(__name__)

# Research/Alert_only 단계에서 자동 주문이 금지된 전략 단계
_BLOCKED_STAGES: frozenset[str] = frozenset(["research", "alert_only"])


class OrderManager:
    """전략 신호를 브로커 주문으로 변환하고 order_log에 기록한다.

    주문 흐름:
      1. Research 전략 차단 (ResearchStrategyError)
      2. RiskManager.check_before_order (Kill Switch, 일일 손실, 노출 한도)
      3. broker.place_order
      4. 성공: risk.on_order_success
         실패: risk.on_order_failure (5회 시 Kill Switch 자동 발동)
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        risk: RiskManager,
        db: Session,
        research_strategies: set[str] | None = None,
        notifier: SlackNotifier | None = None,
    ) -> None:
        """
        Args:
            broker: 브로커 어댑터 (MockBroker 또는 실 브로커).
            risk: 리스크 관리자.
            db: DB 세션 (order_log 기록용).
            research_strategies: 자동 주문이 금지된 strategy_id 집합.
                None 이면 모든 전략 허용.
        """
        self._broker = broker
        self._risk = risk
        self._db = db
        self._notifier = notifier or SlackNotifier()
        self._research: set[str] = research_strategies or set()

    def submit(self, order: Order, daily_pnl: float = 0.0) -> OrderResult:
        """주문을 제출한다.

        Args:
            order: 주문 요청.
            daily_pnl: 당일 손익률 (RiskManager 일일 손실 체크용).

        Returns:
            OrderResult.

        Raises:
            ResearchStrategyError: Research 단계 전략의 자동 주문 시도.
            KillSwitchError: Kill Switch 발동 또는 일일 손실 한도 초과.
            ExposureCapError: 단일 종목 노출 한도 초과.
        """
        return self._submit(order, daily_pnl=daily_pnl, check_entry_risk=True)

    def submit_exit(self, order: Order) -> OrderResult:
        """Submit a strategy exit without applying new-entry exposure checks.

        This is only for a normal strategy exit or stop-loss. Kill Switch
        liquidation remains a separate, explicitly approved workflow.
        """
        if order.side != OrderSide.SELL:
            raise ValueError("submit_exit only accepts sell orders")
        return self._submit(order, daily_pnl=0.0, check_entry_risk=False)

    def _submit(
        self,
        order: Order,
        *,
        daily_pnl: float,
        check_entry_risk: bool,
    ) -> OrderResult:
        if order.strategy_id in self._research:
            raise ResearchStrategyError(order.strategy_id, "research")
        self._raise_if_duplicate_active_order(order)

        if check_entry_risk:
            account = self._broker.get_account_balance()
            self._risk.check_before_order(order, account, daily_pnl)

        try:
            result = self._place_with_retry(order)
            if result.status == OrderStatus.REJECTED:
                self._risk.on_order_failure(order.strategy_id)
            else:
                self._risk.on_order_success(order.strategy_id)
            self._log_order(order, result)
            return result
        except Exception:
            self._risk.on_order_failure(order.strategy_id)
            raise

    def cancel(self, order_id: str) -> bool:
        """주문을 취소한다."""
        return self._broker.cancel_order(order_id)

    def eod_cleanup(self) -> None:
        """장 마감 정리 (중복 탐지 초기화, 미체결 취소)."""
        if hasattr(self._broker, "eod_cleanup"):
            self._broker.eod_cleanup()  # type: ignore[union-attr]

    def sync_broker_state(self) -> dict[str, float | int]:
        """Sync same-day broker fills/open orders into order_log."""
        today_start = datetime.combine(date.today(), dt_time.min)
        expired = self.expire_pending_orders(before=today_start)
        balance = self._broker.get_account_balance()
        sync_errors = 0
        try:
            open_orders = self._broker.get_open_orders()
        except NotImplementedError:
            open_orders = []
        except BrokerAdapterError as exc:
            sync_errors += 1
            open_orders = []
            logger.warning("Broker open-order sync unavailable: %s", exc)
        updated = 0
        try:
            broker_results = self._broker.get_daily_order_results()
        except NotImplementedError:
            broker_results = []
        except BrokerAdapterError as exc:
            sync_errors += 1
            broker_results = []
            logger.warning("Broker daily fill sync unavailable: %s", exc)

        for result in broker_results:
            if not result.order_id:
                continue
            row = (
                self._db.query(OrderLog)
                .filter(OrderLog.order_id == result.order_id)
                .first()
            )
            if row is None:
                # MAPS 외부(MTS 등)에서 제출된 주문 — DB에 삽입하여 화면에 표시
                self._db.add(
                    OrderLog(
                        order_id=result.order_id,
                        strategy_id="external_mts",
                        ticker=result.ticker,
                        side=result.side.value,
                        qty=result.filled_quantity or 0,
                        order_price=None,
                        fill_price=result.avg_price if result.avg_price else None,
                        fill_qty=result.filled_quantity,
                        status=result.status.value,
                        broker=get_settings().maps_broker_mode,
                        mode="live" if get_settings().maps_live_trading_enabled else "mock",
                    )
                )
                updated += 1
                continue
            changed = False
            if row.status != result.status.value:
                row.status = result.status.value
                changed = True
            if result.filled_quantity and row.fill_qty != result.filled_quantity:
                row.fill_qty = result.filled_quantity
                changed = True
            if result.avg_price and row.fill_price != result.avg_price:
                row.fill_price = result.avg_price
                changed = True
            if changed:
                updated += 1
        updated += self._reconcile_same_day_buys({result.order_id for result in broker_results})
        self._db.commit()
        return {
            "cash": balance.cash,
            "positions_value": balance.positions_value,
            "total_assets": balance.total_value,
            "open_orders": len(open_orders),
            "updated_orders": updated,
            "expired_orders": expired,
            "sync_errors": sync_errors,
        }

    def _reconcile_same_day_buys(self, returned_order_ids: set[str]) -> int:
        """Use explicit same-day buy quantities when a broker omits order rows."""
        try:
            buys = self._broker.get_same_day_buys()
        except (BrokerAdapterError, NotImplementedError):
            return 0
        if not isinstance(buys, dict):
            return 0

        today_start = datetime.combine(date.today(), dt_time.min)
        rows = (
            self._db.query(OrderLog)
            .filter(OrderLog.created_at >= today_start)
            .filter(OrderLog.side == OrderSide.BUY.value)
            .filter(OrderLog.status.in_([
                OrderStatus.PENDING.value,
                OrderStatus.PARTIALLY_FILLED.value,
            ]))
            .all()
        )
        rows_by_ticker: dict[str, list[OrderLog]] = {}
        for row in rows:
            if row.order_id not in returned_order_ids:
                rows_by_ticker.setdefault(row.ticker, []).append(row)

        updated = 0
        for ticker, ticker_rows in rows_by_ticker.items():
            evidence = buys.get(ticker)
            if evidence is None or evidence.quantity <= 0 or len(ticker_rows) != 1:
                continue
            row = ticker_rows[0]
            fill_qty = min(evidence.quantity, row.qty)
            status = (
                OrderStatus.FILLED.value
                if fill_qty >= row.qty
                else OrderStatus.PARTIALLY_FILLED.value
            )
            if row.status != status or row.fill_qty != fill_qty:
                row.status = status
                row.fill_qty = fill_qty
                if evidence.avg_price:
                    row.fill_price = evidence.avg_price
                updated += 1
        return updated

    def expire_pending_orders(self, *, before: datetime | None = None) -> int:
        """Expire unresolved orders during scheduler-driven cleanup."""
        query = self._db.query(OrderLog).filter(OrderLog.status.in_([
            OrderStatus.PENDING.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ]))
        if before is not None:
            query = query.filter(OrderLog.created_at < before)
        count = query.update({"status": "expired"}, synchronize_session=False)
        self._db.commit()
        return count

    def block_strategy(self, strategy_id: str) -> None:
        """전략을 Research 단계로 차단한다."""
        self._research.add(strategy_id)

    def unblock_strategy(self, strategy_id: str) -> None:
        """전략의 Research 차단을 해제한다."""
        self._research.discard(strategy_id)

    # ------------------------------------------------------------------

    def _log_order(self, order: Order, result: OrderResult) -> None:
        """order_log 테이블에 감사 로그를 기록한다."""
        logger.info(
            "order_log [%s] %s %s qty=%s status=%s",
            result.strategy_id,
            result.side.value,
            result.ticker,
            result.filled_quantity,
            result.status.value,
        )
        try:
            self._db.add(
                OrderLog(
                    order_id=result.order_id,
                    strategy_id=result.strategy_id,
                    ticker=result.ticker,
                    side=result.side.value,
                    qty=order.quantity,
                    order_price=order.limit_price,
                    fill_price=result.avg_price if result.avg_price else None,
                    fill_qty=result.filled_quantity,
                    status=result.status.value,
                    broker=get_settings().maps_broker_mode,
                    mode="live" if get_settings().maps_live_trading_enabled else "mock",
                )
            )
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            logger.warning(
                "order_log duplicate order_id=%s (%s %s %s) — skipping DB insert",
                result.order_id, result.strategy_id, result.side.value, result.ticker,
            )

    def _place_with_retry(self, order: Order) -> OrderResult:
        settings = get_settings()
        attempts = max(1, settings.maps_order_retry_attempts)
        backoff = settings.maps_order_retry_backoff_seconds
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._broker.place_order(order)
            except DuplicateOrderError:
                raise
            except BrokerAdapterError as exc:
                last_exc = exc
                if not _is_transient_broker_error(exc):
                    raise
            if attempt < attempts:
                time.sleep(backoff * (2 ** (attempt - 1)))
                self._raise_if_duplicate_active_order(order)
        final_exc = last_exc or BrokerAdapterError("order retry failed")
        self._notifier.send_order_alert(
            level="ERROR",
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            message=str(final_exc),
            fields={"side": order.side.value, "attempts": attempts},
        )
        raise final_exc

    def _raise_if_duplicate_active_order(self, order: Order) -> None:
        today_start = datetime.combine(date.today(), dt_time.min)
        existing = (
            self._db.query(OrderLog)
            .filter(OrderLog.created_at >= today_start)
            .filter(OrderLog.strategy_id == order.strategy_id)
            .filter(OrderLog.ticker == order.ticker)
            .filter(OrderLog.side == order.side.value)
            .filter(OrderLog.status.in_([
                OrderStatus.PENDING.value,
                OrderStatus.PARTIALLY_FILLED.value,
                OrderStatus.FILLED.value,
            ]))
            .first()
        )
        if isinstance(existing, OrderLog):
            self._notifier.send_order_alert(
                level="WARN",
                strategy_id=order.strategy_id,
                ticker=order.ticker,
                message="Duplicate active order blocked.",
                fields={"side": order.side.value, "existing_order_id": existing.order_id},
            )
            raise DuplicateOrderError(order.ticker)


def _is_transient_broker_error(exc: BrokerAdapterError) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("timeout", "tempor", "429", "500", "502", "503", "504", "rate limit"))
