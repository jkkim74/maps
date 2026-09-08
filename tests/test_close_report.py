"""장마감 텔레그램 리포트 렌더 테스트.

숫자는 다이제스트에서만 오고, 실패 잡은 잡별 마지막 행이 아니라 전부 세어야 하며,
'확인 필요' 는 데이터가 뒷받침하는 줄만 나온다.
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
    AnalysisRun,
    JobRunLog,
    LimitUpDailyGuard,
    MarketRegimeLog,
    OrderLog,
    PortfolioSnapshot,
    SecurityMetadata,
)
from maps.common.settings import MapsSettings
from maps.ops.close_report import _failed_jobs_today, build_close_report

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
def settings(tmp_path) -> MapsSettings:
    return MapsSettings(
        maps_market_regime_override="mixed",
        maps_weekly_trend_override="pass",
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_blog_dir=str(tmp_path / "blog"),
    )


def _job(db, name: str, status: str, message: str = "") -> None:
    db.add(JobRunLog(
        name=name, status=status, ref_date=REF_DATE,
        started_at=dt.datetime(2026, 7, 27, 0, 0), message=message,
    ))


def test_report_lists_all_failed_jobs_not_just_the_last(db, settings) -> None:
    """재실행이 성공해도 그날의 실패는 남는다. 리포트 잡 자신은 제외한다."""
    _job(db, "validation", "failed", "boom")
    _job(db, "validation", "success")
    _job(db, "broker_sync", "failed", "timeout")
    _job(db, "broker_sync", "failed", "timeout")
    _job(db, "daily_close_report", "failed", "self")
    db.commit()

    failed = _failed_jobs_today(db, REF_DATE)

    assert failed == [("broker_sync", 2, "timeout"), ("validation", 1, "boom")]
    text = build_close_report(db, settings, REF_DATE)
    assert "⚠️ 실패 3건" in text
    assert "validation ×1 — boom" in text
    assert "validation 실패 1건" in text


def test_report_renders_trades_market_and_account(db, settings) -> None:
    db.add(MarketRegimeLog(
        ref_date=REF_DATE, raw_regime="mixed", applied_regime="mixed",
        weekly_trend="pass", vol_regime="normal", entry_limit_ratio=0.5,
    ))
    db.add(SecurityMetadata(
        ticker="475150", name="SK이터닉스", market="KOSDAQ", security_type="stock",
    ))
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
    db.add(PortfolioSnapshot(
        ref_date=REF_DATE, source="broker", total_assets=1_020_000.0,
        cash=690_000.0, positions_value=330_000.0, holdings={"475150": 4},
        holding_details={"475150": {
            "name": "SK이터닉스", "quantity": 4, "avg_price": 80_000.0,
            "current_price": 82_500.0, "evaluation_value": 330_000.0,
            "unrealized_pnl": 10_000.0, "unrealized_pnl_pct": 0.03125,
        }},
    ))
    db.commit()

    text = build_close_report(db, settings, REF_DATE)

    assert "🖥 <b>시스템</b> ✅ 정상" in text
    assert "📊 <b>장세</b> mixed (raw mixed) · 주간추세 pass · 변동성 normal" in text
    assert "신규매수 한도 50%" in text
    assert "총 1,020,000원 · 현금 690,000" in text
    assert "<code>475150</code> SK이터닉스 4주 @80,000 평가 +3.12%" in text
    assert "🛒 <b>매매</b> 매수 1 · 매도 1" in text
    assert "매수 <code>475150</code> SK이터닉스 52주 @79,500 [filled] (donchian_v2)" in text
    assert "매도 <code>475150</code> SK이터닉스 52주 @60,800 [filled] — stop_loss" in text


def test_report_quiet_day_says_so(db, settings) -> None:
    text = build_close_report(db, settings, REF_DATE)

    assert " · 체결 없음" in text
    assert "🚀 <b>상한가 전략</b> 꺼짐" in text          # 기본 설정은 상한가 비활성
    assert "🔍 <b>분석 파이프라인</b> 실행 기록 없음" in text
    assert "16시 분석 파이프라인 실행 기록 없음" in text
    assert "블로그 원고 미생성" in text


def test_attention_flags_entry_block_and_data_gaps(db, settings, tmp_path) -> None:
    for offset in range(9):
        db.add(MarketRegimeLog(
            ref_date=REF_DATE - dt.timedelta(days=offset), raw_regime="mixed",
            applied_regime="mixed", weekly_trend="fail", entry_limit_ratio=0.0,
        ))
    db.add(LimitUpDailyGuard(
        ref_date=REF_DATE, halted_reasons=["kosdaq_drawdown"],
        scan_rejections={"111111": "ineligible_security:listing_unknown"},
    ))
    db.add(AnalysisRun(
        ref_date=REF_DATE, status="completed", picks_count=0,
        note="2단계 strategy-selector: entry_limit_ratio=0.0",
    ))
    blog_dir = tmp_path / "blog"
    blog_dir.mkdir()
    (blog_dir / f"{REF_DATE.isoformat()}.txt").write_text("글", encoding="utf-8")
    db.commit()
    settings = settings.model_copy(update={"maps_limit_up_enabled": True})

    text = build_close_report(db, settings, REF_DATE)

    assert "신규매수 한도 0% — 2026-07-19부터 9거래일째" in text
    assert "신규매수 한도 0% 가 9거래일째 — weekly_trend=fail" in text
    assert "상한가 감시 탈락에 데이터 공백 1건" in text
    assert "상한가 일일 가드 발동: kosdaq_drawdown" in text
    assert "감시제외 1 (listing_unknown 1) · 가드 kosdaq_drawdown" in text
    assert "픽 0 · 2단계 strategy-selector: entry_limit_ratio=0.0" in text
    assert "블로그 원고 미생성" not in text


def test_html_is_escaped(db, settings) -> None:
    _job(db, "validation", "failed", "KIS HTTP 403: <AppKey> & more")
    db.commit()

    text = build_close_report(db, settings, REF_DATE)

    assert "&lt;AppKey&gt; &amp; more" in text
    assert "<AppKey>" not in text
