from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.api import candidates as candidates_api
from maps.api.auth import Identity
from maps.common.db import Base
from maps.common.models import AppUser, CandidateSnapshot, UniverseQualityLog
from maps.common.passwords import hash_password


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


def _snapshot(
    ticker: str,
    score: float,
    market: str,
    *,
    score_ready: bool = True,
    coverage: float = 1.0,
) -> CandidateSnapshot:
    """테스트용 후보 스냅샷 한 행 (기준일 고정)."""
    return CandidateSnapshot(
        ref_date=dt.date(2026, 8, 13),
        strategy_id="pullback_v3",
        ticker=ticker,
        name=f"종목{ticker}",
        market=market,
        factor_score=score,
        trend_strength=50.0,
        ts_bucket="S3",
        final_score=score,
        score_ready=score_ready,
        score_coverage_ratio=coverage,
        weekly_pass=True,
    )


def _seed_user(factory, monkeypatch, username: str, preferences: dict) -> None:
    """개인 설정을 가진 계정을 만들고 요청 주체를 그 계정으로 고정한다."""
    db = factory()
    try:
        user = AppUser(
            username=username,
            password_hash=hash_password("pw12345678"),
            role="user",
            status="active",
            preferences=preferences,
        )
        db.add(user)
        db.commit()
        identity = Identity(id=user.id, username=user.username, role=user.role)
    finally:
        db.close()
    monkeypatch.setattr(candidates_api, "current_identity", lambda request: identity)


def _seed_snapshots(factory, snapshots: list[CandidateSnapshot]) -> None:
    """후보 스냅샷을 커밋한다."""
    db = factory()
    try:
        db.add_all(snapshots)
        db.commit()
    finally:
        db.close()


def test_candidates_empty_without_snapshot(ctx) -> None:
    client, _factory = ctx

    response = client.get("/api/v1/candidates")

    assert response.status_code == 200
    data = response.json()
    assert data["final_count"] == 0
    assert data["candidates"] == []
    assert data["ready_count"] == 0
    assert data["incomplete_count"] == 0
    assert data["incomplete_candidates"] == []


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
            score_ready=True,
            score_coverage_ratio=1.0,
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
            score_ready=True,
            score_coverage_ratio=1.0,
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
    assert data["ready_count"] == 2
    assert data["incomplete_count"] == 0
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
                score_ready=True,
                score_coverage_ratio=1.0,
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


def test_candidates_separates_incomplete_score_from_trade_ranking(ctx) -> None:
    """부분 100점이 더 낮은 완성점수보다 주 목록 위에 보이면 안 된다."""
    client, factory = ctx
    ready = _snapshot("READY", 70.0, "KOSPI")
    partial = _snapshot(
        "PARTIAL",
        100.0,
        "KOSPI",
        score_ready=False,
        coverage=0.3,
    )
    partial.score_status = "partial"
    partial.missing_components = [
        "earnings_revision_score",
        "crowd_neglect_score",
    ]
    _seed_snapshots(factory, [partial, ready])

    body = client.get("/api/v1/candidates").json()

    assert body["final_count"] == 2
    assert body["ready_count"] == 1
    assert body["incomplete_count"] == 1
    assert [row["ticker"] for row in body["candidates"]] == ["READY"]
    assert [row["ticker"] for row in body["incomplete_candidates"]] == ["PARTIAL"]
    incomplete = body["incomplete_candidates"][0]
    assert incomplete["final_score"] == 100.0
    assert incomplete["score_coverage_ratio"] == 0.3
    assert incomplete["missing_components"] == [
        "earnings_revision_score",
        "crowd_neglect_score",
    ]


def test_personal_min_score_does_not_hide_incomplete_audit_list(
    ctx,
    monkeypatch,
) -> None:
    """부분점수는 비교 불가라 개인 점수 임계값으로 감사 목록에서 숨기지 않는다."""
    client, factory = ctx
    _seed_snapshots(
        factory,
        [
            _snapshot("READY", 70.0, "KOSPI"),
            _snapshot("PARTIAL", 10.0, "KOSPI", score_ready=False, coverage=0.3),
        ],
    )
    _seed_user(factory, monkeypatch, "partialuser", {"candidate_min_score": 80.0})

    body = client.get("/api/v1/candidates").json()

    assert body["candidates"] == []
    assert [row["ticker"] for row in body["incomplete_candidates"]] == ["PARTIAL"]


def test_personal_min_score_filters_list(ctx, monkeypatch) -> None:
    """개인 최소 점수 미만 후보는 목록에서 빠진다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 20.0, "KOSPI"),
    ])
    _seed_user(factory, monkeypatch, "filteruser", {"candidate_min_score": 50.0})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["000001"]


def test_personal_market_filters_list(ctx, monkeypatch) -> None:
    """선택한 시장만 남는다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000003", 90.0, "KOSDAQ"),
        _snapshot("000004", 100.0, "KOSPI", score_ready=False, coverage=0.3),
        _snapshot("000005", 100.0, "KOSDAQ", score_ready=False, coverage=0.3),
    ])
    _seed_user(factory, monkeypatch, "marketuser", {"candidate_markets": ["KOSPI"]})

    body = client.get("/api/v1/candidates").json()
    assert [c["ticker"] for c in body["candidates"]] == ["000001"]
    assert [c["ticker"] for c in body["incomplete_candidates"]] == ["000004"]


def test_auth_disabled_returns_everything(ctx) -> None:
    """인증이 꺼진 환경(로컬·테스트 기본)에서는 필터가 걸리지 않는다."""
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 1.0, "KOSDAQ"),
    ])

    body = client.get("/api/v1/candidates").json()
    assert len(body["candidates"]) == 2


def test_filter_runs_before_limit(ctx, monkeypatch) -> None:
    """필터가 .limit(200) 앞에서 걸린다.

    `candidate_min_score` 는 정렬 기준(final_score)과 같은 컬럼의 단조 임계값이라
    "필터→정렬→limit" 과 "정렬→limit→필터" 가 수학적으로 동치다 — 통과하는 행은
    이미 최상위권이라 어느 순서로 잘라도 결과가 같다. 그래서 정렬 기준과 무관한
    `candidate_markets` 를 쓴다: 고득점 KOSDAQ 210행 + 저득점 KOSPI 1행을 넣고
    KOSPI 만 남기도록 필터링하면, limit 뒤에서 필터링될 경우 KOSDAQ 200행에 밀려
    KOSPI 행이 잘려 나가 결과가 빈다.
    """
    client, factory = ctx
    rows = [_snapshot(f"1{i:05d}", 90.0, "KOSDAQ") for i in range(210)]
    rows.append(_snapshot("999999", 10.0, "KOSPI"))
    _seed_snapshots(factory, rows)
    _seed_user(factory, monkeypatch, "limituser", {"candidate_markets": ["KOSPI"]})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["999999"]


def test_counts_stay_pipeline_values(ctx, monkeypatch) -> None:
    """집계는 파이프라인 통계다 — 개인 필터로 줄어들지 않는다.

    `UniverseQualityLog` 를 심지 않은 정상 경로(가장 흔한 경우)를 쓴다 — 이 경로에서
    `universe_count` 는 `len(rows)`(필터+limit 적용된 후보) 로 폴백해서는 안 되고,
    `final_count` 와 같은 미필터·미제한 파이프라인 카운트를 써야 한다.
    """
    client, factory = ctx
    _seed_snapshots(factory, [
        _snapshot("000001", 90.0, "KOSPI"),
        _snapshot("000002", 20.0, "KOSPI"),
    ])
    _seed_user(factory, monkeypatch, "countuser", {"candidate_min_score": 50.0})

    body = client.get("/api/v1/candidates").json()
    assert len(body["candidates"]) == 1
    assert body["final_count"] == 2
    assert body["universe_count"] == 2


def test_min_score_filter_uses_canonical_score_in_rerank_mode(ctx, monkeypatch) -> None:
    """개인 최소 점수는 주문 게이트와 **같은 점수 컬럼**을 봐야 한다.

    `rerank` 모드의 정본은 `candidate_min_score_expression()` = `coalesce(rule_score,
    final_score)` 다. 원시 `final_score` 로 거르면 주문 파이프라인은 80점으로 취급해
    주문을 내는 종목이 화면에서는 45점으로 숨는다 — 화면과 주문이 어긋난다.
    """
    client, factory = ctx
    row = _snapshot("000001", 45.0, "KOSPI")
    row.rule_score = 80.0
    row.ai_scoring_mode = "rerank"
    _seed_snapshots(factory, [row])
    _seed_user(factory, monkeypatch, "rerankuser", {"candidate_min_score": 50.0})

    tickers = [c["ticker"] for c in client.get("/api/v1/candidates").json()["candidates"]]
    assert tickers == ["000001"]


def test_min_score_filter_uses_final_score_when_ai_off(ctx, monkeypatch) -> None:
    """AI 가 꺼진 모드에서는 정본이 `final_score` 그대로다."""
    client, factory = ctx
    row = _snapshot("000002", 45.0, "KOSPI")
    row.rule_score = 80.0
    row.ai_scoring_mode = "off"
    _seed_snapshots(factory, [row])
    _seed_user(factory, monkeypatch, "offuser", {"candidate_min_score": 50.0})

    assert client.get("/api/v1/candidates").json()["candidates"] == []
