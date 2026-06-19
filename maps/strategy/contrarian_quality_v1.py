"""Contrarian Quality Accumulation V1 전략 — 코스톨라니 역발상 분할 매수.

전략군: contrarian_quality (허용 MDD p95 = 25%)

코스톨라니 스타일: 공포장 또는 약세장 하락 시, 대중이 기피하지만
기업가치와 업황 회복 가능성이 있는 역발상 우량주를 3단계 분할 매수한다.

진입 조건 (AND):
  (1) valuation_margin_score ≥ 65 (가치 안전마진 확보)
  (2) 52주 고점 대비 -20% 이상 하락 (공포 구간)
  (3) 거래량이 완전히 소멸하지 않음 (최근 5일 평균 > 0)
  (4) 추세 점수(trend_strength) < 45 (아직 하락 또는 바닥 구간)
  (5) 단기 RSI(14) < 40 (과매도 수준)
  (6) 부채 과다 위험 없음 (별도 필터는 ValuationMarginScorer가 담당)

제외 조건:
  - 상장 180일 미만 신규 상장 종목
  - valuation_margin_score < 60 (점수 미충족)

분할 매수 3단계:
  1차: 계획 비중의 25%
  2차: 추가 -5% 하락 또는 5일 지지 확인 시 35%
  3차: 추세 회복 신호(close > MA20) 확인 시 40%

손절: thesis 무효화(실적 급락·재무위험 급등) 우선,
      기술적 긴급 손절은 고점 대비 -35% 또는 ATR×3

preferred_regimes: weak, mixed (WEAK+HIGH에서 선택적 허용)
strategy_type: CONTRARIAN_QUALITY
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.strategy.base import BaseStrategy, StrategyType


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class ContrarianQualityAccumulationV1Strategy(BaseStrategy):
    """코스톨라니 역발상 분할 매수 전략 V1.

    공포 구간에서 가치 안전마진을 확보한 우량주를 분할 매수한다.
    WEAK+HIGH 시황에서 contrarian_accumulation_enabled 시 선택적으로 허용된다.
    """

    strategy_id = "contrarian_quality_accumulation_v1"
    strategy_group = "contrarian_quality"
    strategy_type = StrategyType.CONTRARIAN_QUALITY
    preferred_regimes: frozenset[str] = frozenset({"weak", "mixed"})

    # 진입 파라미터 기본값
    _HIGH_DROP_THRESHOLD = 0.20   # 52주 고점 대비 하락 기준 (-20%)
    _RSI_PERIOD = 14
    _RSI_OVERSOLD = 40.0          # RSI 과매도 기준
    _MA20 = 20                    # 추세 회복 확인 MA
    _MA60 = 60                    # 중기 추세 MA
    _VOL_MIN_DAYS = 5             # 거래량 소멸 판단 최소 기간

    # 손절 기준
    _EMERGENCY_STOP_PCT = 0.35    # 52주 고점 대비 -35% 긴급 손절
    _THESIS_STOP_FACTOR = 0.92   # 진입가 대비 -8% 기본 기술적 손절 (thesis_stop은 외부에서 관리)

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """역발상 분할 매수 진입/청산 신호를 생성한다."""
        rsi_oversold = float(params.get("rsi_oversold", self._RSI_OVERSOLD))
        high_drop_pct = float(params.get("high_drop_pct", self._HIGH_DROP_THRESHOLD))
        min_bars = self._MA60 + 5

        if len(data) < min_bars:
            df = data.copy()
            df["entry_signal"] = False
            df["exit_signal"] = False
            df["stop_price"] = np.nan
            df["buy_stage"] = 0
            return df

        df = data.copy()
        close = df["close"]
        high = df["high"]
        volume = df["volume"]

        df["_rsi14"] = _rsi(close, period=self._RSI_PERIOD)
        df["_ma20"] = close.rolling(self._MA20).mean()
        df["_ma60"] = close.rolling(self._MA60).mean()
        df["_vol5_avg"] = volume.rolling(self._VOL_MIN_DAYS).mean()

        # 52주 고점 (252 거래일)
        df["_high52w"] = high.rolling(min(252, len(df))).max()

        # ── 진입 조건 ──────────────────────────────────────────────────────────
        # (1) 52주 고점 대비 하락 충분
        cond_drop = close <= df["_high52w"] * (1.0 - high_drop_pct)
        # (2) 거래량 소멸 없음 (5일 평균 > 0)
        cond_volume = df["_vol5_avg"] > 0
        # (3) RSI 과매도
        cond_rsi = df["_rsi14"] < rsi_oversold
        # (4) MA20 아래 (아직 회복 전 — 분할 매수 1차)
        cond_below_ma20 = close < df["_ma20"]

        df["entry_signal"] = cond_drop & cond_volume & cond_rsi & cond_below_ma20

        # ── 청산 조건 ──────────────────────────────────────────────────────────
        # MA20 회복 (추세 회복 신호) — CORE 종목은 thesis stop 우선이나
        # 전략 자체로는 MA20 크로스오버를 청산 트리거로 사용
        ma20_cross_up = (close >= df["_ma20"]) & (close.shift(1) < df["_ma20"].shift(1))
        df["exit_signal"] = ma20_cross_up
        # 진입 당일 청산 방지
        df.loc[df["entry_signal"], "exit_signal"] = False

        # ── 손절가 ─────────────────────────────────────────────────────────────
        # 진입가 대비 -8% (기술적 기본 손절)
        # thesis_stop 및 emergency_stop은 KostolanyPriceCalculator가 별도 산출
        df["stop_price"] = close * self._THESIS_STOP_FACTOR

        # ── 매수 단계 (buy_stage) ──────────────────────────────────────────────
        # 1차: 진입 신호 발생 시
        # 2차: 진입 후 -5% 추가 하락
        # 3차: MA20 회복 확인
        # 스케줄러·주문 사이클에서 buy_stage를 읽어 포지션 비중을 결정한다.
        df["buy_stage"] = 0
        df.loc[df["entry_signal"], "buy_stage"] = 1

        # 내부 컬럼 정리
        df.drop(columns=["_rsi14", "_ma20", "_ma60", "_vol5_avg", "_high52w"], inplace=True)
        return df

    def param_grid(self) -> list[dict]:
        """rsi_oversold × high_drop_pct = 3 × 3 = 9 조합."""
        return [
            {"rsi_oversold": rsi, "high_drop_pct": drop}
            for rsi in [35.0, 40.0, 45.0]
            for drop in [0.15, 0.20, 0.25]
        ]

    def required_bars(self, params: dict) -> int:
        return self._MA60 + 5

    @property
    def default_params(self) -> dict:
        return {
            "rsi_oversold": self._RSI_OVERSOLD,
            "high_drop_pct": self._HIGH_DROP_THRESHOLD,
        }
