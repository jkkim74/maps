"""SCR-07 백테스트 콘솔 API — 기간·대상·판정 테스트."""

from __future__ import annotations

import datetime as dt
import sys
import types

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import BacktestRunLog, HistoricalOHLCV, SecurityMetadata


def _make_memory_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _ohlcv(ticker: str, day: dt.date, close: float, volume: int) -> HistoricalOHLCV:
    return HistoricalOHLCV(
        ticker=ticker, date=day, open=close, high=close, low=close, close=close, volume=volume
    )


@pytest.fixture
def client():
    from main import app
    from maps.api.deps import get_db

    engine, factory = _make_memory_factory()

    db = factory()
    for day, close in ((dt.date(2019, 1, 2), 100.0), (dt.date(2026, 7, 31), 120.0)):
        db.add(_ohlcv("005930", day, close, 1_000))
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


def _patch_data(monkeypatch, tickers=("005930",)):
    """유니버스·데이터 로딩을 신규 시그니처로 대체한다."""
    from maps.api import backtest as bt

    df = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=500),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1_000,
    })
    monkeypatch.setattr(
        bt.HistoricalOHLCVRepository, "top_tickers_by_trading_value",
        lambda self, *, start=None, end=None, min_bars=1, limit=30, pool=None: list(tickers),
    )
    monkeypatch.setattr(
        bt.HistoricalOHLCVRepository, "to_dataframe",
        lambda self, ticker, start=None, end=None: df,
    )
    return bt


def _fake_result(**overrides):
    import numpy as np

    from maps.backtest.engine import BacktestResult

    # numpy 스칼라 그대로 — psycopg2가 np.float64를 못 받아 운영에서 INSERT가
    # 죽었던 회귀 재현 (라우터가 float/int로 강제 변환해야 한다).
    base = dict(
        strategy_id="pullback_v3",
        start_date=dt.date(2020, 1, 1),
        end_date=dt.date(2021, 6, 1),
        initial_capital=100_000_000,
        final_value=110_000_000,
        cagr=np.float64(0.07),
        mdd=np.float64(-0.09),
        sharpe=np.float64(0.6),
        total_trades=17,
    )
    base.update(overrides)
    return BacktestResult(**base)


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
    assert body["universe_options"]["markets"] == ["KOSPI", "KOSDAQ"]
    assert body["universe_options"]["indices"] == ["kosdaq150", "kospi200"]


def test_recent_runs_carry_cagr_and_trade_count(client) -> None:
    """최근 실행 목록은 backtest_run_log 실측값을 반환해야 한다.

    과거에는 WFA 결과를 원천으로 써서 net_cagr/trade_count가 항상 None이었다.
    구 실행 행(기간·판정 컬럼 NULL)도 오류 없이 verdict=None으로 나와야 한다.
    """
    body = client.get("/api/v1/backtest").json()

    assert len(body["recent_runs"]) == 1
    run = body["recent_runs"][0]
    assert run["run_id"] == "bt_pullback_v3_seed0001"
    assert run["source"] == "manual"
    assert run["net_cagr"] == pytest.approx(0.12)
    assert run["trade_count"] == 42
    assert run["mdd"] == pytest.approx(-0.18)
    assert run["verdict"] is None
    assert run["start_date"] is None


def test_recent_runs_can_filter_scheduled_validation_source(client) -> None:
    """최근 실행 목록은 수동·자동 출처를 구분해 조회할 수 있어야 한다."""
    from main import app
    from maps.api.deps import get_db

    override = app.dependency_overrides[get_db]
    session_generator = override()
    db = next(session_generator)
    try:
        db.add(BacktestRunLog(
            run_id="val_20260805_123456789abc",
            strategy_id="pullback_v3",
            source="scheduled_validation",
            status="done",
            net_cagr=0.08,
            trade_count=31,
        ))
        db.commit()
    finally:
        session_generator.close()

    automatic = client.get(
        "/api/v1/backtest?source=scheduled_validation"
    ).json()["recent_runs"]
    manual = client.get("/api/v1/backtest?source=manual").json()["recent_runs"]

    assert [row["run_id"] for row in automatic] == ["val_20260805_123456789abc"]
    assert automatic[0]["source"] == "scheduled_validation"
    assert [row["run_id"] for row in manual] == ["bt_pullback_v3_seed0001"]
    assert client.get("/api/v1/backtest?source=unknown").status_code == 422


def test_run_persists_result_to_log(client, monkeypatch) -> None:
    """POST /run 결과가 저장돼 다음 목록 조회에 나타나야 한다."""
    bt = _patch_data(monkeypatch)
    monkeypatch.setattr(bt.BacktestEngine, "run", lambda self, s, p, d: _fake_result())

    response = client.post("/api/v1/backtest/run", json={"strategy_id": "pullback_v3"})

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    listed = client.get("/api/v1/backtest").json()["recent_runs"]
    assert listed[0]["run_id"] == run_id  # 최신순 첫 행
    assert listed[0]["net_cagr"] == pytest.approx(0.07)
    assert listed[0]["trade_count"] == 17


def test_run_with_period_persists_dates_and_verdict(client, monkeypatch) -> None:
    """기간 지정 실행은 기간·판정·근거를 저장하고 목록에 반환해야 한다."""
    bt = _patch_data(monkeypatch)
    monkeypatch.setattr(
        bt.BacktestEngine, "run", lambda self, s, p, d: _fake_result(total_trades=40)
    )

    response = client.post("/api/v1/backtest/run", json={
        "strategy_id": "pullback_v3", "start": "2020-01-01", "end": "2021-06-01",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["start_date"] == "2020-01-01"
    assert body["end_date"] == "2021-06-01"
    assert body["verdict"] == "PASS"  # sharpe 0.6≥0.3, |mdd| 0.09≤0.18, 거래 40≥30, cagr>0
    assert all(c["passed"] for c in body["verdict_criteria"])
    assert len(body["verdict_criteria"]) == 4

    listed = client.get("/api/v1/backtest").json()["recent_runs"][0]
    assert listed["start_date"] == "2020-01-01"
    assert listed["verdict"] == "PASS"
    assert listed["stats"]["tickers"] == ["005930"]


def test_verdict_fail_records_breached_criterion(client, monkeypatch) -> None:
    """판정 근거 없이 PASS/FAIL만 내려오면 안 된다 — 위반 기준이 breakdown에 남아야 한다."""
    import numpy as np

    bt = _patch_data(monkeypatch)
    monkeypatch.setattr(
        bt.BacktestEngine, "run",
        lambda self, s, p, d: _fake_result(mdd=np.float64(-0.50), total_trades=40),
    )

    body = client.post("/api/v1/backtest/run", json={"strategy_id": "pullback_v3"}).json()

    assert body["verdict"] == "FAIL"  # pullback_short 한도 0.18 < 0.50
    breached = [c for c in body["verdict_criteria"] if not c["passed"]]
    assert [c["criterion"] for c in breached] == ["worst_mdd"]
    assert breached[0]["threshold"] == pytest.approx(0.18)


def test_run_rejects_inverted_period(client) -> None:
    """start가 end보다 뒤면 400이어야 한다."""
    response = client.post("/api/v1/backtest/run", json={
        "strategy_id": "pullback_v3", "start": "2022-01-01", "end": "2020-01-01",
    })

    assert response.status_code == 400
    assert "start" in response.json()["detail"]


def test_portfolio_mode_runs_replay_engine(client, monkeypatch) -> None:
    """mode=portfolio는 PortfolioReplayEngine 경로로 실행되고 연도별 지표를 저장한다."""
    from maps.backtest.portfolio_replay import PortfolioResult

    bt = _patch_data(monkeypatch)
    dates = [d.date() for d in pd.date_range("2020-01-31", periods=24, freq="ME")]
    equity = [100_000_000 * (1.01 ** i) for i in range(24)]
    fake = PortfolioResult(
        strategy_id="pullback_v3", fill_mode="next_open",
        initial_capital=100_000_000, final_value=equity[-1],
        cagr=0.12, mdd=-0.12, sharpe=0.9, total_trades=45, win_rate=0.55,
        equity_curve=equity, dates=dates,
    )
    monkeypatch.setattr(bt.PortfolioReplayEngine, "run", lambda self, s, p, d: fake)

    body = client.post("/api/v1/backtest/run", json={
        "strategy_id": "pullback_v3", "mode": "portfolio",
    }).json()

    assert body["mode"] == "portfolio"
    assert body["verdict"] == "PASS"
    assert body["stats"]["win_rate"] == pytest.approx(0.55)
    assert set(body["stats"]["yearly_returns"]) == {"2020", "2021"}
    assert body["stats"]["positive_month_ratio"] == pytest.approx(1.0)


def test_universe_ranked_by_trading_value() -> None:
    """알파벳순 30개 회귀 방지 — 기간 내 거래대금 내림차순 + min_bars + pool 필터."""
    from maps.data.ohlcv_repo import HistoricalOHLCVRepository

    engine, factory = _make_memory_factory()
    db = factory()
    try:
        window = [dt.date(2020, 1, 2), dt.date(2020, 1, 3)]
        # AAA: 거래대금 낮음 / BBB: 높음 / CCC: 가장 높지만 봉 1개 / DDD: 기간 밖만 높음
        for day in window:
            db.add(_ohlcv("AAA", day, 100.0, 10))
            db.add(_ohlcv("BBB", day, 100.0, 1_000))
        db.add(_ohlcv("CCC", window[0], 100.0, 100_000))
        db.add(_ohlcv("DDD", dt.date(2021, 1, 4), 100.0, 100_000))
        for day in window:
            db.add(_ohlcv("DDD", day, 100.0, 1))
        db.commit()

        repo = HistoricalOHLCVRepository(db)
        top = repo.top_tickers_by_trading_value(
            start=window[0], end=window[-1], min_bars=2, limit=30
        )
        assert top == ["BBB", "AAA", "DDD"]  # CCC는 min_bars 미달, DDD는 기간 밖 거래량 미산입

        pooled = repo.top_tickers_by_trading_value(
            start=window[0], end=window[-1], min_bars=2, limit=30, pool=["AAA", "DDD"]
        )
        assert pooled == ["AAA", "DDD"]  # 풀 밖 고거래대금(BBB)은 제외
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resolve_universe_pool_filters() -> None:
    """market/sector/theme/recent_ipo 풀이 security_metadata 기준으로 걸러져야 한다."""
    from maps.api import backtest as bt
    from maps.api.schemas import BacktestRunRequest

    engine, factory = _make_memory_factory()
    db = factory()
    try:
        db.add(SecurityMetadata(
            ticker="005930", name="삼성전자", market="KOSPI", security_type="STOCK",
            sector="반도체", theme="HBM", listing_date=dt.date(1975, 6, 11),
        ))
        db.add(SecurityMetadata(
            ticker="900001", name="새내기", market="KOSDAQ", security_type="STOCK",
            sector="바이오", listing_date=dt.date(2026, 7, 1),
        ))
        db.add(SecurityMetadata(
            ticker="123456", name="ETF상품", market="ETF", security_type="ETF",
        ))
        db.commit()

        def resolve(**kwargs):
            return bt._resolve_universe_pool(db, BacktestRunRequest(**kwargs))

        pool, label = resolve(universe="market", universe_arg="KOSPI")
        assert pool == ["005930"] and label == "market:KOSPI"

        pool, _ = resolve(universe="sector", universe_arg="바이오")
        assert pool == ["900001"]

        pool, _ = resolve(universe="theme", universe_arg="HBM")
        assert pool == ["005930"]

        pool, label = resolve(universe="recent_ipo", universe_arg="90", end=dt.date(2026, 8, 1))
        assert pool == ["900001"] and label == "recent_ipo:90"

        pool, label = resolve(universe="custom", tickers=["005930", "005930", " 000660 "])
        assert pool == ["005930", "000660"] and label == "custom:2"  # dedupe·trim

        with pytest.raises(HTTPException) as exc:
            resolve(universe="custom", tickers=[f"{i:06d}" for i in range(31)])
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            resolve(universe="galaxy")
        assert exc.value.status_code == 400
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resolve_universe_index_uses_login_guard(monkeypatch) -> None:
    """지수 구성종목 조회는 pykrx 호출 전 KRX 로그인 가드를 반드시 거쳐야 한다."""
    from maps.api import backtest as bt
    from maps.api.schemas import BacktestRunRequest

    guard_calls: list[int] = []
    monkeypatch.setattr(bt, "ensure_krx_login_guard", lambda: guard_calls.append(1))
    fake_stock = types.SimpleNamespace(
        get_index_portfolio_deposit_file=lambda code: ["005930", "000660"]
    )
    monkeypatch.setitem(sys.modules, "pykrx", types.SimpleNamespace(stock=fake_stock))
    bt._INDEX_CACHE.clear()

    pool, label = bt._resolve_universe_pool(
        None, BacktestRunRequest(universe="index", universe_arg="kospi200")
    )

    assert pool == ["005930", "000660"]
    assert label == "index:kospi200"
    assert guard_calls == [1]

    # 같은 날 재호출은 캐시 사용 — 가드·pykrx 재호출 없음
    bt._resolve_universe_pool(None, BacktestRunRequest(universe="index", universe_arg="kospi200"))
    assert guard_calls == [1]
    bt._INDEX_CACHE.clear()


def test_extended_stats_payoff_and_win_rate() -> None:
    """손익비(평균이익/평균손실)·가중 승률 계산이 정확해야 한다."""
    from maps.api.backtest import _extended_stats_per_ticker
    from maps.backtest.engine import TradeRecord

    def trade(pnl: float) -> TradeRecord:
        return TradeRecord(
            ticker="T", entry_date=dt.date(2020, 1, 2), exit_date=dt.date(2020, 1, 10),
            entry_price=100.0, exit_price=100.0 + pnl, qty=1,
            gross_pnl=pnl, net_pnl=pnl, exit_reason="signal",
        )

    r1 = _fake_result(total_trades=2, win_rate=0.5)
    r1.trade_list = [trade(30.0), trade(-10.0)]
    r2 = _fake_result(total_trades=2, win_rate=1.0)
    r2.trade_list = [trade(10.0), trade(10.0)]

    stats = _extended_stats_per_ticker([r1, r2], ["T1", "T2"])

    # 가중 승률 = (0.5×2 + 1.0×2) / 4 = 0.75
    assert stats["win_rate"] == pytest.approx(0.75)
    # 평균이익 (30+10+10)/3 = 16.67, 평균손실 10 → 손익비 1.67
    assert stats["payoff_ratio"] == pytest.approx(1.67, abs=0.01)
    assert stats["yearly_returns"] is None
    assert stats["tickers"] == ["T1", "T2"]


def test_to_dataframe_sets_ticker_as_index_name() -> None:
    """엔진이 index.name을 TradeRecord.ticker로 쓴다 — 미설정이면 전부 "unknown"."""
    from maps.data.ohlcv_repo import HistoricalOHLCVRepository

    engine, factory = _make_memory_factory()
    db = factory()
    try:
        db.add(_ohlcv("005930", dt.date(2020, 1, 2), 100.0, 10))
        db.commit()

        df = HistoricalOHLCVRepository(db).to_dataframe("005930")
        assert df.index.name == "005930"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
