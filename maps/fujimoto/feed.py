"""Shared actual quote tape, continuity and bounded subscription allocation."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from maps.fujimoto.repository import FujimotoRepository, utc_naive
from maps.fujimoto.replay import quote_signal
from maps.limit_up.feed import FeedQuote


def allocate_subscriptions(capacity: int, held, pending, candidates) -> tuple:
    """One stable allocation; owned/pending protection always precedes observation."""
    ordered = tuple(dict.fromkeys([*held, *pending, *candidates]))
    return ordered[:capacity], ordered[capacity:]


class FujimotoFeed:
    """Per-cycle net-profit duration using clocks captured before queueing."""
    def __init__(self, db, key: str, *, capacity: int | None = None):
        self.repo, self.key, self._duration = FujimotoRepository(db), key, {}
        from maps.common.settings import get_settings
        from maps.common.models import FujimotoEvidence
        self.capacity = capacity if capacity is not None else get_settings().maps_fujimoto_tape_rows
        self._recorded = db.query(FujimotoEvidence).filter_by(kind="quote", account_key=key).count()
        self._capacity_notice = False
        self._generation, self._seen_generation = 0, {}
        self._subscriptions, self._outage_start = (), None

    def record_subscriptions(self, tickers, *, now: datetime) -> None:
        """Record actual sent subscriptions; received quotes must still prove coverage."""
        self._subscriptions = tuple(tickers)
        self.repo.record_evidence("feed_recording", "*", now, now,
            {"kind": "subscriptions", "at": now.isoformat(), "tickers": self._subscriptions}, account_key=self.key)
        self.repo.session.commit()

    def reset(self, reason: str) -> None:
        """Reconnect/disconnect and parse gaps invalidate every duration."""
        self._duration.clear()
        self._generation += 1
        now = datetime.now(timezone.utc)
        if reason == "disconnect" and self._outage_start is None:
            self._outage_start = (now, self._subscriptions)
        elif reason == "reconnect" and self._outage_start is not None:
            start, tickers = self._outage_start
            self.repo.record_evidence("feed_recording", "*", now, now,
                {"kind": "outage", "reason": "disconnect", "start": start.isoformat(),
                 "end": now.isoformat(), "tickers": tickers}, account_key=self.key)
            self._outage_start = None
        self.repo.record_evidence("feed_quality", "*", now, now,
            {"reason": reason, "continuous": False}, account_key=self.key)
        self.repo.session.commit()

    def on_quote(self, quote: FeedQuote, *, now: datetime, cycle_id: int = 0,
                 cost_basis: float = 0, quantity: int = 0, costs_complete: bool = True,
                 sale_cost_rate: float = .00215, slippage: float = .001, persist: bool = True) -> tuple:
        """Persist actual arrival provenance and return fresh bid/net-profit signal."""
        payload = {"ticker": quote.ticker, "exchange_at": quote.exchange_at.isoformat() if quote.exchange_at else None,
            "received_at": quote.received_utc.isoformat() if quote.received_utc else None,
            "connected": True, "gap": quote.gap, "bid": quote.best_bid_price,
            "ask": quote.best_ask_price, "bid_size": quote.best_bid_qty,
            "ask_size": quote.best_ask_qty, "total_bid": quote.total_bid_qty,
            "total_ask": quote.total_ask_qty}
        if persist:
            payload["gap"] = payload["gap"] or self._seen_generation.get(quote.ticker, 0) != self._generation
            self._seen_generation[quote.ticker] = self._generation
        fresh = quote.received_utc is not None and 0 <= (utc_naive(now) - utc_naive(quote.received_utc)).total_seconds() <= 3
        if not fresh or quote.best_ask_qty <= 0:
            payload["gap"] = True
        if persist:
            # Missing clocks do not receive fabricated historical timestamps.
            observed = quote.received_utc or now
            if self._recorded < self.capacity:
                self.repo.record_evidence("quote", quote.ticker, observed, observed,
                    {**payload, "processed_at": now.isoformat()}, account_key=self.key)
                self._recorded += 1
            elif not self._capacity_notice:
                self.reset("tape_capacity_exhausted")
                self._capacity_notice = True
        identity = cycle_id, quote.ticker
        since, last = self._duration.get(identity, (None, None))
        since, last, bid, signal = quote_signal(since, last, payload, cost_basis,
                                               quantity if costs_complete else 0, sale_cost_rate, slippage=slippage)
        if self._recorded >= self.capacity:
            since, signal = None, False
        self._duration[identity] = since, last
        return bid, signal
