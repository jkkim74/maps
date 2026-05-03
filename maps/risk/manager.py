"""리스크 관리자 — Kill Switch 포함.

Kill Switch 원칙:
  - 신규 진입 차단: 자동 (사용자 승인 불필요)
  - 보유 포지션 청산: 사용자 승인 필수
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from maps.common.exceptions import (
    ExposureCapError,
    KillSwitchError,
    UnauthorizedLiquidationError,  # noqa: F401
)
from maps.common.models import KillSwitchLog
from maps.execution.broker_adapter import AccountBalance, BrokerAdapter, Order

logger = logging.getLogger(__name__)

_CONSEC_FAILURE_THRESHOLD: int = 5     # 연속 실패 → Kill Switch


class KillSwitchReason(str, Enum):
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MDD_LIMIT = "mdd_limit"
    MANUAL = "manual"
    RISK_METRIC_BREACH = "risk_metric_breach"
    CONSECUTIVE_FAILURE = "consecutive_failure"


@dataclass
class KillSwitchEvent:
    """Kill Switch 발동 이벤트."""

    strategy_id: str
    reason: KillSwitchReason
    triggered_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    detail: str = ""
    new_entry_blocked: bool = True
    liquidation_approved: bool = False
    approved_by: str | None = None


@dataclass
class RiskConfig:
    """리스크 한도 설정."""

    daily_loss_limit: float = 0.015     # 일일 손실 한도 (1.5%)
    mdd_limit: float = 0.15             # 포트폴리오 MDD 한도 (15%)
    position_size_limit: float = 0.10   # 단일 종목 최대 비중 (10%)


class RiskManager:
    """전략별 리스크를 모니터링하고 Kill Switch를 관리한다."""

    def __init__(
        self,
        broker: BrokerAdapter,
        db: Session,
        config: RiskConfig | None = None,
    ) -> None:
        self._broker = broker
        self._db = db
        self._cfg = config or RiskConfig()
        self._killed: dict[str, KillSwitchEvent] = {}
        self._failure_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 신규: 주문 전 체크
    # ------------------------------------------------------------------

    def check_before_order(
        self,
        order: Order,
        account: AccountBalance,
        daily_pnl: float = 0.0,
    ) -> None:
        """주문 전 리스크 체크. 위반 시 예외 발생.

        체크 순서:
          1. Kill Switch 활성 여부
          2. 일일 손실 한도 (1.5%)
          3. 단일 종목 노출 한도 (position_size_limit)

        Args:
            order: 제출할 주문.
            account: 현재 계좌 잔고.
            daily_pnl: 당일 손익률 (음수 = 손실). 기본 0.0.

        Raises:
            KillSwitchError: Kill Switch 활성 또는 일일 손실 한도 초과.
            ExposureCapError: 단일 종목 노출 한도 초과.
        """
        if self.is_new_entry_blocked(order.strategy_id):
            raise KillSwitchError(
                f"Kill Switch 발동 중 — 신규 주문 차단: {order.strategy_id}"
            )

        if daily_pnl <= -self._cfg.daily_loss_limit:
            self._trigger_kill(
                order.strategy_id,
                KillSwitchReason.DAILY_LOSS_LIMIT,
                f"일일 손실 {daily_pnl:.2%} > 한도 {self._cfg.daily_loss_limit:.2%}",
            )
            raise KillSwitchError(
                f"일일 손실 한도 초과 → Kill Switch 발동: {order.strategy_id}"
            )

        # limit_price 없는 시장가 주문도 current_price로 노출 검사
        effective_price = order.limit_price or order.current_price
        if effective_price and effective_price > 0 and account.total_value > 0:
            order_value = order.quantity * effective_price
            exposure = order_value / account.total_value
            if exposure > self._cfg.position_size_limit:
                raise ExposureCapError(order.ticker, exposure)

    # ------------------------------------------------------------------
    # 신규: 주문 성공/실패 카운터
    # ------------------------------------------------------------------

    def on_order_success(self, strategy_id: str) -> None:
        """주문 성공 시 연속 실패 카운터를 리셋한다."""
        self._failure_counts[strategy_id] = 0

    def on_order_failure(self, strategy_id: str) -> KillSwitchEvent | None:
        """주문 실패 시 카운터를 증가시키고, 5회 시 Kill Switch를 자동 발동한다.

        Returns:
            Kill Switch 발동 시 KillSwitchEvent, 아니면 None.
        """
        self._failure_counts[strategy_id] = (
            self._failure_counts.get(strategy_id, 0) + 1
        )
        count = self._failure_counts[strategy_id]
        logger.debug("연속 실패 [%s]: %d회", strategy_id, count)

        if count >= _CONSEC_FAILURE_THRESHOLD:
            return self._trigger_kill(
                strategy_id,
                KillSwitchReason.CONSECUTIVE_FAILURE,
                f"연속 주문 실패 {count}회 >= {_CONSEC_FAILURE_THRESHOLD}회",
            )
        return None

    # ------------------------------------------------------------------
    # 신규: 별칭
    # ------------------------------------------------------------------

    def deactivate(self, strategy_id: str, approved_by: str) -> None:
        """Kill Switch를 해제한다 (release 의 별칭)."""
        self.release(strategy_id, approved_by)

    # ------------------------------------------------------------------
    # 기존: 일반 Kill Switch 체크
    # ------------------------------------------------------------------

    def is_new_entry_blocked(self, strategy_id: str) -> bool:
        """신규 진입이 차단되어 있으면 True (메모리 + DB 동시 확인).

        API를 통한 외부 상태 변경(deactivate)도 반영한다.
        """
        if strategy_id in self._killed:
            return True
        # DB의 최신 이벤트가 "trigger"이면 메모리에 없더라도 차단
        latest = (
            self._db.query(KillSwitchLog)
            .filter(KillSwitchLog.strategy_id == strategy_id)
            .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
            .first()
        )
        if latest and latest.event_type in ("trigger", "approved"):
            # "approved" = 청산 승인됐지만 신규 진입은 여전히 차단
            self._killed[strategy_id] = KillSwitchEvent(
                strategy_id=strategy_id,
                reason=KillSwitchReason(latest.reason),
                detail=latest.value or "",
                new_entry_blocked=True,
                liquidation_approved=(latest.event_type == "approved"),
            )
            return True
        if latest and latest.event_type == "deactivate":
            self._killed.pop(strategy_id, None)
        return False

    def check_and_trigger(
        self,
        strategy_id: str,
        daily_pnl: float,
        current_mdd: float,
    ) -> KillSwitchEvent | None:
        """리스크 지표를 확인하고 필요 시 Kill Switch를 자동 발동한다.

        신규 진입 차단은 자동. 보유 청산은 발동하지 않는다.
        """
        if strategy_id in self._killed:
            return self._killed[strategy_id]

        if daily_pnl <= -self._cfg.daily_loss_limit:
            return self._trigger_kill(
                strategy_id,
                KillSwitchReason.DAILY_LOSS_LIMIT,
                f"일일 손실 {daily_pnl:.2%} >= 한도 {self._cfg.daily_loss_limit:.2%}",
            )

        if abs(current_mdd) >= self._cfg.mdd_limit:
            return self._trigger_kill(
                strategy_id,
                KillSwitchReason.MDD_LIMIT,
                f"MDD {current_mdd:.2%} >= 한도 {self._cfg.mdd_limit:.2%}",
            )

        return None

    def approve_liquidation(self, strategy_id: str, approved_by: str) -> KillSwitchEvent:
        """보유 포지션 청산을 승인한다 (사용자 승인 필수).

        Raises:
            KillSwitchError: Kill Switch가 발동되지 않은 전략에 승인 요청 시.
        """
        if strategy_id not in self._killed:
            raise KillSwitchError(f"Kill Switch가 발동되지 않은 전략: {strategy_id}")

        event = self._killed[strategy_id]
        event.liquidation_approved = True
        event.approved_by = approved_by
        self._log_kill_switch(event)
        logger.info("청산 승인 [%s] by %s", strategy_id, approved_by)
        return event

    def release(self, strategy_id: str, released_by: str) -> None:
        """Kill Switch를 해제한다.

        Raises:
            KillSwitchError: Kill Switch가 발동되지 않은 전략.
        """
        if strategy_id not in self._killed:
            raise KillSwitchError(f"Kill Switch가 발동되지 않은 전략: {strategy_id}")
        event = self._killed.pop(strategy_id)
        self._failure_counts.pop(strategy_id, None)
        # new_entry_blocked=False 로 설정해야 "deactivate" event_type으로 기록됨
        event.new_entry_blocked = False
        event.liquidation_approved = True
        event.approved_by = released_by
        self._log_kill_switch(event)
        logger.info("Kill Switch 해제 [%s] by %s", strategy_id, released_by)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _trigger_kill(
        self,
        strategy_id: str,
        reason: KillSwitchReason,
        detail: str = "",
    ) -> KillSwitchEvent:
        """Kill Switch를 발동하고 로그를 기록한다."""
        event = KillSwitchEvent(
            strategy_id=strategy_id,
            reason=reason,
            detail=detail,
            new_entry_blocked=True,
            liquidation_approved=False,
        )
        self._killed[strategy_id] = event
        self._log_kill_switch(event)
        logger.warning("Kill Switch 발동 [%s]: %s", strategy_id, detail)
        return event

    def _log_kill_switch(self, event: KillSwitchEvent) -> None:
        """kill_switch_log 테이블에 감사 로그를 기록한다.

        event_type 3-way 분류:
          trigger   — kill switch 발동 (진입 차단)
          approved  — 청산 승인 (진입 차단 유지, 포지션 청산만 허용)
          deactivate — kill switch 완전 해제 (진입 허용)
        """
        if not event.new_entry_blocked:
            event_type = "deactivate"
        elif event.liquidation_approved:
            event_type = "approved"
        else:
            event_type = "trigger"
        self._db.add(
            KillSwitchLog(
                strategy_id=event.strategy_id,
                event_type=event_type,
                reason=event.reason.value,
                value=event.detail if event.detail else None,
                new_entry_blocked=event.new_entry_blocked,
                approved_by=event.approved_by,
            )
        )
        self._db.commit()
