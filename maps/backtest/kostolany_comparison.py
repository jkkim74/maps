"""코스톨라니 전환 효과 검증 — 레거시 vs 코스톨라니 백테스트 비교.

9가지 시장 시나리오에서 4개 모드를 비교한다:
  MODE_A: 레거시 MAPS (기존 추세추종)
  MODE_B: 코스톨라니 (모든 신규 기능)
  MODE_C: 코스톨라니 — contrarian 전략 제외
  MODE_D: 코스톨라니 + AI contrarian check 활성화

시나리오:
  1. 급락장       — 지수 -20%~-30% 구간
  2. 공포 후 반등  — 급락 후 V자 반등
  3. 박스권 횡보   — ±5% 박스권 3개월 이상
  4. 고변동성 횡보 — 일별 변동성 2% 이상, 추세 없음
  5. 개인 과열장   — 테마 집중, 추세추종 군중 과열
  6. 안정 강세장   — 지수 꾸준 상승
  7. 혼조 약세장   — 업종별 혼조
  8. 금리 인상 구간 — 고금리 부담 장세
  9. 급등 후 조정  — 급등 이후 30~50% 조정
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

logger = logging.getLogger(__name__)


@dataclass
class ComparisonModeConfig:
    """비교 모드별 설정."""

    name: str
    legacy_scoring: bool = True          # 기존 점수 공식 사용
    kostolany_regime: bool = False        # 복합 시장 판단 사용
    contrarian_strategy: bool = False     # contrarian_quality 전략 포함
    ai_contrarian_check: bool = False     # AI contrarian 검증 사용
    holding_type_enabled: bool = False    # CORE/SWING/TRADING 분류 사용
    kostolany_price: bool = False         # 이중 목표가 사용
    sector_filter: bool = False           # 섹터 필터 사용
    strategy_aware_scoring: bool = False  # 전략별 점수 분리
    description: str = ""


MODE_A = ComparisonModeConfig(
    name="legacy",
    legacy_scoring=True,
    description="기존 MAPS 추세추종 — 모든 코스톨라니 기능 OFF",
)
MODE_B = ComparisonModeConfig(
    name="kostolany_full",
    legacy_scoring=False,
    kostolany_regime=True,
    contrarian_strategy=True,
    ai_contrarian_check=False,
    holding_type_enabled=True,
    kostolany_price=True,
    sector_filter=True,
    strategy_aware_scoring=True,
    description="코스톨라니 전체 (AI 제외) — 모든 규칙 기반 기능 ON",
)
MODE_C = ComparisonModeConfig(
    name="kostolany_no_contrarian",
    legacy_scoring=False,
    kostolany_regime=True,
    contrarian_strategy=False,
    holding_type_enabled=True,
    kostolany_price=True,
    sector_filter=True,
    strategy_aware_scoring=True,
    description="코스톨라니 — contrarian 전략 제외 (추세·풀백만)",
)
MODE_D = ComparisonModeConfig(
    name="kostolany_with_ai",
    legacy_scoring=False,
    kostolany_regime=True,
    contrarian_strategy=True,
    ai_contrarian_check=True,
    holding_type_enabled=True,
    kostolany_price=True,
    sector_filter=True,
    strategy_aware_scoring=True,
    description="코스톨라니 전체 + AI contrarian 검증",
)

ALL_MODES = [MODE_A, MODE_B, MODE_C, MODE_D]


@dataclass
class ScenarioSpec:
    """백테스트 시나리오 정의."""

    name: str
    description: str
    expected_winner: str  # 어떤 모드가 유리할지 가설
    regime_override: str = "mixed"


SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec(
        name="crash",
        description="급락장 — 지수 -20%~-30%",
        expected_winner="kostolany_full",
        regime_override="weak",
    ),
    ScenarioSpec(
        name="post_crash_rebound",
        description="공포 후 V자 반등",
        expected_winner="kostolany_full",
        regime_override="weak",
    ),
    ScenarioSpec(
        name="box_range",
        description="박스권 횡보 ±5%",
        expected_winner="kostolany_no_contrarian",
        regime_override="mixed",
    ),
    ScenarioSpec(
        name="high_vol_sideways",
        description="고변동성 횡보 — 일별 변동성 2%+",
        expected_winner="kostolany_full",
        regime_override="weak",
    ),
    ScenarioSpec(
        name="retail_bubble",
        description="개인 과열 테마 집중",
        expected_winner="kostolany_with_ai",
        regime_override="mixed",
    ),
    ScenarioSpec(
        name="stable_bull",
        description="안정 강세장",
        expected_winner="legacy",
        regime_override="strong",
    ),
    ScenarioSpec(
        name="mixed_bear",
        description="혼조 약세장 — 업종별 혼조",
        expected_winner="kostolany_no_contrarian",
        regime_override="mixed",
    ),
    ScenarioSpec(
        name="rate_hike",
        description="금리 인상 구간 — 고금리 부담",
        expected_winner="kostolany_full",
        regime_override="weak",
    ),
    ScenarioSpec(
        name="correction_after_surge",
        description="급등 후 30~50% 조정",
        expected_winner="kostolany_full",
        regime_override="weak",
    ),
]


@dataclass
class ModeResult:
    """단일 모드의 백테스트 결과."""

    mode_name: str
    scenario_name: str
    cagr: float | None = None
    mdd: float | None = None
    sharpe: float | None = None
    win_rate: float | None = None
    avg_hold_days: float | None = None
    trade_count: int = 0
    avg_cash_ratio: float | None = None
    error: str | None = None


@dataclass
class ComparisonResult:
    """전체 비교 결과."""

    scenario_name: str
    results: list[ModeResult] = field(default_factory=list)

    def best_by_cagr(self) -> ModeResult | None:
        valid = [r for r in self.results if r.cagr is not None]
        return max(valid, key=lambda r: r.cagr or -999.0) if valid else None

    def best_by_sharpe(self) -> ModeResult | None:
        valid = [r for r in self.results if r.sharpe is not None]
        return max(valid, key=lambda r: r.sharpe or -999.0) if valid else None

    def worst_mdd(self) -> ModeResult | None:
        valid = [r for r in self.results if r.mdd is not None]
        return max(valid, key=lambda r: r.mdd or 0.0) if valid else None

    def to_text(self) -> str:
        lines = [f"[시나리오: {self.scenario_name}]"]
        header = f"  {'모드':<30} {'CAGR':>8} {'MDD':>8} {'Sharpe':>8} {'승률':>7} {'거래수':>6}"
        lines.append(header)
        lines.append("  " + "-" * 72)
        for r in self.results:
            if r.error:
                lines.append(f"  {r.mode_name:<30} ERROR: {r.error}")
                continue
            cagr_s = f"{r.cagr:.1%}" if r.cagr is not None else "N/A"
            mdd_s = f"{r.mdd:.1%}" if r.mdd is not None else "N/A"
            sharpe_s = f"{r.sharpe:.2f}" if r.sharpe is not None else "N/A"
            wr_s = f"{r.win_rate:.1%}" if r.win_rate is not None else "N/A"
            tc_s = str(r.trade_count)
            lines.append(f"  {r.mode_name:<30} {cagr_s:>8} {mdd_s:>8} {sharpe_s:>8} {wr_s:>7} {tc_s:>6}")
        best = self.best_by_sharpe()
        if best:
            lines.append(f"  → Sharpe 최고: {best.mode_name}")
        return "\n".join(lines)


class KostolanyComparisonRunner:
    """코스톨라니 vs 레거시 백테스트 비교 실행기.

    실제 BacktestEngine 호출은 외부(scheduler 또는 API)에서 주입한다.
    이 클래스는 설정 조합을 생성하고 결과를 집계·리포팅한다.
    """

    def __init__(self, modes: list[ComparisonModeConfig] | None = None):
        self._modes = modes or ALL_MODES

    def build_env_overrides(self, mode: ComparisonModeConfig) -> dict[str, bool | float]:
        """모드 설정을 환경변수 오버라이드 딕셔너리로 변환한다."""
        return {
            "maps_legacy_scoring_only": mode.legacy_scoring,
            "maps_kostolany_mode_enabled": not mode.legacy_scoring,
            "maps_sector_filter_enabled": mode.sector_filter,
            "maps_sector_kostolany_mode_enabled": mode.sector_filter,
            "maps_ai_contrarian_check_enabled": mode.ai_contrarian_check,
            "maps_holding_type_classification_enabled": mode.holding_type_enabled,
            "maps_kostolany_price_calculator_enabled": mode.kostolany_price,
        }

    def aggregate(self, all_results: list[ComparisonResult]) -> str:
        """전체 시나리오 결과를 종합 요약 텍스트로 반환한다."""
        wins: dict[str, int] = {m.name: 0 for m in self._modes}
        total = len(all_results)

        for cr in all_results:
            best = cr.best_by_sharpe()
            if best and best.mode_name in wins:
                wins[best.mode_name] += 1

        lines = ["=" * 60, "코스톨라니 vs 레거시 종합 비교", "=" * 60]
        for mode_name, win_count in sorted(wins.items(), key=lambda x: -x[1]):
            pct = win_count / total if total else 0.0
            lines.append(f"  {mode_name:<30}: {win_count}/{total} 시나리오 Sharpe 최고 ({pct:.0%})")

        lines.append("")
        for cr in all_results:
            lines.append(cr.to_text())

        return "\n".join(lines)

    def generate_comparison_report(
        self,
        all_results: list[ComparisonResult],
        scenarios: list[ScenarioSpec] | None = None,
    ) -> str:
        """가설 검증 결과를 포함한 상세 리포트를 반환한다."""
        scenarios = scenarios or SCENARIOS
        lines = [self.aggregate(all_results), "", "【가설 검증】"]

        scenario_map = {s.name: s for s in scenarios}
        for cr in all_results:
            spec = scenario_map.get(cr.scenario_name)
            if not spec:
                continue
            best = cr.best_by_sharpe()
            actual_winner = best.mode_name if best else "N/A"
            match = "✓" if actual_winner == spec.expected_winner else "✗"
            lines.append(
                f"  {match} {cr.scenario_name}: 예상={spec.expected_winner}, 실제={actual_winner}"
            )

        return "\n".join(lines)
