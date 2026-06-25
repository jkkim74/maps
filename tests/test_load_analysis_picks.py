"""scripts/load_analysis_picks.py 로더 테스트."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick
from scripts.load_analysis_picks import _build_rationale, _load_payload, load_picks

_TODAY = datetime.date(2026, 6, 25)
_PLAN = [
    {"ticker": "005930", "name": "삼성전자", "entry": 70000, "target": 80000,
     "stop_loss": 66000, "risk_reward": 2.5, "position_size_pct": 3.2, "max_loss_pct": 1.8},
    {"ticker": "000660", "name": "SK하이닉스", "entry": 180000, "target": 210000,
     "stop_loss": 168000, "risk_reward": 2.5, "position_size_pct": 2.5, "max_loss_pct": 1.6},
]


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _load(plan, factory, **kw):
    return load_picks(
        plan, regime=kw.get("regime"), context=kw.get("context"),
        source=kw.get("source", "analyze"), ref_date=_TODAY,
        dry_run=kw.get("dry_run", False), session_factory=factory,
    )


def test_build_rationale() -> None:
    assert _build_rationale(_PLAN[0]) == "R:R 2.5 · size 3.2% · maxloss 1.8%"
    assert _build_rationale({"ticker": "x"}) is None


def test_load_payload_trade_plan_wrapper(tmp_path) -> None:
    import json
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"trade_plan": _PLAN}), encoding="utf-8")
    assert len(_load_payload(str(f))) == 2


def test_load_payload_bare_array(tmp_path) -> None:
    import json
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_PLAN), encoding="utf-8")
    assert len(_load_payload(str(f))) == 2


def test_dry_run_does_not_write(factory) -> None:
    result = _load(_PLAN, factory, dry_run=True)
    assert len(result) == 2
    assert all("id" not in r for r in result)
    with factory() as s:
        assert s.query(AnalysisPick).count() == 0


def test_load_writes_and_maps_fields(factory) -> None:
    result = _load(_PLAN, factory, regime="mixed", context="pullback_v3 / 반도체")
    assert all("id" in r for r in result)
    with factory() as s:
        rows = s.query(AnalysisPick).order_by(AnalysisPick.ticker).all()
        assert len(rows) == 2
        samsung = next(r for r in rows if r.ticker == "005930")
        assert samsung.buy_price == 70000
        assert samsung.target_price == 80000
        assert samsung.stop_price == 66000
        assert samsung.regime == "mixed"
        assert samsung.strategy_context == "pullback_v3 / 반도체"
        assert samsung.source == "analyze"
        assert samsung.state == "WATCH"
        assert "R:R 2.5" in samsung.rationale


def test_skips_items_missing_prices(factory) -> None:
    plan = [
        {"ticker": "005930", "name": "삼성전자", "entry": 70000, "target": 80000, "stop_loss": 66000},
        {"ticker": "000660", "name": "결손", "entry": 180000},  # target/stop 누락 → 제외
        {"ticker": "", "name": "빈티커"},                        # ticker 누락 → 제외
    ]
    result = _load(plan, factory)
    assert [r["ticker"] for r in result] == ["005930"]
    with factory() as s:
        assert s.query(AnalysisPick).count() == 1
