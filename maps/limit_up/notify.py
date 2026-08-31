"""상한가 V1 운영 알림 — 감시 등록·트리거·청산을 텔레그램으로 밀어 준다.

🔴 **호출은 절대 블로킹하면 안 된다.** 여기 함수는 전부 서비스 펌프
스레드(`runtime._service_pump`)에서 불린다. 텔레그램 HTTP 가 그 스레드를 5초
붙잡으면 뒤에 줄 선 손절 주문이 그만큼 밀린다 — 패키지 문서의 "브로커 호출은
이벤트 루프에서 실행하지 않는다" 와 같은 이유다. 그래서 메시지 **문자열은
호출 스레드에서 만들고**(ORM 객체는 스레드 간에 넘기지 않는다) 발송만 워커에
맡긴다.

발송 실패는 삼킨다. 알림은 관측 수단이지 안전 장치가 아니다 — 텔레그램이
죽었다고 엔진이 멈추면 관측을 위해 매매를 잃는다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from maps.ops.notifications import get_telegram_notifier

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용
    from maps.common.models import LimitUpSession

logger = logging.getLogger(__name__)

# 워커 1개 — 알림이 사건 순서대로 도착해야 사람이 읽을 수 있다.
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="limit-up-notify")

# 알림을 낼 이벤트만 여기 적는다. 화이트리스트라서 새 action 이 생겨도
# 명시적으로 등록하기 전에는 조용하다 — 원장 이벤트는 알림보다 훨씬 촘촘하다.
_ALERTS: dict[str, str] = {
    "fire_net": "🎯 상한가 트리거",
    "market_sell": "🔻 청산 주문",
    "next_open_sell": "🔻 익일 시가 청산",
    "eod_sell": "🔻 장마감 청산",
    "overnight_trim_sell": "✂️ 오버나이트 트림",
    "after_hours_exit": "🌙 시간외 탈출",
    "cancel_no_fill": "🚫 미체결 종료",
}


def push(text: str) -> None:
    """Queue one Telegram line without blocking the caller."""
    _POOL.submit(_send, text)


def _send(text: str) -> None:
    """Deliver one queued line; never propagate a messaging failure."""
    try:
        get_telegram_notifier().send_message(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("상한가 알림 발송 실패: %s", exc)


def watch_started(session: "LimitUpSession") -> None:
    """Announce a newly watched +25% candidate."""
    push(
        f"👀 <b>상한가 감시</b> <code>{session.ticker}</code> · {session.market}\n"
        f"상한가 {session.upper_limit_price:,}원 · 트리거 {session.trigger_price:,}원\n"
        f"모드 {session.execution_mode}"
    )


def event_alert(
    session: "LimitUpSession", action: str, payload: dict[str, Any] | None
) -> None:
    """Announce one whitelisted ledger event.

    Args:
        session: The session the event belongs to.
        action: Ledger action name; unlisted names are silent.
        payload: The event payload, used for grid legs and exit reasons.
    """
    label = _ALERTS.get(action)
    if label is None:
        return
    push(f"{label} <code>{session.ticker}</code>\n{_body(session, payload)}")


def _body(session: "LimitUpSession", payload: dict[str, Any] | None) -> str:
    """Render the shared detail block for one session event."""
    data = payload or {}
    lines = [
        f"상한가 {session.upper_limit_price:,}원 · 모드 {session.execution_mode}"
    ]
    grid = data.get("grid")
    if grid:
        lines.append(
            " / ".join(
                f"{leg['name']} {leg['quantity']:,}주 @ {leg['price']:,}원"
                for leg in grid
            )
        )
    if data.get("quantity"):
        lines.append(f"수량 {data['quantity']:,}주")
    reason = data.get("reason") or session.end_reason
    if reason:
        lines.append(f"사유 {reason}")
    if session.realized_pnl is not None:
        lines.append(f"실현손익 {session.realized_pnl:+,.0f}원")
    return "\n".join(lines)
