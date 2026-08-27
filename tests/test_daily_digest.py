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
    AnalysisPick,
    AnalysisPickLeg,
    CandidateSnapshot,
    HistoricalOHLCV,
    HoldingRegimeAudit,
    MarketRegimeLog,
    OrderLog,
    PortfolioSnapshot,
    PromotionHistory,
    SecurityMetadata,
    StockReportRun,
    UniverseQualityLog,
)
from maps.common.settings import MapsSettings
from maps.market.trading_rules import trading_days_ago
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
        score_ready=True, score_coverage_ratio=1.0,
        weekly_pass=True,
    ))
    db.add(CandidateSnapshot(
        ref_date=dt.date(2026, 7, 24), strategy_id="donchian_v2", ticker="475150",
        name="SK이터닉스", market="KOSDAQ", factor_score=68.0, trend_strength=80.0,
        ts_bucket="S1", final_score=72.0, score_reason="20일 신고가 돌파 + 거래대금 상위",
        score_ready=True, score_coverage_ratio=1.0,
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


def _seed_sector_ohlcv(db) -> None:
    """업종 수익률 계산에 필요한 최소 데이터 — 업종당 3종목 이상이어야 집계된다."""
    plan = {"화학": (1000, 1300), "금융": (2000, 2100), "유통": (3000, 2700)}
    for sector, (start, end) in plan.items():
        for n in range(3):
            ticker = f"{abs(hash(sector)) % 900 + 100}{n:03d}"
            db.add(SecurityMetadata(
                ticker=ticker, name=f"{sector}{n}", market="KOSPI",
                security_type="stock", sector=sector,
            ))
            for day, close in ((REF_DATE - dt.timedelta(days=40), start), (REF_DATE, end)):
                db.add(HistoricalOHLCV(
                    ticker=ticker, date=day, open=close, high=close,
                    low=close, close=close, volume=10_000,
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
    assert digest.candidate_ready_total == 1
    assert digest.candidate_incomplete_total == 1
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


def test_digest_prefers_pinned_order_context_and_displays_kst(db, settings) -> None:
    """장 마감 후보가 달라져도 매수 근거와 주문 시각은 제출 당시 값이어야 한다."""
    _seed(db)
    row = db.query(OrderLog).filter(OrderLog.order_id == "B1").one()
    row.decision_context = {
        "version": 1,
        "origin": "live",
        "candidate": {
            "snapshot_id": 123,
            "ref_date": "2026-07-24",
            "score": 38.27,
            "score_reason": "주문 당시 후보 근거",
        },
        "market": {
            "ref_date": "2026-07-27",
            "source": "order_cycle",
            "regime": "mixed",
            "weekly_trend": "pass",
            "vol_regime": "high",
            "entry_limit_ratio": 0.25,
        },
    }
    # _seed의 당일 후보 근거는 서로 다르다. 이 값이 붙으면 사후 스냅샷 누출이다.
    current = db.query(CandidateSnapshot).filter(
        CandidateSnapshot.ref_date == REF_DATE,
        CandidateSnapshot.ticker == "475150",
        CandidateSnapshot.strategy_id == "donchian_v2",
    ).one()
    current.score_reason = "장 마감 후 달라진 후보 근거"
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    execution = next(e for e in digest.executions if e.order_id == "B1")
    assert execution.entry_rationale == "주문 당시 후보 근거"
    assert execution.decision_context["candidate"]["score"] == 38.27
    assert execution.created_at == "2026-07-27T08:55:18+09:00"
    assert execution.warnings == []


def test_legacy_order_uses_only_pre_execution_candidate_and_warns(db, settings) -> None:
    """감사 컬럼 도입 전 주문에 당일 장 마감 후보를 매수 근거로 붙이면 안 된다."""
    _seed(db)
    prior = db.query(CandidateSnapshot).filter(
        CandidateSnapshot.ref_date == dt.date(2026, 7, 24),
        CandidateSnapshot.ticker == "475150",
        CandidateSnapshot.strategy_id == "donchian_v2",
    ).one()
    prior.score_reason = "직전 거래일 후보 근거"
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    execution = next(e for e in digest.executions if e.order_id == "B1")
    assert execution.decision_context is None
    assert execution.entry_rationale == "직전 거래일 후보 근거"
    assert execution.warnings == ["DECISION_CONTEXT_INFERRED"]


def test_strategy_trade_execution_includes_pick_audit_context(db, settings) -> None:
    _seed(db)
    decision_date = dt.date(2026, 7, 24)
    db.add(MarketRegimeLog(
        ref_date=decision_date, raw_regime="weak", applied_regime="weak",
        up_count=1, total_assets=8, weekly_trend="fail", vol_regime="high",
        floor_applied=False, breadth_pct=0.12, entry_limit_ratio=0.0,
    ))
    pick = AnalysisPick(
        ref_date=decision_date, ticker="475150", name="SK이터닉스", market="KOSDAQ",
        buy_price=80_000.0, target_price=95_000.0, stop_price=74_000.0,
        rationale="승인 당시 분석 근거", regime="strong",
        strategy_context="manual approval", ai_recommendation="WATCH",
        strategy_trade_enabled=True, state="ARMED",
    )
    db.add(pick)
    db.flush()
    db.add(OrderLog(
        order_id="PICK-LEG-2", strategy_id=f"strategy_trade:{pick.id}:leg:2:try:1",
        ticker="475150", side="buy", qty=3, order_price=79_000.0,
        fill_price=78_900.0, fill_qty=3, status="filled", broker="kis", mode="mock",
        created_at=dt.datetime(2026, 7, 27, 0, 5, 0),
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    execution = next(e for e in digest.executions if e.order_id == "PICK-LEG-2")
    assert execution.analysis_pick_id == pick.id
    assert execution.entry_rationale == "승인 당시 분석 근거"
    assert execution.ai_recommendation == "WATCH"
    assert execution.approval_regime == "strong"
    assert execution.strategy_context == "manual approval"
    assert execution.warnings == [
        "AI_RECOMMENDATION_NOT_BUY",
        "MARKET_ENTRY_BLOCK_OVERRIDDEN",
    ]


def test_digest_separates_remaining_conditional_entries(db, settings) -> None:
    _seed(db)
    pick = AnalysisPick(
        ref_date=REF_DATE, ticker="475150", name="SK이터닉스", market="KOSDAQ",
        buy_price=80_000.0, target_price=95_000.0, stop_price=74_000.0,
        qty=10, trade_mode="split", total_budget=800_000.0,
        strategy_trade_enabled=True, state="BOUGHT", ai_recommendation="BUY",
    )
    pick.legs = [
        AnalysisPickLeg(
            sequence=1, entry_price=80_000.0, weight_pct=40,
            planned_qty=4, filled_qty=4, status="FILLED",
        ),
        AnalysisPickLeg(
            sequence=2, entry_price=77_000.0, weight_pct=60,
            planned_qty=6, filled_qty=2, order_id="LEG-2", status="PARTIAL",
        ),
    ]
    db.add(pick)
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert len(digest.conditional_entries) == 1
    entry = digest.conditional_entries[0]
    assert entry.pick_id == pick.id
    assert entry.trade_mode == "split"
    assert entry.filled_legs == 1
    assert entry.total_legs == 2
    assert entry.next_leg_sequence == 2
    assert entry.next_entry_price == 77_000.0
    assert entry.remaining_qty == 4
    assert entry.status == "order_pending"
    assert entry.ai_recommendation == "BUY"


def test_digest_reports_decision_time_portfolio_details(db, settings) -> None:
    db.add_all([
        PortfolioSnapshot(
            ref_date=REF_DATE - dt.timedelta(days=3), source="broker",
            total_assets=1_000_000.0, cash=700_000.0, positions_value=300_000.0,
        ),
        PortfolioSnapshot(
            ref_date=REF_DATE, source="broker", total_assets=1_020_000.0,
            cash=690_000.0, positions_value=330_000.0, holdings={"475150": 4},
            holding_details={
                "475150": {
                    "name": "SK이터닉스", "quantity": 4, "avg_price": 80_000.0,
                    "current_price": 82_500.0, "evaluation_value": 330_000.0,
                    "unrealized_pnl": 10_000.0, "unrealized_pnl_pct": 0.03125,
                }
            },
        ),
    ])
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.portfolio is not None
    assert digest.portfolio.daily_pnl_pct == pytest.approx(0.02)
    assert digest.portfolio.data_complete is True
    assert digest.portfolio.holdings[0].ticker == "475150"
    assert digest.portfolio.holdings[0].unrealized_pnl == 10_000.0


def test_digest_connects_shadow_overlay_by_entry_order_and_counts_actions(db, settings) -> None:
    db.add(PortfolioSnapshot(
        ref_date=REF_DATE,
        source="broker",
        total_assets=1_020_000.0,
        cash=690_000.0,
        positions_value=330_000.0,
        holdings={"475150": 4},
        holding_details={
            "475150": {
                "name": "SK이터닉스",
                "quantity": 4,
                "avg_price": 80_000.0,
                "current_price": 82_500.0,
                "evaluation_value": 330_000.0,
                "unrealized_pnl": 10_000.0,
                "unrealized_pnl_pct": 0.03125,
            }
        },
    ))
    entry = OrderLog(
        order_id="overlay-entry",
        strategy_id="donchian_v2",
        ticker="475150",
        side="buy",
        qty=4,
        fill_qty=4,
        status="filled",
        created_at=dt.datetime(2026, 7, 26, 23, 55),
    )
    db.add(entry)
    db.flush()
    db.add(HoldingRegimeAudit(
        ref_date=REF_DATE,
        position_key=f"order:{entry.id}",
        ticker="475150",
        strategy_id="donchian_v2",
        entry_regime="mixed",
        current_regime="mixed",
        weekly_trend="fail",
        vol_regime="high",
        action="exit",
        reason_code="CONFIRMED_ADVERSE_REGIME",
        confirmed=True,
        mode="shadow",
        details={
            "current_adverse_causes": ["weekly_fail"],
            "confirmed_adverse_causes": ["weekly_fail"],
        },
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.portfolio is not None
    overlay = digest.portfolio.holdings[0].regime_overlay
    assert overlay is not None
    assert overlay.action == "exit"
    assert overlay.mode == "shadow"
    assert overlay.confirmed_adverse_causes == ["weekly_fail"]
    assert digest.portfolio.regime_overlay_summary == {
        "hold": 0,
        "watch": 0,
        "exit": 1,
    }

    db.add(AnalysisPick(
        ref_date=REF_DATE,
        ticker="475150",
        name="SK인터넷",
        source="manual",
        state="BOUGHT",
        strategy_trade_enabled=True,
    ))
    db.commit()

    digest_after_pick = build_daily_digest(db, settings, REF_DATE)

    assert digest_after_pick.portfolio is not None
    assert digest_after_pick.portfolio.holdings[0].regime_overlay is None
    assert digest_after_pick.portfolio.regime_overlay_summary == {
        "hold": 0,
        "watch": 0,
        "exit": 0,
    }


def test_digest_marks_legacy_portfolio_details_incomplete(db, settings) -> None:
    db.add(PortfolioSnapshot(
        ref_date=REF_DATE, source="broker", total_assets=1_000_000.0,
        cash=700_000.0, positions_value=300_000.0, holdings={"475150": 4},
        holding_details=None,
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.portfolio is not None
    assert digest.portfolio.data_complete is False
    assert digest.portfolio.warnings == ["HOLDING_DETAILS_UNAVAILABLE"]
    assert digest.portfolio.holdings == []


def test_unmeasured_factors_are_flagged(db, settings) -> None:
    """심리·유동성은 피드 미연결 상태다. 실측인 척하면 없는 분석을 지어낸 글이 된다."""
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    factors = {f.name: f for f in digest.market.factors}
    assert factors["psychology"].measured is False
    assert factors["liquidity"].measured is False
    assert factors["psychology"].note                      # 왜 미측정인지 사유가 있어야 한다
    # Legacy rows without factor audit metadata must remain fail-closed.
    assert factors["price_trend"].measured is False


def test_sectors_are_observed_even_when_filter_is_off(db, settings) -> None:
    """필터가 꺼져 있어도 강세업종은 계산해 남긴다 — 나중에 켤지 판단할 근거가 된다."""
    _seed_sector_ohlcv(db)
    assert settings.maps_sector_filter_enabled is False

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.sectors is not None
    assert digest.sectors.applied_to_trading is False   # 매매엔 안 쓰였다는 표시가 핵심
    assert digest.sectors.selector == "legacy"          # kostolany 모드도 꺼져 있다
    assert [s.sector for s in digest.sectors.selected]   # 그래도 관측값은 있어야 한다
    assert "관측 전용" in (digest.sectors.reason or "")


def test_sectors_report_placeholder_inputs_in_kostolany_mode(db) -> None:
    """코스톨라니 스코어러는 가중치 절반이 중립값이다 — 글에서 감추면 안 된다."""
    _seed_sector_ohlcv(db)
    settings = MapsSettings(
        maps_market_regime_override="mixed",
        maps_weekly_trend_override="pass",
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_sector_kostolany_mode_enabled=True,
    )

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.sectors.selector == "kostolany"
    assert digest.sectors.applied_to_trading is False
    assert "earnings_revision" in digest.sectors.placeholder_inputs


def test_market_context_extracts_report_text(db, settings) -> None:
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    assert len(digest.market_context) == 1
    excerpt = digest.market_context[0].excerpt
    assert "외국인 순매도 우위" in excerpt
    assert "<" not in excerpt and "b{}" not in excerpt     # 태그·style 내용 제거


def test_market_context_exposes_failed_report_without_invalid_html(db, settings) -> None:
    _seed(db)
    db.add(StockReportRun(
        report_type="summary", status="failed", trade_date="20260727",
        html_content="<p>invalid market summary</p>",
        error_message="index metadata invalid: kospi max_gap_days=39",
        created_at=dt.datetime(2026, 7, 27, 9, 5, 0),
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    failed = next(item for item in digest.market_context if item.report_type == "summary")
    assert failed.status == "failed"
    assert failed.excerpt == ""
    assert failed.error_message == "index metadata invalid: kospi max_gap_days=39"


def test_digest_survives_empty_database(db, settings) -> None:
    """데이터가 없는 날도 글은 나와야 한다(휴장·수집 실패)."""
    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.candidates == []
    assert digest.executions == []
    assert digest.market is not None       # 로그가 없으면 재계산으로 채운다
    assert digest.market.source == "recomputed"


def test_market_surfaces_korea_weak_guard_flag(db, settings) -> None:
    """가드로 내린 WEAK인지 투표 결과 WEAK인지 글에서 구분할 수 있어야 한다."""
    db.add(MarketRegimeLog(
        ref_date=REF_DATE, raw_regime="mixed", applied_regime="weak",
        up_count=4, total_assets=8, weekly_trend="pass", vol_regime="high",
        floor_applied=False, korea_weak_guard_applied=True, breadth_pct=0.123,
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.market.regime == "weak"
    assert digest.market.korea_weak_guard_applied is True


def test_universe_stats_come_from_quality_log(db, settings) -> None:
    """제외 통계는 DQ 필터의 감사 로그가 정본이다 — 스냅샷 행 수를 세면 항상 0%가 된다."""
    _seed(db)
    db.add(UniverseQualityLog(
        ref_date=REF_DATE, mode="live", total_candidates=2771,
        kept_count=1818, excluded_count=953, rejection_ratio=0.344,
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert digest.universe_total == 2771
    assert digest.universe_excluded == 953
    assert digest.universe_rejection_ratio == 0.344


def test_top_candidates_dedupe_across_strategies(db, settings) -> None:
    """같은 종목이 전략 수만큼 상위권을 차지하면 안 된다 — 최고 점수 1행만."""
    _seed(db)
    db.add(CandidateSnapshot(
        ref_date=REF_DATE, strategy_id="pullback_v3", ticker="475150",
        name="SK이터닉스", market="KOSDAQ", factor_score=70.0, trend_strength=60.0,
        ts_bucket="S2", final_score=66.0, score_ready=True,
        score_coverage_ratio=1.0, weekly_pass=True,
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    tickers = [c.ticker for c in digest.candidates]
    assert tickers.count("475150") == 1
    winner = next(c for c in digest.candidates if c.ticker == "475150")
    assert winner.strategy_id == "donchian_v2"          # final_score 74.0 > 66.0
    assert digest.candidate_total == 2                  # 고유 ticker 수 (행 수 3이 아니라)


def test_digest_separates_partial_scores_and_prefers_complete_duplicate(
    db,
    settings,
) -> None:
    """부분 100점은 순위 밖으로 이동하고 같은 티커의 완성 전략이 주 목록을 대표한다."""
    db.add_all(
        [
            CandidateSnapshot(
                ref_date=REF_DATE, strategy_id="contrarian_quality_accumulation_v1",
                ticker="DUP", name="중복부분", market="KOSPI", factor_score=100.0,
                trend_strength=50.0, ts_bucket="S3", final_score=100.0,
                score_ready=False, score_coverage_ratio=0.3, score_status="partial",
                missing_components=["earnings_revision_score"], weekly_pass=True,
            ),
            CandidateSnapshot(
                ref_date=REF_DATE, strategy_id="pullback_v3", ticker="DUP",
                name="중복완성", market="KOSPI", factor_score=70.0,
                trend_strength=70.0, ts_bucket="S2", final_score=70.0,
                score_ready=True, score_coverage_ratio=1.0, score_status="complete",
                weekly_pass=True,
            ),
            CandidateSnapshot(
                ref_date=REF_DATE, strategy_id="contrarian_quality_accumulation_v1",
                ticker="PARTIAL", name="부분전용", market="KOSPI", factor_score=100.0,
                trend_strength=50.0, ts_bucket="S3", final_score=100.0,
                score_ready=False, score_coverage_ratio=0.3, score_status="partial",
                missing_components=["crowd_neglect_score"], weekly_pass=True,
            ),
            CandidateSnapshot(
                ref_date=REF_DATE, strategy_id="pullback_v3", ticker="READY",
                name="완성전용", market="KOSPI", factor_score=60.0,
                trend_strength=60.0, ts_bucket="S2", final_score=60.0,
                score_ready=True, score_coverage_ratio=1.0, score_status="complete",
                weekly_pass=True,
            ),
        ]
    )
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    assert [row.ticker for row in digest.candidates] == ["DUP", "READY"]
    assert [row.ticker for row in digest.incomplete_candidates] == ["PARTIAL"]
    assert digest.candidate_total == 3
    assert digest.candidate_ready_total == 2
    assert digest.candidate_incomplete_total == 1
    assert digest.incomplete_candidates[0].final_score == 100.0
    assert digest.incomplete_candidates[0].missing_components == ["crowd_neglect_score"]


def test_price_source_labels_rule_based_fallback(db, settings) -> None:
    """AI 미작동 시 ai_buy_price 는 룰 기반 폴백이다 — 출처를 정직하게 밝혀야 한다."""
    _seed(db)
    digest = build_daily_digest(db, settings, REF_DATE)

    assert all(c.price_source == "rule" for c in digest.candidates)


def test_candidates_backfill_from_analysis_pick(db, settings) -> None:
    """/analyze 결과(analysis_pick)가 있으면 메모·가격을 보강하고 출처를 표시한다."""
    _seed(db)
    db.add(AnalysisPick(
        ref_date=REF_DATE, ticker="475150", name="SK이터닉스", market="KOSDAQ",
        buy_price=80000.0, target_price=95000.0, stop_price=74000.0,
        rationale="20일 돌파 후 눌림, 수급 양호",
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    pick_candidate = next(c for c in digest.candidates if c.ticker == "475150")
    assert pick_candidate.price_source == "analysis_pick"
    assert pick_candidate.ai_buy_price == 80000.0
    assert pick_candidate.ai_analysis_memo == "20일 돌파 후 눌림, 수급 양호"


def test_strategy_stage_defaults_to_research_and_flags_orderable(db, settings) -> None:
    """승격 이력 없는 전략은 null 이 아니라 research 로, 주문 자격 여부와 함께 표시한다."""
    _seed(db)
    db.add(PromotionHistory(
        strategy_id="donchian_v2", from_stage="alert_only", to_stage="mock_candidate",
        tradeability_score=65.0, passed=True,
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    by_id = {s.strategy_id: s for s in digest.strategies}
    assert by_id["pullback_v2"].stage == "research"
    assert by_id["pullback_v2"].orderable is False
    assert by_id["donchian_v2"].stage == "mock_candidate"
    # mock 브로커(모의 계좌)에서는 mock_candidate 도 주문 대상이다
    assert by_id["donchian_v2"].orderable is True


def test_html_to_text_drops_script_and_style() -> None:
    html = "<div><script>var a=1;</script><style>p{color:red}</style><p>본문  텍스트</p></div>"
    assert _html_to_text(html) == "본문 텍스트"


# ── 픽 기준일 만료 (2026-07-30 사고) ────────────────────────────────────────
# _latest_picks 는 `ref_date <= ref_date` 상한만 있고 하한이 없어서, 몇 달 전 픽이
# 오늘 다이제스트의 매수·손절·목표가를 조용히 채우고 있었다.

def test_stale_pick_does_not_backfill_digest(db, settings) -> None:
    _seed(db)
    db.add(AnalysisPick(
        ref_date=trading_days_ago(REF_DATE, 30), ticker="475150", name="SK이터닉스",
        market="KOSDAQ", buy_price=80000.0, target_price=95000.0, stop_price=74000.0,
        rationale="한 달 전 근거",
    ))
    db.commit()

    digest = build_daily_digest(db, settings, REF_DATE)

    candidate = next(c for c in digest.candidates if c.ticker == "475150")
    assert candidate.price_source == "rule"       # 만료 픽은 가격 출처가 될 수 없다
    assert candidate.ai_analysis_memo != "한 달 전 근거"


def test_backdated_digest_uses_its_own_ref_date_window(db, settings) -> None:
    """과거 날짜로 재생성해도 그날 기준 신선했던 픽은 살아 있어야 한다.

    cutoff 를 `date.today()` 로 잡으면 블로그 백필 시 픽이 전부 빠지고
    price_source 가 조용히 analysis_pick → rule 로 뒤집힌다.
    """
    old_ref = REF_DATE - dt.timedelta(days=90)
    db.add(SecurityMetadata(
        ticker="475150", name="SK이터닉스", market="KOSDAQ", security_type="stock",
    ))
    db.add(CandidateSnapshot(
        ref_date=old_ref, strategy_id="donchian_v2", ticker="475150",
        name="SK이터닉스", market="KOSDAQ", factor_score=70.0, trend_strength=80.0,
        ts_bucket="S1", final_score=74.0, score_ready=True,
        score_coverage_ratio=1.0, weekly_pass=True,
    ))
    db.add(AnalysisPick(
        ref_date=old_ref, ticker="475150", name="SK이터닉스", market="KOSDAQ",
        buy_price=80000.0, target_price=95000.0, stop_price=74000.0,
        rationale="그날 기준으로는 신선했던 근거",
    ))
    db.commit()

    digest = build_daily_digest(db, settings, old_ref)

    candidate = next(c for c in digest.candidates if c.ticker == "475150")
    assert candidate.price_source == "analysis_pick"
    assert candidate.ai_buy_price == 80000.0


def _order_with_liquidity(db, *, ticker: str, original_qty: int, qty: int, reason: str):
    """유동성 한도가 적용된 매수 주문 한 건."""
    from maps.common.models import OrderLog

    db.add(OrderLog(
        order_id=f"liq-{ticker}",
        strategy_id="donchian_v2",
        ticker=ticker,
        side="buy",
        qty=qty,
        order_price=1434,
        fill_price=1434,
        fill_qty=qty,
        status="filled",
        decision_context={
            "version": 1,
            "origin": "live",
            "liquidity": {
                "original_qty": original_qty,
                "turnover_20d": 37_606_136.0,
                "limit_amount": 752_122.72,
                "reason": reason,
            },
        },
    ))
    db.commit()


def test_digest_counts_liquidity_capped_orders(db) -> None:
    """축소된 주문이 다이제스트에 집계되고 원래 수량이 보존돼야 한다."""
    ref_date = dt.date.today()
    _order_with_liquidity(
        db, ticker="195990", original_qty=2323, qty=524, reason="LIQUIDITY_CAPPED"
    )

    digest = build_daily_digest(db, MapsSettings(), ref_date)

    assert digest.liquidity_capped_total == 1
    assert any("195990" in note for note in digest.liquidity_notes)
    assert any("2323" in note for note in digest.liquidity_notes)


def test_digest_ignores_orders_without_liquidity_cap(db) -> None:
    """축소되지 않은 정상 주문은 세지 않는다."""
    ref_date = dt.date.today()
    _order_with_liquidity(
        db, ticker="005930", original_qty=100, qty=100, reason=None
    )

    digest = build_daily_digest(db, MapsSettings(), ref_date)

    assert digest.liquidity_capped_total == 0
    assert digest.liquidity_notes == []
