from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import CandidateSnapshot
from maps.data.security_repo import Security
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
        # final_score = 0.6 * factor_score + 0.4 * trend_strength
        # OHLCV 없으면 trend_strength=50.0(기본값) 사용
        # 005930: factor=100.0, ts=50.0 → final = 0.6*100 + 0.4*50 = 80.0
        assert rows[0].final_score == 80.0
        # 000660: factor=50.0,  ts=50.0 → final = 0.6*50  + 0.4*50 = 50.0
        assert rows[1].final_score == 50.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
