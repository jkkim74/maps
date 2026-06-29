"""텔레그램 봇 웹훅 — 편입 알림의 [무장][무장해제] 인라인 버튼 콜백 처리.

`/analyze` 결과 알림(maps.ops.notifications.TelegramNotifier.send_analysis_picks)이 보낸
메시지의 버튼을 누르면 텔레그램이 이 엔드포인트로 callback_query를 POST한다.
콜백 데이터(`arm:{pick_id}` / `disarm:{pick_id}`)를 파싱해 기존 무장/무장해제 로직
(maps.api.analysis_picks.arm_pick/disarm_pick)을 그대로 재사용한다.

이 라우터는 세션 쿠키 없이 외부(텔레그램 서버)에서 호출되므로 maps.api.auth 의
공개 경로로 등록돼 있다. 실제 보안은 (1) secret_token 헤더 검증과
(2) chat_id 화이트리스트가 담당한다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from maps.api.analysis_picks import arm_pick, disarm_pick
from maps.api.deps import DbDep
from maps.common.settings import get_settings
from maps.ops.notifications import get_telegram_notifier
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["Telegram Webhook"])

_OK = JSONResponse({"ok": True})


def _parse_action(data: str) -> tuple[str, int] | None:
    """콜백 데이터 `arm:{id}` / `disarm:{id}` 를 (action, pick_id)로 파싱한다."""
    action, _, raw_id = data.partition(":")
    if action not in ("arm", "disarm") or not raw_id.isdigit():
        return None
    return action, int(raw_id)


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = DbDep) -> JSONResponse:
    """텔레그램 callback_query를 받아 무장/무장해제를 수행한다.

    텔레그램 재시도를 막기 위해 어떤 경우에도 200을 반환한다(인증 실패는 403).
    """
    settings = get_settings()

    # (1) secret_token 검증 — 미설정이거나 불일치면 거부(fail-closed).
    secret = settings.telegram_webhook_secret
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or header != secret:
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    try:
        update: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _OK

    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return _OK  # callback_query 외 업데이트는 무시

    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    # (2) chat 화이트리스트 — 설정된 chat_id에서 온 콜백만 처리.
    if str(chat_id) != str(settings.telegram_chat_id):
        logger.warning("텔레그램 웹훅: 허용되지 않은 chat_id=%s 무시", chat_id)
        return _OK

    tg = get_telegram_notifier()
    callback_id = callback.get("id", "")
    data = str(callback.get("data") or "")
    parsed = _parse_action(data)
    if parsed is None:
        tg.answer_callback(callback_id, "알 수 없는 동작입니다.")
        return _OK

    action, pick_id = parsed
    try:
        item = arm_pick(pick_id, db) if action == "arm" else disarm_pick(pick_id, db)
    except Exception as exc:  # noqa: BLE001 — HTTPException 등 사유를 그대로 노출
        detail = getattr(exc, "detail", None) or str(exc)
        tg.answer_callback(callback_id, f"실패: {detail}")
        return _OK

    label = "무장됨 🔫" if action == "arm" else "무장해제됨 🛑"
    tg.answer_callback(callback_id, label)
    message_id = message.get("message_id")
    if message_id is not None:
        tg.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"<b>{item.name}</b> (<code>{item.ticker}</code>)\n"
                f"매수 {item.buy_price} / 목표 {item.target_price} / 손절 {item.stop_price}\n"
                f"➡️ <b>{item.state}</b> ({label})"
            ),
        )
    return _OK
