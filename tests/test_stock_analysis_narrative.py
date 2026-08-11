"""The visible stock narrative must use the executable analysis prices."""

from __future__ import annotations

import json


def test_narrative_prompt_uses_authoritative_trade_plan(monkeypatch) -> None:
    import boto3

    from maps.stock_analysis.analyzer import stream_llm_analysis

    captured = {}

    class FakeClient:
        def invoke_model_with_response_stream(self, **kwargs):
            captured["body"] = json.loads(kwargs["body"])
            event = {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "완료"},
            }
            return {"body": [{"chunk": {"bytes": json.dumps(event).encode()}}]}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeClient())
    plan = {
        "recommendation": "WATCH",
        "entries": [70_000, 68_000, 66_000],
        "target": 78_000,
        "stop": 64_000,
        "rationale": "가격 대기",
        "source": "AI",
        "message": None,
    }

    chunks = list(
        stream_llm_analysis(
            {"종목명": "삼성전자", "종목코드": "005930", "기술적분석": {}},
            aws_access_key_id="key",
            aws_secret_access_key="secret",
            trade_plan=plan,
        )
    )
    prompt = captured["body"]["messages"][0]["content"]

    assert chunks == ["완료"]
    for price in ("70000", "68000", "66000", "78000", "64000"):
        assert price in prompt
    assert "다른 가격을 만들지 마세요" in prompt
