"""코스톨라니 스타일 가격 산출 — trading_target / value_target 분리.

기존 plan_target(RR 비율 기준)을 대체·보완하는 코스톨라니식 이중 목표가 체계.

technical_stop: MA 이탈·ATR 기준 기술적 손절가
thesis_stop:    thesis 무효화 (실적 급락·업황 피크·재무위험 급등)
emergency_stop: 급락·유동성 붕괴 대응
trading_target: RR·저항선·전고점 기반 단기 목표가
value_target:   적정 PER/PBR·역사적 밴드 기반 장기 목표가

holding_type별 우선 적용:
- CORE    → value_target 중심, thesis_stop 우선
- TRADING → trading_target 중심, technical_stop 우선
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PriceResult:
    """코스톨라니 가격 산출 결과."""

    plan_buy: float | None
    technical_stop: float | None
    thesis_stop: float | None
    emergency_stop: float | None
    trading_target: float | None
    value_target: float | None
    first_sell_price: float | None
    final_sell_price: float | None
    holding_type: str
    reason: str


@dataclass
class PriceInput:
    """가격 산출 입력."""

    holding_type: str = "SWING"
    current_close: float = 0.0
    atr14: float | None = None
    per: float | None = None
    pbr: float | None = None
    historical_per_avg: float | None = None
    historical_pbr_avg: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    operating_profit_growth: float | None = None
    eps_forward: float | None = None   # 12개월 예상 EPS
    bps: float | None = None           # 주당순자산
    rr_ratio: float = 2.0              # 목표 RR 비율
    slippage_pct: float = 0.01


class KostolanyPriceCalculator:
    """코스톨라니 스타일 이중 목표가·손절가 산출기.

    데이터가 없으면 None을 반환하며, 시스템이 중단되지 않는다.
    """

    # 기본 손절 비율 (technical_stop)
    _TECHNICAL_STOP_CORE = 0.10
    _TECHNICAL_STOP_SWING = 0.07
    _TECHNICAL_STOP_TRADING = 0.04

    # emergency_stop: 진입가 대비 최대 손실 허용
    _EMERGENCY_STOP_PCT = 0.15

    def calculate(self, inp: PriceInput) -> PriceResult:
        """holding_type과 입력 데이터 기반으로 가격을 산출한다."""
        if inp.current_close <= 0:
            return PriceResult(
                plan_buy=None,
                technical_stop=None,
                thesis_stop=None,
                emergency_stop=None,
                trading_target=None,
                value_target=None,
                first_sell_price=None,
                final_sell_price=None,
                holding_type=inp.holding_type,
                reason="current_close_unavailable",
            )

        plan_buy = round(inp.current_close * (1.0 + inp.slippage_pct))
        holding = (inp.holding_type or "SWING").upper()

        technical_stop = self._technical_stop(inp, plan_buy, holding)
        thesis_stop = self._thesis_stop(inp, plan_buy)
        emergency_stop = round(plan_buy * (1.0 - self._EMERGENCY_STOP_PCT)) if plan_buy else None
        trading_target = self._trading_target(inp, plan_buy, technical_stop)
        value_target = self._value_target(inp)
        first_sell, final_sell = self._sell_prices(holding, trading_target, value_target)

        return PriceResult(
            plan_buy=float(plan_buy),
            technical_stop=technical_stop,
            thesis_stop=thesis_stop,
            emergency_stop=emergency_stop,
            trading_target=trading_target,
            value_target=value_target,
            first_sell_price=first_sell,
            final_sell_price=final_sell,
            holding_type=holding,
            reason=self._reason(holding, value_target, trading_target),
        )

    def _technical_stop(self, inp: PriceInput, plan_buy: float, holding: str) -> float | None:
        """ATR 또는 고정 % 기반 기술적 손절가."""
        if inp.atr14 and inp.atr14 > 0:
            atr_multiplier = {"CORE": 3.0, "SWING": 2.0, "TRADING": 1.5}.get(holding, 2.0)
            atr_stop = plan_buy - atr_multiplier * inp.atr14
            return float(round(atr_stop))

        stop_pct = {
            "CORE": self._TECHNICAL_STOP_CORE,
            "SWING": self._TECHNICAL_STOP_SWING,
            "TRADING": self._TECHNICAL_STOP_TRADING,
        }.get(holding, self._TECHNICAL_STOP_SWING)
        return float(round(plan_buy * (1.0 - stop_pct)))

    @staticmethod
    def _thesis_stop(inp: PriceInput, plan_buy: float) -> float | None:
        """thesis 무효화 기반 손절가.

        가치 기반 데이터가 없으면 None을 반환한다.
        BPS(주당순자산)의 0.8배를 thesis stop으로 사용:
        → BPS 이하로 주가 하락 = 순자산 이하 거래 = thesis 훼손 신호
        """
        if inp.bps and inp.bps > 0:
            return float(round(inp.bps * 0.80))
        return None

    def _trading_target(self, inp: PriceInput, plan_buy: float, technical_stop: float | None) -> float | None:
        """RR 비율·저항선·52주 고점 기반 단기 목표가."""
        # 1차: RR 기반
        if technical_stop and technical_stop > 0:
            risk = plan_buy - technical_stop
            if risk > 0:
                rr_target = round(plan_buy + risk * inp.rr_ratio)
                # 52주 고점이 있으면 그 아래로 cap
                if inp.high_52w and inp.high_52w > plan_buy:
                    return float(min(rr_target, round(inp.high_52w * 0.98)))
                return float(rr_target)

        # 2차: 52주 고점 기반
        if inp.high_52w and inp.high_52w > inp.current_close:
            return float(round(inp.high_52w * 0.95))

        return None

    def _value_target(self, inp: PriceInput) -> float | None:
        """적정 PER/PBR·역사적 밴드 기반 장기 목표가."""
        candidates: list[float] = []

        # PER 기반
        if inp.eps_forward and inp.eps_forward > 0 and inp.historical_per_avg and inp.historical_per_avg > 0:
            per_target = inp.eps_forward * inp.historical_per_avg
            candidates.append(per_target)

        # PBR 기반
        if inp.bps and inp.bps > 0 and inp.historical_pbr_avg and inp.historical_pbr_avg > 0:
            pbr_target = inp.bps * inp.historical_pbr_avg
            candidates.append(pbr_target)

        if not candidates:
            return None

        # 보수적으로 평균 사용
        avg_target = sum(candidates) / len(candidates)
        if avg_target > inp.current_close:
            return float(round(avg_target))
        return None

    @staticmethod
    def _sell_prices(
        holding: str,
        trading_target: float | None,
        value_target: float | None,
    ) -> tuple[float | None, float | None]:
        """1차 매도가(first_sell)와 최종 매도가(final_sell)를 결정한다."""
        if holding == "CORE":
            # CORE: 1차 매도=trading_target, 최종 매도=value_target
            return trading_target, value_target
        if holding in ("SWING", "TRADING"):
            # SWING/TRADING: trading_target 기반
            if trading_target:
                first = round(trading_target * 0.85)
                return float(first), trading_target
        return trading_target, value_target

    @staticmethod
    def _reason(holding: str, value_target: float | None, trading_target: float | None) -> str:
        parts = [f"holding_type={holding}"]
        if value_target:
            parts.append(f"value_target={value_target:,.0f}")
        if trading_target:
            parts.append(f"trading_target={trading_target:,.0f}")
        return "; ".join(parts)
