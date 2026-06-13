"""장세 분석 — MarketRegime + WeeklyTrend + VolRegime.

설계서 SCR-03 기준.
MarketRegime: 5개 자산군의 5주 이동평균 기반 3단계 분류 (strong | mixed | weak).
WeeklyTrend:  10주/20주 MA 및 20주/40주 MA 방향 기반 통과 여부.
VolRegime:    KOSPI 20주 실현변동성 기반 변동성 국면 (low | normal | high).
              high_vol 시 entry_limit_ratio를 1단계 하향한다 (WEAK+HIGH → 완전 중단).
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── 지수 티커 매핑 ────────────────────────────────────────────────────────────
_KRX_TICKERS: dict[str, str] = {
    "KOSPI": "1001",
    "KOSDAQ": "2001",
}
_YF_TICKERS: dict[str, str] = {
    "S&P 500": "^GSPC",
    "NASDAQ":  "^IXIC",
    "USD/KRW": "KRW=X",
    "금":      "GC=F",
    "WTI":     "CL=F",
    "구리":    "HG=F",
}


class RegimeLabel(str, Enum):
    STRONG = "strong"
    MIXED = "mixed"
    WEAK = "weak"


class WeeklyTrendLabel(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class VolRegimeLabel(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


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
    vol_regime: VolRegimeLabel = VolRegimeLabel.NORMAL  # 변동성 국면

    # ── 한도 비율 테이블 (vol_regime 반영) ────────────────────────────────────
    # weekly_trend FAIL          → 0.0 (항상)
    # strong + low/normal + pass → 1.0
    # strong + high       + pass → 0.5  (1단계 하향)
    # mixed  + low/normal + pass → 0.5
    # mixed  + high       + pass → 0.25 (1단계 하향)
    # weak   + low/normal + pass → 0.25
    # weak   + high       + pass → 0.0  (완전 중단)

    @property
    def entry_limit_ratio(self) -> float:
        """현재 매트릭스 셀에 따른 진입 한도 비율.

        고변동성(HIGH) 국면에서는 비율을 1단계 하향한다.
        """
        if self.weekly_trend == WeeklyTrendLabel.FAIL:
            return 0.0
        base_mapping = {
            RegimeLabel.STRONG: 1.0,
            RegimeLabel.MIXED: 0.5,
            RegimeLabel.WEAK: 0.25,
        }
        base = base_mapping[self.regime]
        if self.vol_regime == VolRegimeLabel.HIGH:
            downgrade = {1.0: 0.5, 0.5: 0.25, 0.25: 0.0}
            return downgrade[base]
        return base


class PriceSeriesProvider(Protocol):
    """자산군 주가 시계열 제공 인터페이스."""

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        """최근 n_weeks 주봉 종가를 반환한다."""
        ...


class CombinedWeeklyProvider:
    """pykrx(국내) + yfinance(해외) 통합 주봉 종가 제공자."""

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name in _KRX_TICKERS:
            return self._from_krx(asset_name, n_weeks)
        if asset_name in _YF_TICKERS:
            return self._from_yfinance(asset_name, n_weeks)
        return []

    def _from_krx(self, asset_name: str, n_weeks: int) -> list[float]:
        ticker = _KRX_TICKERS[asset_name]
        try:
            from pykrx import stock  # noqa: PLC0415
        except ImportError:
            logger.warning("pykrx not installed")
            return []

        end = datetime.date.today()
        start = end - datetime.timedelta(days=max(n_weeks * 10, 120))
        prev_raise = logging.raiseExceptions
        prev_level = logging.root.manager.disable
        try:
            logging.raiseExceptions = False
            logging.disable(logging.CRITICAL)
            df = stock.get_index_ohlcv_by_date(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                ticker,
                freq="d",
            )
        except Exception as exc:
            logger.warning("KRX daily data unavailable [%s]: %s", asset_name, exc)
            return []
        finally:
            logging.disable(prev_level)
            logging.raiseExceptions = prev_raise

        if df is None or df.empty:
            return []

        close_col = "종가" if "종가" in df.columns else "Close" if "Close" in df.columns else None
        if close_col is None:
            logger.warning("KRX close column missing [%s]: cols=%s", asset_name, list(df.columns))
            return []

        weekly = df[close_col].resample("W").last().dropna()
        return [float(v) for v in weekly.tail(n_weeks).tolist()]

    def _from_yfinance(self, asset_name: str, n_weeks: int) -> list[float]:
        ticker = _YF_TICKERS[asset_name]
        try:
            import yfinance as yf  # noqa: PLC0415
        except ImportError:
            logger.warning("yfinance not installed; overseas data unavailable")
            return []

        end = datetime.date.today() + datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=max(n_weeks * 10, 120))
        try:
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.warning("yfinance data unavailable [%s]: %s", asset_name, exc)
            return []

        if df is None or df.empty:
            return []

        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        weekly = close.resample("W").last().dropna()
        return [float(v) for v in weekly.tail(n_weeks).tolist()]


class MarketRegimeAnalyzer:
    """MarketRegime × WeeklyTrend × VolRegime 매트릭스를 계산한다."""

    _ASSETS = ["KOSPI", "KOSDAQ", "S&P 500", "NASDAQ", "USD/KRW", "금", "WTI", "구리"]
    _MA5W = 5   # 5주 이동평균
    _MA10W = 10
    _MA20W = 20
    _MA40W = 40

    # 변동성 국면 기준 (KOSPI 20주 수익률 연환산 표준편차)
    _VOL_LOOKBACK = 20           # 20주 수익률 윈도우
    _VOL_LOW_THRESHOLD = 0.12    # 12% 미만 → LOW
    _VOL_HIGH_THRESHOLD = 0.20   # 20% 초과 → HIGH

    def __init__(
        self,
        provider: PriceSeriesProvider | None = None,
        *,
        override_regime: str | None = None,
        override_trend: str | None = None,
    ) -> None:
        self._provider = provider
        self._override_regime = override_regime
        self._override_trend = override_trend

    def analyze(self) -> RegimeResult:
        """현재 장세를 분석한다.

        override_regime / override_trend 가 지정되면 해당 값을 즉시 반환한다.
        provider가 없으면 혼조(mixed) + pass 기본값을 반환한다.
        """
        if self._override_regime is not None or self._override_trend is not None:
            return self._override_result()
        if self._provider is None:
            return self._stub_result()
        return self._compute()

    def _override_result(self) -> RegimeResult:
        """수동 오버라이드 값으로 RegimeResult 를 생성한다."""
        try:
            regime = RegimeLabel(self._override_regime) if self._override_regime else RegimeLabel.MIXED
        except ValueError:
            logger.warning("유효하지 않은 regime 오버라이드: %s → MIXED 사용", self._override_regime)
            regime = RegimeLabel.MIXED
        try:
            weekly_trend = WeeklyTrendLabel(self._override_trend) if self._override_trend else WeeklyTrendLabel.PASS
        except ValueError:
            logger.warning("유효하지 않은 weekly_trend 오버라이드: %s → PASS 사용", self._override_trend)
            weekly_trend = WeeklyTrendLabel.PASS
        logger.info(
            "시황 오버라이드 적용: regime=%s trend=%s", regime.value, weekly_trend.value
        )
        return RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=None,
        )

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
        vol_regime = self._compute_vol_regime()

        return RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=kospi_ts,
            assets=assets,
            vol_regime=vol_regime,
        )

    def _compute_vol_regime(self) -> VolRegimeLabel:
        """KOSPI 20주 수익률 표준편차로 변동성 국면을 분류한다.

        annualized_vol < 12%  → LOW
        12% ≤ vol ≤ 20%       → NORMAL
        vol > 20%              → HIGH
        """
        if self._provider is None:
            return VolRegimeLabel.NORMAL
        try:
            closes = self._provider.get_weekly_closes("KOSPI", self._VOL_LOOKBACK + 1)
            if len(closes) < self._VOL_LOOKBACK:
                return VolRegimeLabel.NORMAL
            arr = np.array(closes, dtype=float)
            returns = np.diff(arr) / arr[:-1]
            annualized_vol = float(returns.std() * np.sqrt(52))
            if annualized_vol < self._VOL_LOW_THRESHOLD:
                return VolRegimeLabel.LOW
            if annualized_vol > self._VOL_HIGH_THRESHOLD:
                return VolRegimeLabel.HIGH
            return VolRegimeLabel.NORMAL
        except Exception:
            return VolRegimeLabel.NORMAL

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


def create_regime_analyzer(settings) -> MarketRegimeAnalyzer:
    """설정 기반 MarketRegimeAnalyzer 를 생성한다.

    - maps_market_regime_override / maps_weekly_trend_override 가 'auto' 가 아니면
      해당 오버라이드 값을 즉시 적용한다.
    - 그 외에는 CombinedWeeklyProvider(pykrx + yfinance) 로 실 데이터를 분석한다.
    """
    override_regime = getattr(settings, "maps_market_regime_override", "auto") or "auto"
    override_trend = getattr(settings, "maps_weekly_trend_override", "auto") or "auto"

    has_override = override_regime != "auto" or override_trend != "auto"
    if has_override:
        return MarketRegimeAnalyzer(
            provider=None,
            override_regime=override_regime if override_regime != "auto" else None,
            override_trend=override_trend if override_trend != "auto" else None,
        )

    return MarketRegimeAnalyzer(provider=CombinedWeeklyProvider())
