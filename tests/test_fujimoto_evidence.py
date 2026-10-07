"""Causal financial, dividend, valuation and price evidence contracts."""
from dataclasses import replace
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from maps.common.exceptions import DataQualityError
from maps.fujimoto.evidence import (
    AnnualRecord, FinancialRecord, SectorSnapshot, ValuationRecord,
    annual_quality, financial_status, screen, rank_candidates,
)
from maps.fujimoto.indicators import indicators, completed_weeks, wilder_rsi

CUTOFF = date(2026, 4, 7)


def annual(year, value, **changes):
    record = AnnualRecord(
        ticker="A", period_end=date(year, 12, 31), receipt=str(year),
        publication_date=date(year + 1, 3, 20),
        first_observed_at=datetime(year + 1, 3, 20, tzinfo=timezone.utc),
        available_date=date(year + 1, 3, 23 if year == 2025 else 24),
        basis="CFS", currency="KRW", share_basis="split-adjusted-v1",
        revenue=value, operating_profit=value / 10, dividend_per_share=value / 100,
    )
    return replace(record, **changes)


def bars(n=120):
    idx = pd.bdate_range("2025-01-02", periods=n)
    close = pd.Series([100 + (i % 9) for i in range(n)], index=idx)
    return pd.DataFrame(dict(open=close, high=close + 2, low=close - 2,
                             close=close, volume=100, turnover=close * 100))


def test_three_consecutive_comparable_annual_records():
    rows = [annual(y, v) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    assert annual_quality(rows, CUTOFF).passed
    assert not annual_quality(rows[:2], CUTOFF).passed
    assert not annual_quality([rows[0], replace(rows[1], share_basis=None), rows[2]], CUTOFF).passed
    assert not annual_quality([replace(rows[0], period_end=date(2022, 12, 31)), *rows[1:]], CUTOFF).passed
    assert not annual_quality([rows[0], replace(rows[1], basis="OFS"), rows[2]], CUTOFF).passed
    assert annual_quality([replace(r, dividend_per_share=1) for r in rows], CUTOFF).passed
    assert not annual_quality([*rows[:2], replace(rows[-1], dividend_per_share=0.1)], CUTOFF).passed


def test_old_annual_window_expires_after_next_fiscal_report_window():
    rows = [annual(y, v) for y, v in [(2022, 100), (2023, 110), (2024, 120)]]
    assert annual_quality(rows, date(2026, 3, 1)).passed
    assert "expired_annual_history" in annual_quality(rows, CUTOFF).reasons


def test_correction_is_not_backdated_and_pending_correction_blocks_old_values():
    rows = [annual(y, v) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    correction = replace(rows[-1], receipt="2025-correction", publication_date=date(2026, 4, 6),
                         first_observed_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
                         available_date=CUTOFF, revenue=None, operating_profit=None,
                         dividend_per_share=None)
    assert annual_quality([*rows, correction], date(2026, 4, 3)).passed
    assert "pending_revision" in annual_quality([*rows, correction], CUTOFF).reasons
    later = replace(correction, first_observed_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
                    available_date=date(2026, 4, 10), revenue=90, operating_profit=9,
                    dividend_per_share=1.2)
    assert "pending_revision" in annual_quality([*rows, correction, later], CUTOFF).reasons
    assert not annual_quality([*rows, correction, later], date(2026, 4, 10)).passed


def test_annual_dps_must_be_explicit_not_daily_fundamental():
    rows = [annual(y, v, dividend_per_share=None) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    assert "missing_dividend" in annual_quality(rows, CUTOFF).reasons
    with pytest.raises(DataQualityError):
        annual(2025, 120, available_date=date(2026, 3, 20))


def test_missing_financials_are_not_deterioration():
    row = FinancialRecord("A", date(2025, 12, 31), "r", date(2026, 3, 20),
                          datetime(2026, 3, 20, tzinfo=timezone.utc), date(2026, 3, 23),
                          "CFS", "KRW", 120, 100, 12, 10)
    assert financial_status([], CUTOFF) == "missing"
    assert financial_status([row], CUTOFF) == "maintained"
    assert financial_status([replace(row, revenue=90)], CUTOFF) == "deteriorated"
    assert financial_status([replace(row, operating_profit=-1)], CUTOFF) == "deteriorated"
    assert financial_status([replace(row, prior_revenue=None)], CUTOFF) == "missing"
    assert financial_status([row], date(2026, 10, 7)) == "missing"
    pending = replace(row, receipt="r-corrected", publication_date=date(2026, 4, 6),
                      first_observed_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
                      available_date=CUTOFF, revenue=None)
    assert financial_status([row, pending], CUTOFF) == "missing"


def test_available_mixed_tickers_rejected_before_period_collapse():
    row = FinancialRecord("A", date(2025, 12, 31), "r", date(2026, 3, 20),
                          datetime(2026, 3, 20, tzinfo=timezone.utc), date(2026, 3, 23),
                          "CFS", "KRW", 90, 100, 9, 10)
    foreign = replace(row, ticker="B", receipt="z", revenue=120, operating_profit=12)
    with pytest.raises(DataQualityError, match="mixed_ticker_evidence"):
        financial_status([row, foreign], CUTOFF)
    # An observation not available at cutoff must not leak even its identity.
    future = replace(foreign, first_observed_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
                     available_date=date(2026, 4, 10))
    assert financial_status([row, future], CUTOFF) == "deteriorated"
    rows = [annual(y, v) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    foreign_annual = replace(rows[-1], ticker="B", receipt="z")
    with pytest.raises(DataQualityError, match="mixed_ticker_evidence"):
        annual_quality([*rows, foreign_annual], CUTOFF)


def test_sector_median_uses_exact_historical_universe_and_price_cutoff():
    snap = SectorSnapshot(CUTOFF, datetime(2026, 4, 7, tzinfo=timezone.utc),
                          (("A", "s"), ("B", "s"), ("C", "other")))
    vals = [ValuationRecord(t, CUTOFF, datetime(2026, 4, 7, tzinfo=timezone.utc), p)
            for t, p in [("A", 10), ("B", 12), ("C", 1), ("future", 1)]]
    rows = [annual(y, v) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    frame = bars(21)
    frame.index = pd.bdate_range(end=CUTOFF, periods=21)
    result = screen("A", rows, vals, snap, frame, CUTOFF, eligible=True)
    assert result.passed and result.sector_median_per == 11
    assert not screen("A", rows, vals, replace(snap, ref_date=date(2026, 4, 8)), frame, CUTOFF, eligible=True).passed
    future = frame.copy()
    future.loc[pd.Timestamp("2026-04-08")] = [1000] * len(future.columns)
    assert screen("A", rows, vals, snap, future, CUTOFF, eligible=True) == result
    assert [r.ticker for r in rank_candidates([replace(result, ticker="B"), result])] == ["A", "B"]
    assert [r.ticker for r in rank_candidates([replace(result, ticker="B", average_turnover_20=1e6), result])] == ["B", "A"]
    assert not screen("A", rows, vals[:1], snap, frame, CUTOFF, eligible=True).passed
    assert not screen("A", rows, vals, snap, frame.drop(frame.index[-3]), CUTOFF, eligible=True).passed
    late = replace(snap, available_at=datetime(2026, 4, 7, 23, tzinfo=timezone.utc))
    assert not screen("A", rows, vals, late, frame, CUTOFF, eligible=True).passed


def test_twenty_session_return_requires_twenty_deltas():
    snap = SectorSnapshot(CUTOFF, datetime(2026, 4, 7, tzinfo=timezone.utc), (("A", "s"),))
    vals = [ValuationRecord("A", CUTOFF, datetime(2026, 4, 7, tzinfo=timezone.utc), 10)]
    rows = [annual(y, v) for y, v in [(2023, 100), (2024, 110), (2025, 120)]]
    frame = bars(21)
    frame.index = pd.bdate_range(end=CUTOFF, periods=21)
    frame["close"] = [100] * 20 + [121]
    frame["open"] = frame.close
    frame["high"] = frame.close + 2
    frame["low"] = frame.close - 2
    assert "rapid_rise" in screen("A", rows, vals, snap, frame, CUTOFF, eligible=True).reasons
    assert "missing_price_history" in screen("A", rows, vals, snap, frame.iloc[1:], CUTOFF, eligible=True).reasons


@pytest.mark.parametrize("values,expected", [([10] * 16, 50), (list(range(16)), 100), (list(range(16, 0, -1)), 0)])
def test_wilder_rsi_boundaries(values, expected):
    result = wilder_rsi(pd.Series(values, dtype=float))
    assert result.iloc[:14].isna().all()
    assert result.iloc[-1] == expected


def test_wilder_seed_and_recursive_smoothing():
    values = pd.Series([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 12, 11], dtype=float)
    result = wilder_rsi(values)
    assert result.iloc[14] == pytest.approx(100 * 13 / 14)
    assert result.iloc[15] == pytest.approx(100 * (13 * 13 / 14) / 14)


def test_incomplete_and_holiday_weeks():
    frame = bars(5)
    frame.index = pd.to_datetime(["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"])
    assert completed_weeks(frame, date(2026, 4, 9)).empty
    assert len(completed_weeks(frame.iloc[:4], date(2026, 4, 9), closed_dates=(date(2026, 4, 10),))) == 1
    assert completed_weeks(frame.drop(frame.index[1]), date(2026, 4, 10)).empty


def test_shifted_cloud_and_indicators_are_prefix_invariant():
    frame = bars()
    cutoff = frame.index[100].date()
    full = indicators(frame, cutoff)
    prefix = indicators(frame.iloc[:101], cutoff)
    pd.testing.assert_frame_equal(full, prefix)
    assert full.cloud_a.iloc[-1] == pytest.approx((full.tenkan.iloc[-27] + full.kijun.iloc[-27]) / 2)
    assert full.lag_confirm.iloc[-1] == (full.close.iloc[-1] > full.close.iloc[-27])
    broken = frame.copy()
    broken.loc[broken.index[20], "close"] = float("nan")
    with pytest.raises(DataQualityError):
        indicators(broken, cutoff)


def test_indicator_atr_uses_wilder_seed_and_completed_flag():
    frame = bars(30)
    frame["open"] = frame["close"] = 100
    frame["high"], frame["low"] = 102, 98
    result = indicators(frame, frame.index[-1].date())
    assert result.atr14.iloc[-1] == 4
    assert result.atr14.iloc[:13].isna().all()
    frame["complete"] = True
    frame.loc[frame.index[-1], "complete"] = False
    with pytest.raises(DataQualityError):
        indicators(frame, frame.index[-1].date())
