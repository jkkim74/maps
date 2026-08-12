"""장세 분석 — MarketRegime + WeeklyTrend + VolRegime.

설계서 SCR-03 기준.
MarketRegime: 8개 자산의 5주 이동평균 기반 3단계 분류 (strong | mixed | weak).
              자산 목록은 `_ASSETS` 가 정본이다 — KOSPI·KOSDAQ·S&P 500·NASDAQ·
              USD/KRW·금·WTI·구리.
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
# pykrx 지수 API 실패 시 사용하는 yfinance 폴백 티커.
# KRX 지수 엔드포인트(get_index_ohlcv_by_date)가 빈 응답을 반환해도
# 국내 지수가 국면 계산에서 누락되지 않도록 한다.
_KRX_YF_FALLBACK: dict[str, str] = {
    "KOSPI":  "^KS11",
    "KOSDAQ": "^KQ11",
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


class BreadthLabel(str, Enum):
    """시장폭(breadth) 국면 — 상승종목 비율 기반."""

    STRONG = "strong"
    NORMAL = "normal"
    WEAK = "weak"
    UNKNOWN = "unknown"   # 데이터 부족/미계산 → 가드 미적용(안전 기본값)


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

    All observation fields are optional. Missing inputs remain missing and are
    excluded from the display score while readiness stays fail-closed.
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
    measured_liquidity_score: float | None = None
    measured_psychology_score: float | None = None
    factor_sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketRegimeResult:
    """Kostolany-style composite market score result."""

    legacy_regime: str
    composite_regime: str
    price_trend_score: float
    volatility_score: float
    liquidity_score: float | None
    foreign_fx_score: float
    psychology_score: float | None
    final_market_score: float
    reason: str
    coverage_ratio: float = 0.0
    score_status: str = "unavailable"
    score_ready: bool = False
    measured_factors: tuple[str, ...] = ()
    missing_factors: tuple[str, ...] = ()
    factor_sources: dict[str, str] = field(default_factory=dict)
    policy_regime: str | None = None


class PlaceholderKostolanyDataProvider:
    """Placeholder provider until macro, flow, and sentiment feeds are wired."""

    def enrich(self, base: MarketRegimeInput) -> MarketRegimeInput:
        return base


class LiquidityCycleScorer:
    """Scores liquidity cycle quality on a 0-100 scale.

    입력이 하나도 없으면(피드 미연결) None — 측정 불가를 중립 50과 구분한다.
    """

    neutral_score = 50.0

    def score(self, data: MarketRegimeInput) -> float | None:
        values = [
            data.policy_rate_direction,
            data.yield_curve_change,
            data.usdkrw_stability,
            data.foreign_net_buying,
            self._invert(data.margin_debt_growth),
            data.customer_deposit_change,
            self._invert(data.credit_spread_risk),
        ]
        return _average_or_none(values)

    @staticmethod
    def _invert(value: float | None) -> float | None:
        return None if value is None else 100.0 - _clamp_score(value)


class PsychologyScorer:
    """Scores market psychology on a 0-100 scale.

    입력이 하나도 없으면(피드 미연결) None — 측정 불가를 중립 50과 구분한다.
    """

    neutral_score = 50.0

    def score(self, data: MarketRegimeInput) -> float | None:
        values = [
            self._overheat_penalty(data.turnover_surge),
            self._overheat_penalty(data.retail_overheat),
            data.news_sentiment,
            self._overheat_penalty(data.theme_concentration),
            data.new_high_ratio,
            self._invert(data.sharp_drop_ratio),
        ]
        return _average_or_none(values)

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
        # 팩터별 실측값 — None이면 미측정(피드 미연결). 미측정 팩터는 점수에서
        # 제외하고 실측 팩터의 가중치만으로 재정규화한다. 중립 50을 섞으면
        # 가중치의 상당분이 상수가 되어 점수가 실제 데이터보다 온화해진다.
        measured: dict[str, float] = {}
        if data.price_trend_score is not None:
            measured["price_trend"] = _clamp_score(data.price_trend_score)
        if data.volatility_score is not None:
            measured["volatility"] = _clamp_score(data.volatility_score)
        liquidity_measured = (
            _clamp_score(data.measured_liquidity_score)
            if data.measured_liquidity_score is not None
            else self._liquidity.score(data)
        )
        if liquidity_measured is not None:
            measured["liquidity"] = liquidity_measured
        if data.foreign_fx_score is not None:
            measured["foreign_fx"] = _clamp_score(data.foreign_fx_score)
        psychology_measured = (
            _clamp_score(data.measured_psychology_score)
            if data.measured_psychology_score is not None
            else self._psychology.score(data)
        )
        if psychology_measured is not None:
            measured["psychology"] = psychology_measured

        # Missing top-level factors stay None. A displayed neutral 50 is
        # indistinguishable from an actual observation and is unsafe for orders.
        price_score = measured.get("price_trend", 50.0)
        vol_score = measured.get("volatility", 50.0)
        liquidity_score = measured.get("liquidity")
        foreign_fx_score = measured.get("foreign_fx", 50.0)
        psychology_score = measured.get("psychology")

        if measured:
            weight_sum = sum(self._WEIGHTS[name] for name in measured)
            final_score = round(
                sum(self._WEIGHTS[name] * value for name, value in measured.items()) / weight_sum,
                2,
            )
        else:
            final_score = 50.0

        composite = self._classify(data.legacy_regime, final_score, liquidity_score)
        unmeasured = [name for name in self._WEIGHTS if name not in measured]
        coverage = round(sum(self._WEIGHTS[name] for name in measured), 4)
        measured_parts = ", ".join(
            f"{name}={measured[name]:.1f}" for name in self._WEIGHTS if name in measured
        )
        reason = f"legacy={data.legacy_regime}, final={final_score:.1f}"
        if measured_parts:
            reason += f", {measured_parts}"
        if unmeasured:
            reason += f"; 미측정 제외: {','.join(unmeasured)}"
        if composite == "contrarian":
            reason += "; weak price trend with improving liquidity"
        elif (
            data.legacy_regime == "strong"
            and composite == "mixed"
            and liquidity_score is not None
            and liquidity_score < 35.0
        ):
            reason += "; strong trend downgraded by weak liquidity"

        return MarketRegimeResult(
            legacy_regime=data.legacy_regime,
            composite_regime=composite,
            price_trend_score=round(price_score, 2),
            volatility_score=round(vol_score, 2),
            liquidity_score=round(liquidity_score, 2) if liquidity_score is not None else None,
            foreign_fx_score=round(foreign_fx_score, 2),
            psychology_score=round(psychology_score, 2) if psychology_score is not None else None,
            final_market_score=final_score,
            reason=reason,
            coverage_ratio=coverage,
            score_status=("complete" if not unmeasured else "partial" if measured else "unavailable"),
            score_ready=coverage == 1.0,
            measured_factors=tuple(name for name in self._WEIGHTS if name in measured),
            missing_factors=tuple(unmeasured),
            factor_sources=dict(data.factor_sources),
            policy_regime=data.legacy_regime,
        )

    @staticmethod
    def _classify(legacy_regime: str, final_score: float, liquidity_score: float | None) -> str:
        if legacy_regime == RegimeLabel.WEAK.value and liquidity_score is not None and liquidity_score >= 65.0 and final_score >= 45.0:
            return "contrarian"
        if final_score >= 67.0:
            composite = RegimeLabel.STRONG.value
        elif final_score >= 40.0:
            composite = RegimeLabel.MIXED.value
        else:
            composite = RegimeLabel.WEAK.value
        if legacy_regime == RegimeLabel.STRONG.value and liquidity_score is not None and liquidity_score < 35.0:
            return RegimeLabel.MIXED.value
        return composite


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _average_or_none(values: list[float | None]) -> float | None:
    """비-None 입력들의 평균. 입력이 하나도 없으면 None (미측정)."""
    scored = [_clamp_score(v) for v in values if v is not None]
    if not scored:
        return None
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
    # KOSPI 하한선으로 WEAK→MIXED 상향이 적용됐는지("빌려온 MIXED") 여부.
    floor_applied: bool = False
    # Korea weak guard로 MIXED→WEAK 하향이 적용됐는지 여부 (플로어의 대칭).
    korea_weak_applied: bool = False
    # 시장폭 국면 — 후보 생성 단계에서 DB 기반으로 주입(미주입 시 UNKNOWN).
    breadth: BreadthLabel = BreadthLabel.UNKNOWN
    # 히스테리시스·floor 판정 근거 (오버라이드/스텁 시 None)
    up_count: int | None = None
    total_assets: int | None = None
    kospi_above_ma5w: bool | None = None
    kospi_above_ma10w: bool | None = None

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

        # 시장폭(breadth) 가드: KOSPI 하한선으로 빌려온 MIXED("지수만 상승")이고
        # 시장폭이 약하면(좁은 장) 추격성 모멘텀·돌파·추세추종은 보류하고
        # 방어적 PULLBACK·CONTRARIAN_QUALITY만 통과시킨다. ratio는 0으로 만들지 않는다.
        if self.floor_applied and self.breadth == BreadthLabel.WEAK:
            if st in {
                StrategyEntryType.BREAKOUT,
                StrategyEntryType.MOMENTUM,
                StrategyEntryType.MULTI_ASSET_TREND,
            }:
                return StrategyEntryPolicy(
                    strategy_type=st.value,
                    market_mode=market_mode,
                    allowed=False,
                    entry_limit_ratio=0.0,
                    reason="narrow_breadth_blocks_momentum_breakout",
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


def korea_weak_guard_triggered(result: RegimeResult, *, ts_threshold: float) -> bool:
    """MIXED 라벨을 한국 실측 약세가 부정하는지 판정한다 (KOSPI 플로어의 대칭).

    글로벌 8자산 투표에서 한국은 2표뿐이라, KOSPI가 주선을 깊게 깨고 종목 확산이
    무너져도 해외 자산 몇 개로 라벨이 MIXED에 머물 수 있다. 조건 (모두 AND):

    - 라벨이 MIXED (플로어 적용 후 — 플로어는 상회를 요구하므로 상호배타)
    - KOSPI 5주선·10주선 모두 하회 (오버라이드/스텁의 None은 미충족)
    - KOSPI 추세강도 ≤ ts_threshold **또는** breadth WEAK (아는 경우만)
    """
    if result.regime != RegimeLabel.MIXED:
        return False
    if result.kospi_above_ma5w is not False or result.kospi_above_ma10w is not False:
        return False
    ts_weak = result.kospi_ts is not None and result.kospi_ts <= ts_threshold
    breadth_weak = result.breadth == BreadthLabel.WEAK
    return ts_weak or breadth_weak


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
        """pykrx 지수 데이터를 반환하되, 실패 시 yfinance 폴백을 사용한다."""
        result = self._krx_index_weekly(asset_name, n_weeks)
        if result:
            return result
        fallback = _KRX_YF_FALLBACK.get(asset_name)
        if fallback:
            logger.warning(
                "KRX 지수 데이터 실패 [%s] — yfinance(%s) 폴백 사용", asset_name, fallback
            )
            return self._yf_weekly(fallback, n_weeks)
        return []

    def _krx_index_weekly(self, asset_name: str, n_weeks: int) -> list[float]:
        """pykrx 지수 주봉 종가를 반환한다 (실패 시 빈 리스트)."""
        ticker = _KRX_TICKERS[asset_name]
        try:
            from maps.data.krx_auth import ensure_krx_login_guard  # noqa: PLC0415

            ensure_krx_login_guard()
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
        return self._yf_weekly(_YF_TICKERS[asset_name], n_weeks)

    def _yf_weekly(self, ticker: str, n_weeks: int) -> list[float]:
        """yfinance 일봉을 주봉으로 변환한 종가 리스트를 반환한다."""
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
            logger.warning("yfinance data unavailable [%s]: %s", ticker, exc)
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
    # 위험회피 자산: 가격 상승이 한국 주식 관점에서 risk-off 신호인 자산.
    # up_ratio 집계 시 above_ma5w의 반대를 risk-on으로 카운트한다 (표시 방향은 유지).
    _RISK_OFF_ASSETS = {"USD/KRW"}
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
        self._volatility_measured = False

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
        kospi_above_ma5w = False
        kospi_above_ma10w = False

        for name in self._ASSETS:
            # KOSPI는 floor 판정용 MA10W 계산을 위해 더 긴 주봉을 가져온다.
            n_weeks = (self._MA10W + 1) if name == "KOSPI" else (self._MA5W + 1)
            closes = self._provider.get_weekly_closes(name, n_weeks)
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
            if name == "KOSPI":
                kospi_above_ma5w = above
                if len(closes) >= self._MA10W:
                    ma10 = float(np.array(closes[-self._MA10W:], dtype=float).mean())
                    kospi_above_ma10w = last > ma10

            assets.append(AssetTrendInfo(name=name, direction=direction, value=last, above_ma5w=above))
            # 위험회피 자산(USD/KRW 등)은 하락이 risk-on — 카운트만 반전, 표시 방향은 그대로.
            counts_up = (not above) if name in self._RISK_OFF_ASSETS else above
            if counts_up:
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

        # KOSPI 하한선(floor): 국내 대표지수가 5주MA 위(상승추세)이고 주간추세가
        # 통과면, 글로벌 매크로 약세가 전체 국면을 WEAK로 끌어내리지 못하도록
        # 최소 MIXED를 보장한다. (한국 시장을 트레이딩하므로 국내 추세를 우선)
        # MA5W 하루 회복만으로 발동하면 국면이 매일 뒤집히므로 MA10W 동반 회복을
        # 요구한다. MA10W 아래인 경우의 "2일 연속 MA5W 위" 확인은 판정 이력이
        # 필요해 apply_hysteresis(maps.market.regime_history)에서 수행한다.
        floor_applied = False
        if (
            regime == RegimeLabel.WEAK
            and kospi_above_ma5w
            and kospi_above_ma10w
            and weekly_trend == WeeklyTrendLabel.PASS
        ):
            logger.info(
                "KOSPI 상승추세(MA5W+MA10W) + weekly_trend pass → 국면 하한선 적용 (weak→mixed)"
            )
            regime = RegimeLabel.MIXED
            floor_applied = True

        result = RegimeResult(
            regime=regime,
            weekly_trend=weekly_trend,
            limit_ratio=0.0,
            kospi_ts=kospi_ts,
            assets=assets,
            vol_regime=vol_regime,
            floor_applied=floor_applied,
            up_count=up_count,
            total_assets=total,
            kospi_above_ma5w=kospi_above_ma5w,
            kospi_above_ma10w=kospi_above_ma10w,
        )
        return self._with_composite(result)

    def _compute_vol_regime(self) -> VolRegimeLabel:
        """KOSPI 20주 수익률 표준편차로 변동성 국면을 분류한다.

        annualized_vol < 12%  → LOW
        12% ≤ vol ≤ 20%       → NORMAL
        vol > 20%              → HIGH
        """
        self._volatility_measured = False
        if self._provider is None:
            return VolRegimeLabel.NORMAL
        try:
            closes = self._provider.get_weekly_closes("KOSPI", self._VOL_LOOKBACK + 1)
            if len(closes) < self._VOL_LOOKBACK:
                return VolRegimeLabel.NORMAL
            arr = np.array(closes, dtype=float)
            returns = np.diff(arr) / arr[:-1]
            annualized_vol = float(returns.std() * np.sqrt(52))
            self._volatility_measured = True
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
        price_measured = any(asset.value is not None for asset in result.assets)
        fx_measured = any(
            asset.name == "USD/KRW" and asset.value is not None
            for asset in result.assets
        )
        factor_sources: dict[str, str] = {}
        if price_measured:
            factor_sources["price_trend"] = "market.weekly_price"
        if self._volatility_measured:
            factor_sources["volatility"] = "market.kospi_realized_volatility"
        if fx_measured:
            factor_sources["foreign_fx"] = "market.usdkrw_weekly_trend"
        base_input = MarketRegimeInput(
            legacy_regime=result.regime.value,
            vol_regime=result.vol_regime.value,
            weekly_trend=result.weekly_trend.value,
            price_trend_score=self._price_trend_score(result) if price_measured else None,
            volatility_score=(
                self._volatility_score(result.vol_regime)
                if self._volatility_measured else None
            ),
            foreign_fx_score=self._foreign_fx_score(result.assets) if fx_measured else None,
            factor_sources=factor_sources,
        )
        enriched = self._kostolany_provider.enrich(base_input)
        result.composite = self._composite_scorer.score(enriched)
        if result.composite.score_ready:
            rank = {
                RegimeLabel.WEAK.value: 0,
                RegimeLabel.MIXED.value: 1,
                RegimeLabel.STRONG.value: 2,
            }
            composite_regime = result.composite.composite_regime
            if composite_regime in rank and rank[composite_regime] < rank[result.regime.value]:
                result.regime = RegimeLabel(composite_regime)
            object.__setattr__(result.composite, "policy_regime", result.regime.value)
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


def create_regime_analyzer(
    settings, *, ref_date: datetime.date | None = None
) -> MarketRegimeAnalyzer:
    """설정 기반 MarketRegimeAnalyzer 를 생성한다.

    - maps_market_regime_override / maps_weekly_trend_override 가 'auto' 가 아니면
      해당 오버라이드 값을 즉시 적용한다.
    - 그 외에는 CombinedWeeklyProvider(pykrx + yfinance) 로 실 데이터를 분석한다.
    """
    override_regime = getattr(settings, "maps_market_regime_override", "auto") or "auto"
    override_trend = getattr(settings, "maps_weekly_trend_override", "auto") or "auto"

    has_override = override_regime != "auto" or override_trend != "auto"
    kostolany_enabled = bool(getattr(settings, "maps_kostolany_regime_enabled", False))
    kostolany_provider = None
    if kostolany_enabled:
        from maps.market.feeds import DatabaseKostolanyDataProvider

        kostolany_provider = DatabaseKostolanyDataProvider(ref_date=ref_date)
    if has_override:
        return MarketRegimeAnalyzer(
            provider=None,
            override_regime=override_regime if override_regime != "auto" else None,
            override_trend=override_trend if override_trend != "auto" else None,
            kostolany_enabled=kostolany_enabled,
            kostolany_provider=kostolany_provider,
        )

    return MarketRegimeAnalyzer(
        provider=CombinedWeeklyProvider(),
        kostolany_enabled=kostolany_enabled,
        kostolany_provider=kostolany_provider,
    )
