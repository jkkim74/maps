"""Actual clock/depth and shared allocation regressions."""
from datetime import datetime, timedelta, timezone
from maps.limit_up.feed import FeedQuote, KIS_ASK_COLUMNS, parse_kis_ws_message


def test_parser_preserves_total_depth_and_receipt_clock():
    now = datetime(2026, 10, 8, 0, 0, 1, tzinfo=timezone.utc)
    values = {"MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "090001",
        "ASKP1": "1100", "BIDP1": "1090", "ASKP_RSQN1": "5", "BIDP_RSQN1": "6",
        "TOTAL_ASKP_RSQN": "100", "TOTAL_BIDP_RSQN": "400"}
    raw = "0|H0STASP0|001|" + "^".join(values.get(k, "0") for k in KIS_ASK_COLUMNS)
    quote = parse_kis_ws_message(raw, received_at=1., received_utc=now)[0]
    assert quote.received_utc == quote.exchange_at == now
    assert quote.total_ask_qty == 100 and quote.total_bid_qty == 400


def test_duration_resets_on_delayed_processing_reconnect_and_zero_ask(db):
    from maps.fujimoto.feed import FujimotoFeed
    feed = FujimotoFeed(db, "account")
    now = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
    def quote(second, **changes):
        at = now + timedelta(seconds=second)
        fields = dict(ticker="AAA", best_ask_price=1101, best_ask_qty=100,
            best_bid_price=1100, best_bid_qty=400, received_at=float(second),
            total_ask_qty=100, total_bid_qty=400, received_utc=at, exchange_at=at)
        fields.update(changes)
        return FeedQuote(**fields)
    for second in range(31):
        result = feed.on_quote(quote(second), now=now + timedelta(seconds=second),
            cycle_id=1, cost_basis=10000, quantity=10)
    assert result[1] is True
    assert feed.on_quote(quote(31), now=now + timedelta(seconds=35),
        cycle_id=1, cost_basis=10000, quantity=10) == (None, False)
    feed.reset("reconnect")
    assert feed.on_quote(quote(36, best_ask_qty=0), now=now + timedelta(seconds=36),
        cycle_id=1, cost_basis=10000, quantity=10) == (None, False)


def test_allocator_prioritizes_all_held_and_pending():
    from maps.fujimoto.feed import allocate_subscriptions
    selected, blocked = allocate_subscriptions(3, ["UP", "OWN"], ["WAIT"], ["CAND", "OWN"])
    assert selected == ("UP", "OWN", "WAIT")
    assert blocked == ("CAND",)


def test_tape_capacity_marks_gap_and_blocks_profit_without_losing_stop_price(db):
    from maps.fujimoto.feed import FujimotoFeed
    feed = FujimotoFeed(db, "account", capacity=1)
    now = datetime.now(timezone.utc)
    q = FeedQuote("AAA", 1101, 100, 1100, 400, 1., 100, 400, now, now)
    feed.on_quote(q, now=now)
    bid, signal = feed.on_quote(q, now=now, cycle_id=1, quantity=10, cost_basis=10000)
    assert bid == 1100 and signal is False
    assert feed.repo.evidence_as_of("feed_quality", "*", datetime.max,
        account_key="account")[-1].payload["reason"] == "tape_capacity_exhausted"


def test_shared_runtime_observes_when_upper_strategy_is_disabled(db):
    from unittest.mock import Mock
    from maps.common.settings import MapsSettings
    from maps.limit_up.runtime import KISIntradayRuntime
    upper, fujimoto = Mock(), Mock()
    now = datetime.now(timezone.utc)
    runtime = KISIntradayRuntime(settings=MapsSettings(_env_file=None), db=db,
        adapter=Mock(), service=upper, fujimoto=fujimoto, upper_enabled=False)
    quote = FeedQuote("AAA", 1101, 10, 1100, 30, 1., 10, 30, now, now)
    runtime._apply_feed_event(quote, now)
    upper.on_quote.assert_not_called()
    fujimoto.on_quote.assert_called_once()
