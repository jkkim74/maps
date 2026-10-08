"""Deterministic two-mode event replay with shared liquidity and fill transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from maps.common.exceptions import DataQualityError
from maps.fujimoto.domain import (CycleState, Decision, Mode, RuleEvidence, evaluate,
                                  build_rule_evidence, ranked_admission, empty_cycle_expired)
from maps.fujimoto.repository import (AccountLimits, FillEvent, apply_fill_transition,
                                     fingerprint, json_data, money, size_buy, utc_naive)
from maps.market.trading_rules import is_krx_closed_date, round_down_krx_price


@dataclass(frozen=True)
class SessionBar:
    """Observed completed daily execution bar; halts never imply tradability."""
    open: float
    high: float
    low: float
    close: float
    volume: int
    halted: bool = False

    def __post_init__(self) -> None:
        if any(money(v) <= 0 for v in (self.open, self.high, self.low, self.close)) or not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise DataQualityError("invalid_replay_bar")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0 or not isinstance(self.halted, bool):
            raise DataQualityError("invalid_replay_volume")


@dataclass(frozen=True)
class ReplayInput:
    """Completed evidence plus actual execution bars; no implicit account budget.

    tape contains actual normalized quote observations; no synthetic order books.
    Annual/candidate provenance must be included for a measured promotion result.
    """
    budget: float
    evidence: dict[date, dict[str, RuleEvidence]]
    bars: dict[date, dict[str, SessionBar]]
    participation: float = .01
    fee_rate: float = .00015
    tax_rate: float = .002
    slippage: float = .001
    account_ticker_limit: float = .1
    minimum_cash_fraction: float = .325
    tape: tuple[dict, ...] = ()
    provenance: dict = field(default_factory=dict)
    closed_dates: tuple[date, ...] = ()
    screening: dict[date, dict[str, dict]] = field(default_factory=dict)
    candidate_order: dict[date, tuple[str, ...]] = field(default_factory=dict)
    recording: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if money(self.budget) <= 0 or not 0 < self.participation <= 1 or not 0 < self.account_ticker_limit <= 1 or not 0 <= self.minimum_cash_fraction <= 1:
            raise DataQualityError("invalid_replay_budget_or_limits")
        for v in (self.fee_rate, self.tax_rate, self.slippage):
            if money(v) >= 1:
                raise DataQualityError("invalid_replay_cost")
        for day, rows in self.evidence.items():
            if any(row.as_of != day for row in rows.values()):
                raise DataQualityError("misdated_rule_evidence")
        for day, tickers in self.candidate_order.items():
            if len(tickers) != len(set(tickers)) or any(t not in self.evidence.get(day, {}) for t in tickers):
                raise DataQualityError("invalid_recorded_candidate_order")


@dataclass(frozen=True)
class ReplayResult:
    """Reproducible measured accounting; missing inputs remain explicit coverage gaps."""
    final_cash: dict[str, float]
    final_quantities: dict[str, int]
    equity: tuple[dict, ...]
    fills: tuple[dict, ...]
    reasons: tuple[str, ...]
    data_hash: str
    params_hash: str
    code_hash: str
    with_orderbook: bool
    diagnostics: tuple[str, ...] = ()


def next_session(day: date, closed_dates: tuple[date, ...] = ()) -> date:
    """Signal validity is the first actual KRX session, never the next data row."""
    result = day + timedelta(days=1)
    while is_krx_closed_date(result, extra_closed_dates=closed_dates):
        result += timedelta(days=1)
    return result


def quote_session_date(quote: dict) -> date | None:
    """Use actual received UTC/offset time for KST session slicing, including WFA."""
    try:
        timestamp = datetime.fromisoformat(quote["received_at"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(ZoneInfo("Asia/Seoul")).date()
    except (KeyError, TypeError, ValueError):
        return None


def code_fingerprint() -> str:
    """Fingerprint all shared rule/accounting code used by this research runner."""
    root = Path(__file__).parent
    return fingerprint({name: (root / name).read_text(encoding="utf-8")
                        for name in ("domain.py", "evidence.py", "indicators.py", "repository.py", "replay.py", "validation.py",
                                     "service.py", "feed.py", "sources.py")})


def expected_net_sale(bid: float, quantity: int, sale_cost_rate: float, slippage: float = 0) -> Decimal:
    """Net proceeds at the same slipped bid and fee/tax basis used for fills."""
    return money(bid) * quantity * (Decimal(1) - money(slippage)) * (Decimal(1) - money(sale_cost_rate))


class RecordingIndex:
    """Parse recording once; timestamp lookup is logarithmic, never a history scan."""

    def __init__(self, recording: tuple[dict, ...]):
        self.valid = True
        subscriptions, outages = [], {}
        try:
            for row in recording:
                if row.get("kind") == "subscriptions":
                    subscriptions.append((utc_naive(datetime.fromisoformat(row["at"])), frozenset(row["tickers"])))
                elif row.get("kind") == "outage":
                    left, right = (utc_naive(datetime.fromisoformat(row[k])) for k in ("start", "end"))
                    if right < left:
                        raise ValueError("reversed_recording_interval")
                    for ticker in row["tickers"]:
                        outages.setdefault(ticker, []).append((left, right))
        except (KeyError, TypeError, ValueError):
            self.valid = False
        subscriptions.sort(key=lambda row: row[0])
        self.subscription_times = tuple(row[0] for row in subscriptions)
        self.subscriptions = tuple(row[1] for row in subscriptions)
        self.outages, self.outage_times = {}, {}
        for ticker, rows in outages.items():
            merged = []
            for left, right in sorted(rows):
                if merged and left <= merged[-1][1]:
                    merged[-1] = merged[-1][0], max(right, merged[-1][1])
                else:
                    merged.append((left, right))
            self.outages[ticker] = tuple(merged)
            self.outage_times[ticker] = tuple(left for left, _ in merged)

    def subscribed(self, ticker: str, stamp: datetime) -> bool:
        """Require a sent subscription and, after outage, a renewed subscription."""
        index = bisect_right(self.subscription_times, stamp) - 1
        if not self.valid or index < 0 or ticker not in self.subscriptions[index]:
            return False
        outage = bisect_right(self.outage_times.get(ticker, ()), stamp) - 1
        return outage < 0 or self.outages[ticker][outage][1] <= self.subscription_times[index]

    def intervals(self, ticker: str, start: datetime, end: datetime) -> tuple:
        """Return only outage intervals intersecting the requested owned session."""
        times = self.outage_times.get(ticker, ())
        rows = self.outages.get(ticker, ())
        return tuple((left, right) for left, right in rows[max(0, bisect_right(times, start) - 1):bisect_right(times, end)]
                     if right >= start)


def held_tape_coverage(ticker: str, start: datetime, end: datetime, quotes: list[dict],
                       recording: tuple[dict, ...] | RecordingIndex) -> tuple[bool, bool]:
    """Prove the owned interval with subscribed fresh arrivals or bounded known outages.

    A subscription request alone proves no data coverage. Silence longer than
    the runtime three-second freshness limit is unknown, never 'no trigger'.
    Outages must have both endpoints observed in one live recorder instance.
    """
    recording = recording if isinstance(recording, RecordingIndex) else RecordingIndex(recording)
    intervals = list(recording.intervals(ticker, start, end))
    outage = bool(intervals)
    if not recording.valid:
        return False, outage
    try:
        last = None
        for quote in quotes:
            _, last, bid, _ = quote_signal(None, last, quote, 0, 0, 0)
            if bid is None:
                continue
            stamp = utc_naive(datetime.fromisoformat(quote["received_at"]))
            if recording.subscribed(ticker, stamp):
                intervals.append((stamp, stamp + timedelta(seconds=3)))
    except (KeyError, TypeError, ValueError):
        return False, outage
    cursor, observed = start + timedelta(seconds=3), False
    for left, right in sorted(intervals):
        if right < cursor:
            continue
        if left > cursor:
            break
        observed = True
        cursor = max(cursor, right)
        if cursor >= end:
            return True, outage
    return observed and cursor >= end, outage


def quote_signal(since: datetime | None, last: datetime | None, quote: dict,
                 cost_basis: float, quantity: int, sale_cost_rate: float, *, slippage: float = 0) -> tuple:
    """Replay actual quote freshness/continuity and net-profitable 30s imbalance.

    Input timestamps are ISO UTC or offset-aware, with naive strings treated as
    UTC. Missing/invalid quotes reset continuity and never fabricate a stop price.
    Runtime may use this same pure function for its recorded feed events.
    """
    from maps.fujimoto.repository import utc_naive
    try:
        exchange = utc_naive(datetime.fromisoformat(quote["exchange_at"]))
        received = utc_naive(datetime.fromisoformat(quote["received_at"]))
        bid, ask = float(money(quote["bid"])), float(money(quote["ask"]))
        total_bid, total_ask = float(money(quote["total_bid"])), float(money(quote["total_ask"]))
        if quote.get("connected") is not True or quote.get("gap", False) or bid <= 0 or ask <= bid or total_ask <= 0 or not 0 <= (received - exchange).total_seconds() <= 3:
            return None, None, None, False
        if last is not None and exchange <= last:
            return None, last, None, False
        if last is not None and (exchange - last).total_seconds() > 3:
            since = None
        profitable = quantity > 0 and expected_net_sale(bid, quantity, sale_cost_rate, slippage) > money(cost_basis)
        if not profitable or total_bid / total_ask < 3:
            return None, exchange, bid, False
        since = since or exchange
        return since, exchange, bid, (exchange - since).total_seconds() >= 30
    except (KeyError, TypeError, ValueError, DataQualityError):
        return None, None, None, False


def screening_evidence(snapshot: dict, ticker: str, cutoff: date, *, maximum_return_20: float = .2,
                       minimum_dividend_growth: float = 0, closed_dates: tuple[date, ...] = ()) -> RuleEvidence:
    """Rebuild real screening AND technical/financial evidence from frozen raw inputs.

    Snapshot keys: annual_records/financial_records/valuations are exact dataclass
    dictionaries; sectors is SectorSnapshot dictionary; prices are OHLCV/turnover
    dictionaries with ISO date; eligible is historical DataQualityFilter result.
    """
    from maps.fujimoto.evidence import (AnnualRecord, FinancialRecord, ValuationRecord,
                                       SectorSnapshot, financial_status, screen)
    def records(cls: type, rows: list[dict]) -> list:
        result = []
        for row in rows:
            row = dict(row)
            for key in ("period_end", "publication_date", "available_date", "ref_date"):
                if key in row and isinstance(row[key], str):
                    row[key] = date.fromisoformat(row[key])
            for key in ("first_observed_at", "available_at"):
                if key in row and isinstance(row[key], str):
                    row[key] = datetime.fromisoformat(row[key])
            if "memberships" in row:
                row["memberships"] = tuple(tuple(pair) for pair in row["memberships"])
            for key in ("revenue", "prior_revenue", "operating_profit", "prior_operating_profit", "dividend_per_share", "per"):
                if isinstance(row.get(key), str):
                    try:
                        row[key] = float(Decimal(row[key]))
                    except (ArithmeticError, ValueError) as exc:
                        raise DataQualityError("invalid_numeric_screening_evidence") from exc
            result.append(cls(**row))
        return result
    annual = records(AnnualRecord, snapshot["annual_records"])
    financial = records(FinancialRecord, snapshot.get("financial_records", []))
    valuations = records(ValuationRecord, snapshot["valuations"])
    sector = records(SectorSnapshot, [snapshot["sectors"]])[0] if snapshot.get("sectors") else None
    frame = pd.DataFrame(snapshot["prices"])
    frame.index = pd.to_datetime(frame.pop("date"))
    selection = screen(ticker, annual, valuations, sector, frame, cutoff, eligible=snapshot.get("eligible") is True,
                       maximum_return_20=maximum_return_20, minimum_dividend_growth=minimum_dividend_growth)
    return build_rule_evidence(frame, cutoff, selection, financial_status(financial, cutoff), closed_dates=closed_dates)


def replay(data: ReplayInput, *, with_orderbook: bool = False, cost_multiplier: float = 1,
           first_rsi: float = 40, maximum_return_20: float = .2, minimum_dividend_growth: float = 0) -> ReplayResult:
    """Execute next-session intentions only at subscribed actual quote events.

    Daily-only replay cannot claim an intraday stop execution. Recorded quotes
    carry actual bid liquidity/timestamps and are required for those exits.
    Completed-session financial evidence is available after close, so its first
    executable quote is the next session. Daily bars never create submission or
    ownership. Displayed ask/bid liquidity and daily participation bound fills.
    """
    if (money(cost_multiplier) <= 0 or not 0 <= first_rsi <= 100
            or data.slippage * cost_multiplier >= 1
            or (data.fee_rate + data.tax_rate) * cost_multiplier >= 1):
        raise DataQualityError("invalid_replay_parameter")
    cash = {m: money(data.budget) / 2 for m in Mode}
    states, budgets, costs, pnl, spent, pending = {}, {}, {}, {}, {}, {}
    marks: dict[str, float] = {}
    fills, equity, reasons, diagnostics = [], [], set(), set()
    if with_orderbook and not data.tape:
        reasons.add("missing_recorded_tape")
    volume_used: dict[tuple[date, str], int] = {}
    quote_used: dict[tuple[int, str], int] = {}
    sequence = 0
    continuity = {}
    recording = RecordingIndex(data.recording)
    tape_by_day: dict[date, list[tuple[int, dict]]] = {}
    for index, quote in enumerate(data.tape):
        quote_day = quote_session_date(quote)
        if quote_day is None:
            reasons.add("invalid_recorded_tape")
        else:
            tape_by_day.setdefault(quote_day, []).append((index, quote))
    cycle_numbers, closed_on, started_on = {}, {}, {}
    fee_rate, tax_rate, slippage = data.fee_rate * cost_multiplier, data.tax_rate * cost_multiplier, data.slippage * cost_multiplier

    def nav(mode: Mode) -> float:
        return float(cash[mode]) + sum(s.quantity * marks[t] for (m, t), s in states.items() if m == mode and s.quantity and t in marks)

    def execute(key: tuple, decision: Decision, day: date, bar: SessionBar | None, quantity: int,
                limit: float, stop: float | None, quote_index: int | None = None,
                phase: str = "open") -> None:
        nonlocal sequence
        mode, ticker = key
        state = states[key]
        sequence += 1
        zero = FillEvent(sequence, "replay", None, 0, 0, 0, 0, "RESERVED", day)
        state = replace(state, pending_order=True)
        if bar is None or bar.halted:
            (reasons if bar is None else diagnostics).add("missing_execution_bar" if bar is None else "exchange_halt")
            amount, price = 0, 0
        else:
            available = max(0, int(bar.volume * data.participation) - volume_used.get((day, ticker), 0))
            if quote_index is not None:
                available = max(0, int(bar.volume * data.participation) - quote_used.get((quote_index, decision.action), 0))
                daily = data.bars.get(day, {}).get(ticker)
                if daily is not None:
                    available = min(available, max(0, int(daily.volume * data.participation) - volume_used.get((day, ticker), 0)))
            # Share daily participation across modes rather than filling both from the same volume.
            if decision.action == "buy":
                available //= max(1, sum(1 for k in pending if k[1] == ticker))
                price = min(limit, bar.open * (1 + slippage))
                touched = bar.low <= limit and (bar.open <= limit or bar.low * (1 + slippage) <= limit)
            else:
                price = money(bar.open) * (Decimal(1) - money(slippage))
                touched = price >= money(limit)
            amount = min(quantity, available) if touched else 0
        gross = money(price) * amount
        fees, tax = gross * money(fee_rate), gross * money(tax_rate if decision.action == "sell" else 0)
        terminal = "FILLED" if amount == quantity else "EXPIRED"
        current = FillEvent(sequence, "replay", None, amount, gross, fees, tax, terminal, day)
        transition = apply_fill_transition(state, costs[key], pnl[key], decision, quantity, zero, current, stop)
        states[key], costs[key], pnl[key] = transition.state, transition.cost_basis, transition.realized_pnl
        cash[mode] += transition.cash_delta
        if amount:
            volume_used[day, ticker] = volume_used.get((day, ticker), 0) + amount
            if quote_index is not None:
                quote_used[quote_index, decision.action] = quote_used.get((quote_index, decision.action), 0) + amount
            fills.append({"date": day.isoformat(), "mode": mode.value, "ticker": ticker, "quantity": amount,
                          "execution_phase": phase,
                          "executed_at": data.tape[quote_index]["received_at"] if quote_index is not None else None,
                          "action": decision.action, "reason": decision.reason, "price": float(price),
                          "fees": float(fees), "tax": float(tax), "cash_delta": float(transition.cash_delta),
                          "realized_pnl": float(transition.realized_pnl), "buy_stage": transition.state.buy_stage,
                          "completed_cycle": decision.action == "sell" and transition.state.quantity == 0,
                          "cycle_number": cycle_numbers[key]})
            if decision.action == "sell" and transition.state.quantity == 0:
                closed_on[key] = day
            if decision.action == "buy":
                leg = key, decision.buy_stage
                spent[leg] = spent.get(leg, 0) + float(gross + fees + tax)

    days = set(data.bars) | set(data.evidence)
    if days:
        cursor, end = min(days), max(days)
        while cursor < end:
            if not is_krx_closed_date(cursor, extra_closed_dates=data.closed_dates):
                days.add(cursor)
            cursor += timedelta(days=1)
    for day in sorted(days):
        bars = data.bars.get(day, {})
        session_start = datetime.combine(day, datetime.min.time())  # 09:00 KST = 00:00 UTC
        session_end = session_start + timedelta(hours=6, minutes=20)
        # These are unsent intentions. Runtime cannot submit at a daily open without
        # a fresh subscribed quote; both BUY and next-session SELL wait for one.
        for key, (due, decision, qty, limit, stop) in list(pending.items()):
            if due < day:
                states[key] = replace(states[key], pending_order=False)
                reasons.add("unavailable_next_session")
                pending.pop(key)
        held_from = {key: session_start for key, state in states.items() if state.quantity}
        held_until = {key: session_end for key, state in states.items() if state.quantity}
        quote_clocks = {}
        submission_at = {}
        # Actual quotes, in recorded receive order, drive intraday stops/imbalance.
        for quote_index, quote in tape_by_day.get(day, ()):
            stamp = utc_naive(datetime.fromisoformat(quote["received_at"]))
            if not session_start <= stamp < session_end:
                continue
            ticker = quote.get("ticker")
            daily = bars.get(ticker)
            if daily is not None and daily.halted:
                diagnostics.add("exchange_halt")
                continue
            _, last, bid, _ = quote_signal(None, quote_clocks.get(ticker), quote, 0, 0, 0)
            quote_clocks[ticker] = last
            if bid is None or not recording.subscribed(ticker, stamp):
                continue
            liquidity = quote.get("bid_size")
            if isinstance(liquidity, bool) or not isinstance(liquidity, int) or liquidity < 0:
                reasons.add("invalid_recorded_tape")
                continue
            executed = set()
            for key, (due, decision, qty, limit, stop) in list(pending.items()):
                if due != day or key[1] != ticker:
                    continue
                if daily is None:
                    reasons.add("missing_execution_bar")
                    continue
                submission_at.setdefault(key, stamp)
                if states[key].quantity:
                    emergency = evaluate(key[0], RuleEvidence(day, None, live_price=bid),
                                         replace(states[key], pending_order=False))
                    if emergency.action == "sell" and emergency.timing == "intraday":
                        decision, qty, limit = emergency, emergency.sell_quantity, 0
                quantity = quote.get("ask_size") if decision.action == "buy" else liquidity
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    reasons.add("missing_submission_liquidity")
                    continue
                price = float(money(quote["ask"])) if decision.action == "buy" else bid
                if decision.action == "buy" and price > limit:
                    continue
                if submission_at[key] < stamp:
                    relevant = [q for _, q in tape_by_day.get(day, ()) if q.get("ticker") == ticker]
                    covered, outage = held_tape_coverage(ticker, submission_at[key], stamp, relevant, recording)
                    if not covered or outage:
                        # A resting order may have filled while the recorder could
                        # not observe it. Never certify later ownership as exact.
                        reasons.add("unknown_order_execution_timing")
                before = states[key].quantity
                execute(key, decision, day, SessionBar(price, price, price, price, quantity),
                        qty, limit, stop, quote_index, phase="intraday")
                pending.pop(key)
                executed.add(key)
                if states[key].quantity and not before:
                    held_from[key], held_until[key] = stamp, session_end
                elif before and not states[key].quantity:
                    held_until[key] = stamp
            for mode in Mode:
                key = mode, ticker
                if key in executed or key not in states or not states[key].quantity or states[key].pending_order:
                    continue
                since, last = continuity.get(key, (None, None))
                since, last, bid, trigger = quote_signal(since, last, quote, costs[key], states[key].quantity,
                                                       fee_rate + tax_rate, slippage=slippage)
                continuity[key] = since, last
                if bid is None:
                    continue
                evidence = RuleEvidence(day, None, live_price=bid, orderbook_take_profit=trigger and with_orderbook)
                decision = evaluate(mode, evidence, states[key])
                if decision.sell_target_ninths:
                    states[key] = replace(states[key], sell_basis_quantity=states[key].sell_basis_quantity or decision.sell_basis_quantity,
                                          sell_target_ninths=max(states[key].sell_target_ninths, decision.sell_target_ninths))
                if decision.action == "sell" and decision.timing == "intraday" and liquidity:
                    # Quote bid size is actual displayed liquidity; execution still uses
                    # conservative participation, shared by both modes for this quote.
                    bar = SessionBar(bid, bid, bid, bid, liquidity)
                    execute(key, decision, day, bar, decision.sell_quantity,
                            money(bid) * (Decimal(1) - money(slippage)), states[key].stop_price,
                            quote_index, phase="intraday")
                    if not states[key].quantity:
                        held_until[key] = stamp
        for key, end in held_until.items():
            ticker = key[1]
            if bars.get(ticker) and bars[ticker].halted:
                diagnostics.add("exchange_halt")
                continue
            relevant = [q for _, q in tape_by_day.get(day, ()) if q.get("ticker") == ticker]
            covered, outage = held_tape_coverage(ticker, held_from[key], end, relevant, recording)
            if not covered:
                reasons.add("missing_held_session_tape")
            if outage:
                diagnostics.add("observed_feed_outage")
        for key, (due, decision, qty, limit, stop) in list(pending.items()):
            if due != day:
                continue
            daily = bars.get(key[1])
            outages = recording.intervals(key[1], session_start, session_end)
            if recording.valid and any(left <= session_start and right >= session_end for left, right in outages):
                diagnostics.add("observed_feed_outage")
            elif daily is None:
                reasons.add("missing_execution_bar")
            elif daily.halted:
                diagnostics.add("exchange_halt")
            elif decision.action == "sell" or daily.low <= limit:
                reasons.add("missing_submission_tape")
                if decision.action == "buy":
                    reasons.add("unknown_intraday_fill_order")
            states[key] = replace(states[key], pending_order=False)
            pending.pop(key)
        for key, state in list(states.items()):
            if empty_cycle_expired(state, started_on[key], day):
                del states[key]
        for ticker, bar in bars.items():
            marks[ticker] = bar.close
        for mode in Mode:
            ranking = {ticker: rank for rank, ticker in enumerate(data.candidate_order.get(day, ()))}
            ordered = []
            for ticker, evidence in sorted(data.evidence.get(day, {}).items(), key=lambda row: (ranking.get(row[0], len(ranking)), row[0])):
                snapshot = data.screening.get(day, {}).get(ticker)
                if snapshot is not None:
                    evidence = screening_evidence(snapshot, ticker, day, maximum_return_20=maximum_return_20,
                                                  minimum_dividend_growth=minimum_dividend_growth, closed_dates=data.closed_dates)
                ordered.append((ticker, evidence))
            active = [t for (m, t), s in states.items() if m == mode and (s.quantity or s.pending_order or not s.buy_stage)]
            eligible = [t for t, e in ordered if evaluate(mode, e, CycleState(), first_rsi_threshold=first_rsi).action == "buy"]
            admitted = ranked_admission(active, eligible)
            for ticker, evidence in ordered:
                key = mode, ticker
                if key not in states or (not states[key].quantity and states[key].buy_stage and closed_on.get(key, day) < day):
                    if evaluate(mode, evidence, CycleState(), first_rsi_threshold=first_rsi).action != "buy":
                        continue
                    if ticker not in admitted:
                        diagnostics.add("mode_position_limit")
                        continue
                    states[key], costs[key], pnl[key] = CycleState(), Decimal(0), Decimal(0)
                    budgets[key] = nav(mode) * (.1 if mode == Mode.SAFE else .135)
                    cycle_numbers[key] = cycle_numbers.get(key, 0) + 1
                    started_on[key] = day
                    for stage in (1, 2, 3):
                        spent.pop((key, stage), None)
                if not with_orderbook:
                    evidence = replace(evidence, orderbook_take_profit=False)
                # Caller-generated intraday booleans are not executable tape evidence.
                evidence = replace(evidence, live_price=None, orderbook_take_profit=False)
                decision = evaluate(mode, evidence, states[key], first_rsi_threshold=first_rsi)
                if decision.sell_target_ninths:
                    states[key] = replace(states[key], sell_basis_quantity=states[key].sell_basis_quantity or decision.sell_basis_quantity,
                                          sell_target_ninths=max(states[key].sell_target_ninths, decision.sell_target_ninths))
                if decision.reason == "rebound_reduction" and not decision.sell_quantity:
                    states[key] = replace(states[key], rebound_reduced=True)
                elif decision.reason == "rebound_reduction" and not states[key].rebound_basis_quantity:
                    states[key] = replace(states[key], rebound_basis_quantity=states[key].quantity)
                if decision.action == "hold":
                    continue
                if decision.action == "buy":
                    account_nav = sum(nav(m) for m in Mode)
                    reserved = sum(q * lim * (1 + fee_rate) for _, d, q, lim, _ in pending.values() if d.action == "buy")
                    ticker_exposure = sum(s.quantity * marks.get(t, 0) for (m, t), s in states.items() if t == ticker)
                    ticker_exposure += sum(q * lim * (1 + fee_rate) for k, (_, d, q, lim, _) in pending.items() if k[1] == ticker and d.action == "buy")
                    limits = AccountLimits(account_nav, sum(float(cash[m]) for m in Mode), ticker_exposure,
                                           reserved, data.account_ticker_limit, data.minimum_cash_fraction)
                    mode_reserved = sum(q * lim * (1 + fee_rate) for k, (_, d, q, lim, _) in pending.items() if k[0] == mode and d.action == "buy")
                    decision = replace(decision, price_cap=round_down_krx_price(decision.price_cap))
                    plan = size_buy(mode, states[key], decision, budgets[key], nav(mode), float(cash[mode]), mode_reserved,
                                    float(costs[key]), spent.get((key, decision.buy_stage), 0), limits,
                                    atr14=evidence.atr14, fee_rate=fee_rate)
                    qty, stop, limit = plan.quantity, plan.stop_price, decision.price_cap
                else:
                    qty, stop, limit = decision.sell_quantity, states[key].stop_price, 0
                if not qty:
                    diagnostics.add("budget_or_risk_wait")
                    continue
                pending[key] = next_session(day, data.closed_dates), decision, qty, limit, stop
                states[key] = replace(states[key], pending_order=True)
        if any(s.quantity and t not in bars for (m, t), s in states.items()):
            reasons.add("stale_valuation")
        equity.append({"date": day.isoformat(), "safe": nav(Mode.SAFE), "original": nav(Mode.ORIGINAL),
                       "combined": sum(nav(m) for m in Mode)})
    if pending:
        # These are unsent next-session research intentions, not broker UNKNOWN.
        # Expire at the window boundary; owned shares stay marked, never liquidated.
        diagnostics.add("window_end_intentions_expired")
    return ReplayResult({m.value: float(cash[m]) for m in Mode},
                        {f"{m.value}:{t}": s.quantity for (m, t), s in states.items()},
                        tuple(equity), tuple(fills), tuple(sorted(reasons)), fingerprint(data),
                        fingerprint({"cost_multiplier": cost_multiplier, "first_rsi": first_rsi, "with_orderbook": with_orderbook,
                                     "maximum_return_20": maximum_return_20, "minimum_dividend_growth": minimum_dividend_growth}),
                        code_fingerprint(), with_orderbook, tuple(sorted(diagnostics)))


def input_from_json(payload: dict) -> ReplayInput:
    """Restore stored causal inputs without trusting stored result metrics."""
    data = dict(payload)
    data["evidence"] = {date.fromisoformat(day): {ticker: RuleEvidence(**dict(row, as_of=date.fromisoformat(row["as_of"])))
                        for ticker, row in rows.items()} for day, rows in data["evidence"].items()}
    data["bars"] = {date.fromisoformat(day): {ticker: SessionBar(**row) for ticker, row in rows.items()}
                    for day, rows in data["bars"].items()}
    data["closed_dates"] = tuple(date.fromisoformat(day) for day in data.get("closed_dates", ()))
    data["tape"] = tuple(data.get("tape", ()))
    data["recording"] = tuple(data.get("recording", ()))
    data["screening"] = {date.fromisoformat(day): rows for day, rows in data.get("screening", {}).items()}
    data["candidate_order"] = {date.fromisoformat(day): tuple(tickers) for day, tickers in data.get("candidate_order", {}).items()}
    return ReplayInput(**data)


def stress_losses(invested_fraction: float) -> dict[str, float]:
    """Gap/halts can defeat stops: report uncapped simultaneous marked losses."""
    if not 0 <= invested_fraction <= 1:
        raise DataQualityError("invalid_stress_exposure")
    return {"simultaneous_68pct": invested_fraction * .68, "total_loss": invested_fraction,
            "halt_unliquidatable": invested_fraction, "three_lower_limits": invested_fraction * (1 - .7 ** 3)}
