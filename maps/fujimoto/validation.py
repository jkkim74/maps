"""Measured stateful research; no caller-supplied pass flags or live permission."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import math
import uuid
import zlib

import numpy as np

from maps.common.constants import WF_NEGATIVE_FOLD_MAX, WF_OOS_IS_G2P_MIN
from maps.common.exceptions import DataQualityError
from maps.common.models import (FujimotoEvidence, ValidationRun, ParameterPlateauResults,
                                WalkForwardResults, WalkForwardFoldResults, MonteCarloSequenceResults)
from maps.fujimoto.replay import ReplayInput, input_from_json, replay, stress_losses, quote_session_date
from maps.fujimoto.repository import FujimotoRepository, fingerprint, json_data
from maps.validation.monte_carlo import MonteCarloValidator
from maps.validation.plateau import ParameterPlateauTester


def statistics(equity: list[float], initial: float) -> dict:
    """Derive returns/MDD/Sharpe from cash-conserving replay equity, including day 1."""
    values = np.asarray([initial, *equity], dtype=float)
    returns = np.diff(values) / values[:-1]
    drawdown = 1 - values / np.maximum.accumulate(values)
    std = float(np.std(returns, ddof=1)) if len(returns) >= 2 else 0
    losses = -float(returns[returns < 0].sum())
    gains = float(returns[returns > 0].sum())
    return {"return": float(values[-1] / initial - 1), "mdd": float(max(drawdown)),
            "sharpe": float(np.mean(returns) / std * math.sqrt(252)) if std > 0 else 0,
            "g2p": min(3., gains / losses) if losses else (3. if gains else 0.),
            "daily_returns": returns.tolist()}


def run_research(data: ReplayInput) -> dict:
    """Run reproducible baseline, actual tape on/off, doubled costs and RSI neighbors.

    WFA resets cycle/account state for independent chronological IS/OOS windows;
    the legacy boolean BacktestEngine/WalkForwardAnalyzer cannot model these legs.
    Existing plateau and seeded block-bootstrap MC remain the metric primitives.
    """
    baseline = replay(data)
    variants = (list(product((38, 40, 42), (.18, .2, .22), (-.01, 0, .01)))
                if data.screening else [(rsi, .2, 0) for rsi in (38, 40, 42)])
    neighbors = []
    for rsi, surge, dividend in variants:
        measured = json_data(replay(data, first_rsi=rsi, maximum_return_20=surge, minimum_dividend_growth=dividend))
        measured["parameters"] = {"first_rsi": rsi, "maximum_return_20": surge, "minimum_dividend_growth": dividend}
        neighbors.append(measured)
    days = sorted(data.evidence)
    folds = []
    if len(days) >= 60:
        chunks = np.array_split(days, 6)
        for i in range(1, 6):
            fold = {}
            for label, selected in (("is", chunks[i - 1]), ("oos", chunks[i])):
                window = set(selected)
                subset = replace(data, evidence={d: rows for d, rows in data.evidence.items() if d in window},
                                 bars={d: rows for d, rows in data.bars.items() if d in window},
                                 screening={d: rows for d, rows in data.screening.items() if d in window},
                                 candidate_order={d: rows for d, rows in data.candidate_order.items() if d in window},
                                 tape=tuple(q for q in data.tape if quote_session_date(q) in window))
                fold[label] = json_data(replay(subset))
            folds.append(fold)
    return {"baseline": json_data(baseline), "with_orderbook": json_data(replay(data, with_orderbook=True)),
            "without_orderbook": json_data(baseline), "cost_double": json_data(replay(data, cost_multiplier=2)),
            "neighbors": neighbors, "wfa": folds,
            "stress": {"safe": stress_losses(.5), "original": stress_losses(.675), "combined": stress_losses(.5875)},
            "runner": "fujimoto_stateful_v1"}


@dataclass(frozen=True)
class ValidationResult:
    """Combined supplemental gate; existing promotion policy remains mandatory."""
    status: str
    reasons: tuple[str, ...]
    metrics: dict
    replay_id: int
    fingerprint: str


def validation(repository: FujimotoRepository, replay_id: int, *, account_mdd_limit: float = .28) -> ValidationResult:
    """Recompute from stored inputs, verify fingerprints, derive all measured gates."""
    stored = repository.session.get(FujimotoEvidence, replay_id)
    if stored is None or stored.kind != "replay" or not 0 < account_mdd_limit <= 1:
        raise DataQualityError("invalid_replay_validation")
    if fingerprint(stored.payload) != stored.fingerprint:
        raise DataQualityError("replay_evidence_changed")
    data = input_from_json(stored.payload["inputs"])
    report = run_research(data)
    if fingerprint(report) != fingerprint(stored.payload["report"]):
        raise DataQualityError("replay_result_or_code_mismatch")
    reasons, failures, metrics = set(), set(), {}
    if not data.tape:
        reasons.add("missing_recorded_tape")
    if not data.provenance.get("annual_evidence") or not data.provenance.get("candidate_evidence"):
        reasons.add("missing_source_provenance")
    if any(ticker not in data.screening.get(day, {}) for day, rows in data.evidence.items() for ticker in rows):
        reasons.add("insufficient_screening_neighborhood")
    if any(day not in data.candidate_order for day in data.evidence):
        reasons.add("missing_recorded_candidate_ranking")
    reasons.update(report["baseline"]["reasons"])
    # Reproducible tape-direction cohorts, not a claimed official regime model.
    regimes, previous_marks = {}, {}
    for day, bars in sorted(data.bars.items()):
        changes = [bar.close / previous_marks[ticker] - 1 for ticker, bar in bars.items() if ticker in previous_marks]
        if changes:
            change = float(np.mean(changes))
            regimes[day.isoformat()] = "rising" if change > .003 else "falling" if change < -.003 else "sideways"
        previous_marks.update({ticker: bar.close for ticker, bar in bars.items()})
    if any(list(regimes.values()).count(label) < 10 for label in ("rising", "falling", "sideways")):
        reasons.add("insufficient_market_regimes")
    for mode, initial, group, limit in (("safe", data.budget / 2, "fujimoto_safe", .15),
                                      ("original", data.budget / 2, "fujimoto_original", .25),
                                      ("combined", data.budget, "portfolio_total", account_mdd_limit)):
        base = statistics([row[mode] for row in report["baseline"]["equity"]], initial)
        base["regime_returns"] = {label: [value for row, value in zip(report["baseline"]["equity"], base["daily_returns"])
                                          if regimes.get(row["date"]) == label] for label in ("rising", "falling", "sideways")}
        exits = [f for f in report["baseline"]["fills"] if f["completed_cycle"] and (mode == "combined" or f["mode"] == mode)]
        base["exit_samples"] = len(exits)
        if len(exits) < 30:
            reasons.add("insufficient_trade_samples")
        if len(base["daily_returns"]) < 30:
            reasons.add("insufficient_daily_returns")
        else:
            mc = MonteCarloValidator(n_simulations=1000, seed=42).validate(mode, group, base["daily_returns"])
            base["mc_p95"] = mc.mdd_p95
            if mc.mdd_p95 > limit:
                failures.add(mode + "_mc_limit")
        neighbor_rows = [dict(**row["parameters"], **statistics([e[mode] for e in row["equity"]], initial))
                         for row in report["neighbors"]]
        plateau = ParameterPlateauTester().run(neighbor_rows,
            param_keys=["first_rsi", "maximum_return_20", "minimum_dividend_growth"],
            center_params={"first_rsi": 40, "maximum_return_20": .2, "minimum_dividend_growth": 0})
        base["plateau"] = json_data(plateau)
        base["positive_neighbor_ratio"] = sum(row["return"] > 0 for row in neighbor_rows) / len(neighbor_rows)
        base["neighbor_count"] = len(neighbor_rows)
        if base["return"] <= 0 or base["positive_neighbor_ratio"] < .6 or not plateau.passed:
            failures.add(mode + "_parameter_neighborhood")
        doubled = statistics([e[mode] for e in report["cost_double"]["equity"]], initial)
        base["cost_double"] = doubled
        if doubled["return"] <= 0:
            failures.add(mode + "_cost_double")
        folds = []
        for fold in report["wfa"]:
            ins = statistics([e[mode] for e in fold["is"]["equity"]], initial)
            oos = statistics([e[mode] for e in fold["oos"]["equity"]], initial)
            fold_exits = [f for f in fold["oos"]["fills"] if f["completed_cycle"] and (mode == "combined" or f["mode"] == mode)]
            folds.append({"sharpe": oos["sharpe"], "g2p_ratio": oos["g2p"] / ins["g2p"] if ins["g2p"] else 0,
                          "exit_samples": len(fold_exits), "is": ins, "oos": oos,
                          "is_start": fold["is"]["equity"][0]["date"], "is_end": fold["is"]["equity"][-1]["date"],
                          "oos_start": fold["oos"]["equity"][0]["date"], "oos_end": fold["oos"]["equity"][-1]["date"]})
        base["wfa"] = folds
        if len(folds) < 5 or sum(not f["exit_samples"] for f in folds) > 1:
            reasons.add("insufficient_wfa")
        elif np.mean([f["sharpe"] for f in folds]) <= 0 or sum(f["sharpe"] < 0 for f in folds) > WF_NEGATIVE_FOLD_MAX or np.mean([f["g2p_ratio"] for f in folds]) < WF_OOS_IS_G2P_MIN:
            failures.add(mode + "_wfa")
        metrics[mode] = base
    # Tape availability is not tape validation: actual on/off runs need independent exits.
    taped_exits = [f for f in report["with_orderbook"]["fills"] if f["reason"] == "orderbook_take_profit"]
    if len({(f["mode"], f["ticker"], f["cycle_number"]) for f in taped_exits}) < 30:
        reasons.add("insufficient_orderbook_samples")
    metrics["with_orderbook"] = report["with_orderbook"]
    metrics["without_orderbook"] = report["without_orderbook"]
    metrics["stress"] = report["stress"]
    status = "insufficient" if reasons else "failed" if failures else "passed"
    return ValidationResult(status, tuple(sorted(reasons | failures)), metrics, replay_id, fingerprint(report))


def persist_validation(repository: FujimotoRepository, replay_id: int, *, account_mdd_limit: float = .28) -> tuple:
    """Persist reproducible standard components plus supplemental combined evidence.

    INSUFFICIENT never becomes COMPLETE; measured failures retain actual metrics.
    No PromotionHistory is created, and callers still require the existing gate.
    """
    from datetime import date
    from maps.fujimoto.domain import Mode
    from maps.promotion.evidence import strategy_fingerprint, validation_metrics
    result = validation(repository, replay_id, account_mdd_limit=account_mdd_limit)
    evidence = repository.session.get(FujimotoEvidence, replay_id)
    data = input_from_json(evidence.payload["inputs"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_date = max(data.evidence) if data.evidence else now.date()
    snapshot = json.dumps(evidence.payload["inputs"], sort_keys=True, allow_nan=False).encode()
    runs = []
    for mode in Mode:
        measured = result.metrics[mode.value]
        code_hash, params_hash = strategy_fingerprint(mode.strategy_id)
        run = ValidationRun(id=str(uuid.uuid4()), strategy_id=mode.strategy_id, run_date=run_date,
                            status="INSUFFICIENT" if result.status == "insufficient" else "COMPLETE",
                            code_hash=code_hash, params_hash=params_hash, data_hash=hashlib.sha256(snapshot).hexdigest(),
                            input_snapshot=zlib.compress(snapshot), created_at=now,
                            manifest={"format": "fujimoto-stateful-json-v1", "fujimoto_replay_id": replay_id,
                                      "account_key": evidence.account_key, "combined_status": result.status,
                                      "combined_fingerprint": result.fingerprint, "mc_seed": 42, "mc_simulations": 1000,
                                      "replay_code_hash": evidence.payload["report"]["baseline"]["code_hash"]})
        common = dict(validation_run_id=run.id, strategy_id=mode.strategy_id, run_date=run_date)
        ratio = measured["positive_neighbor_ratio"]
        grade = "A" if ratio >= .8 else "B" if ratio >= .6 else "C" if ratio >= .4 else "D" if ratio >= .2 else "F"
        combinations = measured["neighbor_count"]
        plateau = ParameterPlateauResults(**common, total_combinations=combinations, positive_combinations=round(ratio * combinations),
                                          positive_ratio=ratio, grade=grade, best_params_json=json.dumps({"first_rsi": 40, "maximum_return_20": .2, "minimum_dividend_growth": 0}))
        limit = .15 if mode == Mode.SAFE else .25
        mc = MonteCarloSequenceResults(**common, strategy_group=f"fujimoto_{mode.value}", n_simulations=1000,
                                      mdd_p95=measured.get("mc_p95", 1.), mdd_limit=limit,
                                      mc_within_limit=measured.get("mc_p95", 1.) <= limit)
        folds = measured["wfa"]
        wfa = WalkForwardResults(**common, n_folds=len(folds),
                                sharpe_mean=float(np.mean([f["sharpe"] for f in folds])) if folds else 0,
                                sharpe_std=float(np.std([f["sharpe"] for f in folds])) if folds else 0,
                                negative_folds=sum(f["sharpe"] < 0 for f in folds),
                                mean_g2p=float(np.mean([f["g2p_ratio"] for f in folds])) if folds else 0,
                                passed=len(folds) == 5 and "insufficient_wfa" not in result.reasons and mode.value + "_wfa" not in result.reasons,
                                fail_reasons_json=json.dumps(result.reasons))
        run.metrics = {**validation_metrics(plateau, mc, wfa), "replay_equivalent_passed": result.status == "passed",
                       "replay_trading_days": len(measured["daily_returns"]),
                       "replay_completed_trades": measured["exit_samples"], "is_cagr": measured["return"],
                       "mock_sharpe": measured["sharpe"], "fujimoto_combined_status": result.status}
        repository.session.add_all([run, plateau, mc, wfa])
        repository.session.flush()
        for index, fold in enumerate(folds):
            repository.session.add(WalkForwardFoldResults(wfa_run_id=wfa.id, strategy_id=mode.strategy_id,
                fold_idx=index, is_start=date.fromisoformat(fold["is_start"]), is_end=date.fromisoformat(fold["is_end"]),
                oos_start=date.fromisoformat(fold["oos_start"]), oos_end=date.fromisoformat(fold["oos_end"]),
                is_sharpe=fold["is"]["sharpe"], oos_sharpe=fold["oos"]["sharpe"],
                is_g2p=fold["is"]["g2p"], oos_g2p=fold["oos"]["g2p"], g2p_ratio=fold["g2p_ratio"],
                best_params_json=json.dumps({"first_rsi": 40})))
        runs.append(run)
    repository.session.flush()
    return result, runs
