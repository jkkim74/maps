"""주문 관리자 — 신호를 주문으로 변환하고 감사 로그를 기록한다."""

from __future__ import annotations

import logging
import time
import zoneinfo
from dataclasses import replace
from datetime import date, datetime, time as dt_time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError, ResearchStrategyError
from maps.common.models import OrderLog
from maps.common.settings import get_settings
from maps.execution.broker_adapter import (
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    order_log_id,
)
from maps.ops.notifications import SlackNotifier
from maps.risk.manager import RiskManager

logger = logging.getLogger(__name__)

# Research/Alert_only 단계에서 자동 주문이 금지된 전략 단계
_BLOCKED_STAGES: frozenset[str] = frozenset(["research", "alert_only"])

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


def _normalize_filled_row(row: OrderLog) -> bool:
    """FILLED 로 확정된 주문의 빈 체결 수량·가격을 주문 값으로 채운다.

    브로커가 ``status=filled`` 를 주면서 체결수량 0 을 함께 반환하는 경우가 있다
    (2026-07-31 운영 확인: 004490 매도가 ``filled`` + ``fill_qty=0`` 으로 남았다).
    하위 집계가 전부 ``fill_qty > 0`` 을 요구하므로 — 매매일지(`trade_review`),
    승격 게이트의 `mock_months` — 수량이 비면 그 체결은 **통째로 사라진다**.

    부분체결에는 적용하지 않는다. 실제 체결 수량이 중요한데 주문 수량으로 덮으면
    보유하지 않은 수량을 체결로 기록하게 된다.

    :param row: 갱신할 주문 로그 행.
    :return: 값을 채웠으면 ``True``.
    """
    changed = False
    if not row.fill_qty:
        row.fill_qty = row.qty
        changed = True
    if not row.fill_price:
        row.fill_price = row.order_price
        changed = True
    return changed


def _kst_today_start_utc() -> datetime:
    """오늘(KST) 자정을 UTC naive 시각으로 반환한다."""
    return kst_day_bounds_utc(datetime.now(_KST).date())[0]


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

    def submit_exit(self, order: Order, *, exit_reason: str | None = None) -> OrderResult:
        """Submit a strategy exit without applying new-entry exposure checks.

        This is only for a normal strategy exit or stop-loss. Kill Switch
        liquidation remains a separate, explicitly approved workflow.

        exit_reason(stop_loss|take_profit|signal|bracket)은 order_log에 그대로
        기록된다. 왜 팔았는지가 감사 로그에 남지 않으면 사후 검증이 불가능하다.
        """
        if order.side != OrderSide.SELL:
            raise ValueError("submit_exit only accepts sell orders")
        return self._submit(
            order, daily_pnl=0.0, check_entry_risk=False, exit_reason=exit_reason,
        )

    def _submit(
        self,
        order: Order,
        *,
        daily_pnl: float,
        check_entry_risk: bool,
        exit_reason: str | None = None,
    ) -> OrderResult:
        if order.strategy_id in self._research:
            raise ResearchStrategyError(order.strategy_id, "research")
        self._raise_if_duplicate_active_order(order)

        if check_entry_risk:
            account = self._broker.get_account_balance()
            self._risk.check_before_order(order, account, daily_pnl)

        try:
            result = self._place_with_retry(order)
            settings = get_settings()
            result = replace(
                result,
                order_id=order_log_id(
                    result.order_id,
                    broker=settings.maps_broker_mode,
                    account_no=settings.kis_account_no,
                    submitted_at=result.submitted_at,
                ),
            )
            if result.status == OrderStatus.REJECTED:
                logger.warning(
                    "주문 거부됨 [%s %s %s]: 브로커가 REJECTED 반환",
                    order.strategy_id, order.ticker, order.side.value,
                )
                self._risk.on_order_failure(
                    order.strategy_id,
                    reason=f"broker rejected ({order.ticker} {order.side.value})",
                )
            else:
                self._risk.on_order_success(order.strategy_id)
            self._log_order(order, result, exit_reason=exit_reason)
            return result
        except Exception as exc:
            # 관측성: 브로커 예외(KIS 에러코드 등)를 사유와 함께 WARNING으로 남기고 카운터에 반영
            logger.warning(
                "주문 제출 실패 [%s %s %s]: %s",
                order.strategy_id, order.ticker, order.side.value, exc,
            )
            self._risk.on_order_failure(order.strategy_id, reason=str(exc))
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
        # 주문 시각(8:55 KST = 23:55 UTC)이 UTC 날짜 경계를 넘지 않도록 KST 자정 기준으로 만료
        today_start = _kst_today_start_utc()
        # expire_pending_orders는 아래 브로커 포지션 대조가 끝난 뒤 마지막에 실행한다.
        # (전일 제출됐지만 브로커에서 실제 체결된 매도가 동기화 전에 만료로 오기록되는 버그 방지)
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

        settings = get_settings()
        broker_result_ids: set[str] = set()
        for result in broker_results:
            if not result.order_id:
                continue
            stored_order_id = order_log_id(
                result.order_id,
                broker=settings.maps_broker_mode,
                account_no=settings.kis_account_no,
                submitted_at=result.submitted_at,
            )
            broker_result_ids.add(stored_order_id)
            row = (
                self._db.query(OrderLog)
                .filter(OrderLog.order_id == stored_order_id)
                .first()
            )
            if row is None and stored_order_id != result.order_id:
                submitted = result.submitted_at
                if submitted.tzinfo is not None:
                    submitted = submitted.astimezone(_KST).replace(tzinfo=None)
                day_start, day_end = kst_day_bounds_utc(submitted.date())
                row = (
                    self._db.query(OrderLog)
                    .filter(OrderLog.order_id == result.order_id)
                    .filter(OrderLog.broker == settings.maps_broker_mode)
                    .filter(OrderLog.ticker == result.ticker)
                    .filter(OrderLog.side == result.side.value)
                    .filter(OrderLog.created_at >= day_start)
                    .filter(OrderLog.created_at < day_end)
                    .first()
                )
            if row is None:
                # MAPS 외부(MTS 등)에서 제출된 주문 — DB에 삽입하여 화면에 표시
                self._db.add(
                    OrderLog(
                        order_id=stored_order_id,
                        strategy_id="external_mts",
                        ticker=result.ticker,
                        side=result.side.value,
                        qty=result.filled_quantity or 0,
                        order_price=None,
                        fill_price=result.avg_price if result.avg_price else None,
                        fill_qty=result.filled_quantity,
                        status=result.status.value,
                        broker=get_settings().maps_broker_mode,
                        mode=_order_log_mode(),
                    )
                )
                updated += 1
                continue
            broker_result_ids.add(row.order_id)
            if row.ticker != result.ticker or row.side != result.side.value:
                sync_errors += 1
                logger.error(
                    "Broker order identity mismatch: order_id=%s db=%s/%s broker=%s/%s",
                    row.order_id,
                    row.ticker,
                    row.side,
                    result.ticker,
                    result.side.value,
                )
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
            if row.status == OrderStatus.FILLED.value:
                changed = _normalize_filled_row(row) or changed
            if changed:
                updated += 1
        updated += self._reconcile_same_day_buys(broker_result_ids)

        # 포지션 기반 매도 체결 폴백: KIS VTS는 장전 시장가 주문을 daily CCLD에서
        # 반환하지 않는 경우가 있으므로, 브로커 포지션에서 사라진 종목의 pending SELL을
        # filled로 처리한다.
        try:
            current_positions = set(self._broker.get_positions().keys())
        except NotImplementedError:
            current_positions = None
        except BrokerAdapterError as exc:
            sync_errors += 1
            current_positions = None
            logger.warning("Broker position sync unavailable: %s", exc)

        if current_positions is not None:
            # 전일분 포함 모든 pending 매도를 브로커 포지션과 대조한다 (created_at 제한 없음).
            # 만료 처리 전에 실행되어, 브로커에서 이미 체결돼 포지션이 사라진 매도를 filled로 보정한다.
            pending_sells = (
                self._db.query(OrderLog)
                .filter(OrderLog.status == OrderStatus.PENDING.value)
                .filter(OrderLog.side == OrderSide.SELL.value)
                .filter(OrderLog.order_id.notin_(broker_result_ids))
                .all()
            )
            for sell_row in pending_sells:
                if sell_row.ticker not in current_positions:
                    sell_row.status = OrderStatus.FILLED.value
                    # 제출 시점 브로커 응답의 체결수량 0이 그대로 남는 경우가 있어
                    # 0도 미기록으로 취급한다 (브로커 결과 경로와 같은 규칙).
                    _normalize_filled_row(sell_row)
                    updated += 1
                    logger.info(
                        "Position-based fill: sell [%s %s] marked filled (ticker absent from broker)",
                        sell_row.order_id,
                        sell_row.ticker,
                    )
            # 포지션 대조 결과를 DB에 반영(flush)한 뒤 만료 처리해야, bulk update가 보정된 행을 건너뛴다.
            self._db.flush()

        # 포지션 대조 후에도 미해결인 전일 이전 주문만 만료 처리한다.
        expired = self.expire_pending_orders(before=today_start)

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

        today_start = _kst_today_start_utc()
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

    def _log_order(
        self, order: Order, result: OrderResult, *, exit_reason: str | None = None,
    ) -> None:
        """order_log 테이블에 감사 로그를 기록한다."""
        logger.info(
            "order_log [%s] %s %s qty=%s status=%s%s",
            result.strategy_id,
            result.side.value,
            result.ticker,
            result.filled_quantity,
            result.status.value,
            f" exit_reason={exit_reason}" if exit_reason else "",
        )
        try:
            self._db.add(
                OrderLog(
                    order_id=result.order_id,
                    strategy_id=result.strategy_id,
                    ticker=result.ticker,
                    side=result.side.value,
                    qty=order.quantity,
                    order_price=order.limit_price or order.current_price or None,
                    fill_price=result.avg_price if result.avg_price else None,
                    fill_qty=result.filled_quantity,
                    status=result.status.value,
                    broker=get_settings().maps_broker_mode,
                    mode=_order_log_mode(),
                    exit_reason=exit_reason,
                    atr14=order.atr14,
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
        # 08:55 KST 제출분은 UTC 로 전일 23:55 — KST 자정 기준이라야 같은 거래일로 잡힌다.
        today_start = _kst_today_start_utc()
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
