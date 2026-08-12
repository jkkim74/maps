from __future__ import annotations

import datetime as dt
import json
import urllib.request

from maps.common.settings import MapsSettings
from maps.market.feeds import _fetch_naver_headlines, _reconcile_news_counts


class _Response:
    """Small context-manager response used by the URL regression test."""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        """Return one same-day news item."""
        return json.dumps(
            {
                "items": [
                    {
                        "title": "국내 증시",
                        "description": "장 마감",
                        "originallink": "https://example.com/news/1",
                        "pubDate": "Wed, 12 Aug 2026 16:00:00 +0900",
                    }
                ]
            }
        ).encode()


def test_fetch_naver_headlines_uses_api_hub_endpoint_and_headers(monkeypatch) -> None:
    """New API HUB credentials must not be sent to the retired Developers endpoint."""
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        requests.append(request)
        assert timeout == 15
        return _Response()

    monkeypatch.setattr("maps.market.feeds.urllib.request.urlopen", fake_urlopen)
    settings = MapsSettings(
        naver_client_id="client-id",
        naver_client_secret="client-secret",
        maps_market_news_query_limit=10,
    )

    headlines = _fetch_naver_headlines(settings, dt.date(2026, 8, 12))

    assert len(requests) == 3
    assert len(headlines) == 1
    for request in requests:
        assert request.full_url.startswith(
            "https://naverapihub.apigw.ntruss.com/search/v1/news?"
        )
        assert request.get_header("X-ncp-apigw-api-key-id") == "client-id"
        assert request.get_header("X-ncp-apigw-api-key") == "client-secret"
        assert request.get_header("X-naver-client-id") is None


def test_reconcile_news_counts_preserves_proportions_and_exact_total() -> None:
    """Small model arithmetic drift must not discard an otherwise valid score."""
    result: dict[str, object] = {
        "positive_count": 72,
        "neutral_count": 15,
        "negative_count": 12,
    }

    _reconcile_news_counts(result, 100)

    assert result == {
        "positive_count": 73,
        "neutral_count": 15,
        "negative_count": 12,
    }
