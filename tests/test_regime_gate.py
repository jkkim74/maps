"""장세 게이트 정책 테스트 — USD/KRW 반전, 히스테리시스, floor."""
from __future__ import annotations

import datetime as dt

from maps.common.models import MarketRegimeLog
from maps.common.settings import MapsSettings
from maps.market.regime import (
    BreadthLabel,
    MarketRegimeAnalyzer,
    RegimeLabel,
    RegimeResult,
    WeeklyTrendLabel,
)
from maps.market.regime_history import apply_hysteresis


class FakeWeeklyProvider:
    """자산별 up/down 방향을 지정하는 가짜 주봉 제공자."""

    def __init__(self, up_assets: set[str]) -> None:
        self._up_assets = up_assets

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name in self._up_assets:
            return [float(100 + i) for i in range(n_weeks)]
        return [float(100 - i) for i in range(n_weeks)]


def test_usdkrw_rise_does_not_count_as_risk_on() -> None:
    # S&P/NASDAQ/금 상승 + USD/KRW 상승(원화 약세=risk-off) → 3/8 → weak
    # (반전 없이는 4/8=50% → mixed 가 되어버리는 케이스)
    provider = FakeWeeklyProvider(up_assets={"S&P 500", "NASDAQ", "금", "USD/KRW"})
    result = MarketRegimeAnalyzer(provider).analyze()

    assert result.regime == RegimeLabel.WEAK
    usdkrw = next(a for a in result.assets if a.name == "USD/KRW")
    assert usdkrw.direction == "up"  # 표시 방향은 실제 가격 방향 유지


def test_usdkrw_fall_counts_as_risk_on() -> None:
    # S&P/NASDAQ/금 상승 + USD/KRW 하락(원화 강세=risk-on) → 4/8=50% → mixed
    provider = FakeWeeklyProvider(up_assets={"S&P 500", "NASDAQ", "금"})
    result = MarketRegimeAnalyzer(provider).analyze()

    assert result.regime == RegimeLabel.MIXED
    usdkrw = next(a for a in result.assets if a.name == "USD/KRW")
    assert usdkrw.direction == "down"


# ── 히스테리시스 ──────────────────────────────────────────────────────────────


def _raw_result(
    regime: RegimeLabel,
    *,
    up_count: int,
    kospi_above_ma5w: bool = False,
    kospi_above_ma10w: bool = False,
    weekly: WeeklyTrendLabel = WeeklyTrendLabel.PASS,
) -> RegimeResult:
    return RegimeResult(
        regime=regime,
        weekly_trend=weekly,
        limit_ratio=0.0,
        kospi_ts=None,
        up_count=up_count,
        total_assets=8,
        kospi_above_ma5w=kospi_above_ma5w,
        kospi_above_ma10w=kospi_above_ma10w,
    )


def _add_prev_log(
    db,
    ref_date: dt.date,
    *,
    applied: str,
    kospi_above_ma5w: bool = False,
) -> None:
    db.add(MarketRegimeLog(
        ref_date=ref_date,
        raw_regime=applied,
        applied_regime=applied,
        up_count=4,
        total_assets=8,
        weekly_trend="pass",
        vol_regime="normal",
        floor_applied=False,
        kospi_above_ma5w=kospi_above_ma5w,
        source="test",
    ))
    db.commit()


def test_hysteresis_holds_previous_regime_in_buffer_band(db) -> None:
    # 3/8=37.5%는 buffer band(35~45%) — 전일 mixed면 mixed 유지
    today = dt.date(2026, 7, 2)
    _add_prev_log(db, today - dt.timedelta(days=1), applied="mixed")
    raw = _raw_result(RegimeLabel.WEAK, up_count=3)

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.MIXED
    row = db.query(MarketRegimeLog).filter(MarketRegimeLog.ref_date == today).one()
    assert row.raw_regime == "weak"
    assert row.applied_regime == "mixed"


def test_hysteresis_confirms_weak_below_band(db) -> None:
    # 2/8=25%는 band 아래 — 전일 mixed여도 weak 확정
    today = dt.date(2026, 7, 2)
    _add_prev_log(db, today - dt.timedelta(days=1), applied="mixed")
    raw = _raw_result(RegimeLabel.WEAK, up_count=2)

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.WEAK


def test_hysteresis_keeps_raw_when_no_history(db) -> None:
    today = dt.date(2026, 7, 2)
    raw = _raw_result(RegimeLabel.WEAK, up_count=3)

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.WEAK
    assert db.query(MarketRegimeLog).count() == 1


def test_hysteresis_applies_floor_on_second_day_above_ma5w(db) -> None:
    # MA10W 아래(stateless floor 미발동)라도 KOSPI가 2일 연속 MA5W 위면 하한선 적용
    today = dt.date(2026, 7, 2)
    _add_prev_log(db, today - dt.timedelta(days=1), applied="weak", kospi_above_ma5w=True)
    raw = _raw_result(RegimeLabel.WEAK, up_count=2, kospi_above_ma5w=True)

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.MIXED
    assert result.floor_applied is True


def test_hysteresis_upserts_same_day_row(db) -> None:
    today = dt.date(2026, 7, 2)
    apply_hysteresis(db, _raw_result(RegimeLabel.WEAK, up_count=2), today, source="order_cycle")
    apply_hysteresis(db, _raw_result(RegimeLabel.MIXED, up_count=4), today, source="candidate_generation")

    rows = db.query(MarketRegimeLog).filter(MarketRegimeLog.ref_date == today).all()
    assert len(rows) == 1
    assert rows[0].applied_regime == "mixed"
    assert rows[0].source == "candidate_generation"


# ── Korea weak guard (mixed→weak 하향) ───────────────────────────────────────


def _mixed_below_mas(*, kospi_ts: float | None = None) -> RegimeResult:
    """글로벌 투표는 MIXED(4/8)인데 KOSPI는 5·10주선 모두 하회인 결과."""
    result = _raw_result(RegimeLabel.MIXED, up_count=4)
    result.kospi_ts = kospi_ts
    return result


def test_korea_weak_guard_downgrades_mixed_on_low_trend_strength(db) -> None:
    today = dt.date(2026, 7, 2)
    raw = _mixed_below_mas(kospi_ts=3.8)

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.WEAK
    assert result.korea_weak_applied is True
    row = db.query(MarketRegimeLog).filter(MarketRegimeLog.ref_date == today).one()
    assert row.raw_regime == "mixed"
    assert row.applied_regime == "weak"
    assert row.korea_weak_guard_applied is True


def test_korea_weak_guard_triggers_on_weak_breadth_alone(db) -> None:
    today = dt.date(2026, 7, 2)
    raw = _mixed_below_mas(kospi_ts=60.0)     # 추세강도는 임계값 위
    raw.breadth = BreadthLabel.WEAK           # 하지만 breadth가 약세

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.WEAK
    assert result.korea_weak_applied is True


def test_korea_weak_guard_not_triggered_above_ma5w(db) -> None:
    today = dt.date(2026, 7, 2)
    raw = _raw_result(RegimeLabel.MIXED, up_count=4, kospi_above_ma5w=True)
    raw.kospi_ts = 3.8

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.MIXED
    assert result.korea_weak_applied is False


def test_korea_weak_guard_respects_disable_flag(db) -> None:
    today = dt.date(2026, 7, 2)
    raw = _mixed_below_mas(kospi_ts=3.8)
    settings = MapsSettings(maps_korea_weak_guard_enabled=False)

    result = apply_hysteresis(db, raw, today, settings=settings)

    assert result.regime == RegimeLabel.MIXED
    assert result.korea_weak_applied is False


def test_korea_weak_guard_overrides_buffer_band_hold(db) -> None:
    # band(3/8)가 직전 mixed를 유지해도, 한국 실측이 결정적으로 약하면 weak.
    today = dt.date(2026, 7, 2)
    _add_prev_log(db, today - dt.timedelta(days=1), applied="mixed")
    raw = _raw_result(RegimeLabel.WEAK, up_count=3)   # band → mixed 유지
    raw.kospi_ts = 3.8

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.WEAK
    assert result.korea_weak_applied is True


def test_korea_weak_guard_skips_override_results(db) -> None:
    # 오버라이드/스텁(up_count=None)은 record-only — 가드도 적용하지 않는다.
    today = dt.date(2026, 7, 2)
    raw = RegimeResult(
        regime=RegimeLabel.MIXED,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=3.8,
    )

    result = apply_hysteresis(db, raw, today)

    assert result.regime == RegimeLabel.MIXED
    assert result.korea_weak_applied is False


def test_korea_weak_guard_entry_effects(db) -> None:
    # 가드 발동 + 고변동성이면 진입 0% + CASH_DEFENSE — 실제 매매 효과 확인.
    from maps.market.regime import VolRegimeLabel

    today = dt.date(2026, 7, 2)
    raw = _mixed_below_mas(kospi_ts=3.8)
    raw.vol_regime = VolRegimeLabel.HIGH

    result = apply_hysteresis(db, raw, today)

    assert result.entry_limit_ratio == 0.0
    assert result.market_mode().value == "CASH_DEFENSE"
