"""Index acknowledgement diagnostics must never disclose response payloads."""

import datetime as dt
import json
import logging
import sys

import pytest

from maps.limit_up.runtime import KISIntradayRuntime


def runtime() -> KISIntradayRuntime:
    """Create only the state needed for control frames, which produce no ticks."""
    result = object.__new__(KISIntradayRuntime)
    result.monotonic = lambda: 0.0
    result.wall_now = lambda: dt.datetime.now(dt.timezone.utc)
    result._feed_tasks = set()
    return result


@pytest.mark.parametrize("code,status", [("0", "success"), ("1", "failure"),
                                          (None, "unknown"), (["secret-code"], "unknown")])
async def test_index_ack_logs_only_fixed_status(caplog, code, status) -> None:
    """Even error text and unusual result values cannot introduce secret output."""
    raw = json.dumps({"header": {"tr_id": "H0UPCNT0", "approval_key": "secret-approval"},
                      "body": {"rt_cd": code, "msg1": "secret-message",
                               "output": {"key": "secret-key", "iv": "secret-iv"}}})
    with caplog.at_level(logging.INFO, logger="maps.limit_up.runtime"):
        assert await runtime().dispatch_message_async(raw) == 0
    assert caplog.messages == [f"코스닥 지수 WS 구독 응답: status={status}"]
    assert "secret" not in caplog.text


@pytest.mark.parametrize("body", [None, [], "secret-body"])
async def test_index_ack_malformed_body_is_unknown(caplog, body) -> None:
    with caplog.at_level(logging.INFO, logger="maps.limit_up.runtime"):
        assert await runtime().dispatch_message_async(json.dumps(
            {"header": {"tr_id": "H0UPCNT0"}, "body": body})) == 0
    assert caplog.messages == ["코스닥 지수 WS 구독 응답: status=unknown"]


async def test_index_name_in_other_response_is_not_an_index_ack(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="maps.limit_up.runtime"):
        assert await runtime().dispatch_message_async(json.dumps({
            "header": {"tr_id": "H0STCNT0"},
            "body": {"msg1": "H0UPCNT0 secret-message"}})) == 0
    assert not caplog.messages


@pytest.mark.parametrize("raw", [
    '{"H0UPCNT0": "secret-invalid"',
    '{"H0UPCNT0": ' + '1' * 5000 + '}',
    "excessive-nesting",
], ids=["truncated", "oversized-integer", "excessive-nesting"])
async def test_malformed_control_frame_has_payload_free_diagnostic(caplog, raw) -> None:
    if raw == "excessive-nesting":
        depth = sys.getrecursionlimit() + 100
        raw = '{"H0UPCNT0": ' + '[' * depth + '"secret-nested"' + ']' * depth + '}'
    with caplog.at_level(logging.INFO, logger="maps.limit_up.runtime"):
        assert await runtime().dispatch_message_async(raw) == 0
    # JSON implementations have different nesting limits. A deep but accepted
    # object without an index header is ignored; rejection has a fixed warning.
    assert caplog.messages in ([], ["KIS control response has invalid JSON"])
    assert "secret" not in caplog.text


@pytest.mark.parametrize("error", [ValueError, RecursionError])
async def test_json_decoder_limits_do_not_disconnect_feed(caplog, monkeypatch, error) -> None:
    """Exercise decoder limits independently of platform-specific JSON limits."""
    def reject(_raw: str) -> None:
        raise error("secret-parser-detail")

    monkeypatch.setattr("maps.limit_up.runtime.json.loads", reject)
    with caplog.at_level(logging.INFO, logger="maps.limit_up.runtime"):
        assert await runtime().dispatch_message_async('{"header":{}}') == 0
    assert caplog.messages == ["KIS control response has invalid JSON"]
    assert "secret" not in caplog.text
