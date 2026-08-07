from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import CandidateSnapshot, UniverseQualityLog


@pytest.fixture
def ctx():
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client, factory

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_candidates_empty_without_snapshot(ctx) -> None:
    client, _factory = ctx

    response = client.get("/api/v1/candidates")

    assert response.status_code == 200
    data = response.json()
    assert data["final_count"] == 0
    assert data["candidates"] == []


def test_candidates_returns_latest_snapshot(ctx) -> None:
    client, factory = ctx
    db = factory()
    try:
        old = dt.date(2026, 5, 3)
        latest = dt.date(2026, 5, 4)
        db.add(UniverseQualityLog(
            ref_date=latest,
            mode="live",
            total_candidates=3,
            kept_count=2,
            excluded_count=1,
            rejection_ratio=1 / 3,
            alert_sent=True,
        ))
        db.add(CandidateSnapshot(
            ref_date=old,
            strategy_id="pullback_v3",
            ticker="000001",
            name="OLD",
            market="KOSPI",
            factor_score=1.0,
            trend_strength=50.0,
            ts_bucket="S3",
            final_score=1.0,
            weekly_pass=True,
        ))
        db.add(CandidateSnapshot(
            ref_date=latest,
            strategy_id="pullback_v3",
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            factor_score=90.0,
            trend_strength=50.0,
            ts_bucket="S3",
            final_score=90.0,
            weekly_pass=True,
        ))
        db.add(CandidateSnapshot(
            ref_date=latest,
            strategy_id="pullback_v3",
            ticker="000660",
            name="SK하이닉스",
            market="KOSPI",
            factor_score=80.0,
            trend_strength=50.0,
            ts_bucket="S3",
            final_score=80.0,
            weekly_pass=True,
        ))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/candidates")

    assert response.status_code == 200
    data = response.json()
    assert data["ref_date"] == "2026-05-04"
    assert data["universe_count"] == 3
    assert data["missing_count"] == 1
    assert data["final_count"] == 2
    assert [item["ticker"] for item in data["candidates"]] == ["005930", "000660"]


def test_candidates_exposes_score_provenance(ctx) -> None:
    """Candidate API separates rule, AI, recommendation, and source metadata."""
    client, factory = ctx
    db = factory()
    try:
        db.add(
            CandidateSnapshot(
                ref_date=dt.date(2026, 8, 7),
                strategy_id="pullback_v3",
                ticker="005930",
                name="삼성전자",
                market="KOSPI",
                factor_score=70,
                trend_strength=75,
                ts_bucket="S4",
                final_score=72,
                rule_score=70,
                ai_technical_score=80,
                recommendation_score=72,
                score_source="AI",
                ai_scoring_mode="rerank",
                ai_status="SUCCESS",
                ai_confidence=0.82,
                ai_reason_codes=["UPTREND", "HEALTHY_PULLBACK"],
                ai_model_id="test-model",
                weekly_pass=True,
            )
        )
        db.commit()
    finally:
        db.close()

    item = client.get("/api/v1/candidates").json()["candidates"][0]

    assert item["rule_score"] == 70
    assert item["ai_score"] == 80
    assert item["recommendation_score"] == item["final_score"] == 72
    assert item["score_source"] == "AI"
    assert item["ai_scoring_mode"] == "rerank"
    assert item["ai_reason_codes"] == ["UPTREND", "HEALTHY_PULLBACK"]
