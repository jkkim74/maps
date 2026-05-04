from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import (
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    PromotionHistory,
    WalkForwardResults,
)


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


def test_strategies_returns_registered_strategies_without_promotion_history(ctx) -> None:
    client, _factory = ctx

    response = client.get("/api/v1/strategies")

    assert response.status_code == 200
    data = response.json()
    ids = {item["strategy_id"] for item in data["strategies"]}
    assert data["total"] >= 1
    assert "pullback_v3" in ids
    pullback = next(item for item in data["strategies"] if item["strategy_id"] == "pullback_v3")
    assert pullback["stage"] == "research"


def test_strategies_overlays_latest_metrics_and_promotion(ctx) -> None:
    client, factory = ctx
    db = factory()
    try:
        run_date = dt.date(2026, 5, 4)
        db.add(ParameterPlateauResults(
            strategy_id="pullback_v3",
            run_date=run_date,
            total_combinations=10,
            positive_combinations=8,
            positive_ratio=0.8,
            grade="A",
        ))
        db.add(MonteCarloSequenceResults(
            strategy_id="pullback_v3",
            strategy_group="pullback_short",
            run_date=run_date,
            n_simulations=100,
            mdd_p95=0.09,
            mdd_limit=0.18,
            mc_within_limit=True,
        ))
        db.add(WalkForwardResults(
            strategy_id="pullback_v3",
            run_date=run_date,
            n_folds=3,
            sharpe_mean=1.0,
            sharpe_std=0.25,
            negative_folds=0,
            mean_g2p=0.8,
            passed=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="research",
            to_stage="mock_candidate",
            tradeability_score=77.7,
            passed=True,
        ))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/strategies")

    assert response.status_code == 200
    item = next(x for x in response.json()["strategies"] if x["strategy_id"] == "pullback_v3")
    assert item["stage"] == "mock_candidate"
    assert item["tradeability_score"] == 77.7
    assert item["plateau_score"] == 80.0
    assert item["mc_mdd_p95"] == 0.09
    assert item["wfa_passed"] is True
    assert item["wfa_cv"] == 0.25
