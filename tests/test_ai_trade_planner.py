"""Structured AI trade-plan validation and Bedrock adapter tests."""

from __future__ import annotations

import json

import pytest

from maps.common.exceptions import AIScoringResponseError


def _buy_payload(**overrides):
    payload = {
        "recommendation": "BUY",
        "entries": [70_000, 68_000, 66_000],
        "target": 80_000,
        "stop": 63_000,
        "rationale": "추세 지지 구간의 분할 진입 계획",
    }
    payload.update(overrides)
    return payload


def _facts():
    from maps.ai.trade_planner import StockTradeFacts

    return StockTradeFacts(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        ref_date="2026-08-10",
        current_price=71_000,
        high_52w=82_000,
        low_52w=52_000,
        ma20=70_500,
        ma60=67_000,
        ma120=64_000,
        rsi14=57.4,
        macd=180.2,
        macd_signal=120.5,
        per=14.2,
        pbr=1.3,
        bps=54_300,
    )


def test_trade_plan_accepts_only_ordered_buy_prices() -> None:
    from maps.ai.trade_planner import AITradePlan

    plan = AITradePlan.from_payload(_buy_payload())

    assert plan.entries == (70_000, 68_000, 66_000)
    assert plan.target == 80_000
    assert plan.stop == 63_000


@pytest.mark.parametrize("recommendation", ["WATCH", "SELL"])
def test_non_buy_trade_plan_rejects_order_prices(recommendation: str) -> None:
    from maps.ai.trade_planner import AITradePlan

    with pytest.raises(AIScoringResponseError):
        AITradePlan.from_payload(_buy_payload(recommendation=recommendation))


@pytest.mark.parametrize(
    "overrides",
    [
        {"entries": [68_000, 70_000, 66_000]},
        {"target": 69_000},
        {"stop": 68_500},
        {"entries": [70_000, 68_000, float("nan")]},
    ],
)
def test_buy_trade_plan_rejects_invalid_price_boundaries(overrides) -> None:
    from maps.ai.trade_planner import AITradePlan

    with pytest.raises(AIScoringResponseError):
        AITradePlan.from_payload(_buy_payload(**overrides))


def test_trade_planner_request_uses_supported_json_schema() -> None:
    from maps.ai.trade_planner import AITradePlanner

    planner = AITradePlanner(
        aws_access_key_id="key",
        aws_secret_access_key="secret",
    )

    body = planner._request_body(_facts())
    encoded_schema = json.dumps(body["output_config"]["format"]["schema"])

    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "minimum" not in encoded_schema
    assert "maximum" not in encoded_schema
    assert "minLength" not in encoded_schema
    assert json.loads(body["messages"][0]["content"])["ticker"] == "005930"


def test_trade_planner_parses_valid_bedrock_response(monkeypatch) -> None:
    from maps.ai.trade_planner import AITradePlanner

    planner = AITradePlanner(
        aws_access_key_id="key",
        aws_secret_access_key="secret",
    )
    monkeypatch.setattr(
        planner,
        "_invoke",
        lambda body: {
            "content": [{"type": "text", "text": json.dumps(_buy_payload())}],
        },
    )

    plan = planner.plan(_facts())

    assert plan.recommendation == "BUY"
    assert plan.entries == (70_000, 68_000, 66_000)

