"""ATH Breakout V2 전략 — 신고가 돌파 + 거래량 급증 확인 + 레짐 필터.

전략군: ath_outlier (허용 MDD p95 = 35%)

진입 조건 AND:
  (1) 전일까지 breakout_period일 고가를 당일 종가가 상향 돌파
  (2) 당일 거래량 > vol_period일 평균 거래량 × vol_multiplier
      → 거래량 급증으로 돌파 신뢰도 강화
  (3) exit_ma > exit_ma.shift(20) — 상승 국면 필터
      (변동성 횡보장·베어마켓 거짓 돌파 차단)

청산 조건:
  (1) 종가가 exit_ma 이동평균 아래로 하락
      OR (2) 종가 < 진입 후 최고점 × (1 - trail_pct) — 트레일링 스탑
손절: 진입가 대비 -12%

param_grid: breakout_period=[120,200] × vol_multiplier=[1.5,2.0] × exit_ma=[20,30] → 8조합
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy


class ATHBreakoutV2Strategy(BaseStrategy):
    """신고가 돌파 + 거래량 급증 확인 + 레짐 필터 전략."""

    strategy_id = "ath_breakout_v2"
    strategy_group = "ath_outlier"
    preferred_regimes: frozenset[str] = frozenset({"strong"})

    _STOP_LOSS_PCT = 0.12
    _TRAIL_PCT = 0.08   # 최고점 대비 -8% 트레일링 스탑
    # 상승 국면 필터: exit_ma가 20봉 전보다 높아야 진입 허용.
    # 횡보·베어마켓에서 발생하는 거짓 돌파(spike-driven false breakout) 차단.
    _REGIME_SHIFT = 20

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """신고가 + 거래량 급증 + 레짐 필터 신호 생성."""
        breakout_period = int(params.get("breakout_period", 200))
        vol_period = int(params.get("vol_period", 20))
        vol_multiplier = float(params.get("vol_multiplier", 1.5))
        exit_ma = int(params.get("exit_ma", 20))

        min_bars = max(breakout_period, vol_period, exit_ma) + self._REGIME_SHIFT + 2
        if len(data) < min_bars:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]
        volume = df["volume"]

        df["_prev_high"] = close.shift(1).rolling(breakout_period).max()
        df["_avg_vol"] = volume.rolling(vol_period).mean()
        df["_exit_ma"] = close.rolling(exit_ma).mean()

        cond_breakout = (close > df["_prev_high"]) & df["_prev_high"].notna()
        cond_volume = volume > df["_avg_vol"] * vol_multiplier
        # 레짐 필터: exit_ma 기울기 양수 (상승 국면에서만 돌파 신뢰)
        cond_regime = df["_exit_ma"] > df["_exit_ma"].shift(self._REGIME_SHIFT)

        df["entry_signal"] = cond_breakout & cond_volume & cond_regime
        df["exit_signal"] = close < df["_exit_ma"]
        df.loc[df["entry_signal"], "exit_signal"] = False

        # 손절: 진입가 × (1 - STOP_LOSS_PCT) or 트레일링 스탑은 엔진이 관리
        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_prev_high", "_avg_vol", "_exit_ma"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """breakout_period × vol_multiplier × exit_ma = 2 × 2 × 2 = 8조합."""
        return [
            {"breakout_period": bp, "vol_multiplier": vm, "exit_ma": em}
            for bp in [120, 200]
            for vm in [1.5, 2.0]
            for em in [20, 30]
        ]

    def required_bars(self, params: dict) -> int:
        breakout_period = int(params.get("breakout_period", 200))
        vol_period = int(params.get("vol_period", 20))
        exit_ma = int(params.get("exit_ma", 20))
        return max(breakout_period, vol_period, exit_ma) + self._REGIME_SHIFT + 2

    @property
    def default_params(self) -> dict:
        return {"breakout_period": 200, "vol_period": 20, "vol_multiplier": 1.5, "exit_ma": 20}
