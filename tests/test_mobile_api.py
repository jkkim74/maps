from __future__ import annotations

from fastapi.testclient import TestClient

from maps.api.schemas import (
    AlertItem,
    DashboardResponse,
    LiveMonitorResponse,
    OrdersResponse,
    RiskResponse,
    SlippageStats,
)


def test_mobile_summary_combines_operational_endpoints(monkeypatch) -> None:
    from main import app
    from maps.api import mobile

    alert = AlertItem(level="WARN", message="Check risk limit", timestamp="09:00")
    monkeypatch.setattr(
        mobile,
        "get_dashboard",
        lambda db: DashboardResponse(
            total_assets=10_000_000,
            total_assets_mom_pct=0.01,
            ytd_cagr=0.05,
            current_mdd=-0.02,
            sharpe_1y=1.1,
            active_strategies=2,
            live_count=1,
            mock_count=1,
            last_updated="2026-05-31",
            contributions=[],
            alerts=[alert],
        ),
    )
    monkeypatch.setattr(
        mobile,
        "get_orders",
        lambda db: OrdersResponse(
            auto_order_active=True,
            pending=[],
            fills_today=[],
            expired=[],
            slippage=SlippageStats(
                large_cap_actual=0.0,
                large_cap_assumed=0.0005,
                mid_small_actual=0.0,
                mid_small_assumed=0.0015,
            ),
        ),
    )
    monkeypatch.setattr(
        mobile,
        "get_risk",
        lambda db: RiskResponse(
            short_term_risk=0.0,
            short_term_limit=0.015,
            long_term_risk=0.1,
            long_term_limit=1.0,
            max_exposure_pct=0.0,
            position_count=0,
            gauges=[],
            holdings=[],
        ),
    )
    monkeypatch.setattr(
        mobile,
        "get_live_monitor",
        lambda db: LiveMonitorResponse(
            auto_response_active=True,
            pending_approval_count=0,
            pending_release_count=0,
            actual_mdd=0.0,
            large_slip_actual=0.0005,
            mid_small_slip_actual=0.0015,
            consec_failures={},
            pending_approvals=[],
            pending_releases=[],
            recent_events=[],
        ),
    )

    response = TestClient(app).get("/api/v1/mobile/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["dashboard"]["total_assets"] == 10_000_000
    assert data["orders"]["auto_order_active"] is True
    assert data["alerts"][0]["message"] == "Check risk limit"
