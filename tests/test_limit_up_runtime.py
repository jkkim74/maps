"""Runtime contract tests for the KIS upper-limit V1 orchestrator."""

from __future__ import annotations

import datetime as dt
import json

KST = dt.timezone(dt.timedelta(hours=9))

from maps.common.models import SecurityMetadata
from maps.limit_up.runtime import (
    DeadmanMonitor,
    engine_active_at,
    eod_stage,
    is_v1_eligible_security,
    subscription_payload,
)


def test_subscription_payload_uses_official_realtime_tr_ids() -> None:
    """Each watched ticker needs both execution and best-book streams."""
    trade = json.loads(subscription_payload("key", "H0STCNT0", "005930"))
    quote = json.loads(subscription_payload("key", "H0STASP0", "005930"))

    assert trade["header"]["approval_key"] == "key"
    assert trade["body"]["input"]["tr_id"] == "H0STCNT0"
    assert quote["body"]["input"]["tr_id"] == "H0STASP0"
    assert quote["body"]["input"]["tr_key"] == "005930"


def test_security_eligibility_fails_closed_for_missing_new_or_preferred(db) -> None:
    """V1 scanner admits only seasoned KOSPI/KOSDAQ common stocks."""
    as_of = dt.date(2026, 8, 28)
    common = SecurityMetadata(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        security_type="STOCK",
        listing_date=dt.date(1975, 6, 11),
    )
    preferred = SecurityMetadata(
        ticker="005935",
        name="삼성전자우",
        market="KOSPI",
        security_type="STOCK",
        listing_date=dt.date(1975, 6, 11),
    )
    new_stock = SecurityMetadata(
        ticker="123456",
        name="신규주",
        market="KOSDAQ",
        security_type="STOCK",
        listing_date=as_of - dt.timedelta(days=20),
    )

    assert is_v1_eligible_security(common, as_of=as_of) is True
    assert is_v1_eligible_security(preferred, as_of=as_of) is False
    assert is_v1_eligible_security(new_stock, as_of=as_of) is False
    assert is_v1_eligible_security(None, as_of=as_of) is False


def test_deadman_sends_success_fail_and_never_logs_secret_url() -> None:
    """Healthchecks receives state while the configured secret stays opaque."""
    sent: list[str] = []
    monitor = DeadmanMonitor("https://hc.example/secret", sender=sent.append)

    assert monitor.ping(healthy=True) is True
    assert monitor.ping(healthy=False) is True
    assert sent == ["https://hc.example/secret", "https://hc.example/secret/fail"]
    assert "secret" not in repr(monitor)


def test_empty_deadman_url_is_a_safe_noop() -> None:
    """Local development must not require an external monitor."""
    monitor = DeadmanMonitor("")

    assert monitor.ping(healthy=True) is False


def test_eod_stage_windows_cover_every_overnight_checkpoint() -> None:
    """A missed window is silent: the carry crosses the night untrimmed."""
    assert eod_stage(dt.time(15, 17, 59)) is None
    assert eod_stage(dt.time(15, 18)) == "cap"
    assert eod_stage(dt.time(15, 19, 59)) == "cap"
    assert eod_stage(dt.time(15, 20)) is None
    assert eod_stage(dt.time(15, 25)) == "confirm"
    assert eod_stage(dt.time(15, 27, 59)) == "confirm"
    assert eod_stage(dt.time(15, 28)) == "force"
    assert eod_stage(dt.time(15, 29, 59)) == "force"
    assert eod_stage(dt.time(15, 30)) is None


def test_engine_is_idle_outside_trading_hours_and_days() -> None:
    """A 24/7 poll loop hammers the broker API; that is how accounts get locked.

    2026-07-27: pykrx re-login retries locked the KRX account 158 times in a day.
    """
    # 2026-08-29 is a Saturday
    assert not engine_active_at(dt.datetime(2026, 8, 29, 10, 0, tzinfo=KST))

    # weekday, but outside engine hours
    assert not engine_active_at(dt.datetime(2026, 8, 28, 8, 49, 59, tzinfo=KST))
    assert not engine_active_at(dt.datetime(2026, 8, 28, 15, 35, 1, tzinfo=KST))
    assert not engine_active_at(dt.datetime(2026, 8, 28, 22, 0, tzinfo=KST))


def test_engine_hours_cover_both_daily_action_windows() -> None:
    """08:59:30 next-open exits and the 15:18-15:28 overnight review must fit."""
    assert engine_active_at(dt.datetime(2026, 8, 28, 8, 59, 30, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 18, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 28, tzinfo=KST))
