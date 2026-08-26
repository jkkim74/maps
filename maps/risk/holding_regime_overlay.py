"""Read-only holding regime policy.

The overlay classifies positions for operator review.  It never submits orders.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from maps.common.constants import STRATEGY_GROUP_MAP
from maps.strategy.catalog import STRATEGY_CLASSES


class HoldingRegimeAction(str, Enum):
    HOLD = "hold"
    WATCH = "watch"
    EXIT = "exit"


@dataclass(frozen=True)
class HoldingRegimeSnapshot:
    ref_date: dt.date
    regime: str
    weekly_trend: str
    vol_regime: str


@dataclass(frozen=True)
class HoldingRegimeDecision:
    action: HoldingRegimeAction
    reason_code: str
    strategy_id: str
    strategy_group: str | None
    entry: HoldingRegimeSnapshot | None
    previous: HoldingRegimeSnapshot | None
    current: HoldingRegimeSnapshot | None
    confirmed: bool = False
    current_adverse_causes: tuple[str, ...] = ()
    confirmed_adverse_causes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        def serialize(value: HoldingRegimeSnapshot | None) -> dict[str, str] | None:
            if value is None:
                return None
            return {
                "ref_date": value.ref_date.isoformat(),
                "regime": value.regime.strip().lower(),
                "weekly_trend": value.weekly_trend.strip().lower(),
                "vol_regime": value.vol_regime.strip().lower(),
            }

        return {
            "action": self.action.value,
            "reason_code": self.reason_code,
            "strategy_id": self.strategy_id,
            "strategy_group": self.strategy_group,
            "entry": serialize(self.entry),
            "previous": serialize(self.previous),
            "current": serialize(self.current),
            "confirmed": self.confirmed,
            "current_adverse_causes": list(self.current_adverse_causes),
            "confirmed_adverse_causes": list(self.confirmed_adverse_causes),
        }


_EXIT_ELIGIBLE_GROUPS = frozenset({
    "pullback_short",
    "ath_outlier",
    "donchian_research",
})
_REGIMES = frozenset({"strong", "mixed", "weak"})
_WEEKLY_TRENDS = frozenset({"pass", "fail"})
_VOL_REGIMES = frozenset({"low", "normal", "high"})


def _is_valid_snapshot(value: HoldingRegimeSnapshot) -> bool:
    return (
        value.regime.strip().lower() in _REGIMES
        and value.weekly_trend.strip().lower() in _WEEKLY_TRENDS
        and value.vol_regime.strip().lower() in _VOL_REGIMES
    )


def _adverse_causes(
    entry: HoldingRegimeSnapshot,
    value: HoldingRegimeSnapshot,
) -> frozenset[str]:
    causes: set[str] = set()
    if value.weekly_trend.strip().lower() == "fail":
        causes.add("weekly_fail")
    if (
        entry.regime.strip().lower() in {"strong", "mixed"}
        and value.regime.strip().lower() == "weak"
    ):
        causes.add("weak_transition")
    return frozenset(causes)


def evaluate_holding_regime(
    strategy_id: str,
    entry: HoldingRegimeSnapshot | None,
    previous: HoldingRegimeSnapshot | None,
    current: HoldingRegimeSnapshot | None,
    as_of: dt.date,
    max_age_days: int,
) -> HoldingRegimeDecision:
    group = STRATEGY_GROUP_MAP.get(strategy_id)
    base = {
        "strategy_id": strategy_id,
        "strategy_group": group,
        "entry": entry,
        "previous": previous,
        "current": current,
    }

    def decide(action: HoldingRegimeAction, reason_code: str, **extra) -> HoldingRegimeDecision:
        return HoldingRegimeDecision(action=action, reason_code=reason_code, **base, **extra)

    if strategy_id not in STRATEGY_CLASSES or group is None:
        return decide(HoldingRegimeAction.HOLD, "UNKNOWN_STRATEGY")
    if entry is None:
        return decide(HoldingRegimeAction.HOLD, "ENTRY_REGIME_UNAVAILABLE")
    if not _is_valid_snapshot(entry):
        return decide(HoldingRegimeAction.HOLD, "ENTRY_REGIME_INVALID")
    if current is None:
        return decide(HoldingRegimeAction.HOLD, "CURRENT_REGIME_UNAVAILABLE")
    if not _is_valid_snapshot(current) or current.ref_date > as_of:
        return decide(HoldingRegimeAction.HOLD, "CURRENT_REGIME_INVALID")
    if (as_of - current.ref_date).days > max_age_days:
        return decide(HoldingRegimeAction.HOLD, "CURRENT_REGIME_STALE")

    current_causes = _adverse_causes(entry, current)
    previous_is_recent = (
        previous is not None
        and _is_valid_snapshot(previous)
        and previous.ref_date < current.ref_date
        and (current.ref_date - previous.ref_date).days <= max_age_days
    )
    previous_causes = _adverse_causes(entry, previous) if previous_is_recent else frozenset()
    confirmed_causes = current_causes & previous_causes
    confirmed = bool(confirmed_causes)
    causes = {
        "confirmed": confirmed,
        "current_adverse_causes": tuple(sorted(current_causes)),
        "confirmed_adverse_causes": tuple(sorted(confirmed_causes)),
    }
    if confirmed and group in _EXIT_ELIGIBLE_GROUPS:
        return decide(HoldingRegimeAction.EXIT, "CONFIRMED_ADVERSE_REGIME", **causes)
    if confirmed:
        return decide(
            HoldingRegimeAction.WATCH,
            "CONFIRMED_ADVERSE_NON_ENFORCEABLE",
            **causes,
        )
    if current_causes:
        return decide(HoldingRegimeAction.WATCH, "ADVERSE_REGIME_UNCONFIRMED", **causes)

    strategy_cls = STRATEGY_CLASSES[strategy_id]
    if current.regime.strip().lower() not in strategy_cls.preferred_regimes:
        return decide(HoldingRegimeAction.WATCH, "CURRENT_REGIME_NOT_PREFERRED", **causes)
    if current.vol_regime.strip().lower() == "high":
        return decide(HoldingRegimeAction.WATCH, "HIGH_VOLATILITY", **causes)
    return decide(HoldingRegimeAction.HOLD, "REGIME_COMPATIBLE", **causes)
