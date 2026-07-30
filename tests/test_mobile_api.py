from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from maps.api.schemas import (
    AlertItem,
    DashboardResponse,
    FillItem,
    HoldingItem,
    LiveMonitorResponse,
    OrdersResponse,
    RiskResponse,
    SlippageStats,
)
from maps.common.models import MarketRegimeLog


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
    # 장세 블록이 응답에 포함된다(로그 없으면 unknown).
    assert data["regime"]["regime"] == "unknown"


def test_mobile_summary_carries_holding_and_fill_display_fields(monkeypatch) -> None:
    """앱이 보유 카드와 체결 카드를 그리는 데 필요한 필드가 전부 실려야 한다.

    이 필드들이 빠지면 화면이 티커만 찍거나(보유), 전략을 'broker'로 수량을
    공백으로 찍는다(체결) — 사용자가 '보유가 안 나온다'로 신고한 지점이다.
    """
    from main import app
    from maps.api import mobile

    monkeypatch.setattr(
        mobile,
        "get_dashboard",
        lambda db: DashboardResponse(
            total_assets=85_000_000,
            total_assets_mom_pct=0.0,
            ytd_cagr=0.0,
            current_mdd=0.0,
            sharpe_1y=0.0,
            active_strategies=1,
            live_count=0,
            mock_count=1,
            last_updated="2026-07-30",
            contributions=[],
            alerts=[],
        ),
    )
    monkeypatch.setattr(
        mobile,
        "get_orders",
        lambda db: OrdersResponse(
            auto_order_active=True,
            pending=[],
            fills_today=[FillItem(
                order_id="kis-089860",
                ticker="089860",
                name="롯데렌탈",
                side="buy",
                fill_price=36_600.0,
                fill_qty=113,
                status="filled",
                created_at="2026-07-29T23:55:32",
                strategy_id="pullback_v3",
                qty=113,
            )],
            expired=[],
            slippage=SlippageStats(
                large_cap_actual=None,
                large_cap_assumed=0.0005,
                mid_small_actual=None,
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
            max_exposure_pct=0.0498,
            position_count=1,
            gauges=[],
            holdings=[HoldingItem(
                ticker="089860",
                name="롯데렌탈",
                strategy_id="pullback_v3",
                entry_price=36_600.0,
                current_price=37_600.0,
                pnl_pct=0.0273,
                exposure_pct=0.0498,
                stop_price=32_416.0,
                quantity=113,
                market_value=4_248_800.0,
            )],
            broker_status="ok",
            active_kill_count=2,
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

    data = TestClient(app).get("/api/v1/mobile/summary").json()

    holding = data["risk"]["holdings"][0]
    assert holding["name"] == "롯데렌탈"
    assert holding["quantity"] == 113
    assert holding["market_value"] == 4_248_800.0
    assert holding["stop_price"] == 32_416.0
    assert data["risk"]["broker_status"] == "ok"
    assert data["risk"]["active_kill_count"] == 2

    fill = data["orders"]["fills_today"][0]
    assert fill["strategy_id"] == "pullback_v3"
    assert fill["qty"] == 113


def test_mobile_regime_maps_latest_log_row(db) -> None:
    from maps.api.mobile import _mobile_regime

    db.add(MarketRegimeLog(
        ref_date=dt.date.today(),
        raw_regime="mixed",
        applied_regime="strong",
        weekly_trend="pass",
        vol_regime="high",
        floor_applied=True,
        breadth_pct=0.42,
        up_count=6,
        total_assets=8,
        source="candidate_generation",
    ))
    db.commit()

    regime = _mobile_regime(db)

    assert regime.regime == "strong"          # applied_regime (히스테리시스 적용값)
    assert regime.weekly_trend == "pass"
    assert regime.vol_regime == "high"
    assert regime.floor_applied is True
    assert regime.up_count == 6 and regime.total_assets == 8
    assert regime.ref_date == dt.date.today().isoformat()


def test_mobile_regime_unknown_when_no_log(db) -> None:
    from maps.api.mobile import _mobile_regime

    regime = _mobile_regime(db)

    assert regime.regime == "unknown"
    assert regime.weekly_trend == "unknown"
    assert regime.up_count is None
