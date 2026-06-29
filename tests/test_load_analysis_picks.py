"""scripts/load_analysis_picks.py 로더 테스트."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick, AnalysisRun
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
        dry_run=kw.get("dry_run", False),
        status=kw.get("status", "completed"), note=kw.get("note"),
        error_message=kw.get("error_message"),
        candidates_count=kw.get("candidates_count"),
        session_factory=factory,
    )


def test_build_rationale() -> None:
    assert _build_rationale(_PLAN[0]) == "R:R 2.5 · size 3.2% · maxloss 1.8%"
    assert _build_rationale({"ticker": "x"}) is None


def test_load_picks_snaps_prices_to_krx_tick(factory) -> None:
    # trade-planner(LLM)가 호가단위 어긋난 값을 줘도 저장 시 KRX 호가로 정규화한다.
    plan = [{"ticker": "035720", "name": "카카오", "market": "KOSPI",
             "entry": 54912, "target": 61137, "stop_loss": 49988}]
    _load(plan, factory)
    with factory() as s:
        pick = s.query(AnalysisPick).filter_by(ticker="035720").one()
    assert pick.buy_price == 55000   # 54912 → 매수가 올림(호가 100원)
    assert pick.target_price == 61100  # 61137 → 최근접 61,100
    assert pick.stop_price == 50000   # 49988 → 최근접 50,000


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


def test_load_payload_empty_allowed(tmp_path) -> None:
    f = tmp_path / "empty.json"
    f.write_text("", encoding="utf-8")
    assert _load_payload(str(f), allow_empty=True) == []
    with pytest.raises(SystemExit):
        _load_payload(str(f), allow_empty=False)


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


def test_load_records_analysis_run(factory) -> None:
    """픽 적재 시 analysis_run 실행기록 1건(status=completed, picks_count=2)을 함께 남긴다."""
    _load(_PLAN, factory, regime="mixed", context="pullback_v3 / 반도체",
          candidates_count=14, note="정상 적재")
    with factory() as s:
        runs = s.query(AnalysisRun).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "completed"
        assert run.picks_count == 2
        assert run.regime == "mixed"
        assert run.strategy_context == "pullback_v3 / 반도체"
        assert run.candidates_count == 14
        assert run.note == "정상 적재"
        assert run.ref_date == _TODAY


def test_empty_plan_records_zero_pick_run(factory) -> None:
    """빈 plan이어도 analysis_run에 picks_count=0 실행기록을 남기고 픽은 0건이다."""
    result = _load([], factory, regime="weak", context="screen-test",
                   candidates_count=14, note="R:R 게이트 전량 탈락")
    assert result == []
    with factory() as s:
        assert s.query(AnalysisPick).count() == 0
        runs = s.query(AnalysisRun).all()
        assert len(runs) == 1
        assert runs[0].status == "completed"
        assert runs[0].picks_count == 0
        assert runs[0].candidates_count == 14


def test_failed_status_records_run(factory) -> None:
    """status=failed 로 호출하면 실패 실행기록을 남긴다(에러 메시지 포함)."""
    _load([], factory, status="failed", error_message="claude exit=124")
    with factory() as s:
        run = s.query(AnalysisRun).one()
        assert run.status == "failed"
        assert run.picks_count == 0
        assert run.error_message == "claude exit=124"


def test_dry_run_records_no_run(factory) -> None:
    """dry_run이면 analysis_pick·analysis_run 둘 다 기록하지 않는다."""
    _load(_PLAN, factory, dry_run=True)
    with factory() as s:
        assert s.query(AnalysisPick).count() == 0
        assert s.query(AnalysisRun).count() == 0


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
