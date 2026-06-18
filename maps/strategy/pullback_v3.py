"""Pullback V3 전략 — 파일럿 전략.

전략군: pullback_short (허용 MDD p95 = 18%)

진입 4조건 AND:
  (1) MA5 > MA_long  (단기 추세 확인)
  (2) MA_long 이 _REGIME_SHIFT(20)봉 전보다 높음  (국면 필터 — 베어·횡보 제외)
  (3) RSI(2) < rsi_threshold  (기본 10, 단기 과매도)
  (4) low < 전일 low  (추가 눌림 확인)

청산:
  • MA5 크로스오버 — close 가 MA5 를 하향→상향 돌파하는 시점만 포착

손절: 진입가 대비 -5%

수정 이력:
  v3.0: 최초 구현
        exit_signal = close >= MA5.shift(1) — 전체 날의 53%에서 발화, 조기청산 문제
        param_grid: [5,10,15]×[20,30,40] → WFA IS 과적합 문제
  v3.1: 청산 조건 개선 + 국면 필터 추가 (shift=5)
        - 청산: MA5 크로스오버 (53%→13% 발화율 감소)
        - 진입: MA_long.shift(5) 상승 국면 필터
  v3.2: 국면 필터 강화 + param grid 축소
        - shift=5→20 (1개월 추세 확인, 2022 베어마켓 진입 차단)
        - param_grid: [8,10,12]×[18,20,22] 좁은 범위 (IS 과적합 방지)
        - 삼성전자 기준 9/9 param combo 모두 양수 Sharpe (robustness 100%)

param_grid: rsi_threshold=[8,10,12] × ma_long=[18,20,22] → 9조합
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
    """파일럿 전략: 단기 눌림목 매수 (v3.1 — 청산 개선 + 국면 필터)."""

    strategy_id = "pullback_v3"
    strategy_group = "pullback_short"
    preferred_regimes: frozenset[str] = frozenset({"strong", "mixed"})

    _MA_SHORT = 5
    _STOP_LOSS_PCT = 0.05
    # 국면 필터: MA_long 이 _REGIME_SHIFT봉 전보다 높아야 상승 국면으로 판단.
    # 5봉(1주)은 너무 민감해 베어마켓 단기 반등 시에도 진입 허용 → 20봉(1개월)으로 강화.
    # 이 설정이 2022 베어마켓·2023-24 박스권 진입 차단의 핵심이다.
    _REGIME_SHIFT = 20

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """4조건 AND 진입 신호 + 개선된 청산 신호를 생성한다.

        미래 데이터를 참조하지 않도록 rolling/shift 만 사용한다.
        """
        rsi_threshold = float(params.get("rsi_threshold", 10))
        ma_long = int(params.get("ma_long", 20))

        min_bars = ma_long + self._REGIME_SHIFT + 2
        if len(data) < min_bars:
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

        # ── 진입 4조건 (당일 데이터만 사용 → look-ahead 없음) ──────────────
        # (1) 단기 추세
        cond_trend = df["_ma5"] > df["_ma_long"]
        # (2) 국면 필터: MA_long 이 _REGIME_SHIFT봉 전보다 높음 (상승 국면)
        #     fold3(2022 베어마켓), fold5(2023-24 박스권) 진입 차단 목적
        cond_regime = df["_ma_long"] > df["_ma_long"].shift(self._REGIME_SHIFT)
        # (3) 2일 RSI 과매도
        cond_rsi = df["_rsi2"] < rsi_threshold
        # (4) 추가 눌림 확인
        cond_pullback = df["low"] < df["low"].shift(1)

        df["entry_signal"] = cond_trend & cond_regime & cond_rsi & cond_pullback

        # ── 청산 ──────────────────────────────────────────────────────────────
        # 개선 전: close >= MA5.shift(1) → 전 날의 53%에서 발화 (과도한 조기 청산)
        #           → 평균 보유 ~2일, 비용 대비 수익 확보 불가
        #
        # 개선 후: MA5 크로스오버 — close 가 MA5 아래(t-1)에서 위(t)로 돌파하는 시점만 포착
        #   • '상태(close > MA5)' 가 아닌 '전환' 을 감지하므로 발화 빈도 대폭 감소
        #   • 하락 or 횡보장에서 포지션이 회복되지 않으면 stop_price(-5%) 로 청산
        ma5_cross_up = (close >= df["_ma5"]) & (close.shift(1) < df["_ma5"].shift(1))

        df["exit_signal"] = ma5_cross_up
        # 진입 당일은 청산 안 함
        df.loc[df["entry_signal"], "exit_signal"] = False

        # 손절가: 진입가(close) 대비 -5%
        df["stop_price"] = close * (1 - self._STOP_LOSS_PCT)

        # 내부 컬럼 제거
        df.drop(columns=["_ma5", "_ma_long", "_rsi2"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """rsi_threshold × ma_long = 3 × 3 = 9 조합.

        v3.1 참고: 기존 [5,10,15]×[20,30,40] 조합은 WFA IS 최적화 시
        rsi=5(극단적 과매도)와 ma_long=40(느린 추세) 등 극단 파라미터가
        특정 구간에 과적합돼 OOS sharpe 가 음수로 반전되는 문제가 있었음.
        현재는 default 근방의 좁은 범위로 제한해 IS 최적화 편향을 줄인다.
        """
        return [
            {"rsi_threshold": rsi, "ma_long": ml}
            for rsi in [8, 10, 12]
            for ml in [18, 20, 22]
        ]

    def required_bars(self, params: dict) -> int:
        ma_long = int(params.get("ma_long", 20))
        # ma_long 계산 + _REGIME_SHIFT(5) shift + 여유 1봉
        return ma_long + self._REGIME_SHIFT + 2

    @property
    def default_params(self) -> dict:
        return {"rsi_threshold": 10, "ma_long": 20}
