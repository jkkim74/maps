"""Operational notification tests."""

from __future__ import annotations

from typing import Any

from maps.common.settings import MapsSettings
from maps.ops.notifications import Notification, SlackNotifier


class FakeResponse:
    status_code = 200


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse()


def test_slack_disabled_is_noop() -> None:
    http = FakeHttp()
    notifier = SlackNotifier(MapsSettings(slack_webhook_url=""), http=http)

    sent = notifier.send(Notification(level="INFO", title="hello", message="world"))

    assert sent is False
    assert http.calls == []


def test_slack_payload_contains_fields() -> None:
    http = FakeHttp()
    notifier = SlackNotifier(MapsSettings(slack_webhook_url="https://hooks.example/slack"), http=http)

    sent = notifier.send(
        Notification(
            level="ERROR",
            title="MAPS job failed",
            message="boom",
            fields={"job": "broker_sync"},
        )
    )

    assert sent is True
    assert http.calls[0]["url"] == "https://hooks.example/slack"
    payload = http.calls[0]["json"]
    assert payload["text"].startswith("[ERROR]")
    assert payload["attachments"][0]["fields"][0]["title"] == "job"
