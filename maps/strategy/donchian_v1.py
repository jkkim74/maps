"""Donchian Channel V1 전략 — 클래식 채널 돌파.

전략군: donchian_research (허용 MDD p95 = 30%)

Donchian Channel:
  상단 = 전일까지 high_period일 최고 종가
  하단 = 전일까지 low_period일 최저 종가

진입: 종가 > 상단 채널 (상향 돌파)
청산: 종가 < 하단 채널 (하향 이탈)
손절: 진입가 대비 -8%

param_grid: high_period=[20,40,60] × low_period=[10,15,20] → 9조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy


class DonchianV1Strategy(BaseStrategy):
    """클래식 Donchian 채널 돌파 전략."""

    strategy_id = "donchian_v1"
    strategy_group = "donchian_research"
    preferred_regimes: frozenset[str] = frozenset({"strong", "mixed"})

    _STOP_LOSS_PCT = 0.08

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Donchian 채널 상단 돌파 신호 생성."""
        high_period = int(params.get("high_period", 40))
        low_period = int(params.get("low_period", 15))

        if len(data) < high_period + 2:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]

        # 전일까지의 채널 경계 (look-ahead 방지)
        df["_upper"] = close.shift(1).rolling(high_period).max()
        df["_lower"] = close.shift(1).rolling(low_period).min()

        df["entry_signal"] = (close > df["_upper"]) & df["_upper"].notna()
        df["exit_signal"] = (close < df["_lower"]) & df["_lower"].notna()
        df.loc[df["entry_signal"], "exit_signal"] = False
        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_upper", "_lower"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """high_period × low_period = 3 × 3 = 9조합."""
        return [
            {"high_period": hp, "low_period": lp}
            for hp in [20, 40, 60]
            for lp in [10, 15, 20]
        ]

    @property
    def default_params(self) -> dict:
        return {"high_period": 40, "low_period": 15}
