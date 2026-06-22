"""모의투자 안전 가드·헬스체크·리스크 배선 테스트.

- describe_trading_mode / real_trading_unconfirmed (가드레일, 항목 5/6)
- /broker-health 엔드포인트 (항목 7)
- _make_risk_manager 노출 한도 배선 (항목 13)
"""

from __future__ import annotations

from maps.common.settings import (
    MapsSettings,
    describe_trading_mode,
    real_trading_unconfirmed,
)
from maps.execution.mock_broker import MockBroker
from maps.ops.scheduler import OperationalPipeline


# ── 가드레일: 트레이딩 모드 판별 ────────────────────────────────────────────────

def test_describe_trading_mode_variants() -> None:
    assert "MOCK" in describe_trading_mode(MapsSettings(maps_broker_mode="mock"))

    disabled = MapsSettings(maps_broker_mode="kis", maps_live_trading_enabled=False)
    assert "비활성" in describe_trading_mode(disabled)

    paper = MapsSettings(
        maps_broker_mode="kis", maps_live_trading_enabled=True, kis_real_trading=False
    )
    assert "PAPER" in describe_trading_mode(paper)

    real = MapsSettings(
        maps_broker_mode="kis", maps_live_trading_enabled=True, kis_real_trading=True
    )
    assert "REAL" in describe_trading_mode(real)


def test_real_trading_unconfirmed_only_when_real_live_and_unconfirmed() -> None:
    # paper(real=false)는 절대 막지 않는다
    paper = MapsSettings(
        maps_broker_mode="kis", maps_live_trading_enabled=True, kis_real_trading=False
    )
    assert real_trading_unconfirmed(paper) is False

    # real + live + 미확인 → 위험(기동 거부 대상)
    real_unconfirmed = MapsSettings(
        maps_broker_mode="kis",
        maps_live_trading_enabled=True,
        kis_real_trading=True,
        maps_confirm_real_trading=False,
    )
    assert real_trading_unconfirmed(real_unconfirmed) is True

    # real + live + 명시 확인 → 허용
    real_confirmed = MapsSettings(
        maps_broker_mode="kis",
        maps_live_trading_enabled=True,
        kis_real_trading=True,
        maps_confirm_real_trading=True,
    )
    assert real_trading_unconfirmed(real_confirmed) is False

    # real=true지만 주문 비활성(live=false) → 주문 안 나가므로 막지 않음
    real_not_live = MapsSettings(
        maps_broker_mode="kis",
        maps_live_trading_enabled=False,
        kis_real_trading=True,
    )
    assert real_trading_unconfirmed(real_not_live) is False


# ── 헬스체크 엔드포인트 ─────────────────────────────────────────────────────────

def test_broker_health_mock_ok(monkeypatch) -> None:
    from maps.api import ops_config

    monkeypatch.setattr(ops_config, "get_settings", lambda: MapsSettings(maps_broker_mode="mock"))
    resp = ops_config.get_broker_health()
    assert resp.ok is True
    assert resp.broker_mode == "mock"
    assert resp.cash is not None
    assert resp.error is None


def test_broker_health_reports_error_without_raising(monkeypatch) -> None:
    from maps.api import ops_config
    from maps.common.exceptions import BrokerAdapterError
    from maps.execution import broker_adapter

    # 브로커 연결 실패를 모사 — 엔드포인트는 예외 대신 ok=false로 안전 반환해야 한다.
    monkeypatch.setattr(
        ops_config, "get_settings", lambda: MapsSettings(maps_broker_mode="kis")
    )

    def _boom(mode=None, **kwargs):
        raise BrokerAdapterError("KIS keys missing")

    monkeypatch.setattr(broker_adapter, "get_broker", _boom)
    resp = ops_config.get_broker_health()
    assert resp.ok is False
    assert resp.error is not None
    assert "KIS keys missing" in resp.error


# ── 리스크 배선 (항목 13) ───────────────────────────────────────────────────────

def test_make_risk_manager_wires_exposure_limits() -> None:
    settings = MapsSettings(
        maps_sector_exposure_limit_enabled=True,
        maps_theme_exposure_limit_enabled=True,
        maps_max_sector_exposure=0.20,
        maps_max_theme_exposure=0.30,
        maps_min_cash_ratio_weak=0.40,
    )
    pipeline = OperationalPipeline(settings=settings)
    rm = pipeline._make_risk_manager(MockBroker(), db=None)
    cfg = rm._cfg
    assert cfg.sector_exposure_limit_enabled is True
    assert cfg.theme_exposure_limit_enabled is True
    assert cfg.sector_exposure_limit == 0.20
    assert cfg.theme_exposure_limit == 0.30
    assert cfg.min_cash_ratio_weak == 0.40
    # 기존 한도도 유지
    assert cfg.position_size_limit == settings.max_single_exposure
