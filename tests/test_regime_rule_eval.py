"""장세 규칙 오프라인 평가기 — 현행 재현이 운영 코드와 같은지, 대안 규칙이 의도대로 다른지."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from maps.market.regime import MarketRegimeAnalyzer

_SPEC = importlib.util.spec_from_file_location(
    "regime_rule_eval", Path(__file__).resolve().parents[1] / "scripts" / "regime_rule_eval.py"
)
rre = importlib.util.module_from_spec(_SPEC)
sys.modules["regime_rule_eval"] = rre
_SPEC.loader.exec_module(rre)


@pytest.fixture
def closes() -> pd.DataFrame:
    """9자산 3년치 합성 일봉 — 후반에 KOSPI 급락을 넣는다."""
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2023-01-02", "2026-03-31")
    frame = {}
    for name in rre._TICKERS:
        steps = rng.normal(0.0004, 0.012, len(days))
        frame[name] = 100 * np.exp(np.cumsum(steps))
    closes = pd.DataFrame(frame, index=days)
    crash = closes.index >= "2026-01-15"
    closes.loc[crash, "KOSPI"] *= np.exp(-0.004 * np.arange(crash.sum()))
    return closes


def test_baseline_replay_matches_production_analyzer(closes) -> None:
    """재생기의 raw 라벨·주간추세·변동성은 운영 분석기를 같은 주봉으로 돌린 값과 같다."""
    day = pd.Timestamp("2026-02-10")
    provider = rre.HistoricalWeeklyProvider(closes, day)
    expected = MarketRegimeAnalyzer(provider)._compute()

    frame = rre.replay(closes, rre.RULE_SETS["baseline"], day, day)

    row = frame.loc[day]
    assert row["raw"] == expected.regime.value
    assert row["weekly_trend"] == expected.weekly_trend.value
    assert row["vol_regime"] == expected.vol_regime.value
    assert row["up_count"] == expected.up_count
    # 히스테리시스 이력이 없는 첫날은 applied == raw (가드 제외)
    if not row["guard"]:
        assert row["applied"] == row["raw"]
        assert row["elr"] == expected.entry_limit_ratio


def test_overseas_assets_lag_one_day(closes) -> None:
    day = pd.Timestamp("2026-02-10")
    provider = rre.HistoricalWeeklyProvider(closes, day)

    kospi_last = provider.get_weekly_closes("KOSPI", 2)[-1]
    spx_last = provider.get_weekly_closes("S&P 500", 2)[-1]

    assert kospi_last == pytest.approx(float(closes.loc[day, "KOSPI"]))
    assert spx_last == pytest.approx(float(closes.loc[day - pd.Timedelta(days=1), "S&P 500"]))


def test_step_rule_never_stricter_than_zero_rule(closes) -> None:
    base = rre.replay(closes, rre.RULE_SETS["baseline"], pd.Timestamp("2026-01-05"), pd.Timestamp("2026-03-31"))
    step = rre.replay(closes, rre.RULE_SETS["a_step"], pd.Timestamp("2026-01-05"), pd.Timestamp("2026-03-31"))

    assert (step["elr"] >= base["elr"]).all()
    failing = base["weekly_trend"] == "fail"
    assert failing.any(), "합성 급락이 주간추세 FAIL 을 만들어야 규칙 차이를 검증할 수 있다"
    assert (base.loc[failing, "elr"] == 0).all()


def test_entry_limit_matrix_matches_production_table() -> None:
    zero, step = rre.Rules(), rre.Rules(weekly_fail="step")
    assert rre.entry_limit("strong", "pass", "normal", zero) == 1.0
    assert rre.entry_limit("strong", "pass", "high", zero) == 0.5
    assert rre.entry_limit("weak", "pass", "high", zero) == 0.0
    assert rre.entry_limit("mixed", "fail", "high", zero) == 0.0
    assert rre.entry_limit("mixed", "fail", "high", step) == 0.0    # 0.25 → 한 단계 아래
    assert rre.entry_limit("strong", "fail", "normal", step) == 0.5


def test_strong_band_holds_previous_label() -> None:
    from maps.market.regime import RegimeLabel, RegimeResult, WeeklyTrendLabel

    result = RegimeResult(
        regime=RegimeLabel.STRONG, weekly_trend=WeeklyTrendLabel.PASS, limit_ratio=0.0,
        kospi_ts=60.0, up_count=6, total_assets=9, kospi_above_ma5w=True, kospi_above_ma10w=True,
    )  # 6/9 = 0.667 — 밴드 안
    prev = {"applied": "mixed", "above5": True}

    held, _, _ = rre.apply_offline_hysteresis(result, rre.Rules(strong_band=(0.62, 0.70)), prev)
    raw, _, _ = rre.apply_offline_hysteresis(result, rre.Rules(), prev)

    assert held == "mixed"
    assert raw == "strong"


def test_sox_rule_swaps_copper(closes) -> None:
    assert "SOX" in rre.RULE_SETS["c_sox"].assets
    assert "구리" not in rre.RULE_SETS["c_sox"].assets
    frame = rre.replay(closes, rre.RULE_SETS["c_sox"], pd.Timestamp("2026-02-10"), pd.Timestamp("2026-02-10"))
    assert int(frame["total_assets"].iloc[0]) == 8


def test_summary_reports_gate_timing(closes) -> None:
    frame = rre.replay(closes, rre.RULE_SETS["baseline"], pd.Timestamp("2026-01-05"), pd.Timestamp("2026-03-31"))
    out = rre.summarize(frame, peak=pd.Timestamp("2026-01-15"), trough=pd.Timestamp("2026-03-31"))
    assert set(out) >= {"exposure_return_pct", "max_drawdown_pct", "switches", "days_to_close_after_peak"}
