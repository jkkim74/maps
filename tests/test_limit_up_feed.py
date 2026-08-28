"""KIS WebSocket normalization, tape buffering, and REST fallback pacing."""

from __future__ import annotations

import pytest

from maps.limit_up.feed import (
    KIS_ASK_COLUMNS,
    KIS_TRADE_COLUMNS,
    RestFallbackLimiter,
    TapeBuffer,
    parse_kis_ws_message,
)


def _message(tr_id: str, columns: tuple[str, ...], values: dict[str, str]) -> str:
    row = [values.get(column, "0") for column in columns]
    return f"0|{tr_id}|001|{'^'.join(row)}"


def test_kis_trade_fixture_maps_buy_flag_turnover_and_strength() -> None:
    """A shifted KIS column would silently break all three entry gates."""
    raw = _message(
        "H0STCNT0",
        KIS_TRADE_COLUMNS,
        {
            "MKSC_SHRN_ISCD": "005930",
            "STCK_PRPR": "99700",
            "ACML_TR_PBMN": "50000000000",
            "CTTR": "151.25",
            "CCLD_DVSN": "1",
        },
    )

    records = parse_kis_ws_message(raw, received_at=12.5)

    assert len(records) == 1
    trade = records[0]
    assert (trade.ticker, trade.price, trade.cumulative_turnover_krw) == (
        "005930",
        99_700,
        50_000_000_000,
    )
    assert trade.execution_strength == 151.25
    assert trade.buy_initiated is True
    assert trade.received_at == 12.5


def test_kis_ask_fixture_maps_only_the_best_level() -> None:
    """LOCKED and EOD decisions must not use total or lower-level quantities."""
    raw = _message(
        "H0STASP0",
        KIS_ASK_COLUMNS,
        {
            "MKSC_SHRN_ISCD": "005930",
            "ASKP1": "100000",
            "ASKP_RSQN1": "0",
            "BIDP1": "100000",
            "BIDP_RSQN1": "100000",
            "TOTAL_BIDP_RSQN": "9999999",
        },
    )

    quote = parse_kis_ws_message(raw, received_at=20.0)[0]

    assert quote.best_ask_price == 100_000
    assert quote.best_ask_qty == 0
    assert quote.best_bid_price == 100_000
    assert quote.best_bid_qty == 100_000


def test_control_message_is_ignored_and_bad_record_fails_closed() -> None:
    """Subscription acknowledgements are not market data and truncation is unsafe."""
    assert parse_kis_ws_message('{"header":{"tr_id":"PINGPONG"}}', received_at=0.0) == []

    try:
        parse_kis_ws_message("0|H0STCNT0|001|005930^090000", received_at=0.0)
    except ValueError as exc:
        assert "field count" in str(exc)
    else:
        raise AssertionError("truncated KIS record was accepted")


def test_tape_buffer_keeps_only_last_sixty_seconds_and_forced_snapshots() -> None:
    """The callback buffer must stay bounded while retaining transition evidence."""
    tape = TapeBuffer(window_seconds=60.0)
    tape.append("005930", at=1.0, payload={"price": 90_000})
    tape.append("005930", at=61.0, payload={"price": 99_000})
    tape.append("005930", at=61.1, payload={"price": 100_000})

    snapshot = tape.snapshot("005930", transition="LOCKED")

    assert [item["price"] for item in snapshot.payload] == [99_000, 100_000]
    assert snapshot.transition == "LOCKED"


def test_rest_fallback_enforces_500ms_and_exponential_429_backoff() -> None:
    """A disconnected WebSocket must not cause a second outage by flooding REST."""
    limiter = RestFallbackLimiter(min_interval_seconds=0.5)

    assert limiter.delay(now=10.0) == 0.0
    limiter.record_call(now=10.0)
    assert limiter.delay(now=10.1) == pytest.approx(0.4)
    limiter.record_rate_limit()
    assert limiter.delay(now=10.1) == 1.0
    limiter.record_rate_limit()
    assert limiter.delay(now=10.1) == 2.0
    limiter.record_success()
    assert limiter.delay(now=10.5) == 0.0
