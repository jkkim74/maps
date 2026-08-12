from __future__ import annotations

import datetime as dt

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
from maps.ops.scheduler import OperationalPipeline


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
