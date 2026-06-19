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


class MarketModeLabel(str, Enum):
    TREND_FOLLOWING = "TREND_FOLLOWING"
    CASH_DEFENSE = "CASH_DEFENSE"
    CONTRARIAN_ACCUMULATION = "CONTRARIAN_ACCUMULATION"
    NORMAL = "NORMAL"


class StrategyEntryType(str, Enum):
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    MOMENTUM = "MOMENTUM"
    MULTI_ASSET_TREND = "MULTI_ASSET_TREND"
    CONTRARIAN_QUALITY = "CONTRARIAN_QUALITY"
    CASH_ONLY = "CASH_ONLY"


@dataclass
class AssetTrendInfo:
    """개별 자산군 추세 정보."""

    name: str
    direction: str      # up | down | flat
    value: float | None = None
    above_ma5w: bool = False


@dataclass(frozen=True)
class StrategyEntryPolicy:
    strategy_type: str
    market_mode: MarketModeLabel
    allowed: bool
    entry_limit_ratio: float
    reason: str


@dataclass(frozen=True)
class MarketRegimeInput:
    """Inputs for the Kostolany-style composite market scorer.

    All fields are optional because the external macro/flow data providers are
    not wired yet. Missing inputs are treated as neutral by the scorers.
    """

    legacy_regime: str
    vol_regime: str
    weekly_trend: str
    price_trend_score: float | None = None
    volatility_score: float | None = None
    foreign_fx_score: float | None = None
    policy_rate_direction: float | None = None
    yield_curve_change: float | None = None
    usdkrw_stability: float | None = None
    foreign_net_buying: float | None = None
    margin_debt_growth: float | None = None
    customer_deposit_change: float | None = None
    credit_spread_risk: float | None = None
    turnover_surge: float | None = None
    retail_overheat: float | None = None
    news_sentiment: float | None = None
    theme_concentration: float | None = None
    new_high_ratio: float | None = None
    sharp_drop_ratio: float | None = None


@dataclass(frozen=True)
class MarketRegimeResult:
    """Kostolany-style composite market score result."""

    legacy_regime: str
    composite_regime: str
    price_trend_score: float
    volatility_score: float
    liquidity_score: float
    foreign_fx_score: float
    psychology_score: float
    final_market_score: float
    reason: str


class PlaceholderKostolanyDataProvider:
    """Placeholder provider until macro, flow, and sentiment feeds are wired."""

    def enrich(self, base: MarketRegimeInput) -> MarketRegimeInput:
        return base


class LiquidityCycleScorer:
    """Scores liquidity cycle quality on a 0-100 scale."""

    neutral_score = 50.0

    def score(self, data: MarketRegimeInput) -> float:
        values = [
            data.policy_rate_direction,
            data.yield_curve_change,
            data.usdkrw_stability,
            data.foreign_net_buying,
            self._invert(data.margin_debt_growth),
            data.customer_deposit_change,
            self._invert(data.credit_spread_risk),
        ]
        return _average_or_neutral(values, self.neutral_score)

    @staticmethod
    def _invert(value: float | None) -> float | None:
        return None if value is None else 100.0 - _clamp_score(value)


class PsychologyScorer:
    """Scores market psychology on a 0-100 scale."""

    neutral_score = 50.0

    def score(self, data: MarketRegimeInput) -> float:
        values = [
            self._overheat_penalty(data.turnover_surge),
            self._overheat_penalty(data.retail_overheat),
            data.news_sentiment,
            self._overheat_penalty(data.theme_concentration),
            data.new_high_ratio,
            self._invert(data.sharp_drop_ratio),
        ]
        return _average_or_neutral(values, self.neutral_score)

    @staticmethod
    def _invert(value: float | None) -> float | None:
        return None if value is None else 100.0 - _clamp_score(value)

    @staticmethod
    def _overheat_penalty(value: float | None) -> float | None:
        if value is None:
            return None
        value = _clamp_score(value)
        return 100.0 - abs(value - 50.0) * 2.0


class MarketRegimeCompositeScorer:
    """Combines trend, volatility, liquidity, FX/foreign, and psychology."""

    _WEIGHTS = {
        "price_trend": 0.30,
        "volatility": 0.20,
        "liquidity": 0.25,
        "foreign_fx": 0.15,
        "psychology": 0.10,
    }

    def __init__(
        self,
        *,
        liquidity_scorer: LiquidityCycleScorer | None = None,
        psychology_scorer: PsychologyScorer | None = None,
    ) -> None:
        self._liquidity = liquidity_scorer or LiquidityCycleScorer()
        self._psychology = psychology_scorer or PsychologyScorer()

    def score(self, data: MarketRegimeInput) -> MarketRegimeResult:
        price_score = _clamp_score(data.price_trend_score if data.price_trend_score is not None else 50.0)
        vol_score = _clamp_score(data.volatility_score if data.volatility_score is not None else 50.0)
        liquidity_score = self._liquidity.score(data)
        foreign_fx_score = _clamp_score(data.foreign_fx_score if data.foreign_fx_score is not None else 50.0)
        psychology_score = self._psychology.score(data)

        final_score = round(
            price_score * self._WEIGHTS["price_trend"]
            + vol_score * self._WEIGHTS["volatility"]
            + liquidity_score * self._WEIGHTS["liquidity"]
            + foreign_fx_score * self._WEIGHTS["foreign_fx"]
            + psychology_score * self._WEIGHTS["psychology"],
            2,
        )
        composite = self._classify(data.legacy_regime, final_score, liquidity_score)
        reason = (
            f"legacy={data.legacy_regime}, final={final_score:.1f}, "
            f"liquidity={liquidity_score:.1f}, psychology={psychology_score:.1f}"
        )
        if composite == "contrarian":
            reason += "; weak price trend with improving liquidity"
        elif data.legacy_regime == "strong" and composite == "mixed":
            reason += "; strong trend downgraded by weak liquidity"

        return MarketRegimeResult(
            legacy_regime=data.legacy_regime,
            composite_regime=composite,
            price_trend_score=round(price_score, 2),
            volatility_score=round(vol_score, 2),
            liquidity_score=round(liquidity_score, 2),
            foreign_fx_score=round(foreign_fx_score, 2),
            psychology_score=round(psychology_score, 2),
            final_market_score=final_score,
            reason=reason,
        )

    @staticmethod
    def _classify(legacy_regime: str, final_score: float, liquidity_score: float) -> str:
        if legacy_regime == RegimeLabel.WEAK.value and liquidity_score >= 65.0 and final_score >= 45.0:
            return "contrarian"
        if final_score >= 67.0:
            composite = RegimeLabel.STRONG.value
        elif final_score >= 40.0:
            composite = RegimeLabel.MIXED.value
        else:
            composite = RegimeLabel.WEAK.value
        if legacy_regime == RegimeLabel.STRONG.value and liquidity_score < 35.0:
            return RegimeLabel.MIXED.value
        return composite


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _average_or_neutral(values: list[float | None], neutral: float) -> float:
    scored = [_clamp_score(v) for v in values if v is not None]
    if not scored:
        return neutral
    return round(sum(scored) / len(scored), 2)


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
    composite: MarketRegimeResult | None = None

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

    def market_mode(self, *, contrarian_enabled: bool = False) -> MarketModeLabel:
        """Return the broad market operating mode without changing legacy regime."""
        if self.regime == RegimeLabel.WEAK and self.vol_regime == VolRegimeLabel.HIGH:
            if contrarian_enabled:
                return MarketModeLabel.CONTRARIAN_ACCUMULATION
            return MarketModeLabel.CASH_DEFENSE
        if self.regime == RegimeLabel.WEAK:
            return MarketModeLabel.CASH_DEFENSE
        if self.regime == RegimeLabel.STRONG:
            return MarketModeLabel.TREND_FOLLOWING
        return MarketModeLabel.NORMAL

    def entry_policy_for_strategy(
        self,
        strategy_type: str | Enum | None,
        *,
        contrarian_enabled: bool = False,
        contrarian_entry_limit_ratio: float = 0.25,
    ) -> StrategyEntryPolicy:
        """Return strategy-type-specific entry permission and limit.

        This extends, but does not replace, the legacy entry_limit_ratio matrix.
        Unknown strategy types are treated as MOMENTUM for safety.
        """
        st = _normalize_strategy_type(strategy_type)
        market_mode = self.market_mode(contrarian_enabled=contrarian_enabled)
        contrarian_ratio = _clamp_ratio(contrarian_entry_limit_ratio)

        if self.weekly_trend == WeeklyTrendLabel.FAIL:
            return StrategyEntryPolicy(
                strategy_type=st.value,
                market_mode=market_mode,
                allowed=False,
                entry_limit_ratio=0.0,
                reason="weekly_trend_fail",
            )

        if self.regime == RegimeLabel.WEAK and self.vol_regime == VolRegimeLabel.HIGH:
            if st == StrategyEntryType.CONTRARIAN_QUALITY and contrarian_enabled:
                return StrategyEntryPolicy(
                    strategy_type=st.value,
                    market_mode=market_mode,
                    allowed=True,
                    entry_limit_ratio=contrarian_ratio,
                    reason="weak_high_vol_contrarian_quality_limited",
                )
            return StrategyEntryPolicy(
                strategy_type=st.value,
                market_mode=market_mode,
                allowed=False,
                entry_limit_ratio=0.0,
                reason="weak_high_vol_blocks_trend_momentum_breakout_trading",
            )

        if self.regime == RegimeLabel.WEAK:
            if st == StrategyEntryType.BREAKOUT:
                return StrategyEntryPolicy(st.value, market_mode, False, 0.0, "weak_market_blocks_breakout")
            if st == StrategyEntryType.CONTRARIAN_QUALITY:
                return StrategyEntryPolicy(
                    st.value,
                    market_mode,
                    contrarian_enabled,
                    contrarian_ratio if contrarian_enabled else 0.0,
                    "weak_market_allows_contrarian_quality" if contrarian_enabled else "contrarian_accumulation_disabled",
                )
            if st == StrategyEntryType.PULLBACK:
                return StrategyEntryPolicy(st.value, market_mode, True, min(self.entry_limit_ratio, 0.25), "weak_market_limits_pullback")
            return StrategyEntryPolicy(st.value, market_mode, True, min(self.entry_limit_ratio, 0.25), "weak_market_limits_trend")

        if self.regime == RegimeLabel.MIXED:
            if st == StrategyEntryType.CONTRARIAN_QUALITY:
                return StrategyEntryPolicy(st.value, market_mode, True, min(contrarian_ratio, 0.25), "mixed_market_allows_partial_contrarian")
            if st == StrategyEntryType.MULTI_ASSET_TREND:
                return StrategyEntryPolicy(st.value, market_mode, True, min(self.entry_limit_ratio, 0.35), "mixed_market_limits_trend")
            return StrategyEntryPolicy(st.value, market_mode, True, self.entry_limit_ratio, "mixed_market_allows_strategy")

        if st == StrategyEntryType.CONTRARIAN_QUALITY:
            return StrategyEntryPolicy(st.value, market_mode, True, min(contrarian_ratio, 0.15), "strong_market_reduces_contrarian")
        return StrategyEntryPolicy(st.value, market_mode, True, self.entry_limit_ratio, "strong_market_allows_trend_breakout_pullback")


def _normalize_strategy_type(strategy_type: str | Enum | None) -> StrategyEntryType:
    raw = getattr(strategy_type, "value", strategy_type) or StrategyEntryType.MOMENTUM.value
    try:
        return StrategyEntryType(str(raw))
    except ValueError:
        return StrategyEntryType.MOMENTUM


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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
        kostolany_enabled: bool = False,
        composite_scorer: MarketRegimeCompositeScorer | None = None,
        kostolany_provider: PlaceholderKostolanyDataProvider | None = None,
    ) -> None:
        self._provider = provider
        self._override_regime = override_regime
        self._override_trend = override_trend
        self._kostolany_enabled = kostolany_enabled
        self._composite_scorer = composite_scorer or MarketRegimeCompositeScorer()
        self._kostolany_provider = kostolany_provider or PlaceholderKostolanyDataProvider()

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
        result = RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=None,
        )
        return self._with_composite(result)

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

        result = RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=kospi_ts,
            assets=assets,
            vol_regime=vol_regime,
        )
        return self._with_composite(result)

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
        result = RegimeResult(
            regime=RegimeLabel.MIXED,
            weekly_trend=WeeklyTrendLabel.PASS,
            limit_ratio=0.5,
            kospi_ts=None,
            assets=assets,
        )
        return self._with_composite(result)

    def _with_composite(self, result: RegimeResult) -> RegimeResult:
        if not self._kostolany_enabled:
            return result
        base_input = MarketRegimeInput(
            legacy_regime=result.regime.value,
            vol_regime=result.vol_regime.value,
            weekly_trend=result.weekly_trend.value,
            price_trend_score=self._price_trend_score(result),
            volatility_score=self._volatility_score(result.vol_regime),
            foreign_fx_score=self._foreign_fx_score(result.assets),
        )
        enriched = self._kostolany_provider.enrich(base_input)
        result.composite = self._composite_scorer.score(enriched)
        return result

    @staticmethod
    def _price_trend_score(result: RegimeResult) -> float:
        if result.kospi_ts is not None:
            return _clamp_score(result.kospi_ts)
        if not result.assets:
            return 50.0
        evaluated = [asset for asset in result.assets if asset.direction in {"up", "down"}]
        if not evaluated:
            return 50.0
        return round(sum(1 for asset in evaluated if asset.direction == "up") / len(evaluated) * 100.0, 2)

    @staticmethod
    def _volatility_score(vol_regime: VolRegimeLabel) -> float:
        if vol_regime == VolRegimeLabel.LOW:
            return 75.0
        if vol_regime == VolRegimeLabel.HIGH:
            return 25.0
        return 50.0

    @staticmethod
    def _foreign_fx_score(assets: list[AssetTrendInfo]) -> float:
        usdkrw = next((asset for asset in assets if asset.name == "USD/KRW"), None)
        if usdkrw is None or usdkrw.direction == "flat":
            return 50.0
        return 40.0 if usdkrw.direction == "up" else 60.0


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
            kostolany_enabled=bool(getattr(settings, "maps_kostolany_regime_enabled", False)),
        )

    return MarketRegimeAnalyzer(
        provider=CombinedWeeklyProvider(),
        kostolany_enabled=bool(getattr(settings, "maps_kostolany_regime_enabled", False)),
    )
