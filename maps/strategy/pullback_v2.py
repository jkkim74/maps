"""Pullback V2 전략 — Stochastic 기반 눌림목 매수.

전략군: pullback_short (허용 MDD p95 = 18%)

진입 3조건 AND:
  (1) MA5 > MA(ma_long)  — 단기 상승 추세
  (2) Stochastic %K(k_period) < k_threshold  — 과매도 눌림목
  (3) low < 전일 low  — 추가 눌림 확인

청산: close >= MA5 (보유 1일 이상)
손절: 진입가 대비 -6%

param_grid: k_period=[5,9,14] × ma_long=[20,30] × k_threshold=[20,25] → 12조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.common.exceptions import StrategyConfigError
from maps.strategy.base import BaseStrategy, StrategyType

_SMOOTH = 3   # Stochastic %K 스무딩 기간


class PullbackV2Strategy(BaseStrategy):
    """Stochastic %K 과매도 + 눌림목 진입 전략."""

    strategy_id = "pullback_v2"
    strategy_group = "pullback_short"
    strategy_type = StrategyType.PULLBACK
    preferred_regimes: frozenset[str] = frozenset({"strong", "mixed"})

    _MA_SHORT = 5
    _STOP_LOSS_PCT = 0.06

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Stochastic 기반 눌림목 신호 생성."""
        k_period = int(params.get("k_period", 9))
        ma_long = int(params.get("ma_long", 30))
        k_threshold = float(params.get("k_threshold", 25.0))

        if len(data) < max(ma_long, k_period) + _SMOOTH + 2:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]

        df["_ma5"] = close.rolling(self._MA_SHORT).mean()
        df["_ma_long"] = close.rolling(ma_long).mean()

        low_min = df["low"].rolling(k_period).min()
        high_max = df["high"].rolling(k_period).max()
        raw_k = 100.0 * (close - low_min) / (high_max - low_min).replace(0.0, np.nan)
        df["_stoch_k"] = raw_k.rolling(_SMOOTH).mean()

        cond_trend = df["_ma5"] > df["_ma_long"]
        cond_stoch = df["_stoch_k"] < k_threshold
        cond_pullback = df["low"] < df["low"].shift(1)

        df["entry_signal"] = cond_trend & cond_stoch & cond_pullback
        df["exit_signal"] = close >= df["_ma5"].shift(1)
        df.loc[df["entry_signal"], "exit_signal"] = False
        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_ma5", "_ma_long", "_stoch_k"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """k_period × ma_long × k_threshold = 3 × 2 × 2 = 12조합."""
        return [
            {"k_period": k, "ma_long": ml, "k_threshold": t}
            for k in [5, 9, 14]
            for ml in [20, 30]
            for t in [20, 25]
        ]

    @property
    def default_params(self) -> dict:
        return {"k_period": 9, "ma_long": 30, "k_threshold": 25}
