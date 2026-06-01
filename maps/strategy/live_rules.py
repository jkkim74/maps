"""Shared live-trading rules derived from strategy definitions."""

from __future__ import annotations


_STOP_LOSS_PCTS: dict[str, float] = {
    "pullback_v3": 0.05,
    "pullback_v2": 0.06,
    "ath_breakout_v1": 0.10,
    "ath_breakout_v2": 0.12,
    "multi_asset_trend_v1": 0.08,
    "donchian_v1": 0.08,
    "donchian_v2": 0.10,
}


def stop_loss_price(strategy_id: str | None, entry_price: float | None) -> float | None:
    """Return the persisted live stop level based on the filled entry price."""
    if not strategy_id or entry_price is None or entry_price <= 0:
        return None
    stop_loss_pct = _STOP_LOSS_PCTS.get(strategy_id)
    if stop_loss_pct is None:
        return None
    return entry_price * (1.0 - stop_loss_pct)
