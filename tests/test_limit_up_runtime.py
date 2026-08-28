"""Runtime contract tests for the KIS upper-limit V1 orchestrator."""

from __future__ import annotations

import datetime as dt
import json

from maps.common.models import SecurityMetadata
from maps.limit_up.runtime import (
    DeadmanMonitor,
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
