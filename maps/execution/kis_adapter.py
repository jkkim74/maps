"""KIS(한국투자증권) 브로커 어댑터 — Phase 5 구현 예정.

KIS Developers Open API (REST) 연동:
  https://apiportal.koreainvestment.com/

필요 환경변수:
  KIS_APP_KEY       — Open API 앱 키
  KIS_APP_SECRET    — Open API 앱 시크릿
  KIS_ACCOUNT_NO    — 계좌번호 (예: "12345678-01")
  KIS_REAL_TRADING  — "true" 이면 실거래, 기본은 모의투자

주요 엔드포인트 (모의투자):
  POST /oauth2/tokenP                                          — 토큰 발급
  POST /uapi/domestic-stock/v1/trading/order-cash             — 주식 주문
  GET  /uapi/domestic-stock/v1/trading/inquire-balance        — 잔고 조회
  GET  /uapi/domestic-stock/v1/trading/inquire-psbl-order     — 주문가능금액
  DELETE /uapi/domestic-stock/v1/trading/order-rvsecncl       — 주문취소
"""

from __future__ import annotations

import datetime
import logging
import os

from maps.common.exceptions import BrokerAdapterError
from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
)

logger = logging.getLogger(__name__)

_KIS_REAL_BASE = "https://openapi.koreainvestment.com:9443"
_KIS_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"


class KISAdapter(BrokerAdapter):
    """KIS Open API 브로커 어댑터 — Phase 5 구현 예정.

    현재는 자격증명 검증 및 구조만 제공한다.
    실제 주문 실행은 Phase 5에서 구현한다.
    """

    def __init__(self) -> None:
        self._app_key = os.getenv("KIS_APP_KEY", "")
        self._app_secret = os.getenv("KIS_APP_SECRET", "")
        self._account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self._real = os.getenv("KIS_REAL_TRADING", "false").lower() == "true"

        missing = [
            k for k, v in {
                "KIS_APP_KEY": self._app_key,
                "KIS_APP_SECRET": self._app_secret,
                "KIS_ACCOUNT_NO": self._account_no,
            }.items()
            if not v
        ]
        if missing:
            raise BrokerAdapterError(
                f"KIS 어댑터: 필수 환경변수 누락 — {', '.join(missing)}"
            )

        self._base_url = _KIS_REAL_BASE if self._real else _KIS_PAPER_BASE
        self._access_token: str | None = None
        self._token_expires_at: datetime.datetime | None = None
        logger.info(
            "KISAdapter 초기화 [%s]: 계좌=%s",
            "실거래" if self._real else "모의투자",
            self._account_no,
        )

    # ------------------------------------------------------------------
    # BrokerAdapter 추상 메서드 — Phase 5 구현 예정
    # ------------------------------------------------------------------

    def place_order(self, order: Order) -> OrderResult:
        """KIS REST API로 주식 현금 주문을 제출한다.

        Phase 5 구현 예정.
        엔드포인트: POST /uapi/domestic-stock/v1/trading/order-cash
        """
        raise NotImplementedError(
            "KISAdapter.place_order — Phase 5 구현 예정. "
            "현재는 MockBroker를 사용하세요."
        )

    def cancel_order(self, order_id: str) -> bool:
        """KIS REST API로 주문을 취소한다.

        Phase 5 구현 예정.
        엔드포인트: DELETE /uapi/domestic-stock/v1/trading/order-rvsecncl
        """
        raise NotImplementedError("KISAdapter.cancel_order — Phase 5 구현 예정")

    def get_position(self, ticker: str) -> Position | None:
        """KIS REST API로 보유 포지션을 조회한다.

        Phase 5 구현 예정.
        엔드포인트: GET /uapi/domestic-stock/v1/trading/inquire-balance
        """
        raise NotImplementedError("KISAdapter.get_position — Phase 5 구현 예정")

    def get_account_balance(self) -> AccountBalance:
        """KIS REST API로 계좌 잔고를 조회한다.

        Phase 5 구현 예정.
        엔드포인트: GET /uapi/domestic-stock/v1/trading/inquire-psbl-order
        """
        raise NotImplementedError("KISAdapter.get_account_balance — Phase 5 구현 예정")

    def is_market_open(self) -> bool:
        """KST 기준 평일 09:00~15:30 여부를 반환한다."""
        _KST = datetime.timezone(datetime.timedelta(hours=9))
        now = datetime.datetime.now(_KST)
        if now.weekday() >= 5:
            return False
        t = now.time()
        return datetime.time(9, 0) <= t <= datetime.time(15, 30)

    # ------------------------------------------------------------------
    # 인증 헬퍼 — Phase 5 구현 예정
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """OAuth2 액세스 토큰을 발급/갱신한다.

        Phase 5 구현 예정.
        엔드포인트: POST /oauth2/tokenP
        """
        raise NotImplementedError("KISAdapter._ensure_token — Phase 5 구현 예정")
