"""과거 `market_regime_log` 행의 composite 점수만 복구하는지 검증한다.

수급 가드 결함으로 8/12~14 행이 coverage 0.65 에 갇혔다. 코드를 고쳐도 저장된 행은
그대로라 그 기준일을 참조하는 후보 주문이 계속 막힌다. 이 스크립트가 그걸 푼다.
단, **결정 기록은 건드리면 안 된다** — 실제 주문을 가른 값이기 때문이다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import (
    HistoricalOHLCV,
    InvestorFlowSnapshot,
    MarketNewsSentiment,
    MarketRegimeLog,
)
from scripts.backfill_market_score import recompute

REF_DATE = dt.date(2026, 8, 13)


@pytest.fixture
def factory():
    """인메모리 세션 팩토리."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_feeds(db, *, with_flows: bool = True) -> None:
    """`_market_observations` 를 만족하는 최소 피드 + 수급 + 뉴스."""
    for offset in range(25):
        date = REF_DATE - dt.timedelta(days=offset)
        for ticker, base in (("000001", 10_000.0), ("000002", 20_000.0)):
            db.add(HistoricalOHLCV(
                date=date, ticker=ticker,
                open=base, high=base, low=base, close=base + offset, volume=1_000,
            ))
    if with_flows:
        db.add_all([
            InvestorFlowSnapshot(
                date=REF_DATE, ticker="000001", market="KOSPI",
                foreign_net_value=100.0, institutional_net_value=200.0,
                individual_net_value=-300.0,
            ),
            # 결측이 섞인 정상적인 하루 — 이래도 커버리지가 차야 한다.
            InvestorFlowSnapshot(
                date=REF_DATE, ticker="000002", market="KOSPI",
                foreign_net_value=None, institutional_net_value=50.0,
                individual_net_value=-50.0,
            ),
        ])
    db.add(MarketNewsSentiment(ref_date=REF_DATE, status="success", score=88.0))


def _seed_partial_row(db) -> None:
    """결함 당시 그대로의 행 — coverage 0.65, liquidity·psychology 미측정."""
    db.add(MarketRegimeLog(
        ref_date=REF_DATE,
        raw_regime="strong",
        applied_regime="strong",
        policy_regime="strong",
        weekly_trend="pass",
        vol_regime="high",
        market_mode="TREND_FOLLOWING",
        entry_limit_ratio=0.5,
        up_count=7,
        total_assets=8,
        floor_applied=False,
        korea_weak_guard_applied=False,
        source="candidate_generation",
        final_market_score=47.71,
        score_reason="legacy=strong, final=47.7; 미측정 제외: liquidity,psychology",
        score_coverage_ratio=0.65,
        score_status="partial",
        score_ready=False,
        factor_scores={
            "price_trend": 56.7, "volatility": 25.0, "liquidity": None,
            "foreign_fx": 60.0, "psychology": None,
        },
        factor_sources={
            "price_trend": "market.weekly_price",
            "volatility": "market.kospi_realized_volatility",
            "foreign_fx": "market.usdkrw_weekly_trend",
        },
        measured_factors=["price_trend", "volatility", "foreign_fx"],
        missing_factors=["liquidity", "psychology"],
    ))


def test_recompute_raises_coverage_to_full_for_a_partial_row(factory, monkeypatch) -> None:
    """0.65 에 갇힌 행이 1.0 / complete / ready 로 복구돼야 한다."""
    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    db = factory()
    _seed_feeds(db)
    _seed_partial_row(db)
    db.commit()

    report = recompute(db, REF_DATE, REF_DATE, apply=True)

    assert [e["action"] for e in report] == ["updated"]
    row = db.query(MarketRegimeLog).one()
    assert row.score_coverage_ratio == 1.0
    assert row.score_ready is True
    assert row.score_status == "complete"
    assert row.factor_scores["liquidity"] is not None
    assert row.factor_scores["psychology"] is not None
    assert row.missing_factors == []
    db.close()


def test_recompute_preserves_decision_columns(factory, monkeypatch) -> None:
    """실제 주문을 가른 결정 기록은 한 글자도 바뀌면 안 된다."""
    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    db = factory()
    _seed_feeds(db)
    _seed_partial_row(db)
    db.commit()

    recompute(db, REF_DATE, REF_DATE, apply=True)

    row = db.query(MarketRegimeLog).one()
    assert row.raw_regime == "strong"
    assert row.applied_regime == "strong"
    assert row.policy_regime == "strong"
    assert row.entry_limit_ratio == 0.5
    assert row.market_mode == "TREND_FOLLOWING"
    assert row.up_count == 7
    assert row.source == "candidate_generation"
    assert row.floor_applied is False
    # 결정 시점 커버리지를 남겨, 재생성된 다이제스트가 스스로 밝히게 한다.
    assert "decision-time coverage=0.65" in row.score_reason
    db.close()


def test_recompute_dry_run_writes_nothing(factory, monkeypatch) -> None:
    """기본은 dry-run 이다 — 보고만 하고 쓰지 않는다."""
    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    db = factory()
    _seed_feeds(db)
    _seed_partial_row(db)
    db.commit()

    report = recompute(db, REF_DATE, REF_DATE)

    assert [e["action"] for e in report] == ["would_update"]
    assert db.query(MarketRegimeLog).one().score_coverage_ratio == 0.65
    db.close()


def test_recompute_skips_when_coverage_would_drop(factory, monkeypatch) -> None:
    """이미 완전한 행을 불완전한 재계산으로 덮으면 안 된다 (멱등·장중 안전)."""
    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    db = factory()
    _seed_feeds(db, with_flows=False)      # 수급 없음 → 재계산은 0.65 밖에 못 낸다
    _seed_partial_row(db)
    db.commit()
    stored = db.query(MarketRegimeLog).one()
    stored.score_coverage_ratio = 1.0
    stored.score_ready = True
    stored.score_status = "complete"
    db.commit()

    report = recompute(db, REF_DATE, REF_DATE, apply=True)

    assert [e["action"] for e in report] == ["would_lower_coverage"]
    refreshed = db.query(MarketRegimeLog).one()
    assert refreshed.score_coverage_ratio == 1.0
    assert refreshed.score_ready is True
    db.close()


def test_recompute_skips_dates_without_a_decision_row(factory, monkeypatch) -> None:
    """행이 없으면 만들지 않는다 — 없던 결정을 지어내지 않는다."""
    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    db = factory()
    _seed_feeds(db)
    db.commit()

    report = recompute(db, REF_DATE, REF_DATE, apply=True)

    assert [e["action"] for e in report] == ["no_decision_row"]
    assert db.query(MarketRegimeLog).count() == 0
    db.close()
