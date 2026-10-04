"""Live eligibility must use reproducible, mutually consistent evidence."""
import datetime as dt
import hashlib
import zlib

import pytest

from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP
from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import (ValidationRun, ParameterPlateauResults,
    MonteCarloSequenceResults, WalkForwardResults, PromotionHistory, OrderLog)
from maps.common.settings import MapsSettings
from maps.execution.safety import utcnow
from maps.promotion.evidence import (evidence_metrics, strategy_fingerprint,
    validation_metrics, require_live_eligibility)
from maps.validation.plateau import ParameterPlateauTester


@pytest.fixture
def evidence(db):
    strategy = "pullback_v3"
    today = dt.date.today()
    code, params = strategy_fingerprint(strategy)
    payload = b'{"frozen_prices": [100, 101]}'
    run = ValidationRun(id="verified", strategy_id=strategy, run_date=today,
        status="COMPLETE", code_hash=code, params_hash=params,
        data_hash=hashlib.sha256(payload).hexdigest(), input_snapshot=zlib.compress(payload),
        manifest={"mc_seed": 42}, created_at=utcnow())
    common = dict(validation_run_id=run.id, strategy_id=strategy, run_date=today)
    group = STRATEGY_GROUP_MAP[strategy]
    parts = [
        ParameterPlateauResults(**common, total_combinations=5, positive_combinations=5,
            positive_ratio=1.0, grade="A"),
        MonteCarloSequenceResults(**common, strategy_group=group, n_simulations=1000,
            mdd_p95=.01, mdd_limit=ALLOWED_MDD[group]["mc_p95_limit"], mc_within_limit=True),
        WalkForwardResults(**common, n_folds=6, sharpe_mean=2, sharpe_std=.1,
            negative_folds=0, mean_g2p=2, passed=True),
    ]
    run.metrics = {**validation_metrics(*parts), "replay_equivalent_passed": True,
        "replay_trading_days": 63, "replay_completed_trades": 20}
    db.add_all([run, *parts, PromotionHistory(strategy_id=strategy,
        from_stage="mock_candidate", to_stage="live_candidate", passed=True,
        tradeability_score=99, validation_run_id=run.id)])
    db.commit()
    return run, parts, MapsSettings(krx_closed_dates="")


def test_consistent_evidence_allows_live_entry(db, evidence):
    run, _, settings = evidence
    assert require_live_eligibility(db, run.strategy_id, settings) == run.id


@pytest.mark.parametrize("change,reason", [
    ("code", "validation_fingerprint_changed"),
    ("params", "validation_fingerprint_changed"),
    ("expired", "validation_expired"),
    ("snapshot", "validation_snapshot_corrupt"),
    ("missing", "validation_components_missing_or_ambiguous"),
    ("mixed", "validation_components_mismatched"),
    ("metrics", "validation_metrics_mismatched"),
    ("mc", "mc_limit_invalid"),
    ("trades", "insufficient_completed_trades_or_track_record"),
])
def test_old_promotion_cannot_override_bad_evidence(db, evidence, change, reason):
    run, parts, settings = evidence
    if change == "code":
        run.code_hash = "changed"
    elif change == "params":
        run.params_hash = "changed"
    elif change == "expired":
        run.run_date -= dt.timedelta(days=50)
    elif change == "snapshot":
        run.input_snapshot = b"corrupt"
    elif change == "missing":
        db.delete(parts[2])
    elif change == "mixed":
        parts[2].strategy_id = "another_strategy"
    elif change == "metrics":
        run.metrics = {**run.metrics, "oos_sharpe": 9}
    elif change == "mc":
        parts[1].mdd_p95 = .9
    else:
        run.metrics = {**run.metrics, "replay_completed_trades": 19}
    db.commit()
    with pytest.raises(ExecutionBlockedError, match=reason):
        require_live_eligibility(db, run.strategy_id, settings)


def test_partial_fills_do_not_count_as_completed_paper_trades(db, evidence):
    run, _, settings = evidence
    run.metrics = {**run.metrics, "replay_equivalent_passed": False}
    for i in range(20):
        db.add(OrderLog(order_id=f"buy-{i}", intent_id=f"intent-{i}", account_key="paper",
            environment="kis_paper", strategy_id=run.strategy_id, ticker="AAA", side="buy",
            qty=1, fill_qty=1, status="filled", code_hash=run.code_hash,
            params_hash=run.params_hash, created_at=utcnow() - dt.timedelta(days=120)))
    db.commit()
    metrics = evidence_metrics(db, run.strategy_id, settings)
    assert metrics["completed_trades"] == 0
    assert not metrics["evidence_valid"]


def test_plateau_centers_on_deployed_parameters():
    grid = [{"period": 10, "sharpe": 1, "mdd": .1},
            {"period": 20, "sharpe": 2, "mdd": .1},
            {"period": 30, "sharpe": 1, "mdd": .1}]
    result = ParameterPlateauTester().run(grid, param_keys=["period"], center_params={"period": 10})
    assert result.best_combo["period"] == 10
    with pytest.raises(ValueError, match="Exactly one"):
        ParameterPlateauTester().run(grid, param_keys=["period"], center_params={"period": 99})
