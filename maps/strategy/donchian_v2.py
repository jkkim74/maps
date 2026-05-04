"""Donchian Channel V2 전략 — 채널 돌파 + 모멘텀 필터.

전략군: donchian_research (허용 MDD p95 = 30%)

진입 조건 AND:
  (1) 종가 > 전일까지 channel_period일 최고 종가 (채널 상단 돌파)
  (2) ROC(roc_period) > 0  — 수익률 모멘텀 양수 (추세 방향 확인)

청산 조건:
  (1) 종가 < 전일까지 exit_period일 최저 종가 (채널 하단 이탈)

손절: 진입가 대비 -10%

param_grid: channel_period=[20,40,60] × roc_period=[5,10,20] → 9조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy


class DonchianV2Strategy(BaseStrategy):
    """Donchian 채널 돌파 + ROC 모멘텀 필터 전략."""

    strategy_id = "donchian_v2"
    strategy_group = "donchian_research"

    _STOP_LOSS_PCT = 0.10
    _EXIT_RATIO = 0.5   # 청산 채널 = 진입 채널의 50% 기간

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """채널 돌파 + ROC 모멘텀 신호 생성."""
        channel_period = int(params.get("channel_period", 40))
        roc_period = int(params.get("roc_period", 10))
        exit_period = max(5, int(channel_period * self._EXIT_RATIO))

        if len(data) < channel_period + roc_period + 2:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]

        df["_upper"] = close.shift(1).rolling(channel_period).max()
        df["_lower"] = close.shift(1).rolling(exit_period).min()

        # ROC: (현재 종가 / N일 전 종가) - 1
        df["_roc"] = close / close.shift(roc_period) - 1.0

        cond_breakout = (close > df["_upper"]) & df["_upper"].notna()
        cond_momentum = df["_roc"] > 0.0

        df["entry_signal"] = cond_breakout & cond_momentum
        df["exit_signal"] = (close < df["_lower"]) & df["_lower"].notna()
        df.loc[df["entry_signal"], "exit_signal"] = False
        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_upper", "_lower", "_roc"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """channel_period × roc_period = 3 × 3 = 9조합."""
        return [
            {"channel_period": cp, "roc_period": rp}
            for cp in [20, 40, 60]
            for rp in [5, 10, 20]
        ]

    @property
    def default_params(self) -> dict:
        return {"channel_period": 40, "roc_period": 10}
