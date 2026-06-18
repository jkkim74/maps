"""Multi-Asset Trend V1 전략 — 이중 이동평균 추세 추종 + 레짐 필터.

전략군: multi_asset (허용 MDD p95 = 22%)

진입 조건 AND:
  (1) MA(ma_fast) > MA(ma_slow)  — 골든크로스 추세 확인
  (2) 종가 > MA(ma_fast)         — 가격이 단기 MA 위에 위치
  (3) MA(ma_slow) > MA(ma_slow).shift(20) — 장기 MA 상승 국면 필터
      (변동성 횡보장 골든/데드크로스 반복 whipsaw 차단)

청산 조건:
  (1) MA(ma_fast) < MA(ma_slow)  — 데드크로스 (추세 전환)
      OR (2) 종가 < MA(ma_fast)  — 가격이 단기 MA 아래로 이탈

손절: 진입가 대비 -8%

param_grid: ma_fast=[15,20,25] × ma_slow=[40,60,80] → 9조합
  ※ ma_fast 하한 10→15 상향: 단기 MA가 짧을수록 변동성 장세에서 골든/데드크로스 반복 위험
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy


class MultiAssetTrendV1Strategy(BaseStrategy):
    """이중 이동평균 골든크로스 추세 추종 + 레짐 필터 전략."""

    strategy_id = "multi_asset_trend_v1"
    strategy_group = "multi_asset"
    preferred_regimes: frozenset[str] = frozenset({"strong", "mixed", "weak"})

    _STOP_LOSS_PCT = 0.08
    # 레짐 필터: ma_slow가 20봉 전보다 높아야 상승 국면.
    # 단기 fast MA(10~15일)는 변동성 장세에서 골든/데드크로스를 반복 생성하므로
    # 장기 slow MA 기울기로 거시적 추세를 확인한다.
    _REGIME_SHIFT = 20

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """이중 MA 골든크로스 + 레짐 필터 신호 생성."""
        ma_fast = int(params.get("ma_fast", 20))
        ma_slow = int(params.get("ma_slow", 60))

        if len(data) < ma_slow + self._REGIME_SHIFT + 2:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            return df

        df = data.copy()
        close = df["close"]

        df["_ma_fast"] = close.rolling(ma_fast).mean()
        df["_ma_slow"] = close.rolling(ma_slow).mean()

        # 전일 기준 크로스 판단 (look-ahead 방지)
        prev_fast = df["_ma_fast"].shift(1)
        prev_slow = df["_ma_slow"].shift(1)

        # 진입: 골든크로스 발생 AND 종가 > 단기 MA AND 레짐 필터
        golden_cross = (df["_ma_fast"] > df["_ma_slow"]) & (prev_fast <= prev_slow)
        price_above = close > df["_ma_fast"]
        # 레짐 필터: slow MA 기울기 양수 (횡보 whipsaw 차단)
        cond_regime = df["_ma_slow"] > df["_ma_slow"].shift(self._REGIME_SHIFT)
        df["entry_signal"] = golden_cross & price_above & cond_regime

        # 청산: 데드크로스 OR 종가가 단기 MA 아래
        dead_cross = df["_ma_fast"] < df["_ma_slow"]
        price_below = close < df["_ma_fast"].shift(1)
        df["exit_signal"] = dead_cross | price_below
        df.loc[df["entry_signal"], "exit_signal"] = False

        df["stop_price"] = close * (1.0 - self._STOP_LOSS_PCT)

        df.drop(columns=["_ma_fast", "_ma_slow"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """ma_fast × ma_slow = 3 × 3 = 9조합.

        ma_fast 하한 10→15 상향: 너무 짧은 fast MA는 변동성 횡보장에서
        골든/데드크로스를 과도하게 생성해 whipsaw 손실을 유발한다.
        """
        return [
            {"ma_fast": f, "ma_slow": s}
            for f in [15, 20, 25]
            for s in [40, 60, 80]
        ]

    def required_bars(self, params: dict) -> int:
        ma_slow = int(params.get("ma_slow", 60))
        return ma_slow + self._REGIME_SHIFT + 2

    @property
    def default_params(self) -> dict:
        return {"ma_fast": 15, "ma_slow": 60}
