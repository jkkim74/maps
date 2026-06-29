"""scripts/normalize_pick_tick_prices.py 테스트."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick


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


def _seed(factory, **kw) -> int:
    base = {
        "ref_date": datetime.date(2026, 6, 25),
        "ticker": "035720", "name": "카카오", "market": "KOSPI", "source": "analyze",
        "buy_price": 54912, "target_price": 61137, "stop_price": 49988,
        "state": "WATCH",
    }
    base.update(kw)
    with factory() as s:
        pick = AnalysisPick(**base)
        s.add(pick)
        s.commit()
        s.refresh(pick)
        return pick.id


def test_apply_snaps_prices(factory, monkeypatch) -> None:
    pid = _seed(factory)
    monkeypatch.setattr("scripts.normalize_pick_tick_prices.SessionLocal", factory)
    from scripts.normalize_pick_tick_prices import normalize

    assert normalize(apply=True) == 1
    with factory() as s:
        p = s.get(AnalysisPick, pid)
    assert p.buy_price == 55000    # 올림
    assert p.target_price == 61100  # 최근접
    assert p.stop_price == 50000    # 최근접


def test_dry_run_does_not_write(factory, monkeypatch) -> None:
    pid = _seed(factory)
    monkeypatch.setattr("scripts.normalize_pick_tick_prices.SessionLocal", factory)
    from scripts.normalize_pick_tick_prices import normalize

    assert normalize(apply=False) == 1
    with factory() as s:
        p = s.get(AnalysisPick, pid)
    assert p.buy_price == 54912  # 미반영


def test_protected_states_skipped(factory, monkeypatch) -> None:
    pid = _seed(factory, state="BOUGHT")
    monkeypatch.setattr("scripts.normalize_pick_tick_prices.SessionLocal", factory)
    from scripts.normalize_pick_tick_prices import normalize

    assert normalize(apply=True) == 0  # BOUGHT은 건너뜀
    with factory() as s:
        p = s.get(AnalysisPick, pid)
    assert p.target_price == 61137  # 변경 없음


def test_include_protected_normalizes_bought(factory, monkeypatch) -> None:
    pid = _seed(factory, state="BOUGHT")
    monkeypatch.setattr("scripts.normalize_pick_tick_prices.SessionLocal", factory)
    from scripts.normalize_pick_tick_prices import normalize

    assert normalize(apply=True, include_protected=True) == 1
    with factory() as s:
        p = s.get(AnalysisPick, pid)
    assert p.target_price == 61100  # BOUGHT도 스냅됨


def test_already_on_grid_no_change(factory, monkeypatch) -> None:
    _seed(factory, ticker="005930", buy_price=70000, target_price=80000, stop_price=66000)
    monkeypatch.setattr("scripts.normalize_pick_tick_prices.SessionLocal", factory)
    from scripts.normalize_pick_tick_prices import normalize

    assert normalize(apply=True) == 0  # 이미 호가 그리드 위
