"""일일 다이제스트 조립 테스트.

다이제스트는 블로그 글의 유일한 수치 출처다. 여기서 값이 새거나 날짜 경계가
어긋나면 글 전체가 조용히 틀린다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import (
    CandidateSnapshot,
    MarketRegimeLog,
    OrderLog,
    SecurityMetadata,
    StockReportRun,
)
from maps.common.settings import MapsSettings
from maps.ops.daily_digest import _html_to_text, build_daily_digest

REF_DATE = dt.date(2026, 7, 27)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def settings() -> MapsSettings:
    # 외부 API(pykrx/yfinance) 미접촉 — 장세는 오버라이드로 고정한다.
    return MapsSettings(
        maps_market_regime_override="mixed",
        maps_weekly_trend_override="pass",
        maps_broker_mode="mock",
        maps_data_provider="mock",
    )


def _seed(db) -> None:
    db.add(MarketRegimeLog(
        ref_date=REF_DATE, raw_regime="mixed", applied_regime="mixed",
        up_count=4, total_assets=8, weekly_trend="pass", vol_regime="normal",
        floor_applied=False, breadth_pct=0.42,
    ))
    db.add(SecurityMetadata(
        ticker="475150", name="SK이터닉스", market="KOSDAQ", security_type="stock",
    ))
    db.add(CandidateSnapshot(
        ref_date=REF_DATE, strategy_id="donchian_v2", ticker="475150",
        name="SK이터닉스", market="KOSDAQ", factor_score=70.0, trend_strength=80.0,
        ts_bucket="S1", final_score=74.0, score_reason="20일 신고가 돌파 + 거래대금 상위",
        weekly_pass=True,
    ))
    db.add(CandidateSnapshot(
        ref_date=REF_DATE, strategy_id="pullback_v3", ticker="005930",
        name="삼성전자", market="KOSPI", factor_score=40.0, trend_strength=30.0,
        ts_bucket="S4", final_score=36.0, weekly_pass=True,
        excluded_reason="sector_filter_excluded:반도체",
    ))
    # 08:55 KST 매수 = 전일 23:55 UTC. 경계 처리가 틀리면 이 행이 통째로 빠진다.
    db.add(OrderLog(
        order_id="B1", strategy_id="donchian_v2", ticker="475150", side="buy",
        qty=52, order_price=81800.0, fill_price=79500.0, fill_qty=52,
        status="filled", broker="kis", mode="mock",
        created_at=dt.datetime(2026, 7, 26, 23, 55, 18),
    ))
    db.add(OrderLog(
        order_id="S1", strategy_id="donchian_v2", ticker="475150", side="sell",
        qty=52, order_price=60800.0, fill_price=60800.0, fill_qty=52,
        status="filled", broker="kis", mode="mock", exit_reason="stop_loss",
        created_at=dt.datetime(2026, 7, 27, 1, 56, 47),
    ))
    db.add(StockReportRun(
        report_type="supply", status="completed", trade_date="20260727",
        html_content="<html><head><style>b{}</style></head><body><p>외국인 순매도 우위</p></body></html>",
        created_at=dt.datetime(2026, 7, 27, 9, 0, 0),
    ))
    db.commit()


def test_digest_collects_all_sections(db, settings) -> None:
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.ref_date == "2026-07-27"
    assert digest.market is not None
    assert digest.market.regime == "mixed"
    assert digest.market.source == "market_regime_log"
    assert digest.candidate_total == 2
    assert digest.candidate_excluded == 1
    assert digest.candidates[0].ticker == "475150"       # final_score 내림차순
    assert digest.candidates[0].score_reason is not None
    assert digest.strategies, "전략 판정이 비어 있으면 '오늘 선택 전략' 섹션을 못 쓴다"


def test_digest_executions_respect_kst_boundary(db, settings) -> None:
    """08:55 KST 주문은 UTC로 전일 23:55 — 당일 체결 목록에 반드시 들어와야 한다."""
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    by_side = {e.side: e for e in digest.executions}
    assert set(by_side) == {"buy", "sell"}
    assert by_side["buy"].fill_price == 79500.0
    assert by_side["buy"].entry_rationale == "20일 신고가 돌파 + 거래대금 상위"
    assert by_side["sell"].exit_reason == "stop_loss"
    assert by_side["sell"].entry_rationale is None      # 매도엔 진입 근거를 붙이지 않는다


def test_unmeasured_factors_are_flagged(db, settings) -> None:
    """심리·유동성은 피드 미연결 상태다. 실측인 척하면 없는 분석을 지어낸 글이 된다."""
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    factors = {f.name: f for f in digest.market.factors}
    assert factors["psychology"].measured is False
    assert factors["liquidity"].measured is False
    assert factors["psychology"].note                      # 왜 미측정인지 사유가 있어야 한다
    assert factors["price_trend"].measured is True


def test_market_context_extracts_report_text(db, settings) -> None:
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    assert len(digest.market_context) == 1
    excerpt = digest.market_context[0].excerpt
    assert "외국인 순매도 우위" in excerpt
    assert "<" not in excerpt and "b{}" not in excerpt     # 태그·style 내용 제거


def test_digest_survives_empty_database(db, settings) -> None:
    """데이터가 없는 날도 글은 나와야 한다(휴장·수집 실패)."""
    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.candidates == []
    assert digest.executions == []
    assert digest.market is not None       # 로그가 없으면 재계산으로 채운다
    assert digest.market.source == "recomputed"


def test_html_to_text_drops_script_and_style() -> None:
    html = "<div><script>var a=1;</script><style>p{color:red}</style><p>본문  텍스트</p></div>"
    assert _html_to_text(html) == "본문 텍스트"
