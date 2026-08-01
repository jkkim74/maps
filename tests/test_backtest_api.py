"""SCR-07 백테스트 콘솔 API — 실행 설정 패널 실측값 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import BacktestRunLog, HistoricalOHLCV


@pytest.fixture
def client():
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = factory()
    for day, close in ((dt.date(2019, 1, 2), 100.0), (dt.date(2026, 7, 31), 120.0)):
        db.add(HistoricalOHLCV(
            ticker="005930",
            date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000,
        ))
    db.add(BacktestRunLog(
        run_id="bt_pullback_v3_seed0001",
        strategy_id="pullback_v3",
        net_cagr=0.12,
        mdd=-0.18,
        sharpe=0.8,
        trade_count=42,
        ticker_count=30,
    ))
    db.commit()
    db.close()

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_backtest_panel_reflects_actual_db_range_and_cost(client) -> None:
    """실행 설정 패널 값은 하드코딩이 아니라 DB 실측·비용 상수여야 한다."""
    response = client.get("/api/v1/backtest")

    assert response.status_code == 200
    body = response.json()
    assert body["data_start"] == "2019-01-02"
    assert body["data_end"] == "2026-07-31"
    assert body["max_tickers"] == 30
    assert "0.18%" in body["cost_summary"]  # 매도 거래세
    assert "0.015%" in body["cost_summary"]  # 편도 수수료


def test_recent_runs_carry_cagr_and_trade_count(client) -> None:
    """최근 실행 목록은 backtest_run_log 실측값을 반환해야 한다.

    과거에는 WFA 결과를 원천으로 써서 net_cagr/trade_count가 항상 None이었다.
    """
    body = client.get("/api/v1/backtest").json()

    assert len(body["recent_runs"]) == 1
    run = body["recent_runs"][0]
    assert run["run_id"] == "bt_pullback_v3_seed0001"
    assert run["net_cagr"] == pytest.approx(0.12)
    assert run["trade_count"] == 42
    assert run["mdd"] == pytest.approx(-0.18)


def test_run_persists_result_to_log(client, monkeypatch) -> None:
    """POST /run 결과가 저장돼 다음 목록 조회에 나타나야 한다."""
    import pandas as pd

    from maps.api import backtest as bt
    from maps.backtest.engine import BacktestResult

    df = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=500),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1_000,
    })
    monkeypatch.setattr(
        bt.HistoricalOHLCVRepository, "list_tickers_with_history",
        lambda self, min_bars: ["005930"],
    )
    monkeypatch.setattr(
        bt.HistoricalOHLCVRepository, "to_dataframe", lambda self, ticker: df,
    )
    fake = BacktestResult(
        strategy_id="pullback_v3",
        start_date=dt.date(2020, 1, 1),
        end_date=dt.date(2021, 6, 1),
        initial_capital=100_000_000,
        final_value=110_000_000,
        cagr=0.07,
        mdd=-0.09,
        sharpe=0.6,
        total_trades=17,
    )
    monkeypatch.setattr(bt.BacktestEngine, "run", lambda self, s, p, d: fake)

    response = client.post("/api/v1/backtest/run", json={"strategy_id": "pullback_v3"})

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    listed = client.get("/api/v1/backtest").json()["recent_runs"]
    assert listed[0]["run_id"] == run_id  # 최신순 첫 행
    assert listed[0]["net_cagr"] == pytest.approx(0.07)
    assert listed[0]["trade_count"] == 17
