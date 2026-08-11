"""Stock-analysis structured trade-plan API tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

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


def _analysis_result():
    return {
        "종목명": "삼성전자",
        "종목코드": "005930",
        "기술적분석": {
            "기준일": "2026-08-10",
            "현재가": 71_000,
            "52주_고가": 82_000,
            "52주_저가": 52_000,
            "RSI14": 57.4,
            "MACD": 180.2,
            "MACD_signal": 120.5,
            "이동평균선": {"MA20": 70_500, "MA60": 67_000, "MA120": 64_000},
        },
        "밸류에이션": {"PER": 14.2, "PBR": 1.3, "BPS": 54_300},
    }


def _sse_events(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_trade_plan_keeps_normalized_watch_prices(monkeypatch) -> None:
    import maps.api.stock_analysis as api
    from maps.ai.trade_planner import AITradePlan

    class WatchPlanner:
        is_configured = True

        def plan(self, facts):
            return AITradePlan.from_payload(
                {
                    "recommendation": "WATCH",
                    "entries": [70_101, 68_101, 66_101],
                    "target": 78_049,
                    "stop": 63_951,
                    "rationale": "가격 대기",
                }
            )

    monkeypatch.setattr(
        api.AITradePlanner,
        "from_settings",
        classmethod(lambda cls: WatchPlanner()),
    )

    response = TestClient(app).post(
        "/api/v1/stock-analysis/trade-plan",
        json=_request_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "recommendation": "WATCH",
        "entries": [70_200.0, 68_200.0, 66_200.0],
        "target": 78_000.0,
        "stop": 64_000.0,
        "rationale": "가격 대기",
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


def test_analysis_stream_reuses_one_trade_plan_for_narrative_and_final_event(
    monkeypatch,
) -> None:
    import maps.api.stock_analysis as api
    import maps.stock_analysis.analyzer as analyzer
    from maps.api.schemas import StockTradePlanResponse

    plan = StockTradePlanResponse(
        recommendation="WATCH",
        entries=[70_000, 68_000, 66_000],
        target=78_000,
        stop=64_000,
        rationale="가격 대기",
        source="AI",
    )
    captured = {}
    saved = []
    monkeypatch.setattr(analyzer, "analyze", lambda *args, **kwargs: _analysis_result())
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: SimpleNamespace(
            dart_api_key="",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
            aws_region="us-east-1",
            aws_bedrock_model_id="model",
        ),
    )

    def fake_generate(request):
        captured["request"] = request.model_dump()
        return plan

    def fake_narrative(data, **kwargs):
        captured["narrative_plan"] = kwargs["trade_plan"]
        yield "분석"

    monkeypatch.setattr(api, "generate_trade_plan", fake_generate)
    monkeypatch.setattr(analyzer, "stream_llm_analysis", fake_narrative)
    monkeypatch.setattr(
        api,
        "save_analysis_history_with_new_session",
        lambda result, narrative, trade_plan: (
            saved.append((result, narrative, trade_plan)) or 41
        ),
    )

    response = TestClient(app).get("/api/v1/stock-analysis/stream?ticker=005930")
    events = _sse_events(response)
    final_event = next(event for event in events if event.get("done"))

    assert response.status_code == 200
    assert captured["request"] == _request_payload()
    assert captured["narrative_plan"] == plan.model_dump(mode="json")
    assert final_event["trade_plan"] == plan.model_dump(mode="json")
    assert saved == [(_analysis_result(), "분석", plan.model_dump(mode="json"))]
    assert final_event["history_id"] == 41
    assert final_event["history_error"] is None


def test_analysis_stream_finishes_with_manual_plan_when_generation_fails(
    monkeypatch,
) -> None:
    import maps.api.stock_analysis as api
    import maps.stock_analysis.analyzer as analyzer
    from maps.api.schemas import StockTradePlanResponse

    monkeypatch.setattr(analyzer, "analyze", lambda *args, **kwargs: _analysis_result())
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: SimpleNamespace(
            dart_api_key="",
            aws_access_key_id="",
            aws_secret_access_key="",
            aws_region="us-east-1",
            aws_bedrock_model_id="model",
        ),
    )
    monkeypatch.setattr(
        api,
        "generate_trade_plan",
        lambda request: StockTradePlanResponse(
            recommendation="WATCH",
            rationale="",
            source="MANUAL_REQUIRED",
            message="AI 매매계획을 사용할 수 없어 수동 입력이 필요합니다.",
        ),
    )
    monkeypatch.setattr(
        api,
        "save_analysis_history_with_new_session",
        lambda result, narrative, trade_plan: 42,
    )

    response = TestClient(app).get("/api/v1/stock-analysis/stream?ticker=005930")
    final_event = next(event for event in _sse_events(response) if event.get("done"))

    assert response.status_code == 200
    assert final_event["trade_plan"]["source"] == "MANUAL_REQUIRED"
    assert final_event["trade_plan"]["entries"] is None
    assert final_event["trade_plan"]["target"] is None
    assert final_event["trade_plan"]["stop"] is None


def test_analysis_stream_reports_history_failure_without_losing_result(monkeypatch) -> None:
    """이력 저장 실패가 완료된 분석 자체를 숨기면 안 된다."""
    import maps.api.stock_analysis as api
    import maps.stock_analysis.analyzer as analyzer

    monkeypatch.setattr(analyzer, "analyze", lambda *args, **kwargs: _analysis_result())
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: SimpleNamespace(
            dart_api_key="",
            aws_access_key_id="",
            aws_secret_access_key="",
            aws_region="us-east-1",
            aws_bedrock_model_id="model",
        ),
    )
    monkeypatch.setattr(
        api,
        "save_analysis_history_with_new_session",
        lambda *args: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    response = TestClient(app).get("/api/v1/stock-analysis/stream?ticker=005930")
    final_event = next(event for event in _sse_events(response) if event.get("done"))

    assert final_event["data"] == _analysis_result()
    assert final_event["history_id"] is None
    assert final_event["history_error"] == "db down"


def test_non_streaming_analysis_persists_snapshot_once(monkeypatch) -> None:
    """단일 응답 분석도 history_id를 응답에만 붙이고 원본에는 넣지 않는다."""
    import maps.api.stock_analysis as api
    import maps.stock_analysis.analyzer as analyzer
    from maps.api.schemas import StockTradePlanResponse

    plan = StockTradePlanResponse(
        recommendation="WATCH",
        rationale="대기",
        source="MANUAL_REQUIRED",
    )
    saved = []
    monkeypatch.setattr(analyzer, "analyze", lambda *args, **kwargs: _analysis_result())
    monkeypatch.setattr(api, "generate_trade_plan", lambda request: plan)

    def fake_save(db, *, result, narrative, trade_plan):
        saved.append((result, narrative, trade_plan))
        return SimpleNamespace(id=17)

    monkeypatch.setattr(api, "save_analysis_history", fake_save)

    response = TestClient(app).post(
        "/api/v1/stock-analysis/analyze", json={"ticker": "005930"}
    )

    assert response.status_code == 200
    assert response.json()["history_id"] == 17
    assert "history_id" not in saved[0][0]
    assert saved[0][1] == ""
    assert saved[0][2] == plan.model_dump(mode="json")
