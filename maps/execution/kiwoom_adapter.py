"""키움증권 OpenAPI+ 브로커 어댑터 — Phase 5 구현 예정.

키움증권 OpenAPI+는 Windows 전용 COM 오브젝트 기반이다.
  - 필요 환경: Windows 10/11 + 키움 OpenAPI+ 설치 (32-bit Python)
  - pywin32 패키지 필요

필요 환경변수:
  KIWOOM_ACCOUNT_NO   — 계좌번호 (예: "1234567890")
  KIWOOM_PASSWORD     — 계좌 비밀번호 (암호화 전달 권장)

주요 TR 코드:
  opt10001 — 주식기본정보요청
  opt10081 — 주식일봉차트조회요청
  OPT10004 — 주식호가요청
  KOA_NORMAL_BUY_KQ_ORD   — KOSDAQ 매수주문
  KOA_NORMAL_SELL_KQ_ORD  — KOSDAQ 매도주문

참고: 키움 OpenAPI+는 GUI 이벤트 루프(QApplication)가 필요하므로
      서버(headless) 환경에서 실행하려면 가상 디스플레이 또는
      별도 Windows 프로세스로 격리해야 한다.
"""

from __future__ import annotations

import datetime
import logging
import os
import platform

from maps.common.exceptions import BrokerAdapterError
from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    Order,
    OrderResult,
    Position,
)

logger = logging.getLogger(__name__)


class KiwoomAdapter(BrokerAdapter):
    """키움증권 OpenAPI+ 브로커 어댑터 — Phase 5 구현 예정.

    Windows COM 오브젝트 기반이므로 Windows 환경에서만 동작한다.
    현재는 자격증명 검증 및 구조만 제공한다.
    """

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise BrokerAdapterError(
                "KiwoomAdapter는 Windows 전용입니다. "
                "Linux/macOS 환경에서는 KISAdapter 또는 MockBroker를 사용하세요."
            )

        self._account_no = os.getenv("KIWOOM_ACCOUNT_NO", "")
        self._password = os.getenv("KIWOOM_PASSWORD", "")

        missing = [
            k for k, v in {
                "KIWOOM_ACCOUNT_NO": self._account_no,
                "KIWOOM_PASSWORD": self._password,
            }.items()
            if not v
        ]
        if missing:
            raise BrokerAdapterError(
                f"키움 어댑터: 필수 환경변수 누락 — {', '.join(missing)}"
            )

        logger.info("KiwoomAdapter 초기화: 계좌=%s", self._account_no)

    # ------------------------------------------------------------------
    # BrokerAdapter 추상 메서드 — Phase 5 구현 예정
    # ------------------------------------------------------------------

    def place_order(self, order: Order) -> OrderResult:
        """키움 OpenAPI+ SendOrder()로 주식 주문을 제출한다.

        Phase 5 구현 예정.
        fOrderType: 1=신규매수, 2=신규매도
        """
        raise NotImplementedError(
            "KiwoomAdapter.place_order — Phase 5 구현 예정. "
            "현재는 MockBroker를 사용하세요."
        )

    def cancel_order(self, order_id: str) -> bool:
        """키움 OpenAPI+ SendOrder()로 주문을 취소한다.

        Phase 5 구현 예정.
        fOrderType: 3=매수취소, 4=매도취소
        """
        raise NotImplementedError("KiwoomAdapter.cancel_order — Phase 5 구현 예정")

    def get_position(self, ticker: str) -> Position | None:
        """키움 OpenAPI+ TR opt10072로 보유 포지션을 조회한다.

        Phase 5 구현 예정.
        """
        raise NotImplementedError("KiwoomAdapter.get_position — Phase 5 구현 예정")

    def get_account_balance(self) -> AccountBalance:
        """키움 OpenAPI+ TR opw00001로 계좌 잔고를 조회한다.

        Phase 5 구현 예정.
        """
        raise NotImplementedError("KiwoomAdapter.get_account_balance — Phase 5 구현 예정")

    def is_market_open(self) -> bool:
        """KST 기준 평일 09:00~15:30 여부를 반환한다."""
        _KST = datetime.timezone(datetime.timedelta(hours=9))
        now = datetime.datetime.now(_KST)
        if now.weekday() >= 5:
            return False
        t = now.time()
        return datetime.time(9, 0) <= t <= datetime.time(15, 30)
