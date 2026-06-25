from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import maps.common.models  # noqa: F401
from maps.common.models import (
    CandidateSnapshot,
    HistoricalOHLCV,
    OrderLog,
    PortfolioSnapshot,
    PromotionHistory,
)
from maps.common.settings import MapsSettings, get_settings, reload_settings
from maps.execution.broker_adapter import OrderSide, OrderStatus
from maps.api.orders import get_orders
from maps.ops.order_preview import build_order_preview
from maps.ops.scheduler import OperationalPipeline

TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def _regime_override(monkeypatch):
    # 레짐 분석의 네트워크 호출을 피한다 (entry_limit_ratio 결정만 영향).
    monkeypatch.setenv("MAPS_MARKET_REGIME_OVERRIDE", "strong")
    monkeypatch.setenv("MAPS_WEEKLY_TREND_OVERRIDE", "pass")
    reload_settings()
    yield
    reload_settings()


def _fire_entry_signal(monkeypatch) -> None:
    monkeypatch.setattr(
        OperationalPipeline, "_latest_strategy_signal",
        staticmethod(lambda db, *, ticker, strategy_id, ref_date: SimpleNamespace(
            entry_signal=True, exit_signal=False, atr14=None)),
    )


def _promo(db, strategy_id: str, stage: str) -> None:
    db.add(PromotionHistory(
        strategy_id=strategy_id, from_stage="research", to_stage=stage,
        tradeability_score=80.0, passed=True,
    ))


def _cand(db, *, ref_date, strategy_id, ticker, name="종목", score=95.0) -> None:
    db.add(CandidateSnapshot(
        ref_date=ref_date, strategy_id=strategy_id, ticker=ticker, name=name,
        market="KOSPI", factor_score=90, trend_strength=80, ts_bucket="S5",
        final_score=score, weekly_pass=True,
    ))


def _ohlcv(db, ticker, date, close=10_000.0) -> None:
    db.add(HistoricalOHLCV(
        ticker=ticker, date=date, open=close, high=close, low=close, close=close, volume=100_000,
    ))


# ── 신규 동작: 신선도 게이트 / 스테이지 일치 / 스킵 집계 ─────────────────────

def test_stale_snapshot_blocks_listing(db):
    _promo(db, "pullback_v3", "live_candidate")
    old = TODAY - dt.timedelta(days=30)
    _cand(db, ref_date=old, strategy_id="pullback_v3", ticker="005930")
    db.commit()

    r = build_order_preview(db, get_settings())
    assert r.snapshot_stale is True
    assert r.snapshot_date == old.isoformat()
    assert r.items == []
    assert r.snapshot_reason and "최신" in r.snapshot_reason


def test_mock_stage_excluded_and_counted(db):
    _promo(db, "donchian_v1", "mock_candidate")
    _cand(db, ref_date=TODAY, strategy_id="donchian_v1", ticker="000660")
    db.commit()

    r = build_order_preview(db, get_settings())
    assert r.snapshot_stale is False
    assert r.eligible_strategies == []           # mock_candidate는 주문 자격 아님
    assert r.mock_candidate_count == 1
    assert r.items == []


def test_skipped_summary_no_price(db):
    # live_candidate 후보지만 OHLCV가 없으면 no_price로 집계(행 미노출)
    _promo(db, "pullback_v3", "live_candidate")
    _cand(db, ref_date=TODAY, strategy_id="pullback_v3", ticker="005930")
    db.commit()

    r = build_order_preview(db, get_settings())
    assert "pullback_v3" in r.eligible_strategies
    assert r.items == []
    assert r.skipped_summary.get("no_price") == 1


def test_orderable_item_when_signal_fires(db, monkeypatch):
    _promo(db, "pullback_v3", "live_candidate")
    _cand(db, ref_date=TODAY, strategy_id="pullback_v3", ticker="005930", name="삼성전자")
    _ohlcv(db, "005930", TODAY, close=70_000.0)
    db.add(PortfolioSnapshot(
        ref_date=TODAY, source="broker", total_assets=100_000_000.0, cash=100_000_000.0,
    ))
    db.commit()
    _fire_entry_signal(monkeypatch)

    r = build_order_preview(db, get_settings())
    assert r.snapshot_stale is False
    assert len(r.items) == 1
    item = r.items[0]
    assert item.ticker == "005930" and item.skipped is False and item.estimated_qty > 0
    assert r.skipped_summary == {}


def test_no_snapshot_empty(db):
    r = build_order_preview(db, get_settings())
    assert r.data_available is False
    assert r.items == []
    assert r.snapshot_stale is False


# ── 기존 의도 유지: 주문 제출된 종목은 미리보기에서 숨겨진다 ──────────────────

def test_preview_hides_candidate_after_order_submission(db, monkeypatch):
    _promo(db, "pullback_v3", "live_candidate")
    _cand(db, ref_date=TODAY, strategy_id="pullback_v3", ticker="AAAA", name="AAAA")
    _ohlcv(db, "AAAA", TODAY, close=10_000.0)
    db.commit()
    monkeypatch.setattr("maps.ops.order_preview.next_trading_day", lambda value: value + dt.timedelta(days=1))
    _fire_entry_signal(monkeypatch)

    before = build_order_preview(db, MapsSettings())
    assert [item.ticker for item in before.items] == ["AAAA"]

    db.add(OrderLog(
        order_id="order-1", strategy_id="pullback_v3", ticker="AAAA",
        side=OrderSide.BUY.value, qty=10, order_price=10_100,
        status=OrderStatus.PENDING.value, created_at=dt.datetime.now(),
    ))
    db.commit()

    after = build_order_preview(db, MapsSettings())
    assert after.data_available is True
    assert after.items == []


def test_orders_read_does_not_expire_stale_pending_row(db):
    db.add(OrderLog(
        order_id="stale-pending", strategy_id="pullback_v3", ticker="AAAA",
        side=OrderSide.BUY.value, qty=10, order_price=10_100,
        status=OrderStatus.PENDING.value, created_at=dt.datetime.now() - dt.timedelta(days=1),
    ))
    db.commit()

    get_orders(db)

    row = db.query(OrderLog).filter(OrderLog.order_id == "stale-pending").one()
    assert row.status == OrderStatus.PENDING.value
