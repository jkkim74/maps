"""Immutable validation inputs and the shared promotion/live-entry gate."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
import zlib
from datetime import date, datetime, time, timedelta
from pathlib import Path

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import (OrderLog, PromotionHistory, ValidationRun,
    ParameterPlateauResults, WalkForwardResults, MonteCarloSequenceResults)
from maps.execution.safety import utcnow
from maps.market.trading_rules import trading_days_ago
from maps.strategy.catalog import STRATEGY_CLASSES


def strategy_fingerprint(strategy_id):
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    # Include shared signal, sizing, cost, and validation rules, not just the class.
    for directory in ("strategy", "indicator", "backtest", "validation"):
        for path in sorted((root / directory).rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    if strategy_id in ("fujimoto_safe_v1", "fujimoto_original_v1"):
        for path in sorted((root / "fujimoto").glob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    digest.update((root / "common" / "constants.py").read_bytes())
    cls = STRATEGY_CLASSES.get(strategy_id)
    params = cls().default_params if cls else {"special_strategy": strategy_id}
    return digest.hexdigest(), hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


class FrozenInputs:
    """Both grid and WFA receive copies of exactly the persisted input frames."""
    def __init__(self, repo, tickers, ref_date):
        self.frames = {ticker: repo.to_dataframe(ticker, end=ref_date).copy(deep=True) for ticker in tickers}

    def to_dataframe(self, ticker, **kwargs):
        return self.frames[ticker].copy(deep=True)

    def create_run(self, db, strategy, ref_date, sample_tickers, wfa_ticker):
        payload = json.dumps({ticker: frame.to_json(orient="table", date_format="iso", double_precision=15)
                              for ticker, frame in sorted(self.frames.items())}, sort_keys=True).encode()
        code, params = strategy_fingerprint(strategy.strategy_id)
        run = ValidationRun(id=str(uuid.uuid4()), strategy_id=strategy.strategy_id,
            run_date=ref_date, status="RUNNING", code_hash=code, params_hash=params,
            data_hash=hashlib.sha256(payload).hexdigest(), input_snapshot=zlib.compress(payload),
            manifest={"sample_tickers": sample_tickers, "wfa_ticker": wfa_ticker,
                "default_params": strategy.default_params, "param_grid": list(strategy.param_grid()),
                "mc_seed": 42, "mc_simulations": 1000, "format": "pandas-table-json-v1"},
            created_at=utcnow())
        db.add(run)
        db.commit()
        return run


def evidence_metrics(db, strategy_id, settings, ref_date=None):
    ref_date = ref_date or date.today()
    run = db.query(ValidationRun).filter(ValidationRun.strategy_id == strategy_id,
        ValidationRun.run_date <= ref_date).order_by(ValidationRun.created_at.desc()).first()
    if run is None:
        return {"evidence_errors": ["validation_run_missing"]}
    errors = []
    if run.status != "COMPLETE":
        errors.append("validation_run_incomplete")
    if (run.code_hash, run.params_hash) != strategy_fingerprint(strategy_id):
        errors.append("validation_fingerprint_changed")
    if run.run_date < trading_days_ago(ref_date, settings.maps_validation_max_age_trading_days, extra_closed_dates=settings.krx_closed_dates):
        errors.append("validation_expired")
    try:
        if hashlib.sha256(zlib.decompress(run.input_snapshot)).hexdigest() != run.data_hash:
            errors.append("validation_snapshot_corrupt")
    except (zlib.error, TypeError):
        errors.append("validation_snapshot_corrupt")
    metrics = dict(run.metrics or {})
    parts = [db.query(model).filter_by(validation_run_id=run.id).all() for model in
             (ParameterPlateauResults, MonteCarloSequenceResults, WalkForwardResults)]
    if any(len(rows) != 1 for rows in parts):
        errors.append("validation_components_missing_or_ambiguous")
    else:
        plateau, mc, wfa = (rows[0] for rows in parts)
        if any(row.strategy_id != strategy_id or row.run_date != run.run_date
               for row in (plateau, mc, wfa)):
            errors.append("validation_components_mismatched")
        measured = validation_metrics(plateau, mc, wfa)
        if any(metrics.get(key) != value for key, value in measured.items()):
            errors.append("validation_metrics_mismatched")
        metrics.update(measured)
        from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP
        group = STRATEGY_GROUP_MAP.get(strategy_id)
        limit = ALLOWED_MDD.get(group, {}).get("mc_p95_limit")
        if (limit is None or mc.strategy_group != group or not math.isfinite(mc.mdd_p95)
                or abs(mc.mdd_p95) > limit or mc.mdd_limit != limit):
            errors.append("mc_limit_invalid")
    # Legacy/unscoped/manual executions never become paper evidence by inference.
    rows = db.query(OrderLog).filter_by(strategy_id=strategy_id, environment="kis_paper",
        code_hash=run.code_hash, params_hash=run.params_hash).filter(OrderLog.fill_qty > 0,
        OrderLog.intent_id.isnot(None), OrderLog.account_key.isnot(None),
        OrderLog.created_at < datetime.combine(ref_date + timedelta(days=1), time.min) - timedelta(hours=9)
    ).order_by(OrderLog.created_at, OrderLog.id).all()
    holdings, completed = {}, 0
    for row in rows:
        holding_key = (row.account_key, row.ticker)
        prior = holdings.get(holding_key, 0)
        new = prior + (row.fill_qty if row.side == "buy" else -row.fill_qty)
        if new < 0:
            errors.append("paper_trade_ownership_mismatch")
        if prior > 0 and new == 0:
            completed += 1
        holdings[holding_key] = new
    months = max((ref_date - rows[0].created_at.date()).days, 0) / 30.44 if rows else 0
    paper_ready = completed >= settings.maps_validation_min_completed_trades and months >= 3
    replay_ready = (metrics.get("replay_equivalent_passed") is True
        and metrics.get("replay_trading_days", 0) >= 63
        and metrics.get("replay_completed_trades", 0) >= settings.maps_validation_min_completed_trades)
    if not (paper_ready or replay_ready):
        errors.append("insufficient_completed_trades_or_track_record")
    metrics.update(validation_run_id=run.id, evidence_errors=errors,
        mock_months=months, completed_trades=completed, evidence_valid=not errors)
    return metrics


def validation_metrics(plateau, mc, wfa):
    metrics = {}
    if plateau is not None:
        metrics.update(plateau_grade=plateau.grade,
                       robustness=max(0.0, min(float(plateau.positive_ratio), 1.0)))
    if mc is not None:
        ratio = abs(float(mc.mdd_p95)) / float(mc.mdd_limit) if mc.mdd_limit else 1.0
        metrics.update(mc_passed=bool(mc.mc_within_limit), mc_mdd_p95=float(mc.mdd_p95),
                       risk=max(0.0, min(1.0 - ratio, 1.0)))
    if wfa is not None:
        metrics.update(wfa_passed=bool(wfa.passed), oos_sharpe=float(wfa.sharpe_mean),
                       recovery=max(0.0, min(float(wfa.mean_g2p) / 2.0, 1.0)))
        metrics["return"] = max(0.0, min(float(wfa.sharpe_mean) / 2.0, 1.0))
    return metrics


def hard_gate_errors(metrics, *, full_live=False):
    errors = list(metrics.get("evidence_errors", []))
    if metrics.get("evidence_valid") is not True:
        errors.append("validation_evidence_required")
    if metrics.get("wfa_passed") is not True:
        errors.append("wfa_failed_or_missing")
    if metrics.get("mc_passed") is not True:
        errors.append("mc_failed_or_missing")
    grades = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    if grades.get(metrics.get("plateau_grade"), -1) < (3 if full_live else 2):
        errors.append("plateau_grade_insufficient")
    sharpe = metrics.get("oos_sharpe")
    if not isinstance(sharpe, (float, int)) or not math.isfinite(sharpe) or sharpe < (0.5 if full_live else 0.3):
        errors.append("oos_sharpe_insufficient")
    return errors


def require_live_eligibility(db, strategy_id, settings):
    stage = db.query(PromotionHistory).filter_by(strategy_id=strategy_id, passed=True).order_by(
        PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc()).first()
    if stage is None or stage.to_stage not in ("live_candidate", "live"):
        raise ExecutionBlockedError("strategy_not_live_eligible")
    metrics = evidence_metrics(db, strategy_id, settings)
    errors = hard_gate_errors(metrics, full_live=stage.to_stage == "live")
    from maps.common.constants import WEIGHT_PRESETS
    score = sum(float(metrics.get(k, 0)) * w for k, w in WEIGHT_PRESETS["balanced"].items()) * 100
    if not math.isfinite(score) or score < 75:
        errors.append("tradeability_below_75")
    if errors:
        raise ExecutionBlockedError(";".join(errors))
    return metrics["validation_run_id"]
