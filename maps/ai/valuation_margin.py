"""Valuation margin scoring for Kostolany-style safety margin checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValuationMarginInput:
    ticker: str
    current_price: float | None = None
    market_cap: float | None = None
    per: float | None = None
    forward_per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    ev_ebitda: float | None = None
    fcf: float | None = None
    debt_ratio: float | None = None
    operating_profit_growth: float | None = None
    consensus_revision: float | None = None
    historical_valuation_band: float | None = None  # 0=bottom, 50=middle, 100=top


@dataclass(frozen=True)
class ValuationMarginResult:
    valuation_score: float
    reason: str


class PlaceholderValuationDataProvider:
    """Placeholder provider until fundamental/consensus feeds are wired."""

    def get(self, ticker: str, current_price: float | None = None) -> ValuationMarginInput:
        return ValuationMarginInput(ticker=ticker, current_price=current_price)


class ValuationMarginScorer:
    """Scores conservative valuation safety margin on a 0-100 scale."""

    neutral_score = 50.0

    def score(self, data: ValuationMarginInput) -> ValuationMarginResult:
        components: list[tuple[str, float]] = []

        if data.historical_valuation_band is not None:
            band = _clamp(data.historical_valuation_band)
            components.append(("historical_band", 100.0 - band))

        per_score = self._per_score(data.per, data.forward_per)
        if per_score is not None:
            components.append(("valuation_multiple", per_score))

        if data.pbr is not None:
            components.append(("pbr", _pbr_score(data.pbr)))

        if data.roe is not None:
            components.append(("roe", _scale(data.roe, lo=0.0, hi=20.0)))

        if data.ev_ebitda is not None:
            components.append(("ev_ebitda", 100.0 - _scale(data.ev_ebitda, lo=3.0, hi=18.0)))

        if data.fcf is not None:
            components.append(("fcf", 75.0 if data.fcf > 0 else 25.0))

        if data.debt_ratio is not None:
            components.append(("debt_ratio", 100.0 - _scale(data.debt_ratio, lo=50.0, hi=250.0)))

        if data.operating_profit_growth is not None:
            components.append(("operating_profit_growth", _scale(data.operating_profit_growth, lo=-30.0, hi=30.0)))

        if data.consensus_revision is not None:
            components.append(("consensus_revision", _scale(data.consensus_revision, lo=-20.0, hi=20.0)))

        if not components:
            return ValuationMarginResult(
                valuation_score=self.neutral_score,
                reason="neutral: valuation data unavailable",
            )

        score = round(sum(value for _name, value in components) / len(components), 2)
        reason = ", ".join(f"{name}={value:.1f}" for name, value in components)
        return ValuationMarginResult(valuation_score=score, reason=reason)

    @staticmethod
    def _per_score(per: float | None, forward_per: float | None) -> float | None:
        values = [v for v in (per, forward_per) if v is not None and v > 0]
        if not values:
            return None
        avg_per = sum(values) / len(values)
        return 100.0 - _scale(avg_per, lo=5.0, hi=35.0)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _scale(value: float, *, lo: float, hi: float) -> float:
    if hi <= lo:
        return 50.0
    return _clamp((float(value) - lo) / (hi - lo) * 100.0)


def _pbr_score(pbr: float) -> float:
    if pbr <= 0:
        return 50.0
    return 100.0 - _scale(pbr, lo=0.5, hi=4.0)
