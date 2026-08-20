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
from maps.common.models import KillSwitchLog, OrderLog
from maps.execution.broker_adapter import AccountBalance, BrokerAdapter, Order, OrderSide
from maps.ops.notifications import SlackNotifier

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
    max_portfolio_exposure: float = 1.0  # 포트폴리오 총 노출 합산 한도 (100% = 현금초과 매수 금지)
    # 8단계: 테마·섹터 노출 한도
    sector_exposure_limit: float = 0.25         # 단일 섹터 최대 비중 (25%)
    theme_exposure_limit: float = 0.35          # 단일 테마 최대 비중 (35%)
    theme_exposure_limit_enabled: bool = False  # 테마 노출 한도 활성 여부
    sector_exposure_limit_enabled: bool = False # 섹터 노출 한도 활성 여부
    # 시장 국면별 최소 현금 비율
    min_cash_ratio_strong: float = 0.15
    min_cash_ratio_mixed: float = 0.25
    min_cash_ratio_weak: float = 0.35


# 테마 매핑 (종목 ticker → 테마명)
# SecurityMetadata.theme 컬럼에 없을 경우 이 딕셔너리를 fallback으로 사용
_THEME_MAP: dict[str, str] = {
    # 실제 배포 시 SecurityMetadata.theme 컬럼으로 관리
}


class RiskManager:
    """전략별 리스크를 모니터링하고 Kill Switch를 관리한다."""

    def __init__(
        self,
        broker: BrokerAdapter,
        db: Session,
        config: RiskConfig | None = None,
        notifier: SlackNotifier | None = None,
    ) -> None:
        self._broker = broker
        self._db = db
        self._cfg = config or RiskConfig()
        self._notifier = notifier or SlackNotifier()
        self._killed: dict[str, KillSwitchEvent] = {}
        self._failure_counts: dict[str, int] = {}
        # 재시작 후 연속실패 카운트는 메모리에서 소실되므로, 전략별 최초 접근 시
        # order_log 감사 로그에서 1회 복원한다(M-2). 복원 완료 전략을 기록.
        self._failure_loaded: set[str] = set()

    # ------------------------------------------------------------------
    # 신규: 주문 전 체크
    # ------------------------------------------------------------------

    def check_before_order(
        self,
        order: Order,
        account: AccountBalance,
        daily_pnl: float = 0.0,
        *,
        risk_strategy_id: str | None = None,
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
        strategy_id = risk_strategy_id or order.strategy_id
        if self.is_new_entry_blocked(strategy_id):
            raise KillSwitchError(
                f"Kill Switch 발동 중 — 신규 주문 차단: {strategy_id}"
            )

        if daily_pnl <= -self._cfg.daily_loss_limit:
            self._trigger_kill(
                strategy_id,
                KillSwitchReason.DAILY_LOSS_LIMIT,
                f"일일 손실 {daily_pnl:.2%} > 한도 {self._cfg.daily_loss_limit:.2%}",
            )
            raise KillSwitchError(
                f"일일 손실 한도 초과 → Kill Switch 발동: {strategy_id}"
            )

        # limit_price 없는 시장가 주문도 current_price로 노출 검사
        effective_price = order.limit_price or order.current_price
        if effective_price and effective_price > 0 and account.total_value > 0:
            order_value = order.quantity * effective_price
            exposure = order_value / account.total_value
            if exposure > self._cfg.position_size_limit:
                raise ExposureCapError(order.ticker, exposure)

        # C-3: 신규 매수는 가용 현금·포트폴리오 총 노출도 함께 검증한다.
        # total_value 기준 단일 노출 검사만으로는 보유 평가액이 커질수록 현금이 거의
        # 없어도 "총자산의 10%" 주문이 통과되어, 누적 주문이 가용 현금을 초과할 수 있다.
        is_buy = getattr(order, "side", OrderSide.BUY) == OrderSide.BUY
        if is_buy and effective_price and effective_price > 0:
            order_value = order.quantity * effective_price
            if order_value > account.cash:
                raise ExposureCapError(
                    order.ticker,
                    order_value / account.total_value if account.total_value > 0 else None,
                    f"가용 현금 부족: 주문 {order_value:,.0f}원 > 현금 {account.cash:,.0f}원",
                )
            if account.total_value > 0:
                portfolio_exposure = (
                    account.positions_value + order_value
                ) / account.total_value
                if portfolio_exposure > self._cfg.max_portfolio_exposure:
                    raise ExposureCapError(
                        order.ticker,
                        portfolio_exposure,
                        f"포트폴리오 총 노출 {portfolio_exposure:.1%} > 한도 "
                        f"{self._cfg.max_portfolio_exposure:.1%}",
                    )

        # 8단계: 섹터·테마 노출 한도 체크 (활성화된 경우)
        if effective_price and effective_price > 0 and account.total_value > 0:
            self._check_exposure_limits(order, account, effective_price)

    # ------------------------------------------------------------------
    # 신규: 주문 성공/실패 카운터
    # ------------------------------------------------------------------

    def on_order_success(self, strategy_id: str) -> None:
        """주문 성공 시 연속 실패 카운터를 리셋한다."""
        self._failure_counts[strategy_id] = 0
        self._failure_loaded.add(strategy_id)

    def _restore_failure_count(self, strategy_id: str) -> int:
        """재시작 후 order_log에서 마지막 성공 이후 연속 REJECTED 수를 복원한다.

        in-memory 카운터는 프로세스 재시작 시 0으로 초기화되므로, 감사 로그(order_log)의
        최근 주문 결과로 연속 실패 횟수를 보수적으로 재구성한다. REJECTED만 실패로 계수하고
        그 외 상태(FILLED/CANCELLED/PARTIAL 등)를 만나면 중단한다(=성공 시 리셋과 동일 의미).
        예외성 실패(로그 미기록)는 복원 불가하나, 과소계상은 안전한 방향이다.
        """
        try:
            rows = (
                self._db.query(OrderLog.status)
                .filter(OrderLog.strategy_id == strategy_id)
                .order_by(OrderLog.created_at.desc())
                .limit(_CONSEC_FAILURE_THRESHOLD)
                .all()
            )
        except Exception:
            return 0
        count = 0
        for (status,) in rows:
            if status == "REJECTED":
                count += 1
            else:
                break
        return count

    def on_order_failure(self, strategy_id: str, reason: str = "") -> KillSwitchEvent | None:
        """주문 실패 시 카운터를 증가시키고, 5회 시 Kill Switch를 자동 발동한다.

        Args:
            strategy_id: 실패한 전략 ID.
            reason: 실패 원인(브로커 예외 메시지·KIS 에러코드 등). 관측성을 위해 WARNING
                로그와 Kill Switch 발동 detail(`kill_switch_log.value`)에 기록된다.

        Returns:
            Kill Switch 발동 시 KillSwitchEvent, 아니면 None.
        """
        # 재시작 직후라면 order_log에서 연속 실패 수를 1회 복원해 카운터를 시드한다(M-2).
        if strategy_id not in self._failure_loaded:
            restored = self._restore_failure_count(strategy_id)
            self._failure_counts[strategy_id] = max(
                self._failure_counts.get(strategy_id, 0), restored
            )
            self._failure_loaded.add(strategy_id)

        self._failure_counts[strategy_id] = (
            self._failure_counts.get(strategy_id, 0) + 1
        )
        count = self._failure_counts[strategy_id]
        # 관측성: 개별 주문 실패도 사유와 함께 WARNING으로 남긴다.
        # (기존 DEBUG 로깅은 INFO 운영 로그에 남지 않아 거부 사유 추적이 불가능했음)
        logger.warning(
            "주문 실패 [%s] %d/%d회: %s",
            strategy_id, count, _CONSEC_FAILURE_THRESHOLD, reason or "(사유 미상)",
        )

        if count >= _CONSEC_FAILURE_THRESHOLD:
            detail = f"연속 주문 실패 {count}회 >= {_CONSEC_FAILURE_THRESHOLD}회"
            if reason:
                detail += f" (마지막 사유: {reason})"
            return self._trigger_kill(
                strategy_id,
                KillSwitchReason.CONSECUTIVE_FAILURE,
                detail,
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
        self._notify_kill_switch(event)
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
        self._notify_kill_switch(event)
        logger.info("Kill Switch 해제 [%s] by %s", strategy_id, released_by)

    # ------------------------------------------------------------------
    # 8단계: 테마·섹터 노출 한도 체크
    # ------------------------------------------------------------------

    def _check_exposure_limits(
        self,
        order: Order,
        account: AccountBalance,
        effective_price: float,
    ) -> None:
        """섹터·테마 노출 한도를 체크하고 초과 시 ExposureCapError를 발생시킨다."""
        from maps.common.models import SecurityMetadata, PortfolioSnapshot

        if not (self._cfg.sector_exposure_limit_enabled or self._cfg.theme_exposure_limit_enabled):
            return

        # 현재 포지션 전체 목록 조회 (broker에서 가져온 포지션 기준)
        try:
            positions: list = self._broker.get_positions() if hasattr(self._broker, "get_positions") else []
        except Exception:
            positions = []

        # 신규 주문 종목의 섹터·테마 조회
        meta = (
            self._db.query(SecurityMetadata)
            .filter(SecurityMetadata.ticker == order.ticker)
            .first()
        )
        order_sector = (meta.sector or "") if meta else ""
        order_theme = (meta.theme or "") if meta else ""
        order_value = order.quantity * effective_price

        if account.total_value <= 0:
            return

        if self._cfg.sector_exposure_limit_enabled and order_sector:
            sector_value = sum(
                p.current_value
                for p in positions
                if hasattr(p, "ticker") and self._get_ticker_sector(p.ticker) == order_sector
            )
            new_sector_ratio = (sector_value + order_value) / account.total_value
            if new_sector_ratio > self._cfg.sector_exposure_limit:
                raise ExposureCapError(
                    order.ticker,
                    new_sector_ratio,
                    f"섹터 '{order_sector}' 노출 {new_sector_ratio:.1%} > 한도 {self._cfg.sector_exposure_limit:.1%}",
                )

        if self._cfg.theme_exposure_limit_enabled and order_theme:
            theme_value = sum(
                p.current_value
                for p in positions
                if hasattr(p, "ticker") and self._get_ticker_theme(p.ticker) == order_theme
            )
            new_theme_ratio = (theme_value + order_value) / account.total_value
            if new_theme_ratio > self._cfg.theme_exposure_limit:
                raise ExposureCapError(
                    order.ticker,
                    new_theme_ratio,
                    f"테마 '{order_theme}' 노출 {new_theme_ratio:.1%} > 한도 {self._cfg.theme_exposure_limit:.1%}",
                )

    def _get_ticker_sector(self, ticker: str) -> str:
        """DB에서 종목의 섹터를 조회한다."""
        from maps.common.models import SecurityMetadata
        try:
            meta = self._db.query(SecurityMetadata).filter(SecurityMetadata.ticker == ticker).first()
            return (meta.sector or "") if meta else ""
        except Exception:
            return ""

    def _get_ticker_theme(self, ticker: str) -> str:
        """DB에서 종목의 테마를 조회한다."""
        from maps.common.models import SecurityMetadata
        try:
            meta = self._db.query(SecurityMetadata).filter(SecurityMetadata.ticker == ticker).first()
            return (meta.theme or "") if meta else ""
        except Exception:
            return ""

    def min_cash_ratio(self, market_regime: str = "mixed") -> float:
        """시장 국면에 따른 최소 현금 비율을 반환한다."""
        regime = market_regime.lower()
        if regime == "strong":
            return self._cfg.min_cash_ratio_strong
        if regime == "weak":
            return self._cfg.min_cash_ratio_weak
        return self._cfg.min_cash_ratio_mixed

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
        self._notify_kill_switch(event)
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

    def _notify_kill_switch(self, event: KillSwitchEvent) -> None:
        if not event.new_entry_blocked:
            event_type = "deactivate"
        elif event.liquidation_approved:
            event_type = "approved"
        else:
            event_type = "trigger"
        self._notifier.send_kill_switch(
            strategy_id=event.strategy_id,
            event_type=event_type,
            reason=event.reason.value,
            detail=event.detail,
            approved_by=event.approved_by,
        )
