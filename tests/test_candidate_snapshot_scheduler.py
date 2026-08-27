from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import CandidateSnapshot, OrderLog, PromotionHistory
from maps.common.settings import MapsSettings
from maps.data.security_repo import Security
from maps.execution.broker_adapter import OrderSide, OrderStatus
from maps.ops.scheduler import OperationalPipeline, StrategySignal, TickerContext


def _memory_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_order_candidates_empty_when_snapshot_stale() -> None:
    engine, factory = _memory_factory()
    db = factory()
    try:
        pipeline = OperationalPipeline(session_factory=factory)
        stale = dt.date.today() - dt.timedelta(days=30)
        db.add(CandidateSnapshot(
            ref_date=stale,
            strategy_id="donchian_v2",
            ticker="AAAA",
            name="AAAA",
            market="KOSPI",
            factor_score=90,
            trend_strength=80,
            ts_bucket="S5",
            final_score=95,
            weekly_pass=True,
        ))
        db.commit()

        # 직전 거래일보다 오래된 후보 → 신선도 가드로 빈 목록
        assert pipeline._order_candidates(db, dt.date.today()) == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_mock_candidate_strategy(db, ref_date: dt.date) -> None:
    """mock_candidate 단계 전략의 당일 후보 1건을 심는다."""
    db.add(CandidateSnapshot(
        ref_date=ref_date,
        strategy_id="donchian_v2",
        ticker="AAAA",
        name="AAAA",
        market="KOSPI",
        factor_score=90,
        trend_strength=80,
        ts_bucket="S5",
        final_score=95,
        weekly_pass=True,
    ))
    db.add(PromotionHistory(
        strategy_id="donchian_v2",
        from_stage="alert_only",
        to_stage="mock_candidate",
        tradeability_score=70.0,
        passed=True,
        evaluated_at=dt.datetime.now() - dt.timedelta(days=1),
    ))
    db.commit()


def test_mock_candidate_orders_on_paper_account_only() -> None:
    """mock_candidate 전략은 모의 계좌에서만 주문 후보가 된다 (mock_months 축적용)."""
    engine, factory = _memory_factory()
    db = factory()
    try:
        _seed_mock_candidate_strategy(db, dt.date.today())

        paper = OperationalPipeline(settings=MapsSettings(maps_broker_mode="mock"), session_factory=factory)
        assert [row.ticker for row in paper._order_candidates(db, dt.date.today())] == ["AAAA"]

        real = OperationalPipeline(
            settings=MapsSettings(maps_broker_mode="kis", kis_real_trading=True),
            session_factory=factory,
        )
        assert real._order_candidates(db, dt.date.today()) == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_ai_order_candidate(
    db,
    *,
    ticker: str,
    mode: str,
    rule_score: float,
    recommendation_score: float,
    ai_status: str = "SUCCESS",
) -> None:
    """Persist one AI-provenance candidate eligible by promotion stage."""
    db.add(
        CandidateSnapshot(
            ref_date=dt.date.today(),
            strategy_id="donchian_v2",
            ticker=ticker,
            name=ticker,
            market="KOSPI",
            factor_score=80,
            trend_strength=70,
            ts_bucket="S4",
            final_score=recommendation_score,
            rule_score=rule_score,
            recommendation_score=recommendation_score,
            score_source="AI",
            ai_scoring_mode=mode,
            ai_status=ai_status,
            weekly_pass=True,
        )
    )


def _promote_ai_test_strategy(db) -> None:
    """Promote the shared test strategy to paper-order eligibility."""
    db.add(
        PromotionHistory(
            strategy_id="donchian_v2",
            from_stage="alert_only",
            to_stage="mock_candidate",
            tradeability_score=70,
            passed=True,
            evaluated_at=dt.datetime.now(),
        )
    )
    db.commit()


def test_rerank_uses_rule_minimum_but_recommendation_order() -> None:
    """Rerank cannot change the rule gate but controls candidate ordering."""
    engine, factory = _memory_factory()
    db = factory()
    try:
        _seed_ai_order_candidate(
            db, ticker="A", mode="rerank", rule_score=11, recommendation_score=2
        )
        _seed_ai_order_candidate(
            db, ticker="B", mode="rerank", rule_score=12, recommendation_score=9
        )
        _promote_ai_test_strategy(db)
        pipeline = OperationalPipeline(
            settings=MapsSettings(maps_candidate_min_score=10),
            session_factory=factory,
        )

        assert [row.ticker for row in pipeline._order_candidates(db, dt.date.today())] == [
            "B",
            "A",
        ]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_rerank_low_rule_score_is_not_rescued_by_ai() -> None:
    """A high AI recommendation cannot rescue a rule-ineligible row."""
    engine, factory = _memory_factory()
    db = factory()
    try:
        _seed_ai_order_candidate(
            db, ticker="A", mode="rerank", rule_score=9, recommendation_score=99
        )
        _promote_ai_test_strategy(db)
        pipeline = OperationalPipeline(
            settings=MapsSettings(maps_candidate_min_score=10),
            session_factory=factory,
        )

        assert pipeline._order_candidates(db, dt.date.today()) == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_replace_skipped_limit_is_excluded_from_orders() -> None:
    """Replace mode never mixes an unscored over-limit row into orders."""
    engine, factory = _memory_factory()
    db = factory()
    try:
        _seed_ai_order_candidate(
            db,
            ticker="A",
            mode="replace",
            rule_score=90,
            recommendation_score=90,
            ai_status="SKIPPED_LIMIT",
        )
        _promote_ai_test_strategy(db)
        pipeline = OperationalPipeline(session_factory=factory)

        assert pipeline._order_candidates(db, dt.date.today()) == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_mock_track_months_counts_from_first_filled_buy() -> None:
    """mock_months = 최초 '체결된' 매수 이후 경과 개월. 미체결 주문은 세지 않는다.

    side 는 반드시 **실제 저장되는 값**(`OrderSide.BUY.value`)으로 시드한다.
    이 테스트가 대문자 "BUY" 로 시드하던 동안 운영에서는 한 건도 집계되지
    않았다 — 아래 회귀 테스트 참고.
    """
    engine, factory = _memory_factory()
    db = factory()
    try:
        today = dt.date.today()
        now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.add(OrderLog(  # 미체결 — 무시돼야 함
            order_id="O-1", strategy_id="donchian_v2", ticker="AAAA",
            side=OrderSide.BUY.value,
            qty=10, fill_qty=0, status=OrderStatus.CANCELLED.value,
            created_at=now_utc - dt.timedelta(days=200),
        ))
        db.add(OrderLog(
            order_id="O-2", strategy_id="donchian_v2", ticker="AAAA",
            side=OrderSide.BUY.value,
            qty=10, fill_qty=10, status=OrderStatus.FILLED.value,
            created_at=now_utc - dt.timedelta(days=95),
        ))
        db.commit()

        months = OperationalPipeline._mock_track_months(db, today)
        assert months["donchian_v2"] == pytest.approx(95 / 30.44)
        assert months["donchian_v2"] >= 3.0  # Live Small 진입 조건 충족
        assert months.get("pullback_v3", 0.0) == 0.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_mock_track_months_reads_side_as_stored_lowercase() -> None:
    """side 비교가 대소문자를 틀리면 mock_months 가 영구히 0.0 이 된다.

    2026-07-31 운영 확인: `_mock_track_months` 가 `side == "BUY"` 로 비교하고
    있었는데 저장값은 소문자 "buy" 라 `SELECT ... WHERE side='BUY'` 가 0건이었다.
    그 결과 두 달치 실체결 트랙레코드가 쌓여 있는데도 승격 게이트가
    `mock_months=0.0` 으로 Live Small 진입을 영구히 막고 있었다.

    OrderManager 가 기록하는 값을 그대로 시드해 같은 실수를 다시 잡는다.
    """
    engine, factory = _memory_factory()
    db = factory()
    try:
        now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.add(OrderLog(
            order_id="O-3", strategy_id="pullback_v3", ticker="005930",
            side="buy",   # OrderManager._log_order 가 저장하는 실제 문자열
            qty=10, fill_qty=10, status="filled",
            created_at=now_utc - dt.timedelta(days=61),
        ))
        db.commit()

        months = OperationalPipeline._mock_track_months(db, dt.date.today())
        assert months.get("pullback_v3", 0.0) > 0.0
        assert months["pullback_v3"] == pytest.approx(61 / 30.44)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_save_candidate_snapshot_replaces_day_strategy_rows() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        pipeline = OperationalPipeline(session_factory=factory)
        ref_date = dt.date(2026, 5, 4)
        first = Security(
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            security_type="STOCK",
            turnover_cache={ref_date: 10_000_000_000.0},
        )
        second = Security(
            ticker="000660",
            name="SK하이닉스",
            market="KOSPI",
            security_type="STOCK",
            turnover_cache={ref_date: 5_000_000_000.0},
        )

        assert pipeline._save_candidate_snapshot(db, ref_date, "pullback_v3", [second]) == 1
        assert pipeline._save_candidate_snapshot(db, ref_date, "pullback_v3", [first, second]) == 2

        rows = (
            db.query(CandidateSnapshot)
            .filter(CandidateSnapshot.ref_date == ref_date)
            .order_by(CandidateSnapshot.final_score.desc())
            .all()
        )
        assert [row.ticker for row in rows] == ["005930", "000660"]
        # Missing trend is excluded rather than fabricated as neutral 50.
        assert rows[0].final_score == 100.0
        assert rows[0].score_ready is False
        assert rows[0].missing_components == ["trend_strength"]
        # 000660: factor=50.0,  ts=50.0 → final = 0.6*50  + 0.4*50 = 50.0
        assert rows[1].final_score == 50.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_snapshot_observation_limit_prefers_complete_score_over_higher_partial(
    monkeypatch,
    db,
) -> None:
    """부분 100점 정렬을 유지하면 더 낮은 완성 후보가 저장 전 탈락한다."""
    ref_date = dt.date(2026, 8, 26)
    partial = Security(
        ticker="PARTIAL",
        name="부분점수",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: 10_000_000_000.0},
    )
    complete = Security(
        ticker="COMPLETE",
        name="완성점수",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: 5_000_000_000.0},
    )
    contexts = {
        partial.ticker: TickerContext(
            frame=pd.DataFrame(),
            trend_strength=50.0,
            ts_bucket="S3",
            close=0.0,
            atr14=None,
            trend_strength_measured=False,
        ),
        complete.ticker: TickerContext(
            frame=pd.DataFrame(),
            trend_strength=50.0,
            ts_bucket="S3",
            close=0.0,
            atr14=None,
            trend_strength_measured=True,
        ),
    }
    monkeypatch.setattr(OperationalPipeline, "_signal_from_frame", lambda *_: None)
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_candidate_snapshot_top_n=1)
    )

    pipeline._save_candidate_snapshot(
        db,
        ref_date,
        "pullback_v3",
        [partial, complete],
        contexts=contexts,
    )

    row = db.query(CandidateSnapshot).one()
    assert row.ticker == "COMPLETE"
    assert row.final_score == 50.0
    assert row.score_ready is True


def test_snapshot_keeps_incomplete_entry_signal_for_audit(monkeypatch, db) -> None:
    """저장 정렬을 바꿔도 실제 진입 신호의 미완성 감사 행은 사라지면 안 된다."""
    ref_date = dt.date(2026, 8, 26)
    stock = Security(
        ticker="SIGNAL",
        name="미완성신호",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: 10_000_000_000.0},
    )
    contexts = {
        stock.ticker: TickerContext(
            frame=pd.DataFrame(),
            trend_strength=50.0,
            ts_bucket="S3",
            close=0.0,
            atr14=None,
            trend_strength_measured=False,
        )
    }
    monkeypatch.setattr(
        OperationalPipeline,
        "_signal_from_frame",
        lambda *_: StrategySignal(
            entry_signal=True,
            exit_signal=False,
            close=10_000.0,
        ),
    )
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_candidate_snapshot_top_n=0)
    )

    pipeline._save_candidate_snapshot(
        db,
        ref_date,
        "pullback_v3",
        [stock],
        contexts=contexts,
    )

    row = db.query(CandidateSnapshot).one()
    assert row.ticker == "SIGNAL"
    assert row.entry_signal is True
    assert row.score_ready is False


def _history_bar(db, ticker: str, date: dt.date, close: float, volume: int) -> None:
    """유동성 계산용 일봉."""
    from maps.common.models import HistoricalOHLCV

    db.add(
        HistoricalOHLCV(
            ticker=ticker,
            date=date,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            source="test",
        )
    )


def _thin_collection(ref_date: dt.date):
    """195990 한 종목짜리 수집 결과 — 당일만 거래대금이 터진 상태."""
    from maps.data.krx_adapter import CollectionResult, OHLCVData, SecurityMeta

    meta = [
        SecurityMeta(
            ticker="195990",
            name="테스트종목",
            market="KOSDAQ",
            security_type="STOCK",
            listing_date=dt.date(2020, 1, 1),
        )
    ]
    return meta, CollectionResult(
        ref_date=ref_date,
        ohlcv=[
            OHLCVData(
                date=ref_date,
                ticker="195990",
                open=1400.0,
                high=1400.0,
                low=1400.0,
                close=1400.0,
                volume=240_000,
            )
        ],
        meta=meta,
    )


def test_universe_turnover_uses_twenty_day_average() -> None:
    """하루 급등 거래대금으로 유니버스를 통과하던 회귀를 막는다.

    2026-08-20 195990: 그날 하루치는 3.36억으로 코스닥 하한 3억을 넘겼지만
    20거래일 평균은 3,760만으로 하한의 1/8이었다.
    """
    engine, factory = _memory_factory()
    db = factory()
    try:
        ref_date = dt.date(2026, 8, 20)
        for i in range(19):
            _history_bar(
                db, "195990", ref_date - dt.timedelta(days=19 - i),
                close=1000.0, volume=10_000,
            )
        _history_bar(db, "195990", ref_date, close=1400.0, volume=240_000)
        db.commit()

        pipeline = OperationalPipeline(session_factory=factory)
        meta, collection = _thin_collection(ref_date)
        securities = pipeline._to_securities(db, meta, collection, ref_date)

        target = next(s for s in securities if s.ticker == "195990")
        assert target.avg_turnover_20d_as_of(ref_date) < 300_000_000
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_universe_turnover_is_zero_when_history_missing() -> None:
    """20거래일 이력이 없으면 0 이라 유동성 필터가 걸러낸다(fail-closed)."""
    engine, factory = _memory_factory()
    db = factory()
    try:
        ref_date = dt.date(2026, 8, 20)
        pipeline = OperationalPipeline(session_factory=factory)
        meta, collection = _thin_collection(ref_date)
        securities = pipeline._to_securities(db, meta, collection, ref_date)

        target = next(s for s in securities if s.ticker == "195990")
        assert target.avg_turnover_20d_as_of(ref_date) == 0.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
