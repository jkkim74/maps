"""Stock-analysis structured trade-plan API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def _request_payload():
    return {
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "ref_date": "2026-08-10",
        "current_price": 71_000,
        "high_52w": 82_000,
        "low_52w": 52_000,
        "ma20": 70_500,
        "ma60": 67_000,
        "ma120": 64_000,
        "rsi14": 57.4,
        "macd": 180.2,
        "macd_signal": 120.5,
        "per": 14.2,
        "pbr": 1.3,
        "bps": 54_300,
    }


def test_trade_plan_returns_manual_required_when_ai_is_unconfigured(monkeypatch) -> None:
    import maps.api.stock_analysis as api

    class UnconfiguredPlanner:
        is_configured = False

    monkeypatch.setattr(
        api.AITradePlanner,
        "from_settings",
        classmethod(lambda cls: UnconfiguredPlanner()),
    )

    response = TestClient(app).post(
        "/api/v1/stock-analysis/trade-plan",
        json=_request_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "recommendation": "WATCH",
        "entries": None,
        "target": None,
        "stop": None,
        "rationale": "",
        "source": "MANUAL_REQUIRED",
        "message": "AI 매매계획을 사용할 수 없어 수동 입력이 필요합니다.",
    }


def test_trade_plan_normalizes_valid_buy_prices_to_krx_ticks(monkeypatch) -> None:
    import maps.api.stock_analysis as api
    from maps.ai.trade_planner import AITradePlan

    class ValidPlanner:
        is_configured = True

        def plan(self, facts):
            return AITradePlan.from_payload(
                {
                    "recommendation": "BUY",
                    "entries": [70_101, 68_101, 66_101],
                    "target": 78_049,
                    "stop": 63_951,
                    "rationale": "분할 진입 후보",
                }
            )

    monkeypatch.setattr(
        api.AITradePlanner,
        "from_settings",
        classmethod(lambda cls: ValidPlanner()),
    )

    response = TestClient(app).post(
        "/api/v1/stock-analysis/trade-plan",
        json=_request_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "recommendation": "BUY",
        "entries": [70_200.0, 68_200.0, 66_200.0],
        "target": 78_000.0,
        "stop": 64_000.0,
        "rationale": "분할 진입 후보",
        "source": "AI",
        "message": None,
    }


def test_trade_plan_falls_back_when_provider_response_is_invalid(monkeypatch) -> None:
    import maps.api.stock_analysis as api
    from maps.common.exceptions import AIScoringResponseError

    class InvalidPlanner:
        is_configured = True

        def plan(self, facts):
            raise AIScoringResponseError("invalid order")

    monkeypatch.setattr(
        api.AITradePlanner,
        "from_settings",
        classmethod(lambda cls: InvalidPlanner()),
    )

    response = TestClient(app).post(
        "/api/v1/stock-analysis/trade-plan",
        json=_request_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "MANUAL_REQUIRED"
    assert body["entries"] is None
    assert body["target"] is None
    assert body["stop"] is None
