"""Pullback V3.3 연구 후보 — 2R 목표와 R 기반 트레일링 청산.

진입은 V3.2와 완전히 동일하다. 기존 MA5 회복 즉시 익절만 제거하고,
진입 시 확정한 손절폭을 1R로 삼아 목표·트레일링을 계산한다.

이 전략은 연구 격리 대상이다. 콘솔/WFA 수동 실행에는 노출하지만 운영
스케줄러의 후보·승격·주문 레지스트리에는 등록하지 않는다.
"""

from __future__ import annotations

import pandas as pd

from maps.common.exceptions import StrategyConfigError
from maps.strategy.base import PositionExitPolicy
from maps.strategy.pullback_v3 import PullbackV3Strategy


# 진입 조건을 고정한 채 청산 구조만 비교하는 사전 등록 연구 조합.
EXIT_RESEARCH_GRID: tuple[dict[str, float], ...] = (
    {"target_r": 1.5, "trail_activate_r": 1.0, "trail_distance_r": 0.5},
    {"target_r": 2.0, "trail_activate_r": 1.0, "trail_distance_r": 0.5},
    {"target_r": 2.0, "trail_activate_r": 1.5, "trail_distance_r": 0.5},
    {"target_r": 2.0, "trail_activate_r": 1.5, "trail_distance_r": 0.75},
    {"target_r": 2.5, "trail_activate_r": 1.5, "trail_distance_r": 0.5},
    {"target_r": 2.5, "trail_activate_r": 1.5, "trail_distance_r": 0.75},
)


class PullbackV33Strategy(PullbackV3Strategy):
    """V3.2 진입 + 상태 기반 2R/트레일링 청산 연구 후보."""

    strategy_id = "pullback_v3_3"

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """V3.2 진입을 재사용하고 MA_long 하향 이탈만 전략 청산으로 낸다."""
        df = super().generate_signals(data, params)
        ma_long = int(params.get("ma_long", 20))
        trend_ma = df["close"].rolling(ma_long).mean()
        df["exit_signal"] = df["close"] < trend_ma
        df.loc[df["entry_signal"], "exit_signal"] = False
        return df

    def position_exit_policy(self, params: dict) -> PositionExitPolicy:
        target_r = float(params.get("target_r", 2.0))
        activate_r = float(params.get("trail_activate_r", 1.5))
        distance_r = float(params.get("trail_distance_r", 0.5))
        if target_r <= 0 or activate_r <= 0 or distance_r <= 0:
            raise StrategyConfigError("R 청산 파라미터는 모두 0보다 커야 합니다.")
        if activate_r > target_r:
            raise StrategyConfigError("트레일링 활성 R은 목표 R보다 클 수 없습니다.")
        return PositionExitPolicy(
            target_r=target_r,
            trailing_activate_r=activate_r,
            trailing_distance_r=distance_r,
        )

    def param_grid(self) -> list[dict]:
        """채택 청산값을 고정하고 기존 9개 진입 조합의 민감도를 검증한다."""
        exit_params = {
            "target_r": 2.0,
            "trail_activate_r": 1.5,
            "trail_distance_r": 0.5,
        }
        return [
            {"rsi_threshold": rsi, "ma_long": ml, **exit_params}
            for rsi in [8, 10, 12]
            for ml in [18, 20, 22]
        ]

    @property
    def default_params(self) -> dict:
        return {
            "rsi_threshold": 10,
            "ma_long": 20,
            "target_r": 2.0,
            "trail_activate_r": 1.5,
            "trail_distance_r": 0.5,
        }
