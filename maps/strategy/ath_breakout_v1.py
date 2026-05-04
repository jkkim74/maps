"""ATH Breakout V1 전략 — N일 신고가 돌파 모멘텀.

전략군: ath_outlier (허용 MDD p95 = 35%)

진입 조건:
  (1) 전일까지 breakout_period일 고가를 당일 종가가 상향 돌파
      → 신고가 갱신 = 강한 모멘텀 확인

청산 조건:
  (1) 종가가 exit_ma 기간 이동평균 아래로 하락
손절: 진입가 대비 -10% (모멘텀 전략 특성상 넉넉하게)

param_grid: breakout_period=[120,200,252] × exit_ma=[20,30,40] → 9조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy


class ATHBreakoutV1Strategy(BaseStrategy):
    """N일 신고가 돌파 진입 전략."""

    strategy_id = "ath_breakout_v1"
    strategy_group = "ath_outlier"

    _STOP_LOSS_PCT = 0.10

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """전일까지 N일 고가 돌파 신호 생성."""
        breakout_period = int(params.get("breakout_period", 200))
        exit_ma = int(params.get("exit_ma", 30))

        if len(data) < breakout_period + 2:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]

        # 전일까지 breakout_period 기간 최고 종가 (look-ahead 방지: shift(1) 후 rolling)
        df["_prev_high"] = close.shift(1).rolling(breakout_period).max()
        df["_exit_ma"] = close.rolling(exit_ma).mean()

        # 진입: 전일 신고가를 당일 종가가 상향 돌파
        df["entry_signal"] = (close > df["_prev_high"]) & df["_prev_high"].notna()
        # 청산: 종가가 exit_ma 아래
        df["exit_signal"] = close < df["_exit_ma"]
        df.loc[df["entry_signal"], "exit_signal"] = False
        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_prev_high", "_exit_ma"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """breakout_period × exit_ma = 3 × 3 = 9조합."""
        return [
            {"breakout_period": bp, "exit_ma": em}
            for bp in [120, 200, 252]
            for em in [20, 30, 40]
        ]

    @property
    def default_params(self) -> dict:
        return {"breakout_period": 200, "exit_ma": 30}
