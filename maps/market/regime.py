"""장세 분석 — MarketRegime + WeeklyTrend.

설계서 SCR-03 기준.
MarketRegime: 5개 자산군의 5주 이동평균 기반 3단계 분류 (strong | mixed | weak).
WeeklyTrend:  10주/20주 MA 및 20주/40주 MA 방향 기반 통과 여부.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

import numpy as np


class RegimeLabel(str, Enum):
    STRONG = "strong"
    MIXED = "mixed"
    WEAK = "weak"


class WeeklyTrendLabel(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class AssetTrendInfo:
    """개별 자산군 추세 정보."""

    name: str
    direction: str      # up | down | flat
    value: float | None = None
    above_ma5w: bool = False


@dataclass
class RegimeResult:
    """장세 분석 결과."""

    regime: RegimeLabel
    weekly_trend: WeeklyTrendLabel
    limit_ratio: float              # 현재 진입 한도 비율 (0~1)
    kospi_ts: float | None          # 코스피 추세 강도 점수
    assets: list[AssetTrendInfo] = field(default_factory=list)
    evaluated_at: datetime.datetime = field(default_factory=datetime.datetime.now)

    # ── 한도 비율 테이블 (설계서 §3) ──────────────────────────────────────────
    # strong + pass  → 1.0
    # mixed  + pass  → 0.5
    # weak   + pass  → 0.25 (ATH/Pullback only)
    # * + fail        → 0.0

    @property
    def entry_limit_ratio(self) -> float:
        """현재 매트릭스 셀에 따른 진입 한도 비율."""
        if self.weekly_trend == WeeklyTrendLabel.FAIL:
            return 0.0
        mapping = {
            RegimeLabel.STRONG: 1.0,
            RegimeLabel.MIXED: 0.5,
            RegimeLabel.WEAK: 0.25,
        }
        return mapping[self.regime]


class PriceSeriesProvider(Protocol):
    """자산군 주가 시계열 제공 인터페이스."""

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        """최근 n_weeks 주봉 종가를 반환한다."""
        ...


class MarketRegimeAnalyzer:
    """MarketRegime × WeeklyTrend 매트릭스를 계산한다.

    현재는 더미 데이터를 반환하는 스텁 구현이며,
    Phase 3 KRX API 연동 시 PriceSeriesProvider 구현체를 주입한다.
    """

    _ASSETS = ["KOSPI", "KOSDAQ", "S&P 500", "NASDAQ", "USD/KRW", "금", "WTI", "구리"]
    _MA5W = 5   # 5주 이동평균
    _MA10W = 10
    _MA20W = 20
    _MA40W = 40

    def __init__(self, provider: PriceSeriesProvider | None = None) -> None:
        self._provider = provider

    def analyze(self) -> RegimeResult:
        """현재 장세를 분석한다.

        provider가 없으면 혼조(mixed) + pass 기본값을 반환한다.
        """
        if self._provider is None:
            return self._stub_result()
        return self._compute()

    def _compute(self) -> RegimeResult:
        """실 데이터로 장세를 계산한다."""
        assert self._provider is not None

        assets: list[AssetTrendInfo] = []
        up_count = 0
        total = 0
        kospi_ts: float | None = None

        for name in self._ASSETS:
            closes = self._provider.get_weekly_closes(name, self._MA5W + 1)
            if len(closes) < self._MA5W:
                assets.append(AssetTrendInfo(name=name, direction="flat"))
                continue

            arr = np.array(closes[-self._MA5W:], dtype=float)
            ma5 = float(arr.mean())
            last = float(closes[-1])
            above = last > ma5
            direction = "up" if above else "down"

            # KOSPI TS: 현재가 vs 5주 MA 괴리율 → 0~100 정규화
            # ±20% 범위를 0~100으로 선형 매핑 (0% 괴리=50점)
            if name == "KOSPI" and ma5 > 0:
                deviation = (last - ma5) / ma5
                kospi_ts = round(max(0.0, min(100.0, 50.0 + deviation * 250)), 1)

            assets.append(AssetTrendInfo(name=name, direction=direction, value=last, above_ma5w=above))
            if direction == "up":
                up_count += 1
            total += 1

        if total == 0:
            regime = RegimeLabel.MIXED
        else:
            up_ratio = up_count / total
            if up_ratio >= 0.7:
                regime = RegimeLabel.STRONG
            elif up_ratio >= 0.4:
                regime = RegimeLabel.MIXED
            else:
                regime = RegimeLabel.WEAK

        weekly_trend = self._check_weekly_trend()

        return RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=kospi_ts,
            assets=assets,
        )

    def _check_weekly_trend(self) -> WeeklyTrendLabel:
        """10/20주 MA 및 20/40주 MA 방향 확인."""
        if self._provider is None:
            return WeeklyTrendLabel.PASS
        try:
            closes = self._provider.get_weekly_closes("KOSPI", self._MA40W + 1)
            if len(closes) < self._MA40W:
                return WeeklyTrendLabel.PASS
            arr = np.array(closes, dtype=float)
            ma10 = float(arr[-self._MA10W:].mean())
            ma20 = float(arr[-self._MA20W:].mean())
            ma40 = float(arr[-self._MA40W:].mean())
            if ma10 > ma20 and ma20 > ma40:
                return WeeklyTrendLabel.PASS
            return WeeklyTrendLabel.FAIL
        except Exception:
            return WeeklyTrendLabel.PASS

    def _stub_result(self) -> RegimeResult:
        assets = [
            AssetTrendInfo(name=n, direction="up") for n in self._ASSETS[:4]
        ] + [
            AssetTrendInfo(name=n, direction="down") for n in self._ASSETS[4:]
        ]
        return RegimeResult(
            regime=RegimeLabel.MIXED,
            weekly_trend=WeeklyTrendLabel.PASS,
            limit_ratio=0.5,
            kospi_ts=None,
            assets=assets,
        )
