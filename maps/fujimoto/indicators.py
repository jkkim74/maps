"""Causal completed-session Wilder RSI, MACD and shifted Ichimoku indicators."""
from __future__ import annotations

from datetime import date, timedelta
from collections.abc import Iterable

import numpy as np
import pandas as pd

from maps.common.exceptions import DataQualityError
from maps.common.settings import get_settings
from maps.market.trading_rules import is_krx_closed_date


def daily_bars(prices: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Validate and copy completed bars at/before the after-close cutoff.

    Date-only data is a contract for completed OHLCV. Optional `complete` must
    be True for every selected bar; caller must not include an unfinished day.
    """
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(prices.columns) or not isinstance(prices.index, pd.DatetimeIndex):
        raise DataQualityError("invalid_ohlcv_schema")
    frame = prices.loc[prices.index.date <= cutoff].copy()
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise DataQualityError("unordered_or_duplicate_bars")
    if "complete" in frame and not frame.complete.eq(True).all():
        raise DataQualityError("incomplete_daily_bar")
    for name in required | ({"turnover"} if "turnover" in frame else set()):
        if not np.isfinite(frame[name].to_numpy(dtype=float)).all():
            raise DataQualityError("nonfinite_ohlcv")
    if (frame[list(required - {"volume"})] <= 0).any().any() or (frame.volume < 0).any():
        raise DataQualityError("invalid_ohlcv_amount")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise DataQualityError("invalid_ohlcv_range")
    if "turnover" in frame and (frame.turnover < 0).any():
        raise DataQualityError("invalid_turnover")
    return frame


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Seed with the arithmetic mean of first period deltas, then smooth recursively."""
    if period < 1 or not np.isfinite(close.to_numpy(dtype=float)).all():
        raise DataQualityError("invalid_rsi_input")
    output = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= period:
        return output
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain, avg_loss = float(gain.iloc[1:period + 1].mean()), float(loss.iloc[1:period + 1].mean())
    for i in range(period, len(close)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
        output.iloc[i] = (50 if avg_gain == avg_loss == 0 else
                          100 if avg_loss == 0 else 100 * avg_gain / (avg_gain + avg_loss))
    return output


def has_session_gap(frame: pd.DataFrame, cutoff: date, *, closed_dates: Iterable[date] | None = None) -> bool:
    """Check explicit daily coverage against existing KRX calendar, never row-count only."""
    if frame.empty:
        return True
    closures = tuple(get_settings().krx_closed_dates if closed_dates is None else closed_dates)
    start = frame.index[0].date()
    expected = {start + timedelta(days=i) for i in range((cutoff - start).days + 1)
                if not is_krx_closed_date(start + timedelta(days=i), extra_closed_dates=closures)}
    return set(frame.index.date) != expected


def _calculate(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate causal indicator columns without changing the bar index."""
    result = frame.copy()
    close = frame.close
    result["rsi"] = wilder_rsi(close)
    true_range = pd.concat((frame.high - frame.low, (frame.high - close.shift()).abs(),
                            (frame.low - close.shift()).abs()), axis=1).max(axis=1)
    atr = pd.Series(np.nan, index=frame.index, dtype=float)
    if len(frame) >= 14:
        atr.iloc[13] = true_range.iloc[:14].mean()
        for i in range(14, len(frame)):
            atr.iloc[i] = (atr.iloc[i - 1] * 13 + true_range.iloc[i]) / 14
    result["atr14"] = atr
    result["macd"] = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    result["macd_signal"] = result.macd.ewm(span=9, adjust=False, min_periods=9).mean()
    result["macd_histogram"] = result.macd - result.macd_signal
    for name, period in (("tenkan", 9), ("kijun", 26), ("span_b", 52)):
        result[name] = (frame.high.rolling(period).max() + frame.low.rolling(period).min()) / 2
    result["cloud_a"] = ((result.tenkan + result.kijun) / 2).shift(26)
    result["cloud_b"] = result.span_b.shift(26)
    result["cloud_top"] = result[["cloud_a", "cloud_b"]].max(axis=1, skipna=False)
    result["lag_confirm"] = close > close.shift(26)
    result["ichimoku_bullish"] = (result.tenkan > result.kijun) & (close > result.cloud_top) & result.lag_confirm
    result["cloud_bearish"] = close <= result.cloud_top
    result["macd_golden"] = (result.macd > result.macd_signal) & (result.macd.shift() <= result.macd_signal.shift())
    result["macd_dead"] = (result.macd < result.macd_signal) & (result.macd.shift() >= result.macd_signal.shift())
    result["tenkan_cross_up"] = (result.tenkan > result.kijun) & (result.tenkan.shift() <= result.kijun.shift())
    result["tenkan_cross_down"] = (result.tenkan < result.kijun) & (result.tenkan.shift() >= result.kijun.shift())
    result["rsi_cross_70"] = (result.rsi > 70) & (result.rsi.shift() <= 70)
    return result


def indicators(prices: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Daily indicator frame calculated after slicing, guaranteeing prefix invariance."""
    return _calculate(daily_bars(prices, cutoff))


def completed_weeks(prices: pd.DataFrame, cutoff: date, *, closed_dates: Iterable[date] | None = None) -> pd.DataFrame:
    """Aggregate only fully observed KRX weeks whose last session has closed."""
    frame = daily_bars(prices, cutoff)
    closures = tuple(get_settings().krx_closed_dates if closed_dates is None else closed_dates)
    records, indexes = [], []
    for _, group in frame.groupby(frame.index.to_period("W-FRI")):
        monday = group.index[0].date() - timedelta(days=group.index[0].weekday())
        sessions = [monday + timedelta(days=i) for i in range(5)
                    if not is_krx_closed_date(monday + timedelta(days=i), extra_closed_dates=closures)]
        if not sessions or sessions[-1] > cutoff or set(group.index.date) != set(sessions):
            continue
        row = dict(open=group.open.iloc[0], high=group.high.max(), low=group.low.min(),
                   close=group.close.iloc[-1], volume=group.volume.sum())
        if "turnover" in group:
            row["turnover"] = group.turnover.sum()
        records.append(row)
        indexes.append(pd.Timestamp(sessions[-1]))
    return pd.DataFrame(records, index=pd.DatetimeIndex(indexes), columns=[c for c in frame if c in {"open", "high", "low", "close", "volume", "turnover"}])


def weekly_indicators(prices: pd.DataFrame, cutoff: date, *, closed_dates: Iterable[date] | None = None) -> pd.DataFrame:
    """Weekly signals over completed weeks only; no retroactive in-progress bars."""
    return _calculate(completed_weeks(prices, cutoff, closed_dates=closed_dates))
