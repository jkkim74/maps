"""Versioned research features; only measured, point-in-time inputs are scored."""
from __future__ import annotations

import datetime as dt
import math
from functools import lru_cache

import pandas as pd

from maps.market.trading_rules import is_krx_closed_date, previous_trading_day

SCORE_VERSION = "contrarian_quality_20261001"


def _clip(value: float) -> float:
    return max(0., min(100., value))


@lru_cache(maxsize=64)
def _dates(ref_date: dt.date, closed: tuple[dt.date, ...]) -> tuple[dt.date, ...]:
    end = ref_date
    if is_krx_closed_date(end, extra_closed_dates=closed):
        end = previous_trading_day(end, extra_closed_dates=closed)
    result = [end]
    for _ in range(79):
        result.append(previous_trading_day(result[-1], extra_closed_dates=closed))
    return tuple(reversed(result))


def _aligned(frame: pd.DataFrame, dates: tuple[dt.date, ...], columns: list[str]) -> pd.DataFrame | None:
    if frame.empty or not set(columns).issubset(frame.columns):
        return None
    data = frame[columns].copy()
    try:
        data.index = pd.to_datetime(data.index).date
        data = data[~data.index.duplicated(keep="last")].reindex(dates)
        data = data.apply(pd.to_numeric, errors="coerce")
        if not all(math.isfinite(float(v)) for v in data.to_numpy().flat):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return data


def contrarian_extra_scores(
    frame: pd.DataFrame,
    flows: pd.DataFrame,
    earnings: dict | None,
    *,
    ref_date: dt.date,
    extra_closed_dates=(),
) -> dict:
    """Return scores plus JSON-safe evidence; omissions remain explicitly missing."""
    dates = _dates(ref_date, tuple(sorted(extra_closed_dates)))
    result: dict = {"_sources": {}, "_evidence": {}}

    def save(key, score, source, evidence):
        if math.isfinite(score):
            result[key] = round(_clip(score), 2)
            result["_sources"][key] = source
            result["_evidence"][key] = evidence

    def missing(key, reason):
        result["_evidence"][key] = {"missing_reason": reason}

    key = "earnings_improvement_score"
    try:
        e = earnings or {}
        revenue, prior = float(e["revenue"]), float(e["prior_revenue"])
        profit, prior_profit = float(e["operating_profit"]), float(e["prior_operating_profit"])
        if not all(math.isfinite(v) for v in (revenue, prior, profit, prior_profit)) or min(revenue, prior) <= 0:
            raise ValueError("invalid revenue")
        growth = revenue / prior - 1
        margin = profit / revenue - prior_profit / prior
        save(key, .3 * _clip(50 + 250 * growth) + .7 * _clip(50 + 1000 * margin),
             "dart.reported_yoy_improvement", {
                 **e.get("evidence", {}), "revenue": str(e["revenue"]),
                 "prior_revenue": str(e["prior_revenue"]), "operating_profit": str(e["operating_profit"]),
                 "prior_operating_profit": str(e["prior_operating_profit"]),
                 "revenue_growth": growth, "margin_change": margin,
             })
    except (KeyError, TypeError, ValueError, OverflowError):
        missing(key, (earnings or {}).get("reason") or "missing_or_invalid_financials")
        result["_evidence"][key].update((earnings or {}).get("evidence", {}))

    key = "crowd_neglect_score"
    prices80 = _aligned(frame, dates, ["close", "volume"])
    if prices80 is not None and (prices80.close > 0).all() and (prices80.volume >= 0).all():
        turnover = prices80.close * prices80.volume
        current, prior = float(turnover.iloc[-20:].mean()), float(turnover.iloc[:-20].mean())
        if current > 0 and prior > 0:
            save(key, 100 * (1 - current / prior), "ohlcv.close_volume_20_vs_prior60", {
                "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
                "recent20_mean": current, "prior60_mean": prior,
            })
    if key not in result:
        missing(key, "requires_80_complete_sessions_and_positive_turnover")

    dates20 = dates[-20:]
    prices20 = _aligned(frame, dates20, ["close", "volume"])
    flow20 = _aligned(flows, dates20, ["foreign_net_value", "institutional_net_value"])
    key = "accumulation_flow_score"
    if prices20 is not None and flow20 is not None and (prices20.close > 0).all() and (prices20.volume >= 0).all():
        turnover = float((prices20.close * prices20.volume).sum())
        net = float(flow20.to_numpy().sum())
        if turnover > 0:
            save(key, 50 + 1000 * net / turnover, "krx.investor_flow20/ohlcv.close_volume20", {
                "start": dates20[0].isoformat(), "end": dates20[-1].isoformat(),
                "net_purchase": net, "close_volume_sum": turnover,
            })
    if key not in result:
        missing(key, "requires_20_complete_flow_and_price_sessions")

    key = "technical_bottom_score"
    prices = _aligned(frame, dates20, ["close", "low"])
    if prices is not None and (prices > 0).all().all() and (prices.low <= prices.close).all():
        l20, l5, p15 = float(prices.low.min()), float(prices.low.iloc[-5:].min()), float(prices.low.iloc[:-5].min())
        m5, pm5 = float(prices.close.iloc[-5:].mean()), float(prices.close.iloc[-10:-5].mean())
        close = float(prices.close.iloc[-1])
        hold, rebound, recovery = _clip(50 + 1000 * (l5 / p15 - 1)), _clip(1000 * (close / l20 - 1)), _clip(50 + 2500 * (m5 / pm5 - 1))
        save(key, .4 * hold + .3 * rebound + .3 * recovery, "ohlcv.bottom_stabilization20", {
            "start": dates20[0].isoformat(), "end": dates20[-1].isoformat(),
            "l20": l20, "l5": l5, "p15": p15, "m5": m5, "pm5": pm5, "close": close,
            "low_hold": hold, "rebound": rebound, "recovery": recovery,
        })
    else:
        missing(key, "requires_20_valid_low_close_sessions")
    return result
