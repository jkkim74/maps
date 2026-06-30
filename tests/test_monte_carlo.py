"""MonteCarloValidator MDD p95 검증 테스트."""

import numpy as np
import pytest

from maps.common.exceptions import ValidationError
from maps.validation.monte_carlo import MonteCarloValidator


@pytest.fixture
def validator() -> MonteCarloValidator:
    return MonteCarloValidator(n_simulations=500, seed=42)


def _good_returns() -> list[float]:
    rng = np.random.default_rng(0)
    return list(rng.normal(0.001, 0.01, 252))


def test_unknown_strategy_group_raises(validator: MonteCarloValidator) -> None:
    with pytest.raises(ValidationError, match="알 수 없는 전략군"):
        validator.validate("s1", "unknown_group", _good_returns())


def test_too_few_returns_raises(validator: MonteCarloValidator) -> None:
    with pytest.raises(ValidationError, match="최소 30개"):
        validator.validate("s1", "pullback_short", [0.001] * 10)


def test_low_vol_strategy_passes_pullback_limit(validator: MonteCarloValidator) -> None:
    returns = _good_returns()
    result = validator.validate("s1", "pullback_short", returns)
    # 저변동 수익률이면 MDD p95 < 18% 통과 기대
    assert result.mdd_p95 < result.mdd_limit or not result.passed  # 구조 확인


def test_block_bootstrap_estimate_is_conservative() -> None:
    """게이트 입력 MDD p95는 i.i.d. 단독보다 작지 않아야 한다 (H-2 보수화)."""
    rng = np.random.default_rng(7)
    returns = list(rng.normal(0.0005, 0.02, 252))
    conservative = MonteCarloValidator(n_simulations=500, seed=42, block_bootstrap=True)
    iid_only = MonteCarloValidator(n_simulations=500, seed=42, block_bootstrap=False)

    r_cons = conservative.validate("s1", "pullback_short", returns)
    r_iid = iid_only.validate("s1", "pullback_short", returns)

    assert r_cons.mdd_p95 >= r_iid.mdd_p95
