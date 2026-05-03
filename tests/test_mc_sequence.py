"""MonteCarloSequenceTester + BlockBootstrapTester 단위 테스트 (Phase 3)."""

from __future__ import annotations

import numpy as np
import pytest

from maps.validation.monte_carlo import (
    BlockBootstrapTester,
    MonteCarloSequenceTester,
    SequenceTestResult,
)


def _trade_pnls(n: int = 50, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(rng.normal(500, 2000, n))


def _daily_returns(n: int = 252, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(rng.normal(0.001, 0.015, n))


# ---------------------------------------------------------------------------
# MonteCarloSequenceTester
# ---------------------------------------------------------------------------

def test_mc_sequence_result_structure() -> None:
    """SequenceTestResult 구조가 올바르게 반환된다."""
    tester = MonteCarloSequenceTester(n_simulations=200, seed=42)
    result = tester.run(_trade_pnls())

    assert isinstance(result, SequenceTestResult)
    assert result.n_simulations == 200
    # MDD는 절댓값 손실 심각도: 0 <= p50 <= p75 <= p90 <= p95
    assert 0 <= result.mdd_p50 <= result.mdd_p75 <= result.mdd_p90 <= result.mdd_p95


def test_mc_sequence_mdd_nonnegative() -> None:
    """MDD 심각도는 항상 0 이상."""
    tester = MonteCarloSequenceTester(n_simulations=100, seed=1)
    result = tester.run(_trade_pnls())
    assert result.mdd_p95 >= 0


def test_mc_sequence_reproducible() -> None:
    """같은 seed면 결과가 동일하다."""
    t1 = MonteCarloSequenceTester(n_simulations=100, seed=99)
    t2 = MonteCarloSequenceTester(n_simulations=100, seed=99)
    r1 = t1.run(_trade_pnls())
    r2 = t2.run(_trade_pnls())
    assert abs(r1.mdd_p95 - r2.mdd_p95) < 1e-10


# ---------------------------------------------------------------------------
# BlockBootstrapTester
# ---------------------------------------------------------------------------

def test_block_bootstrap_returns_all_block_sizes() -> None:
    """run() 은 block_sizes 각각에 대한 결과를 반환한다."""
    tester = BlockBootstrapTester(block_sizes=[5, 10, 20], n_simulations=100, seed=42)
    results = tester.run(_daily_returns())

    assert set(results.keys()) == {5, 10, 20}
    for r in results.values():
        assert isinstance(r, SequenceTestResult)
        assert r.mdd_p95 >= 0


def test_block_bootstrap_cluster_risk_high() -> None:
    """BBoot p95 / MC p95 비율 >= 1.5 이면 군집성 위험 True."""
    tester = BlockBootstrapTester()
    # MC result: p95 작음(5% 손실), BB result: p95 큼(10% 손실)
    mc = SequenceTestResult(n_simulations=100, mdd_p50=0.02, mdd_p75=0.03, mdd_p90=0.04, mdd_p95=0.05)
    bb = SequenceTestResult(n_simulations=100, mdd_p50=0.03, mdd_p75=0.05, mdd_p90=0.07, mdd_p95=0.10)

    ratio, is_risk = tester.cluster_risk_ratio(mc, bb)
    assert ratio == pytest.approx(2.0)
    assert is_risk is True


def test_block_bootstrap_cluster_risk_low() -> None:
    """BBoot p95 / MC p95 비율 < 1.5 이면 군집성 위험 False."""
    tester = BlockBootstrapTester()
    mc = SequenceTestResult(n_simulations=100, mdd_p50=0.05, mdd_p75=0.07, mdd_p90=0.09, mdd_p95=0.10)
    bb = SequenceTestResult(n_simulations=100, mdd_p50=0.05, mdd_p75=0.07, mdd_p90=0.10, mdd_p95=0.12)

    ratio, is_risk = tester.cluster_risk_ratio(mc, bb)
    assert ratio == pytest.approx(1.2)
    assert is_risk is False
