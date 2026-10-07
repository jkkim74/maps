"""Stateful replay execution and evidence-derived validation."""
from datetime import date, datetime

import pytest

from maps.common.exceptions import BacktestError
from maps.fujimoto.domain import Mode, RuleEvidence


def test_catalog_stateful_contract_rejects_boolean_replay():
    from maps.strategy.catalog import STRATEGY_CLASSES, describe_strategy
    for mode in Mode:
        assert describe_strategy(mode.strategy_id) is not None
        with pytest.raises(BacktestError, match="stateful"):
            STRATEGY_CLASSES[mode.strategy_id]().generate_signals(None, {})


def inputs():
    from maps.fujimoto.replay import ReplayInput, SessionBar
    return ReplayInput(budget=1000000, evidence={
        date(2026, 1, 5): {"005930": RuleEvidence(date(2026, 1, 5), 1000, True,
            financial_status="maintained", daily_rsi=40, weekly_rsi=50)},
        date(2026, 1, 6): {"005930": RuleEvidence(date(2026, 1, 6), 1000)},
        date(2026, 1, 7): {"005930": RuleEvidence(date(2026, 1, 7), 1100,
            financial_status="deteriorated")},
    }, bars={
        date(2026, 1, 5): {"005930": SessionBar(1000, 1010, 990, 1000, 10000)},
        date(2026, 1, 6): {"005930": SessionBar(1000, 1010, 990, 1000, 20)},
        date(2026, 1, 7): {"005930": SessionBar(1100, 1110, 1090, 1100, 10000)},
    }, participation=.1, slippage=0, fee_rate=.001, tax_rate=.002)


def test_replay_shared_transition_volume_partial_costs_and_reproducibility():
    from maps.fujimoto.replay import replay
    data = inputs()
    result = replay(data)
    assert result == replay(data)
    assert len(result.fills) == 4  # two buys plus two same-session financial exits
    assert all(fill["quantity"] == 1 for fill in result.fills[:2])  # shared volume 2
    assert result.final_quantities == {"safe:005930": 0, "original:005930": 0}
    assert result.final_cash["safe"] == pytest.approx(500095.7)
    assert result.data_hash and result.code_hash and result.params_hash


def test_next_session_limit_cap_halt_missing_data_and_stress():
    from dataclasses import replace
    from maps.fujimoto.replay import replay, SessionBar, stress_losses
    data = inputs()
    for bar in (SessionBar(1050, 1060, 1040, 1050, 10000),
                SessionBar(1000, 1010, 990, 1000, 10000, halted=True), None):
        modified = {day: dict(rows) for day, rows in data.bars.items()}
        modified[date(2026, 1, 6)] = {} if bar is None else {"005930": bar}
        assert not replay(replace(data, bars=modified)).fills
    assert stress_losses(.675)["simultaneous_68pct"] == pytest.approx(.459)
    assert stress_losses(.675)["total_loss"] == .675


def test_validation_missing_tape_samples_and_stored_runner_is_not_measurement(db):
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.validation import run_research, validation
    repo = FujimotoRepository(db)
    report = run_research(inputs())
    stored = repo.store_replay(inputs(), report)
    result = validation(repo, stored.id)
    assert result.status == "insufficient"
    assert {"safe", "original", "combined"} <= set(result.metrics)
    assert "missing_recorded_tape" in result.reasons
    assert "insufficient_trade_samples" in result.reasons
    assert "insufficient_market_regimes" in result.reasons
    assert report["cost_double"]["final_cash"]["safe"] < report["baseline"]["final_cash"]["safe"]


def test_recorded_tape_duration_costs_and_gap_reset():
    from maps.fujimoto.replay import quote_signal
    since, last = None, None
    for second in range(31):
        quote = {"exchange_at": f"2026-01-06T00:00:{second:02}",
                 "received_at": f"2026-01-06T00:00:{second:02}", "connected": True,
                 "bid": 1100, "ask": 1101, "bid_size": 10, "total_bid": 30, "total_ask": 10}
        since, last, price, trigger = quote_signal(since, last, quote, 1000, 1, .003)
    assert trigger and price == 1100
    quote["exchange_at"] = quote["received_at"] = "2026-01-06T00:00:35"
    since, last, _, trigger = quote_signal(since, last, quote, 1000, 1, .003)
    assert not trigger
    quote["total_ask"] = 0
    assert quote_signal(since, last, quote, 1000, 1, .003)[0] is None


def test_replay_does_not_let_unqualified_watch_candidates_consume_five_slots():
    from dataclasses import replace
    from maps.fujimoto.replay import replay
    data = inputs()
    rows = dict(data.evidence[date(2026, 1, 5)])
    for i in range(10):
        rows[f"00000{i}"] = RuleEvidence(date(2026, 1, 5), 1000)
    changed = dict(data.evidence, **{})
    changed[date(2026, 1, 5)] = rows
    assert len(replay(replace(data, evidence=changed)).fills) == 4


def test_validation_result_metrics_cannot_be_forged(db):
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.validation import run_research, validation
    from maps.common.exceptions import DataQualityError
    repo = FujimotoRepository(db)
    report = run_research(inputs())
    report["baseline"]["final_cash"]["safe"] = 999999999
    stored = repo.store_replay(inputs(), report)
    with pytest.raises(DataQualityError, match="mismatch"):
        validation(repo, stored.id)


def test_measured_validation_persists_compatible_component_links(db):
    import hashlib
    import zlib
    from maps.common.models import ValidationRun, ParameterPlateauResults, WalkForwardResults, MonteCarloSequenceResults
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.validation import run_research, persist_validation
    from maps.promotion.evidence import strategy_fingerprint
    repo = FujimotoRepository(db)
    stored = repo.store_replay(inputs(), run_research(inputs()), account_key="mock:one")
    result, runs = persist_validation(repo, stored.id)
    assert result.status == "insufficient" and len(runs) == 2
    for run in runs:
        assert run.status == "INSUFFICIENT"
        assert (run.code_hash, run.params_hash) == strategy_fingerprint(run.strategy_id)
        assert hashlib.sha256(zlib.decompress(run.input_snapshot)).hexdigest() == run.data_hash
        assert run.manifest["fujimoto_replay_id"] == stored.id
        for model in (ParameterPlateauResults, WalkForwardResults, MonteCarloSequenceResults):
            assert db.query(model).filter_by(validation_run_id=run.id).count() == 1


def test_closed_cycle_can_begin_new_independent_cycle_on_later_session():
    from dataclasses import replace
    from maps.fujimoto.replay import replay, SessionBar
    data = inputs()
    evidence = dict(data.evidence)
    bars = dict(data.bars)
    evidence[date(2026, 1, 8)] = {"005930": RuleEvidence(date(2026, 1, 8), 1000, True,
        financial_status="maintained", daily_rsi=40, weekly_rsi=50)}
    bars[date(2026, 1, 8)] = {"005930": SessionBar(1000, 1010, 990, 1000, 10000)}
    bars[date(2026, 1, 9)] = {"005930": SessionBar(1000, 1010, 990, 1000, 10000)}
    result = replay(replace(data, evidence=evidence, bars=bars))
    buys = [fill for fill in result.fills if fill["action"] == "buy"]
    assert len(buys) == 4
    assert sum(fill["completed_cycle"] for fill in result.fills) == 2
    assert buys[0]["cycle_number"] != buys[-1]["cycle_number"]


def test_daily_low_without_recorded_intraday_quote_never_fabricates_stop_fill():
    from dataclasses import replace
    from maps.fujimoto.replay import replay, SessionBar
    data = inputs()
    bars = dict(data.bars)
    evidence = dict(data.evidence)
    evidence[date(2026, 1, 7)] = {"005930": RuleEvidence(date(2026, 1, 7), 800)}
    bars[date(2026, 1, 7)] = {"005930": SessionBar(800, 810, 700, 800, 10000)}
    result = replay(replace(data, evidence=evidence, bars=bars))
    assert all(fill["action"] == "buy" for fill in result.fills)


def test_rule_threshold_neighborhood_reruns_shared_evaluation():
    from maps.fujimoto.domain import CycleState, evaluate
    evidence = RuleEvidence(date(2026, 1, 5), 1000, True, financial_status="maintained", daily_rsi=42, weekly_rsi=50)
    assert evaluate(Mode.SAFE, evidence, CycleState()).action == "hold"
    assert evaluate(Mode.SAFE, evidence, CycleState(), first_rsi_threshold=42).action == "buy"


def test_raw_screening_neighborhood_changes_real_quality_and_surge_gates():
    import pandas as pd
    from maps.fujimoto.replay import screening_evidence
    from maps.data.dart_financials import available_date
    from maps.market.trading_rules import previous_trading_day
    day = date(2026, 1, 5)
    annual = []
    for year, amount in ((2022, 100), (2023, 110), (2024, 120)):
        published = date(year + 1, 3, 20)
        observed = datetime(year + 1, 3, 20)
        annual.append(dict(ticker="005930", period_end=date(year, 12, 31).isoformat(), receipt=str(year),
            publication_date=published.isoformat(), first_observed_at=observed.isoformat(),
            available_date=available_date(published, observed).isoformat(), basis="CFS", currency="KRW", share_basis="verified",
            revenue=str(amount), operating_profit=str(amount / 10), dividend_per_share="1"))
    dates = [day]
    for _ in range(20):
        dates.append(previous_trading_day(dates[-1]))
    prices = [dict(date=d.isoformat(), open=100 + i * 1.05, high=101 + i * 1.05,
                   low=99 + i * 1.05, close=100 + i * 1.05, volume=1000, turnover=1000000)
              for i, d in enumerate(reversed(dates))]
    raw = {"annual_records": annual, "financial_records": [], "valuations": [dict(ticker="005930", ref_date=day.isoformat(), available_at="2026-01-05T07:00:00", per=10)],
           "sectors": dict(ref_date=day.isoformat(), available_at="2026-01-05T07:00:00", memberships=[["005930", "sector"]]),
           "prices": prices, "eligible": True}
    assert not screening_evidence(raw, "005930", day, maximum_return_20=.2).selection_passed
    assert screening_evidence(raw, "005930", day, maximum_return_20=.22).selection_passed
    assert not screening_evidence(raw, "005930", day, maximum_return_20=.22, minimum_dividend_growth=.01).selection_passed


def test_recorded_quotes_share_each_event_liquidity_and_respect_exchange_halts():
    from dataclasses import replace
    from maps.fujimoto.replay import replay, SessionBar
    data = inputs()
    bars = dict(data.bars)
    bars[date(2026, 1, 6)] = {"005930": SessionBar(1000, 1010, 990, 1000, 40)}
    bars[date(2026, 1, 7)] = {"005930": SessionBar(800, 810, 790, 800, 10000)}
    evidence = dict(data.evidence)
    evidence[date(2026, 1, 7)] = {"005930": RuleEvidence(date(2026, 1, 7), 800)}
    quotes = tuple(dict(ticker="005930", exchange_at=f"2026-01-07T00:00:0{i}",
        received_at=f"2026-01-07T00:00:0{i}", connected=True, bid=800, ask=801,
        bid_size=10, total_bid=30, total_ask=10) for i in (1, 2))
    changed = replace(data, bars=bars, evidence=evidence, tape=quotes)
    result = replay(changed)
    assert result.final_quantities["safe:005930"] == 0
    assert result.final_quantities["original:005930"] == 2
    bars[date(2026, 1, 7)] = {"005930": SessionBar(800, 810, 790, 800, 10000, halted=True)}
    assert all(f["action"] == "buy" for f in replay(replace(changed, bars=bars)).fills)


def test_recorded_candidate_ranking_controls_five_position_allocation():
    from dataclasses import replace
    from maps.fujimoto.replay import replay, SessionBar
    data = inputs()
    rows = dict(data.evidence[date(2026, 1, 5)])
    bars = {day: dict(entries) for day, entries in data.bars.items()}
    for ticker in ("000001", "000002", "000003", "000004", "000005"):
        rows[ticker] = rows["005930"]
        bars[date(2026, 1, 6)][ticker] = SessionBar(1000, 1010, 990, 1000, 10000)
    evidence = dict(data.evidence)
    evidence[date(2026, 1, 5)] = rows
    changed = replace(data, evidence=evidence, bars=bars,
        candidate_order={date(2026, 1, 5): tuple(rows)})
    assert any(f["ticker"] == "005930" for f in replay(changed).fills)


def test_wfa_windows_preserve_actual_quote_timestamps_without_ref_date():
    from dataclasses import replace
    from maps.fujimoto.replay import ReplayInput, SessionBar, next_session, replay
    from maps.fujimoto.validation import run_research
    days = [date(2026, 1, 5)]
    for _ in range(59):
        days.append(next_session(days[-1]))
    evidence = {d: {"005930": RuleEvidence(d, 1000)} for d in days}
    bars = {d: {"005930": SessionBar(1000, 1010, 990, 1000, 1000)} for d in days}
    quotes = tuple(dict(ticker="005930", received_at=f"{d.isoformat()}T00:00:00", exchange_at=f"{d.isoformat()}T00:00:00") for d in days)
    data = ReplayInput(1000000, evidence, bars, tape=quotes)
    report = run_research(data)
    assert len(report["wfa"]) == 5
    window = set(days[10:20])
    expected = replace(data, evidence={d: evidence[d] for d in window}, bars={d: bars[d] for d in window},
                       tape=tuple(q for q in quotes if date.fromisoformat(q["received_at"][:10]) in window))
    assert report["wfa"][0]["oos"]["data_hash"] == replay(expected).data_hash
