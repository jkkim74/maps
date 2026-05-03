"""Pullback V3 전략 — 파일럿 전략.

전략군: pullback_short (허용 MDD p95 = 18%)

진입 3조건 AND:
  (1) MA5 > MA20
  (2) RSI(2) < rsi_threshold (기본 10)
  (3) low < 전일 low (추가 눌림 확인)

청산: close >= MA5 (보유 1일 이상)
손절: 진입가 대비 -5%

param_grid: rsi_threshold=[5,10,15] × ma_long=[20,30,40] → 9조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.common.exceptions import StrategyConfigError
from maps.strategy.base import BaseStrategy


def _rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class PullbackV3Strategy(BaseStrategy):
    """파일럿 전략: 단기 눌림목 매수."""

    strategy_id = "pullback_v3"
    strategy_group = "pullback_short"

    _MA_SHORT = 5
    _STOP_LOSS_PCT = 0.05

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """3조건 AND 진입 신호를 생성한다.

        미래 데이터를 참조하지 않도록 rolling/shift 만 사용한다.
        """
        rsi_threshold = float(params.get("rsi_threshold", 10))
        ma_long = int(params.get("ma_long", 20))

        if len(data) < ma_long + 1:
            data = data.copy()
            data["entry_signal"] = False
            data["exit_signal"] = False
            data["stop_price"] = np.nan
            return data

        df = data.copy()
        close = df["close"]

        df["_ma5"] = close.rolling(self._MA_SHORT).mean()
        df["_ma_long"] = close.rolling(ma_long).mean()
        df["_rsi2"] = _rsi(close, period=2)

        # 진입 3조건 (당일 데이터만 사용 → look-ahead 없음)
        cond_trend = df["_ma5"] > df["_ma_long"]
        cond_rsi = df["_rsi2"] < rsi_threshold
        cond_pullback = df["low"] < df["low"].shift(1)

        df["entry_signal"] = cond_trend & cond_rsi & cond_pullback

        # 청산: close >= MA5 (진입 당일 제외: shift(1) 적용)
        df["exit_signal"] = close >= df["_ma5"].shift(1)
        # 진입 당일은 청산 안 함
        df.loc[df["entry_signal"], "exit_signal"] = False

        # 손절가: 진입가(close) 대비 -5%
        df["stop_price"] = close * (1 - self._STOP_LOSS_PCT)

        # 내부 컬럼 제거
        df.drop(columns=["_ma5", "_ma_long", "_rsi2"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """rsi_threshold × ma_long = 3 × 3 = 9 조합."""
        return [
            {"rsi_threshold": rsi, "ma_long": ml}
            for rsi in [5, 10, 15]
            for ml in [20, 30, 40]
        ]

    @property
    def default_params(self) -> dict:
        return {"rsi_threshold": 10, "ma_long": 20}
