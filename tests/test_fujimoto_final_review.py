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
        bid_size=100000, total_bid=300000, total_ask=100000) for s in range(1, 32))


def sample(*, opening=1000, tape=(), tickers=("A",), budget=4000000):
    entry = tuple({**quotes("2026-01-06", bid=opening - 1, ticker=t)[0],
                   "ask_size": 100000, "exchange_at": "2026-01-06T00:00:00",
                   "received_at": "2026-01-06T00:00:00"} for t in tickers)
    return ReplayInput(budget, {DAY: {t: rule() for t in tickers}},
        {DAY: {t: SessionBar(1000, 1000, 1000, 1000, 100000) for t in tickers},
         DAY + timedelta(days=1): {t: SessionBar(opening, max(opening, 1100), 990, 1000, 100000) for t in tickers}},
        tape=entry + tape, candidate_order={DAY: tickers},
        recording=({"kind": "subscriptions", "at": "2026-01-05T00:00:00", "tickers": tickers},))


def test_normal_constraints_are_diagnostics_and_window_intentions_expire():
    data = sample(tickers=tuple("ABCDEF"), opening=1200)
    data = replace(data, tape=(), bars={DAY: data.bars[DAY], DAY + timedelta(days=1):
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
    assert not result.fills  # Daily low alone can no longer create any ownership.
    assert "unknown_intraday_fill_order" in result.reasons


def test_later_touch_and_recovery_never_claims_an_unobserved_stop_was_avoided():
    data = sample(opening=1100, tape=quotes("2026-01-06", bid=1100))
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
    entry = {**quotes("2026-01-07", bid=999, ticker="F")[0], "ask_size": 100000}
    recording = data.recording + ({"kind": "subscriptions", "at": "2026-01-07T00:00:00", "tickers": ["F"]},)
    result = replay(replace(data, bars=bars, evidence=evidence, tape=(entry,), recording=recording))
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
    assert "missing_submission_tape" in replay(replace(complete, recording=())).reasons
    complete = replace(complete, recording=recording)
    assert not replay(complete).reasons
    for changed in (tape[:-2], tape[:100] + tape[102:],
                    tape[:100] + ({**tape[100], "gap": True},) + tape[101:]):
        assert "missing_held_session_tape" in replay(replace(complete, tape=complete.tape[:1] + changed)).reasons
    unsubscribed = recording + ({"kind": "subscriptions", "at": "2026-01-06T01:00:00", "tickers": []},)
    assert "missing_held_session_tape" in replay(replace(complete, recording=unsubscribed)).reasons


def test_early_stop_still_requires_its_actual_recorded_subscription():
    data = sample(tape=quotes("2026-01-06", bid=800))
    assert any(f["reason"] == "price_stop" for f in replay(data).fills)
    result = replay(replace(data, recording=()))
    assert not result.fills
    assert "missing_submission_tape" in result.reasons


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
        tape=tuple({**quotes(str(d), bid=999)[0], "ask_size": 100000} for d in days),
        recording=({"kind": "subscriptions", "at": "2026-01-05T00:00:00", "tickers": ["A"]},))
    report = run_research(data)
    assert not report["baseline"]["fills"]
    assert "missing_held_session_tape" in report["neighbors"][-1]["reasons"]
    # Lower the measured threshold so every independently reset WFA run owns shares.
    data = replace(data, evidence={d: {"A": rule(d)} for d in days})
    report = run_research(data)
    assert all("missing_held_session_tape" in fold[label]["reasons"]
               for fold in report["wfa"] for label in ("is", "oos"))


@pytest.mark.parametrize("availability", ["outage", "missing_subscription", "removed", "no_quote"])
def test_recording_unavailability_cannot_create_daily_open_ownership(availability):
    recording = ({"kind": "subscriptions", "at": "2026-01-06T00:00:00", "tickers": ["A"]},)
    tape = tuple({**q, "ask_size": 100000} for q in quotes("2026-01-06", bid=999))
    if availability == "outage":
        recording += ({"kind": "outage", "start": "2026-01-06T00:00:00",
                       "end": "2026-01-06T06:20:00", "tickers": ["A"]},)
        tape = quotes("2026-01-05", ticker="OTHER")
    elif availability == "missing_subscription":
        recording = ()
    elif availability == "removed":
        recording += ({"kind": "subscriptions", "at": "2026-01-06T00:00:00", "tickers": []},)
    else:
        tape = ()
    result = replay(replace(sample(), tape=tape, recording=recording))
    assert not result.fills
    assert not any(result.final_quantities.values())


def test_subscription_refresh_deduplicates_per_connection_but_keeps_reconnect(db):
    from maps.fujimoto.feed import FujimotoFeed
    feed = FujimotoFeed(db, "account")
    now = datetime.now(timezone.utc)
    for i in range(20):
        feed.record_subscriptions(["A", "B"] if i % 2 else ["B", "A"], now=now + timedelta(seconds=i))
    records = feed.repo.evidence_as_of("feed_recording", "*", datetime.max, account_key="account")
    assert len(records) == 1
    feed.reset("reconnect")
    feed.record_subscriptions(["A", "B"], now=now + timedelta(seconds=21))
    records = feed.repo.evidence_as_of("feed_recording", "*", datetime.max, account_key="account")
    assert len(records) == 2


def test_recording_lookup_comparisons_are_bounded_not_quotes_times_history(monkeypatch):
    import maps.fujimoto.replay as replay_module
    class CountingDateTime(datetime):
        comparisons = 0

        def __le__(self, other):
            type(self).comparisons += 1
            return super().__le__(other)

        def __lt__(self, other):
            type(self).comparisons += 1
            return super().__lt__(other)

        def __gt__(self, other):
            type(self).comparisons += 1
            return super().__gt__(other)

    monkeypatch.setattr(replay_module, "datetime", CountingDateTime)
    start = datetime(2026, 1, 6)
    recording = tuple({"kind": "subscriptions", "at": (start + timedelta(days=i)).isoformat(),
                       "tickers": ["A"]} for i in range(2001))
    complete, outage = replay_module.held_tape_coverage("A", start, start + timedelta(seconds=30),
                                                       list(quotes("2026-01-06")), recording)
    assert complete and not outage
    assert CountingDateTime.comparisons < 10000


@pytest.mark.parametrize("renewed", [False, True])
@pytest.mark.parametrize("liquidity_recorded", [False, True])
def test_reconnect_acquisition_uses_actual_quote_time_and_recorded_ask(renewed, liquidity_recorded):
    recording = ({"kind": "subscriptions", "at": "2026-01-05T00:00:00", "tickers": ["A"]},
        {"kind": "outage", "start": "2026-01-06T00:00:00", "end": "2026-01-06T01:00:00", "tickers": ["A"]})
    if renewed:
        recording += ({"kind": "subscriptions", "at": "2026-01-06T01:00:01", "tickers": ["A"]},)
    entry = {**quotes("2026-01-06", bid=999)[0], "exchange_at": "2026-01-06T01:00:05",
             "received_at": "2026-01-06T01:00:05"}
    if liquidity_recorded:
        entry["ask_size"] = 100000
    stop = {**quotes("2026-01-06", bid=800)[0], "exchange_at": "2026-01-06T01:00:06",
            "received_at": "2026-01-06T01:00:06"}
    data = replace(sample(), tape=quotes("2026-01-06") + (entry, stop), recording=recording)
    result = replay(data, with_orderbook=True)
    if renewed and liquidity_recorded:
        buys = [f for f in result.fills if f["action"] == "buy"]
        assert len(buys) == 2
        assert all(f["executed_at"] == entry["received_at"] for f in buys)
        stops = [f for f in result.fills if f["reason"] == "price_stop"]
        assert len(stops) == 1 and stops[0]["executed_at"] == stop["received_at"]
        assert not [f for f in result.fills if f["reason"] == "orderbook_take_profit"]
    else:
        assert not result.fills
        assert "missing_submission_tape" in result.reasons


def test_pending_next_session_sell_cannot_liquidate_during_observed_outage():
    data = sample()
    sell_day = date(2026, 1, 7)
    evidence = {**data.evidence, date(2026, 1, 6): {"A": RuleEvidence(date(2026, 1, 6), 1000,
                                                                              financial_status="deteriorated")}}
    bars = {**data.bars, sell_day: {"A": SessionBar(800, 800, 800, 800, 100000)}}
    recording = data.recording + (
        {"kind": "outage", "start": "2026-01-06T00:00:02", "end": "2026-01-06T06:20:00", "tickers": ["A"]},
        {"kind": "outage", "start": "2026-01-07T00:00:00", "end": "2026-01-07T06:20:00", "tickers": ["A"]})
    result = replay(replace(data, evidence=evidence, bars=bars, recording=recording))
    assert len(result.fills) == 2 and all(f["action"] == "buy" for f in result.fills)
    assert all(result.final_quantities.values())
    assert result.equity[-1]["combined"] < result.equity[-2]["combined"]
    assert not result.reasons and "observed_feed_outage" in result.diagnostics


def test_real_feed_payload_exports_ask_liquidity_without_backfill(db):
    from maps.fujimoto.feed import FujimotoFeed
    from maps.limit_up.feed import FeedQuote
    from scripts.fujimoto_research import export_inputs
    at = datetime(2026, 1, 6, tzinfo=timezone.utc)
    feed = FujimotoFeed(db, "account")
    feed.record_subscriptions(["A"], now=at)
    feed.on_quote(FeedQuote("A", 1000, 523, 999, 456, 1., 1000, 3000, at, at), now=at)
    exported = export_inputs(db, "account", 4000000)
    assert exported["tape"][0]["ask_size"] == 523
    result = replay(replace(sample(), tape=tuple(exported["tape"]), recording=tuple(exported["recording"])))
    assert result.fills
    assert sum(f["quantity"] for f in result.fills) <= int(523 * .01)


def test_resting_limit_before_disconnect_cannot_claim_proven_late_acquisition():
    before = {**quotes("2026-01-06", bid=1100)[0], "ask_size": 100000}
    after = {**quotes("2026-01-06", bid=999)[0], "ask_size": 100000,
             "exchange_at": "2026-01-06T01:00:01", "received_at": "2026-01-06T01:00:01"}
    recording = sample().recording + (
        {"kind": "outage", "start": "2026-01-06T00:00:02", "end": "2026-01-06T01:00:00", "tickers": ["A"]},
        {"kind": "subscriptions", "at": "2026-01-06T01:00:00", "tickers": ["A"]})
    result = replay(replace(sample(), tape=(before, after), recording=recording))
    assert len(result.fills) == 2
    assert all(f["executed_at"] == after["received_at"] for f in result.fills)
    assert "unknown_order_execution_timing" in result.reasons


def test_explicit_gap_restarts_book_duration_for_each_owned_mode():
    tape = tuple({**q, "gap": q["received_at"].endswith(":15")} for q in quotes("2026-01-06"))
    result = replay(sample(tape=tape), with_orderbook=True)
    assert not [f for f in result.fills if f["reason"] == "orderbook_take_profit"]
    later = tuple({**tape[-1], "gap": False, "exchange_at": f"2026-01-06T00:00:{s}",
                   "received_at": f"2026-01-06T00:00:{s}"} for s in range(32, 47))
    exits = [f for f in replay(sample(tape=tape + later), with_orderbook=True).fills
             if f["reason"] == "orderbook_take_profit"]
    assert len(exits) == 2
    assert all(f["executed_at"] == "2026-01-06T00:00:46" for f in exits)


def test_partial_buy_never_invents_intraday_cancel_to_enable_stop():
    data = sample()
    entry = {**data.tape[0], "ask_size": 500}
    stop = {**quotes("2026-01-06", bid=800)[0], "ask": 1001, "ask_size": 500}
    result = replay(replace(data, tape=(entry, stop)))
    assert result.fills[0]["buy_stage"] == 0
    assert not [f for f in result.fills if f["action"] == "sell"]
    assert "pending_cancellation_unconfirmed" in result.reasons
    assert "unresolved_order_outcome" in result.reasons
    assert all(o["status"] == "UNKNOWN" and o["remaining_quantity"] > 0 and o["reserved_cash"] > 0
               for o in result.pending_orders)
    assert any(e["status"] == "CANCEL_REQUESTED" for e in result.order_events)
    for order in result.pending_orders:
        assert order["reserved_cash"] == pytest.approx(order["remaining_quantity"] * 1000 * (1 + data.fee_rate))
        fills = [f for f in result.fills if f["mode"] == order["mode"]]
        assert result.final_cash[order["mode"]] == pytest.approx(data.budget / 2 + sum(f["cash_delta"] for f in fills))


def test_multiple_partial_quotes_use_one_cumulative_order_until_full():
    data = sample()
    tape = tuple({**data.tape[0], "ask_size": 1000,
                  "exchange_at": f"2026-01-06T00:00:{s:02}",
                  "received_at": f"2026-01-06T00:00:{s:02}"} for s in range(20))
    result = replay(replace(data, tape=tape))
    for mode, intended in (("safe", 22), ("original", 29)):
        fills = [f for f in result.fills if f["mode"] == mode]
        assert sum(f["quantity"] for f in fills) == intended
        assert all(f["buy_stage"] == 0 for f in fills[:-1])
        assert fills[-1]["buy_stage"] == 1
        assert len({f["order_id"] for f in fills}) == 1
        assert all(f["pending_order"] for f in fills[:-1])
        assert fills[-1]["order_status"] == "FILLED" and not fills[-1]["pending_order"]
        observations = [e for e in result.order_events if e["mode"] == mode]
        assert [e["quantity"] for e in observations] == sorted(e["quantity"] for e in observations)
        assert float(observations[-1]["gross"]) == pytest.approx(sum(f["quantity"] * f["price"] for f in fills))
        assert float(observations[-1]["fees"]) == pytest.approx(sum(f["fees"] for f in fills))
    assert not result.pending_orders


def test_partial_at_window_end_remains_unknown_and_never_advances_next_session():
    data = sample()
    entry = {**data.tape[0], "ask_size": 500}
    bars = {**data.bars, date(2026, 1, 7): {"A": SessionBar(1000, 1000, 1000, 1000, 100000)}}
    later = {**entry, "ask_size": 100000, "exchange_at": "2026-01-07T00:00:00", "received_at": "2026-01-07T00:00:00"}
    result = replay(replace(data, tape=(entry, later), bars=bars))
    assert all(f["date"] == "2026-01-06" and f["buy_stage"] == 0 for f in result.fills)
    assert all(e["status"] not in {"EXPIRED", "CANCELLED", "FILLED"} for e in result.order_events)
    assert all(e["pending_order"] for e in result.order_events)
    assert len(result.pending_orders) == 2
    assert "unresolved_order_outcome" in result.reasons


@pytest.mark.parametrize("loss", ["invalid", "subscription", "between_quotes", "outage"])
def test_recording_or_rejected_quote_loss_restarts_book_timer(loss):
    data = sample(tape=quotes("2026-01-06"))
    tape = data.tape
    recording = data.recording
    if loss == "invalid":
        tape = tuple({**q, "ask": 0} if q["received_at"].endswith(":15") else q for q in tape)
    elif loss in {"subscription", "between_quotes"}:
        removed = "2026-01-06T00:00:15" if loss == "subscription" else "2026-01-06T00:00:15.250000"
        recording += ({"kind": "subscriptions", "at": removed, "tickers": []},
                      {"kind": "subscriptions", "at": "2026-01-06T00:00:15.750000", "tickers": ["A"]})
    else:
        recording += ({"kind": "outage", "start": "2026-01-06T00:00:15.250000",
                       "end": "2026-01-06T00:00:15.750000", "tickers": ["A"]},
                      {"kind": "subscriptions", "at": "2026-01-06T00:00:15.750000", "tickers": ["A"]})
    result = replay(replace(data, tape=tape, recording=recording), with_orderbook=True)
    assert not [f for f in result.fills if f["reason"] == "orderbook_take_profit"]


def test_partial_fills_and_gap_never_bank_pending_book_duration():
    data = sample()
    tape = tuple({**data.tape[0], "ask_size": 1000, "bid": 1050, "ask": 1051,
                  "exchange_at": f"2026-01-06T00:00:{s:02}",
                  "received_at": f"2026-01-06T00:00:{s:02}", "gap": s == 15} for s in range(1, 32))
    entry = {**data.tape[0], "ask_size": 500}
    result = replay(replace(data, tape=(entry,) + tape), with_orderbook=True)
    assert not [f for f in result.fills if f["action"] == "sell"]
    assert all(f["buy_stage"] == 0 for f in result.fills)
    assert len(result.pending_orders) == 2
