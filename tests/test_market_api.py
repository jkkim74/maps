from __future__ import annotations

import datetime as dt

import pytest

from maps.api import market
from maps.common.models import MarketRegimeLog


def _live_provider(monkeypatch) -> None:
    def fake_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name == "KOSPI":
            return [float(100 + i) for i in range(n_weeks)]
        if asset_name == "KOSDAQ":
            return [float(100 - i) for i in range(n_weeks)]
        return []

    monkeypatch.setattr(market._CombinedWeeklyProvider, "get_weekly_closes", fake_weekly_closes)


def test_market_api_uses_weekly_index_provider(db, monkeypatch) -> None:
    _live_provider(monkeypatch)

    response = market.get_market(db)

    assets = {item.name: item for item in response.assets}
    assert response.source == "live"
    assert response.regime == "mixed"
    assert response.weekly_trend == "pass"
    assert response.limit_ratio == 0.5
    assert assets["KOSPI"].direction == "up"
    # KOSPI는 floor 판정용 MA10W 계산을 위해 11주를 요청한다 → 마지막 값 110
    assert assets["KOSPI"].value == 110.0
    assert assets["KOSDAQ"].direction == "down"
    assert assets["KOSDAQ"].value == 95.0
    assert assets["S&P 500"].direction == "flat"


def test_market_api_treats_missing_index_data_as_unavailable(db, monkeypatch) -> None:
    monkeypatch.setattr(
        market._CombinedWeeklyProvider,
        "get_weekly_closes",
        lambda self, asset_name, n_weeks: [],
    )

    response = market.get_market(db)

    assert response.regime == "mixed"
    assert response.weekly_trend == "pass"
    assert response.limit_ratio == 0.5
    assert all(item.direction == "flat" for item in response.assets)


def _forbid_live(monkeypatch) -> None:
    """실시간 경로가 타면 실패한다 — 화면은 외부 시세를 새로 받으면 안 된다."""

    def boom(self, asset_name, n_weeks):  # noqa: ANN001
        raise AssertionError("live provider must not be called when a regime log exists")

    monkeypatch.setattr(market._CombinedWeeklyProvider, "get_weekly_closes", boom)


def _add_log(db, ref_date: dt.date, *, asset_trends) -> MarketRegimeLog:  # noqa: ANN001
    row = MarketRegimeLog(
        ref_date=ref_date,
        raw_regime="weak",
        applied_regime="mixed",
        up_count=3,
        total_assets=8,
        weekly_trend="fail",
        vol_regime="normal",
        kospi_ts=55.8,
        entry_limit_ratio=0.0,
        market_mode="NORMAL",
        composite_regime="mixed",
        policy_regime="mixed",
        final_market_score=52.3,
        score_reason="composite ok",
        score_coverage_ratio=1.0,
        score_status="ready",
        score_ready=True,
        factor_scores={"price_trend": 55.8, "volatility": 50.0, "liquidity": 48.0},
        factor_sources={"price_trend": "market.weekly_price"},
        measured_factors=["price_trend", "volatility", "liquidity"],
        missing_factors=["psychology"],
        asset_trends=asset_trends,
        source="candidate_generation",
    )
    db.add(row)
    db.commit()
    return row


def test_market_api_serves_recent_regime_log_without_live_fetch(db, monkeypatch) -> None:
    """최근 이력이 있으면 KRX·yfinance 를 부르지 않고 히스테리시스 적용 국면을 돌려준다.

    실시간 계산은 요청당 6~28초였다(2026-09-22 실측). 정본도 이력의 applied_regime 이다.
    """
    _forbid_live(monkeypatch)
    today = dt.datetime.now(market._KST).date()
    _add_log(db, today - dt.timedelta(days=1), asset_trends=[
        {"name": "KOSPI", "direction": "up", "value": 3412.5},
        {"name": "금", "direction": "down", "value": None},
    ])

    response = market.get_market(db)

    assert response.source == "regime_log"
    assert response.ref_date == (today - dt.timedelta(days=1)).isoformat()
    assert response.regime == "mixed"            # applied (히스테리시스 적용)
    assert response.legacy_regime == "weak"      # raw
    assert response.weekly_trend == "fail"
    assert response.kospi_ts == 55.8
    assert response.limit_ratio == 0.0
    assert response.market_mode == "NORMAL"
    assert response.price_trend_score == 55.8
    assert response.psychology_score is None
    assert response.final_market_score == 52.3
    assert response.score_ready is True
    assert response.missing_factors == ["psychology"]
    assert response.reason == "composite ok"
    assert [(a.name, a.direction, a.value) for a in response.assets] == [
        ("KOSPI", "up", 3412.5),
        ("금", "down", 0.0),
    ]
    assert response.updated_at is not None and response.updated_at.endswith("+00:00")


def test_market_api_falls_back_to_live_when_log_is_stale(db, monkeypatch) -> None:
    _live_provider(monkeypatch)
    today = dt.datetime.now(market._KST).date()
    _add_log(db, today - dt.timedelta(days=4), asset_trends=[])

    response = market.get_market(db)

    assert response.source == "live"
    assert response.ref_date is None


def test_market_api_falls_back_to_live_for_rows_before_asset_trends_column(db, monkeypatch) -> None:
    """컬럼 추가 이전 행(asset_trends NULL)은 자산 목록이 없어 실시간으로 계산한다."""
    _live_provider(monkeypatch)
    today = dt.datetime.now(market._KST).date()
    _add_log(db, today, asset_trends=None)

    response = market.get_market(db)

    assert response.source == "live"
