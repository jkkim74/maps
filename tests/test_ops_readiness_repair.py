"""Offline regression coverage for operational alert and query repairs."""
import datetime as dt
import logging
from unittest.mock import Mock

import pytest

from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import BrokerAdapter
from maps.execution.kis_adapter import KISAdapter
from tests.test_kis_adapter import FakeSession, FakeResponse


def test_classification_prefers_telegram_and_bounds_plain_text():
    from maps.ops.notifications import ClassificationNotifier, Notification
    telegram, slack = Mock(), Mock()
    telegram.send_message.return_value = True
    notifier = ClassificationNotifier(MapsSettings(_env_file=None), telegram=telegram, slack=slack)
    assert notifier.send(Notification("ERROR", "classification failed", "<source> & " + "x" * 5000))
    text = telegram.send_message.call_args.args[0]
    assert text.startswith("[ERROR] classification failed")
    assert len(text) <= 3500
    assert telegram.send_message.call_args.kwargs["parse_mode"] == ""
    slack.send.assert_not_called()


@pytest.mark.parametrize("failure", [False, RuntimeError("offline")])
def test_classification_falls_back_and_preserves_delivery_result(failure):
    from maps.ops.notifications import ClassificationNotifier, Notification
    telegram, slack = Mock(), Mock()
    if isinstance(failure, Exception):
        telegram.send_message.side_effect = failure
    else:
        telegram.send_message.return_value = failure
    slack.send.return_value = False
    notifier = ClassificationNotifier(MapsSettings(_env_file=None), telegram=telegram, slack=slack)
    notification = Notification("ERROR", "failed", "source unavailable", {"run_id": 4})
    assert notifier.send(notification) is False
    slack.send.assert_called_once_with(notification)
    slack.send.return_value = True
    assert notifier.send(notification) is True


def test_classification_without_channels_is_not_delivered():
    from maps.ops.notifications import ClassificationNotifier, Notification
    settings = MapsSettings(_env_file=None, telegram_bot_token="", telegram_chat_id="", slack_webhook_url="")
    assert ClassificationNotifier(settings).send(Notification("ERROR", "failed", "source")) is False


def test_pipeline_uses_classification_routing_and_preserves_injected_notifier():
    from maps.ops.notifications import ClassificationNotifier
    from maps.ops.scheduler import OperationalPipeline
    settings = MapsSettings(_env_file=None)
    assert isinstance(OperationalPipeline(settings=settings)._classification_jobs().notifier, ClassificationNotifier)
    custom = Mock()
    assert OperationalPipeline(settings=settings, notifier=custom)._classification_jobs().notifier is custom


def test_classification_log_is_bounded_without_losing_durable_details(caplog, monkeypatch):
    from maps.ops.scheduler import OperationalPipeline, MapsOperationalScheduler, JobRun
    settings = MapsSettings(_env_file=None)
    monkeypatch.setattr(MapsOperationalScheduler, "_restore_last_runs", lambda self: None)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=OperationalPipeline(settings=settings))
    scheduler._persist_run = Mock()
    runs = [{"id": i, "status": "failed", "notified_at": None, "error": "large-history-" + "x" * 1000} for i in range(100)]
    run = JobRun(name="classification_check", status="success", started_at=dt.datetime.now(dt.timezone.utc), details={"runs": runs})
    with caplog.at_level(logging.INFO, logger="maps.ops.scheduler"):
        result = scheduler._record("classification_check", lambda: run)
    messages = [r.message for r in caplog.records if "Scheduler job classification_check" in r.message]
    assert len(messages) == 1 and len(messages[0]) < 500
    assert "large-history" not in messages[0]
    assert result.details["runs"] == runs
    assert scheduler._persist_run.call_args.args[0].details["runs"] == runs


def _broker(tmp_path):
    settings = MapsSettings(_env_file=None, maps_log_dir=str(tmp_path), kis_app_key="repair-key",
        kis_app_secret="repair-secret", kis_account_no="12345678-01", kis_real_trading=False,
        kis_paper_base_url="https://paper.example", maps_kis_paper_min_interval_seconds=0.05)
    http = FakeSession()
    return KISAdapter(settings, http=http), http


def test_kis_order_views_share_paginated_query_and_refresh_next_call(tmp_path):
    broker, http = _broker(tmp_path)
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    pending = {**http.open_orders_payload["output1"][0], "ord_dt": day.strftime("%Y%m%d")}
    cancelled = {**pending, "odno": "cancelled", "cncl_yn": "Y"}
    filled = {**pending, "odno": "filled", "rmn_qty": "0", "tot_ccld_qty": "10"}
    http.paged["/uapi/domestic-stock/v1/trading/inquire-daily-ccld"] = [
        FakeResponse({"rt_cd": "0", "output1": [pending], "ctx_area_fk100": "next", "ctx_area_nk100": "page"}, headers={"tr_cont": "F"}),
        FakeResponse({"rt_cd": "0", "output1": [cancelled, filled]}, headers={"tr_cont": "D"}),
    ]
    history, open_orders = broker.get_reconciliation_orders(day, day)
    assert len(history) == 3
    assert [(r.order_id, r.remaining_quantity) for r in open_orders] == [("12345", 4)]
    calls = [c for c in http.calls if c["url"].endswith("inquire-daily-ccld")]
    assert len(calls) == 2
    assert all(c["params"]["CCLD_DVSN"] == "00" for c in calls)
    broker.get_reconciliation_orders(day, day)
    assert len([c for c in http.calls if c["url"].endswith("inquire-daily-ccld")]) == 3


def test_kis_order_views_do_not_treat_historical_pending_as_current(tmp_path):
    broker, http = _broker(tmp_path)
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    yesterday = today - dt.timedelta(days=1)
    row = http.open_orders_payload["output1"][0]
    row["ord_dt"] = yesterday.strftime("%Y%m%d")
    history, pending = broker.get_reconciliation_orders(yesterday, today)
    assert len(history) == 1 and pending == []


def test_kis_order_views_fail_closed_on_incomplete_page(tmp_path):
    from maps.common.exceptions import BrokerAdapterError
    broker, http = _broker(tmp_path)
    http.open_orders_payload = {"rt_cd": "0"}
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    with pytest.raises(BrokerAdapterError):
        broker.get_reconciliation_orders(today, today)


@pytest.mark.parametrize("quantity", [None, "bad", "0", "-1"])
def test_kis_order_views_reject_raw_invalid_quantity_before_terminal_filter(tmp_path, quantity):
    from maps.common.exceptions import BrokerAdapterError
    broker, http = _broker(tmp_path)
    http.open_orders_payload["output1"][0].update(ord_qty=quantity, tot_ccld_qty="0", rmn_qty="4")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    with pytest.raises(BrokerAdapterError, match="open_order_quantity_incomplete"):
        broker.get_reconciliation_orders(today, today)


def test_generic_order_views_preserve_independent_broker_calls():
    broker = Mock()
    broker.get_order_history.return_value = ["history"]
    broker.get_open_orders.return_value = ["pending"]
    today = dt.date(2026, 10, 29)
    assert BrokerAdapter.get_reconciliation_orders(broker, today, today) == (["history"], ["pending"])
    broker.get_order_history.assert_called_once_with(today, today)
    broker.get_open_orders.assert_called_once_with()


def test_kis_order_views_reject_date_rollover(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from maps.execution import kis_adapter
    from maps.common.exceptions import BrokerAdapterError
    broker, _ = _broker(tmp_path)
    stamps = iter([dt.datetime(2026, 10, 29, 23, 59), dt.datetime(2026, 10, 30)])
    clock = SimpleNamespace(datetime=SimpleNamespace(now=lambda zone: next(stamps)))
    monkeypatch.setattr(kis_adapter, "dt", clock)
    monkeypatch.setattr(broker, "get_order_history", lambda *args: [])
    with pytest.raises(BrokerAdapterError, match="reconciliation_date_changed"):
        broker.get_reconciliation_orders(dt.date(2026, 10, 29), dt.date(2026, 10, 29))


def test_kis_order_views_reject_missing_pending_quantity(tmp_path, monkeypatch):
    from maps.execution.broker_adapter import OrderResult, OrderSide, OrderStatus
    from maps.common.exceptions import BrokerAdapterError
    broker, _ = _broker(tmp_path)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    row = OrderResult("order", "", "005930", OrderSide.BUY, OrderStatus.PENDING,
        submitted_at=now, quantity=10, remaining_quantity=None)
    monkeypatch.setattr(broker, "get_order_history", lambda *args: [row])
    with pytest.raises(BrokerAdapterError, match="open_order_quantity_incomplete"):
        broker.get_reconciliation_orders(now.date(), now.date())


def test_reconcile_uses_broker_order_views(db, monkeypatch):
    from maps.execution.mock_broker import MockBroker
    from maps.execution.reconciliation import reconcile
    broker = MockBroker(1_000_000, {})
    calls = []
    def views(self, start, end):
        calls.append((start, end))
        return [], []
    monkeypatch.setattr(MockBroker, "get_reconciliation_orders", views)
    monkeypatch.setattr(broker, "get_order_history", Mock(side_effect=AssertionError("duplicate history")))
    monkeypatch.setattr(broker, "get_open_orders", Mock(side_effect=AssertionError("duplicate pending")))
    reconcile(db, broker, MapsSettings(_env_file=None, maps_broker_mode="mock"))
    assert len(calls) == 1
