"""후보 퍼널 재설계 회귀 테스트.

퍼널을 "유동성·추세 상위 종목 전량 저장"에서 "이 전략이 오늘 사겠다고 한 종목"으로
바꾸면서 생긴 두 가지 함정을 고정한다:

1. 신호 계산이 후보 생성과 주문 시점 두 곳으로 갈라지면 조용히 어긋난다(손절가 전례).
2. 전략마다 유니버스를 다시 도는 구조에서 종목별 OHLCV를 8번씩 읽는다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.common.db import Base
from maps.common.models import HistoricalOHLCV
from maps.common.settings import MapsSettings
from maps.ops.scheduler import OperationalPipeline


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _seed_ohlcv(db, ticker: str, ref_date: dt.date, bars: int = 260) -> None:
    """추세가 살아 있는 OHLCV 를 넣어 전략이 실제로 신호를 낼 수 있게 한다."""
    base = 10_000
    for i in range(bars):
        day = ref_date - dt.timedelta(days=bars - i)
        # 완만한 상승 + 주기적 눌림 — 전략별로 진입/미진입이 갈리도록
        price = base + i * 40 - (300 if i % 17 == 0 else 0)
        db.add(
            HistoricalOHLCV(
                ticker=ticker,
                date=day,
                open=price,
                high=price + 120,
                low=price - 90,
                close=price,
                volume=400_000 + (i % 7) * 20_000,
            )
        )
    db.commit()


def test_signal_from_frame_matches_db_path() -> None:
    """프레임 기반 신호 계산이 DB 기반 경로와 같은 결과를 낸다.

    후보 생성(프레임)과 주문 시점(DB)이 서로 다른 값을 내면 백테스트와 실거래가
    체계적으로 어긋난다. 두 경로가 한 구현을 공유하는지 고정한다.
    """
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    db = factory()
    try:
        _seed_ohlcv(db, "AAAA", ref_date)

        from maps.data.ohlcv_repo import HistoricalOHLCVRepository

        frame = HistoricalOHLCVRepository(db).to_dataframe("AAAA", end=ref_date)

        for strategy_id in ("pullback_v3", "donchian_v2", "ath_breakout_v1"):
            from_frame = OperationalPipeline._signal_from_frame(strategy_id, frame)
            from_db = OperationalPipeline._latest_strategy_signal(
                db, ticker="AAAA", strategy_id=strategy_id, ref_date=ref_date
            )
            assert (from_frame is None) == (from_db is None), strategy_id
            if from_frame is None:
                continue
            assert from_frame.entry_signal == from_db.entry_signal, strategy_id
            assert from_frame.exit_signal == from_db.exit_signal, strategy_id
            assert from_frame.close == from_db.close, strategy_id
            assert from_frame.atr14 == from_db.atr14, strategy_id
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_signal_from_frame_returns_none_for_unknown_strategy() -> None:
    """미등록 전략 ID 는 예외가 아니라 None 이다 (기존 _latest_strategy_signal 규약)."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    db = factory()
    try:
        _seed_ohlcv(db, "AAAA", ref_date, bars=120)
        from maps.data.ohlcv_repo import HistoricalOHLCVRepository

        frame = HistoricalOHLCVRepository(db).to_dataframe("AAAA", end=ref_date)
        assert OperationalPipeline._signal_from_frame("no_such_strategy", frame) is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_rising(db, ticker: str, ref_date: dt.date, bars: int = 200) -> None:
    """단조 상승 — donchian_v2 진입 3조건(채널 돌파·ROC>0·MA40 상승)을 모두 만족한다."""
    for i in range(bars):
        price = 10_000 + i * 100
        db.add(
            HistoricalOHLCV(
                ticker=ticker,
                date=ref_date - dt.timedelta(days=bars - i),
                open=price, high=price + 50, low=price - 50, close=price, volume=500_000,
            )
        )
    db.commit()


def _seed_flat(db, ticker: str, ref_date: dt.date, bars: int = 200) -> None:
    """완전 횡보 — 채널 돌파(>)도 ROC>0도 MA40 상승도 성립하지 않아 진입 신호가 없다."""
    for i in range(bars):
        db.add(
            HistoricalOHLCV(
                ticker=ticker,
                date=ref_date - dt.timedelta(days=bars - i),
                open=10_000, high=10_050, low=9_950, close=10_000, volume=500_000,
            )
        )
    db.commit()


def _security(ticker: str, ref_date: dt.date, turnover: float):
    from maps.data.security_repo import Security

    return Security(
        ticker=ticker,
        name=f"종목{ticker}",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: turnover},
    )


def test_signal_gate_stores_signals_and_top_n_only() -> None:
    """저장 대상 = 신호 있는 종목 전수 ∪ 상위 N. 나머지는 저장하지 않는다.

    지금은 유동성·추세만 보고 유니버스를 전량 저장해 하루 1만 행이 쌓인다. 신호를 후보
    생성 시점으로 끌어와 "이 전략이 오늘 사겠다고 한 종목"만 남긴다.
    """
    from maps.common.models import CandidateSnapshot

    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    db = factory()
    try:
        _seed_rising(db, "SIGNAL", ref_date)   # 신호 O, 유동성 최하
        _seed_flat(db, "TOPLIQ", ref_date)     # 신호 X, 유동성 최상 → 상위 N
        _seed_flat(db, "MIDLIQ", ref_date)     # 신호 X, 상위 N 밖 → 저장 제외
    finally:
        db.close()

    pipeline = OperationalPipeline(
        settings=MapsSettings(
            maps_broker_mode="mock",
            maps_data_provider="mock",
            maps_candidate_snapshot_top_n=1,
        ),
        session_factory=factory,
    )
    universe = [
        _security("SIGNAL", ref_date, 100_000_000),
        _security("TOPLIQ", ref_date, 10_000_000_000),
        _security("MIDLIQ", ref_date, 5_000_000_000),
    ]

    db2 = factory()
    try:
        contexts = pipeline._build_ticker_contexts(db2, universe, ref_date)
        pipeline._save_candidate_snapshot(
            db2, ref_date, "donchian_v2", universe, contexts=contexts
        )
        rows = {r.ticker: r for r in db2.query(CandidateSnapshot).all()}

        assert "SIGNAL" in rows, "신호 있는 종목은 상위 N 밖이어도 저장돼야 한다"
        assert rows["SIGNAL"].entry_signal is True
        assert "TOPLIQ" in rows, "상위 N 종목은 신호가 없어도 관측용으로 저장한다"
        assert rows["TOPLIQ"].entry_signal is False
        assert "MIDLIQ" not in rows, "신호도 없고 상위 N 밖이면 저장하지 않는다"
    finally:
        db2.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_candidate_generation_logs_stage_counts(caplog) -> None:
    """0건이 됐을 때 어느 단계에서 끊겼는지 로그로 구분된다.

    6/23~ 약세장에서 후보 0건이던 전례가 있고 그때는 국면 차단이 원인이었다. 게이트가
    하나 더 늘었으므로 유니버스/신호/상위N 중 어디서 줄었는지 남기지 않으면 또
    "수집이 고장 났나"부터 뒤지게 된다.
    """
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    db = factory()
    try:
        _seed_flat(db, "FLAT1", ref_date)
        _seed_flat(db, "FLAT2", ref_date)
    finally:
        db.close()

    pipeline = OperationalPipeline(
        settings=MapsSettings(
            maps_broker_mode="mock",
            maps_data_provider="mock",
            maps_candidate_snapshot_top_n=1,
        ),
        session_factory=factory,
    )
    universe = [
        _security("FLAT1", ref_date, 10_000_000_000),
        _security("FLAT2", ref_date, 5_000_000_000),
    ]

    db2 = factory()
    try:
        contexts = pipeline._build_ticker_contexts(db2, universe, ref_date)
        with caplog.at_level("INFO", logger="maps.ops.scheduler"):
            pipeline._save_candidate_snapshot(
                db2, ref_date, "donchian_v2", universe, contexts=contexts
            )
    finally:
        db2.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

    line = next(
        (r.getMessage() for r in caplog.records if "후보 저장" in r.getMessage()), None
    )
    assert line is not None, "단계별 카운터 로그가 없다"
    assert "universe=2" in line
    assert "signals=0" in line
    assert "stored=1" in line
    assert "dropped=1" in line


def test_generate_candidates_details_carry_stage_counts() -> None:
    """잡 기록(details)에도 단계별 카운터가 남는다 — 로그가 로테이션돼도 사후 추적 가능해야 한다."""
    engine, factory = _session_factory()
    pipeline = OperationalPipeline(
        settings=MapsSettings(
            maps_broker_mode="mock",
            maps_data_provider="mock",
            maps_live_trading_enabled=False,
            maps_market_regime_override="strong",
        ),
        session_factory=factory,
    )
    try:
        pipeline.collect_data()
        run = pipeline.generate_candidates()

        assert run.status == "success"
        assert run.details["universe_size"] >= 0
        assert run.details["signal_count"] >= 0
        assert run.details["dropped_count"] >= 0
        # 저장 = 신호 + 관측(상위 N). 버려진 것까지 더하면 유니버스 × 전략 수가 된다.
        assert (
            run.details["saved_count"] + run.details["dropped_count"]
            == run.details["universe_size"] * len(run.details["strategies_saved"])
        )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_ticker_ohlcv_is_loaded_once_across_strategies() -> None:
    """유니버스 N종목 × 전략 8개에서 to_dataframe 호출이 N회여야 한다 (8N회 아님).

    전략마다 유니버스를 다시 도는 구조에서는 종목별 OHLCV 로드가 전략 수만큼 곱해진다.
    운영 실측 기준 하루 약 10,080회 — 컨텍스트를 루프 밖에서 한 번만 만들면 1,260회다.
    """
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    tickers = ["AAAA", "BBBB", "CCCC"]

    db = factory()
    try:
        for ticker in tickers:
            _seed_ohlcv(db, ticker, ref_date, bars=150)
    finally:
        db.close()

    from maps.data import ohlcv_repo as repo_module

    calls: list[str] = []
    original = repo_module.HistoricalOHLCVRepository.to_dataframe

    def counting(self, ticker, **kwargs):
        calls.append(ticker)
        return original(self, ticker, **kwargs)

    repo_module.HistoricalOHLCVRepository.to_dataframe = counting
    try:
        from maps.data.security_repo import Security

        universe = [
            Security(
                ticker=ticker,
                name=f"종목{ticker}",
                market="KOSPI",
                security_type="STOCK",
                turnover_cache={ref_date: 1_000_000_000},
            )
            for ticker in tickers
        ]
        pipeline = OperationalPipeline(
            settings=MapsSettings(maps_broker_mode="mock", maps_data_provider="mock"),
            session_factory=factory,
        )
        db2 = factory()
        try:
            contexts = pipeline._build_ticker_contexts(db2, universe, ref_date)
            assert set(contexts) == set(tickers)
            for strategy_id in ("pullback_v3", "donchian_v2", "ath_breakout_v1"):
                pipeline._save_candidate_snapshot(
                    db2, ref_date, strategy_id, universe, contexts=contexts
                )
        finally:
            db2.close()
    finally:
        repo_module.HistoricalOHLCVRepository.to_dataframe = original
        Base.metadata.drop_all(engine)
        engine.dispose()

    assert sorted(calls) == sorted(tickers), (
        f"종목당 1회여야 하는데 {len(calls)}회 호출됨: {calls}"
    )
