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
