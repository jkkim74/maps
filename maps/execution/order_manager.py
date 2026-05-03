"""주문 관리자 — 신호를 주문으로 변환하고 감사 로그를 기록한다."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from maps.common.exceptions import ResearchStrategyError
from maps.common.models import OrderLog
from maps.execution.broker_adapter import BrokerAdapter, Order, OrderResult, OrderStatus
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
        if order.strategy_id in self._research:
            raise ResearchStrategyError(order.strategy_id, "research")

        account = self._broker.get_account_balance()
        self._risk.check_before_order(order, account, daily_pnl)

        try:
            result = self._broker.place_order(order)
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
                broker="mock",
                mode="mock",
            )
        )
        self._db.commit()
