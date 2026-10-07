"""Regressions for the consolidated final review, using actual shared entry paths."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from maps.fujimoto.domain import RuleEvidence, Mode
from maps.fujimoto.replay import ReplayInput, SessionBar, replay


DAY = date(2026, 1, 5)


def rule(day=DAY):
    return RuleEvidence(day, 1000, True, financial_status="maintained", daily_rsi=40, weekly_rsi=50)


def quotes(day, bid=1100, ticker="A"):
    return tuple(dict(ticker=ticker, exchange_at=f"{day}T00:00:{s:02}",
        received_at=f"{day}T00:00:{s:02}", connected=True, bid=bid, ask=bid + 1,
        bid_size=100000, total_bid=300000, total_ask=100000) for s in range(31))


def sample(*, opening=1000, tape=(), tickers=("A",), budget=4000000):
    return ReplayInput(budget, {DAY: {t: rule() for t in tickers}},
        {DAY: {t: SessionBar(1000, 1000, 1000, 1000, 100000) for t in tickers},
         DAY + timedelta(days=1): {t: SessionBar(opening, max(opening, 1100), 990, 1000, 100000) for t in tickers}},
        tape=tape, candidate_order={DAY: tickers})


def test_normal_constraints_are_diagnostics_and_window_intentions_expire():
    data = sample(tickers=tuple("ABCDEF"), opening=1200)
    data = replace(data, bars={DAY: data.bars[DAY], DAY + timedelta(days=1):
        {t: SessionBar(1200, 1200, 1200, 1200, 100000) for t in "ABCDEF"}})
    limited = replay(data)
    assert not limited.reasons
    assert "mode_position_limit" not in limited.reasons
    assert "mode_position_limit" in limited.diagnostics
    assert all(f["action"] == "buy" for f in limited.fills)
    tiny = replay(sample(budget=1))
    assert "budget_or_risk_wait" not in tiny.reasons
    assert "budget_or_risk_wait" in tiny.diagnostics
    assert not tiny.reasons
    final = replay(replace(sample(), bars={DAY: sample().bars[DAY]}))
    assert not final.fills and not final.reasons
    assert "window_end_intentions_expired" in final.diagnostics


def test_daily_later_touch_cannot_sell_earlier_intraday():
    result = replay(sample(opening=1100, tape=quotes("2026-01-06")), with_orderbook=True)
    assert len(result.fills) == 2
    assert all(f["action"] == "buy" for f in result.fills)
    assert all(f["execution_phase"] == "after_intraday" for f in result.fills)
    assert "unknown_intraday_fill_order" in result.reasons


def test_later_touch_and_recovery_never_claims_an_unobserved_stop_was_avoided():
    data = sample(opening=1100, tape=quotes("2026-01-06", bid=800))
    bars = {**data.bars, DAY + timedelta(days=1): {"A": SessionBar(1100, 1100, 750, 1000, 100000)}}
    result = replay(replace(data, bars=bars))
    assert "unknown_intraday_fill_order" in result.reasons
    assert all(f["action"] == "buy" for f in result.fills)


@pytest.mark.parametrize("book", [False, True])
def test_unrelated_or_sparse_tape_cannot_prove_held_session(book):
    for tape in (quotes("2026-01-06", ticker="OTHER"), quotes("2026-01-06")):
        result = replay(sample(tape=tape), with_orderbook=book)
        assert "missing_held_session_tape" in result.reasons


def test_empty_unfilled_cycles_release_slots_next_session():
    data = sample(tickers=tuple("ABCDE"), opening=1200)
    bars = {d: dict(rows) for d, rows in data.bars.items()}
    bars[DAY + timedelta(days=1)] = {t: SessionBar(1200, 1200, 1200, 1200, 100000) for t in "ABCDE"}
    bars[DAY + timedelta(days=2)] = {"F": SessionBar(1000, 1000, 1000, 1000, 100000)}
    evidence = {**data.evidence, DAY + timedelta(days=1): {"F": rule(DAY + timedelta(days=1))}}
    result = replay(replace(data, bars=bars, evidence=evidence))
    assert {f["ticker"] for f in result.fills} == {"F"}


def test_configurable_bounds_fit_full_market_and_sixty_session_tape():
    from maps.common.settings import MapsSettings
    # Sizing metadata, not invented historical evidence: 3,000 names, two screens/day;
    # 20 subscribed instruments, one quote/second, 6h20m/day, 60 sessions.
    candidates, tape = 3000 * 2 * 60, 20 * 22800 * 60
    settings = MapsSettings(_env_file=None, maps_fujimoto_candidate_rows=candidates,
                            maps_fujimoto_tape_rows=tape)
    assert settings.maps_fujimoto_candidate_rows >= candidates
    assert settings.maps_fujimoto_tape_rows >= tape


@pytest.mark.parametrize("multiplier,bid", [(1, 1003), (2, 1006)])
def test_book_exit_never_realizes_loss_due_to_omitted_slippage(multiplier, bid):
    result = replay(sample(tape=quotes("2026-01-06", bid=bid)),
                    with_orderbook=True, cost_multiplier=multiplier)
    assert not [f for f in result.fills if f["reason"] == "orderbook_take_profit"]


@pytest.mark.parametrize("missing_top", [False, True])
@pytest.mark.parametrize("protected", [False, True])
def test_runtime_ranked_slots_do_not_depend_on_quote_arrival(db, monkeypatch, missing_top, protected):
    from maps.common.settings import MapsSettings
    from maps.execution.safety import account_key
    from maps.fujimoto.repository import FujimotoRepository, json_data
    from maps.fujimoto.service import FujimotoService
    from maps.limit_up.feed import FeedQuote
    settings = MapsSettings(_env_file=None)
    now = datetime(2026, 1, 6, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("maps.fujimoto.service.utcnow", lambda: now.replace(tzinfo=None))
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status", lambda *a: "maintained")
    repo, key = FujimotoRepository(db), account_key(settings)
    for mode in Mode:
        repo.configure(key, 7, mode, 500000)
    tickers = tuple(f"{i:06}" for i in range(1, 7))
    observed = now - timedelta(days=1)
    repo.record_evidence("screen", "*", observed, observed, {"ref_date": str(DAY), "ranked": tickers})
    for ticker in tickers:
        repo.record_evidence("candidate", ticker, observed, observed, {"rule": json_data(rule())})
    if protected:
        from maps.fujimoto.domain import CycleState, evaluate
        for config in repo.configurations(key):
            cycle = repo.create_cycle(config.id, "PENDING")
            source = repo.record_evidence("candidate", "PENDING", observed, observed, {"rule": json_data(rule())})
            order = repo.reserve_order(cycle.id, evaluate(Mode(config.mode), rule(), CycleState()), source.id, 1, 1000)
            order.status = "UNKNOWN"
    db.commit()
    service = FujimotoService(db, object(), settings=settings)
    service._control(key, {"execution_mode": "paper", "entries_enabled": True})
    submitted = []
    monkeypatch.setattr(service, "_submit_decision", lambda c, *args: submitted.append(c.ticker))
    for ticker in reversed(tickers):
        stamp = now - timedelta(seconds=10) if missing_top and ticker == tickers[0] else now
        service.on_quote(FeedQuote(ticker, 1001, 100, 1000, 300, 1., 100, 300, stamp, stamp), now=now)
    end = 4 if protected else 5
    expected = set(tickers[1:end] if missing_top else tickers[:end])
    assert {c.ticker for c in repo.cycles(key)} == expected | ({"PENDING"} if protected else set())
    assert set(submitted) == expected


def test_full_held_interval_requires_actual_subscription_and_continuity():
    start = datetime(2026, 1, 6)
    tape = tuple({**quotes("2026-01-06", bid=1000)[0],
        "exchange_at": (start + timedelta(seconds=s)).isoformat(),
        "received_at": (start + timedelta(seconds=s)).isoformat()}
        for s in range(0, 22801, 3))
    recording = ({"kind": "subscriptions", "at": start.isoformat(), "tickers": ["A"]},)
    complete = sample(tape=tape)
    assert "missing_held_session_tape" in replay(complete).reasons
    complete = replace(complete, recording=recording)
    assert not replay(complete).reasons
    for changed in (tape[:-2], tape[:100] + tape[102:],
                    tape[:100] + ({**tape[100], "gap": True},) + tape[101:]):
        assert "missing_held_session_tape" in replay(replace(complete, tape=changed)).reasons
    unsubscribed = recording + ({"kind": "subscriptions", "at": "2026-01-06T01:00:00", "tickers": []},)
    assert "missing_held_session_tape" in replay(replace(complete, recording=unsubscribed)).reasons


def test_early_stop_still_requires_its_actual_recorded_subscription():
    data = sample(tape=quotes("2026-01-06", bid=800))
    result = replay(data)
    assert any(f["reason"] == "price_stop" for f in result.fills)
    assert "missing_held_session_tape" in result.reasons


def test_observed_outage_is_measured_but_an_unbounded_disconnect_is_missing():
    recording = ({"kind": "subscriptions", "at": "2026-01-06T00:00:00", "tickers": ["A"]},
        {"kind": "outage", "start": "2026-01-06T00:00:30", "end": "2026-01-06T06:20:00", "tickers": ["A"]})
    data = replace(sample(tape=quotes("2026-01-06", bid=1000)), recording=recording)
    result = replay(data)
    assert not result.reasons
    assert "observed_feed_outage" in result.diagnostics
    assert "missing_held_session_tape" in replay(replace(data, recording=recording[:1])).reasons


def test_outage_recording_and_export_preserve_actual_interval(db, monkeypatch):
    from maps.fujimoto.feed import FujimotoFeed
    from scripts.fujimoto_research import export_inputs
    start = datetime(2026, 1, 6, tzinfo=timezone.utc)
    class Clock(datetime):
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr("maps.fujimoto.feed.datetime", Clock)
    feed = FujimotoFeed(db, "account")
    feed.record_subscriptions(["A"], now=start)
    feed.reset("disconnect")
    Clock.current += timedelta(minutes=1)
    feed.reset("reconnect")
    exported = export_inputs(db, "account", 1000000)
    assert [r["kind"] for r in exported["recording"]] == ["subscriptions", "outage"]
    assert exported["recording"][1]["start"] == start.isoformat()
    assert exported["recording"][1]["end"] == Clock.current.isoformat()
    assert exported["recording"][1]["tickers"] == ["A"]
    assert all(r["evidence_id"] for r in exported["recording"])
    # A new process cannot invent the interval since a prior disconnected process.
    FujimotoFeed(db, "account").reset("reconnect")
    assert len(export_inputs(db, "account", 1000000)["recording"]) == 2


@pytest.mark.parametrize("multiplier", [1, 2])
def test_shared_runtime_replay_net_sale_at_strict_break_even(db, multiplier):
    from maps.fujimoto.feed import FujimotoFeed
    from maps.fujimoto.replay import quote_signal, expected_net_sale
    from maps.limit_up.feed import FeedQuote
    fee_tax, slip = .00215 * multiplier, .001 * multiplier
    net = expected_net_sale(1004, 10, fee_tax, slip)
    now = datetime(2026, 1, 6, tzinfo=timezone.utc)
    for basis, expected in ((net, False), (net + 1, False), (net - 1, True)):
        feed = FujimotoFeed(db, "account")
        since, last = None, None
        for second in range(31):
            at = now + timedelta(seconds=second)
            quote = dict(ticker="A", exchange_at=at.isoformat(), received_at=at.isoformat(),
                connected=True, bid=1004, ask=1005, bid_size=100, total_bid=300, total_ask=100)
            since, last, _, trigger = quote_signal(since, last, quote, basis, 10, fee_tax, slippage=slip)
            _, runtime = feed.on_quote(FeedQuote("A", 1005, 100, 1004, 300, second, 100, 300, at, at),
                now=at, cycle_id=1, cost_basis=basis, quantity=10, sale_cost_rate=fee_tax,
                slippage=slip, persist=False)
        assert runtime == trigger == expected


def test_validation_keeps_diagnostics_out_of_insufficiency_without_hiding_real_gaps(db):
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.validation import run_research, validation
    data = sample(tickers=tuple("ABCDEF"))
    repo = FujimotoRepository(db)
    result = validation(repo, repo.store_replay(data, run_research(data)).id, with_orderbook=False)
    assert "mode_position_limit" in result.metrics["execution_diagnostics"]
    assert "mode_position_limit" not in result.reasons
    assert "missing_held_session_tape" in result.reasons
    assert "insufficient_trade_samples" in result.reasons


def test_halted_held_sessions_keep_marked_loss_without_inventing_execution():
    data = sample()
    bars = {**data.bars, date(2026, 1, 7): {"A": SessionBar(800, 800, 800, 800, 0, halted=True)}}
    result = replay(replace(data, bars=bars))
    assert "exchange_halt" in result.diagnostics
    assert "exchange_halt" not in result.reasons
    assert result.equity[-1]["combined"] < result.equity[-2]["combined"]
    assert all(f["action"] == "buy" for f in result.fills)


def test_missing_whole_held_session_is_not_skipped():
    data = sample()
    result = replay(replace(data, bars={**data.bars,
        date(2026, 1, 8): {"A": SessionBar(800, 800, 800, 800, 100000)}}))
    assert "2026-01-07" in {row["date"] for row in result.equity}
    assert {"missing_held_session_tape", "stale_valuation"} <= set(result.reasons)


def test_neighbor_only_holdings_and_wfa_do_not_escape_coverage():
    from maps.fujimoto.replay import next_session
    from maps.fujimoto.validation import run_research
    days = [DAY]
    for _ in range(59):
        days.append(next_session(days[-1]))
    data = ReplayInput(4000000,
        {d: {"A": replace(rule(d), daily_rsi=42)} for d in days},
        {d: {"A": SessionBar(1000, 1000, 1000, 1000, 100000)} for d in days},
        tape=quotes("2026-01-06", ticker="UNRELATED"))
    report = run_research(data)
    assert not report["baseline"]["fills"]
    assert "missing_held_session_tape" in report["neighbors"][-1]["reasons"]
    # Lower the measured threshold so every independently reset WFA run owns shares.
    data = replace(data, evidence={d: {"A": rule(d)} for d in days})
    report = run_research(data)
    assert all("missing_held_session_tape" in fold[label]["reasons"]
               for fold in report["wfa"] for label in ("is", "oos"))
