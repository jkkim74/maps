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
        assert rows[0].final_score == 100.0
        assert rows[1].final_score == 50.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
