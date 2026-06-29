"""Operational notifications.

Slack is optional.  When SLACK_WEBHOOK_URL is empty, notification calls are
no-ops so local development and tests stay quiet.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from maps.common.settings import MapsSettings, get_settings

logger = logging.getLogger(__name__)


class HttpPoster(Protocol):
    def post(self, url: str, **kwargs: Any): ...


@dataclass(frozen=True)
class Notification:
    level: str
    title: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)


class SlackNotifier:
    """Small Slack incoming-webhook client."""

    def __init__(
        self,
        settings: MapsSettings | None = None,
        *,
        http: HttpPoster | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http or requests
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._settings.slack_webhook_url)

    def send(self, notification: Notification) -> bool:
        if not self.enabled:
            logger.debug("Slack disabled; notification skipped: %s", notification.title)
            return False

        payload = self._payload(notification)
        try:
            response = self._http.post(
                self._settings.slack_webhook_url,
                json=payload,
                timeout=self._timeout,
            )
            status_code = getattr(response, "status_code", 200)
            if status_code >= 400:
                logger.warning("Slack notification failed: HTTP %s", status_code)
                return False
            return True
        except Exception as exc:
            logger.warning("Slack notification failed: %s", exc)
            return False

    def send_job_failed(self, job_name: str, message: str, details: dict | None = None) -> bool:
        return self.send(
            Notification(
                level="ERROR",
                title=f"MAPS job failed: {job_name}",
                message=message,
                fields=details or {},
            )
        )

    def send_kill_switch(
        self,
        *,
        strategy_id: str,
        event_type: str,
        reason: str,
        detail: str = "",
        approved_by: str | None = None,
    ) -> bool:
        fields: dict[str, Any] = {
            "strategy_id": strategy_id,
            "event_type": event_type,
            "reason": reason,
        }
        if detail:
            fields["detail"] = detail
        if approved_by:
            fields["approved_by"] = approved_by
        level = "CRITICAL" if event_type == "trigger" else "WARN"
        return self.send(
            Notification(
                level=level,
                title=f"MAPS Kill Switch {event_type}: {strategy_id}",
                message=detail or reason,
                fields=fields,
            )
        )

    def send_order_alert(
        self,
        *,
        level: str,
        strategy_id: str,
        ticker: str,
        message: str,
        fields: dict[str, Any] | None = None,
    ) -> bool:
        payload_fields = {"strategy_id": strategy_id, "ticker": ticker, **(fields or {})}
        return self.send(
            Notification(
                level=level,
                title=f"MAPS order alert: {strategy_id}/{ticker}",
                message=message,
                fields=payload_fields,
            )
        )

    def _payload(self, notification: Notification) -> dict[str, Any]:
        color = {
            "INFO": "#60a5fa",
            "WARN": "#f59e0b",
            "ERROR": "#ef4444",
            "CRITICAL": "#dc2626",
        }.get(notification.level.upper(), "#94a3b8")
        fields = [
            {"title": key, "value": str(value), "short": len(str(value)) <= 30}
            for key, value in notification.fields.items()
        ]
        fields.append({"title": "time_utc", "value": dt.datetime.now(dt.timezone.utc).isoformat(), "short": False})
        return {
            "text": f"[{notification.level.upper()}] {notification.title}",
            "attachments": [
                {
                    "color": color,
                    "title": notification.title,
                    "text": notification.message,
                    "fields": fields,
                }
            ],
        }


_notifier: SlackNotifier | None = None


def get_notifier() -> SlackNotifier:
    global _notifier
    if _notifier is None:
        _notifier = SlackNotifier()
    return _notifier


def _esc(text: object) -> str:
    """텔레그램 HTML parse_mode 용 최소 이스케이프."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    """Telegram Bot API 클라이언트.

    `telegram_bot_token` 또는 `telegram_chat_id` 가 비어있으면 모든 호출이 no-op이라
    로컬 개발·테스트에서는 조용히 넘어간다(SlackNotifier와 동일 패턴).
    """

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        settings: MapsSettings | None = None,
        *,
        http: HttpPoster | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http or requests
        self._timeout = timeout

    @property
    def _nonprod_blocked(self) -> bool:
        """운영(MAPS_ENV=production)이 아니면서 명시적 허용도 없으면 실발송을 막는다.

        로컬/테스트가 운영 봇 토큰이 든 .env로 돌아가도 운영 텔레그램 채팅에
        더미 픽이 새지 않게 하는 안전 가드. 비운영에서 일부러 보내려면
        MAPS_TELEGRAM_ALLOW_NONPROD=true 를 설정한다.
        """
        s = self._settings
        return s.maps_env != "production" and not s.maps_telegram_allow_nonprod

    @property
    def enabled(self) -> bool:
        if self._nonprod_blocked:
            return False
        return bool(self._settings.telegram_bot_token and self._settings.telegram_chat_id)

    def _call(self, method: str, payload: dict[str, Any]) -> bool:
        """Bot API 메서드를 호출한다. 실패해도 파이프라인을 막지 않는다."""
        # 환경 가드: enabled를 우회하는 경로(answer_callback, edit_message_text,
        # 명시적 chat_id를 준 send_message)까지 단일 HTTP 통로에서 차단한다.
        if self._nonprod_blocked:
            logger.warning(
                "Telegram %s blocked: MAPS_ENV=%s is not production "
                "(set MAPS_TELEGRAM_ALLOW_NONPROD=true to override)",
                method, self._settings.maps_env,
            )
            return False
        if not self._settings.telegram_bot_token:
            logger.debug("Telegram disabled; %s skipped", method)
            return False
        url = self._API.format(token=self._settings.telegram_bot_token, method=method)
        try:
            response = self._http.post(url, json=payload, timeout=self._timeout)
            status_code = getattr(response, "status_code", 200)
            if status_code >= 400:
                logger.warning("Telegram %s failed: HTTP %s", method, status_code)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram %s failed: %s", method, exc)
            return False

    def send_message(
        self,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        parse_mode: str = "HTML",
        chat_id: str | None = None,
    ) -> bool:
        """텍스트 메시지를 보낸다. buttons는 inline_keyboard 행 리스트."""
        if not self.enabled and chat_id is None:
            logger.debug("Telegram disabled; message skipped")
            return False
        payload: dict[str, Any] = {
            "chat_id": chat_id or self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self._call("sendMessage", payload)

    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        """인라인 버튼 콜백에 대한 토스트 응답."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._call("answerCallbackQuery", payload)

    def edit_message_text(
        self,
        *,
        chat_id: str | int,
        message_id: int,
        text: str,
        buttons: list[list[dict[str, str]]] | None = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """이미 보낸 메시지의 본문/버튼을 갱신한다(상태 반영)."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        payload["reply_markup"] = {"inline_keyboard": buttons or []}
        return self._call("editMessageText", payload)

    def send_analysis_picks(
        self,
        picks: list[dict[str, Any]],
        *,
        regime: str | None = None,
        context: str | None = None,
        ref_date: str | None = None,
    ) -> bool:
        """편입 종목을 요약 헤더 1건 + 종목별 메시지(무장/무장해제 버튼)로 푸시한다.

        `picks`의 각 항목은 load_analysis_picks 의 prepared dict
        (id, ticker, name, buy_price, target_price, stop_price)를 기대한다.
        """
        if not self.enabled:
            logger.debug("Telegram disabled; analysis picks skipped")
            return False
        if not picks:
            return False

        header_lines = [f"📈 <b>MAPS 편입 {len(picks)}종목</b>"]
        if ref_date:
            header_lines.append(f"기준일 {_esc(ref_date)}")
        meta = " · ".join(p for p in (regime, context) if p)
        if meta:
            header_lines.append(_esc(meta))
        ok = self.send_message("\n".join(header_lines))

        for pick in picks:
            pid = pick.get("id")
            lines = [
                f"<b>{_esc(pick.get('name') or pick.get('ticker'))}</b> "
                f"(<code>{_esc(pick.get('ticker'))}</code>)",
                f"매수 {_esc(pick.get('buy_price'))} / 목표 {_esc(pick.get('target_price'))} "
                f"/ 손절 {_esc(pick.get('stop_price'))}",
            ]
            buttons: list[list[dict[str, str]]] | None = None
            if pid is not None:
                buttons = [[
                    {"text": "🔫 무장", "callback_data": f"arm:{pid}"},
                    {"text": "🛑 무장해제", "callback_data": f"disarm:{pid}"},
                ]]
            ok = self.send_message("\n".join(lines), buttons=buttons) and ok
        return ok


_telegram_notifier: TelegramNotifier | None = None


def get_telegram_notifier() -> TelegramNotifier:
    global _telegram_notifier
    if _telegram_notifier is None:
        _telegram_notifier = TelegramNotifier()
    return _telegram_notifier
