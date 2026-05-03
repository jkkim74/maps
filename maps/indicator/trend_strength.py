"""추세 강도(TrendStrength) 계산기.

설계서 SCR-09, SCR-04 기준.
종목별 일봉 기반 0~100 점수를 산출하고 S1~S5 버킷으로 분류한다.

점수 산식 (기본):
  - 20일 이동평균 대비 현재가 위치:     0~40점 (40%)
  - RSI(14):                           0~30점 (30%)
  - 거래량 이동평균 대비:               0~30점 (30%)

S1 [0,20)   매우 약한 추세 — 진입 제외 대상 (설계서 §4 게이트)
S2 [20,40)  약한 추세
S3 [40,60)  중립
S4 [60,80)  강한 추세
S5 [80,100] 매우 강한 추세
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from maps.common.constants import TREND_STRENGTH_BUCKETS


@dataclass
class TsScore:
    """종목 추세 강도 점수."""

    ticker: str
    score: float            # 0~100
    bucket: str             # S1 ~ S5
    bucket_label: str
    above_ma20: bool
    rsi14: float | None
    volume_ratio: float | None
    as_of: datetime.date


@dataclass
class TsUniverseResult:
    """유니버스 전체 추세 강도 집계."""

    ref_date: datetime.date
    scores: list[TsScore] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)    # 데이터 부족 종목

    @property
    def bucket_counts(self) -> dict[str, int]:
        counts = {b["grade"]: 0 for b in TREND_STRENGTH_BUCKETS}
        for s in self.scores:
            counts[s.bucket] = counts.get(s.bucket, 0) + 1
        return counts

    @property
    def s1_excluded_count(self) -> int:
        """S1 (진입 제외) 종목 수."""
        return sum(1 for s in self.scores if s.bucket == "S1")


def _classify_bucket(score: float) -> tuple[str, str]:
    """점수를 버킷 등급과 레이블로 변환한다."""
    for b in TREND_STRENGTH_BUCKETS:
        if b["lo"] <= score < b["hi"]:
            return b["grade"], b["label"]
    return "S1", TREND_STRENGTH_BUCKETS[0]["label"]


class TrendStrengthCalculator:
    """종목별 추세 강도를 계산한다.

    Parameters
    ----------
    ma_period: 이동평균 기간 (기본 20일)
    rsi_period: RSI 기간 (기본 14일)
    vol_period: 거래량 이동평균 기간 (기본 20일)
    min_bars:   최소 필요 데이터 수 (부족 시 missing 처리)
    """

    def __init__(
        self,
        ma_period: int = 20,
        rsi_period: int = 14,
        vol_period: int = 20,
        min_bars: int = 100,
    ) -> None:
        self._ma = ma_period
        self._rsi = rsi_period
        self._vol = vol_period
        self._min_bars = min_bars

    def score_one(self, ticker: str, ohlcv: pd.DataFrame, as_of: datetime.date) -> TsScore | None:
        """단일 종목의 추세 강도를 계산한다.

        Parameters
        ----------
        ohlcv: date 인덱스 OHLCV DataFrame. 컬럼: open, high, low, close, volume.

        Returns None if insufficient data (caller adds to missing list).
        """
        if len(ohlcv) < self._min_bars:
            return None

        close = ohlcv["close"].astype(float)
        volume = ohlcv["volume"].astype(float) if "volume" in ohlcv.columns else None

        last_close = float(close.iloc[-1])

        # ── MA 점수 (0~40) ────────────────────────────────────────────────────
        ma20 = float(close.rolling(self._ma).mean().iloc[-1])
        above_ma = last_close > ma20
        ma_ratio = (last_close - ma20) / ma20 if ma20 > 0 else 0.0
        ma_score = min(max(ma_ratio * 200 + 20, 0.0), 40.0)

        # ── RSI 점수 (0~30) ───────────────────────────────────────────────────
        rsi_val: float | None = None
        rsi_score = 15.0
        if len(close) >= self._rsi + 1:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(self._rsi).mean()
            loss = (-delta.clip(upper=0)).rolling(self._rsi).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_series = 100 - (100 / (1 + rs))
            rsi_val = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else None
            if rsi_val is not None:
                rsi_score = min(max((rsi_val - 30) / 40 * 30, 0.0), 30.0)

        # ── 거래량 점수 (0~30) ────────────────────────────────────────────────
        vol_ratio: float | None = None
        vol_score = 15.0
        if volume is not None and len(volume) >= self._vol:
            vol_ma = float(volume.rolling(self._vol).mean().iloc[-1])
            last_vol = float(volume.iloc[-1])
            if vol_ma > 0:
                vol_ratio = last_vol / vol_ma
                vol_score = min(max((vol_ratio - 0.5) / 1.5 * 30, 0.0), 30.0)

        total = ma_score + rsi_score + vol_score
        bucket, label = _classify_bucket(total)

        return TsScore(
            ticker=ticker,
            score=round(total, 2),
            bucket=bucket,
            bucket_label=label,
            above_ma20=above_ma,
            rsi14=round(rsi_val, 2) if rsi_val is not None else None,
            volume_ratio=round(vol_ratio, 3) if vol_ratio is not None else None,
            as_of=as_of,
        )

    def score_universe(
        self,
        ohlcv_map: dict[str, pd.DataFrame],
        ref_date: datetime.date,
    ) -> TsUniverseResult:
        """유니버스 전체 종목의 추세 강도를 계산한다.

        Parameters
        ----------
        ohlcv_map: {ticker: OHLCV DataFrame} 딕셔너리.
        """
        result = TsUniverseResult(ref_date=ref_date)
        for ticker, df in ohlcv_map.items():
            ts = self.score_one(ticker, df, ref_date)
            if ts is None:
                result.missing.append(ticker)
            else:
                result.scores.append(ts)
        return result
