"""Research catalog adapters; stateful cycles require the dedicated replay runner."""
from __future__ import annotations

import pandas as pd

from maps.common.exceptions import BacktestError
from maps.fujimoto.domain import Mode
from maps.strategy.base import BaseStrategy


class FujimotoSafeV1Strategy(BaseStrategy):
    """Safe research identity with explicit price stops and durable staged cycles."""
    strategy_id = "fujimoto_safe_v1"
    strategy_group = "fujimoto_safe"
    mode = Mode.SAFE
    research_only = True
    stop_policy = "required"

    @property
    def default_params(self) -> dict:
        """Hypothesis defaults, never measured validation or trading permission."""
        return {"first_rsi": 40, "weekly_rsi": 70, "buy_weights": [1, 2, 6],
                "maximum_return_20": .2, "minimum_dividend_growth": 0}

    def param_grid(self) -> list[dict]:
        """Dedicated stateful runner supports local first-entry RSI neighbors."""
        return [{"first_rsi": value} for value in (38, 40, 42)]

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Fail explicitly instead of misrepresenting staged ownership as booleans."""
        raise BacktestError("Fujimoto requires maps.fujimoto.replay stateful replay")


class FujimotoOriginalV1Strategy(FujimotoSafeV1Strategy):
    """Original-inspired, account-capped research identity without price stops."""
    strategy_id = "fujimoto_original_v1"
    strategy_group = "fujimoto_original"
    mode = Mode.ORIGINAL
    stop_policy = "intentional_none"
