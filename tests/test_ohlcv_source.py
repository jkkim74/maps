"""Official per-security source contracts; no operational network or database."""
import datetime as dt

import pytest
import requests

from maps.data.ohlcv_source import NormalizedKRXSource

DAY = dt.date(2026, 10, 30)
NOW = dt.datetime(2026, 10, 30, 8, tzinfo=dt.timezone.utc)


def row(**changes):
    value = dict(TRD_DD="2026/10/30", TDD_OPNPRC="3,780", TDD_HGPRC="3,780",
                 TDD_LWPRC="3,685", TDD_CLSPRC="3,700", ACC_TRDVOL="23,062")
    return value | changes


class Client:
    def __init__(self, raw=None, adjusted=None):
        self.raw = [row()] if raw is None else raw
        self.adjusted = [row(TDD_CLSPRC="3,600")] if adjusted is None else adjusted
        self.calls = []

    def request(self, params, *, timeout):
        self.calls.append((params, timeout))
        value = self.raw if params["adjStkPrc"] == "1" else self.adjusted
        if isinstance(value, Exception):
            raise value
        return {"output": value}


def collect(client, **kwargs):
    return NormalizedKRXSource(client, interval=0, utc_now=lambda: NOW, **kwargs).collect(
        DAY, {"004840": "KR7004840004"})


def test_regular_close_and_adjusted_close_are_separate():
    client = Client()
    result = collect(client)
    item = result.items[0]
    assert item.status == "valid"
    assert item.bar.close == 3700 and item.bar.adj_close == 3600
    assert item.bar.volume == 23062
    assert len(client.calls) == 2
    assert all(timeout == (3, 15) for _, timeout in client.calls)
    assert item.evidence["regular"]["request"]["bld"].endswith("MDCSTAT01701")
    assert item.evidence["regular"]["observed_at"] == NOW.isoformat()
    assert len(item.evidence["regular"]["sha256"]) == 64


def test_no_trade_is_distinct_and_does_not_request_adjustment():
    client = Client(raw=[row(TDD_OPNPRC="0", TDD_HGPRC="0", TDD_LWPRC="0", ACC_TRDVOL="0")])
    result = collect(client)
    assert result.items[0].status == "no_trade"
    assert result.items[0].bar is None
    assert result.quality()["no_trade_count"] == 1
    assert len(client.calls) == 1


def test_positive_flat_zero_volume_is_valid():
    result = collect(Client(raw=[row(TDD_OPNPRC="3,700", TDD_HGPRC="3,700",
                                     TDD_LWPRC="3,700", ACC_TRDVOL="0")]))
    assert result.items[0].status == "valid"


@pytest.mark.parametrize("change", [{"TDD_CLSPRC": "3,830"}, {"TDD_OPNPRC": "0"},
    {"TDD_CLSPRC": "NaN"}, {"ACC_TRDVOL": "1.5"}, {"TDD_CLSPRC": "-"},
    {"TRD_DD": "2026/10/29"}, {"TDD_HGPRC": None}])
def test_invalid_regular_response_never_produces_bar(change):
    result = collect(Client(raw=[row(**change)]))
    assert result.items[0].status == "failed"
    assert result.items[0].bar is None


@pytest.mark.parametrize("raw", [[], [row(), row()]])
def test_empty_or_duplicate_day_is_failed(raw):
    assert collect(Client(raw=raw)).items[0].status == "failed"


def test_adjustment_failure_preserves_regular_bar_without_fabricating_adjusted_close():
    item = collect(Client(adjusted=[])).items[0]
    assert item.status == "adjustment_missing"
    assert item.bar.close == 3700 and item.bar.adj_close is None


def test_request_budget_marks_unprocessed_items():
    result = collect(Client(), max_requests=1)
    assert result.requests == 1
    assert result.items[0].status == "adjustment_missing"
    assert result.quality()["status"] == "partial"


def test_transient_failure_is_retried_once_only():
    client = Client(raw=requests.Timeout("sensitive upstream body"))
    result = collect(client)
    assert len(client.calls) == 2
    assert "sensitive" not in str(result.items[0].as_dict())


def test_identity_missing_and_noncurrent_date_do_not_fetch():
    client = Client()
    source = NormalizedKRXSource(client, interval=0, utc_now=lambda: NOW)
    result = source.collect(DAY, {"00AB10": None})
    assert result.items[0].reason == "identity_unavailable"
    assert client.calls == []
    result = source.collect(DAY - dt.timedelta(days=1), {"004840": "KR7004840004"})
    assert result.error == "current_completed_session_required"
    assert client.calls == []


def test_request_callback_charges_before_network_and_resume_skips_finished_symbols():
    calls = []
    client = Client()
    original = client.request

    def request(*args, **kwargs):
        calls.append("network")
        return original(*args, **kwargs)

    client.request = request
    source = NormalizedKRXSource(client, interval=0, utc_now=lambda: NOW)
    result = source.collect(DAY, {"004840": "KR7004840004", "00AB10": None},
                            skip_tickers={"00AB10"}, initial_requests=7,
                            on_request=lambda count: calls.append(count))
    assert calls == [8, "network", 9, "network"]
    assert result.requests == 9 and len(result.items) == 1


def test_authentication_failure_stops_other_symbols_without_retry():
    from maps.common.exceptions import DataCollectionError
    client = Client(raw=DataCollectionError("authentication_unavailable"))
    source = NormalizedKRXSource(client, interval=0, utc_now=lambda: NOW)
    result = source.collect(DAY, {"004840": "KR7004840004", "005930": "KR7005930003"})
    assert len(client.calls) == 1
    assert result.error == "authentication_unavailable"
    assert all(i.status == "unprocessed" for i in result.items)


def test_discovery_requires_both_markets_and_explicit_identity():
    class Discovery(Client):
        def request(self, params, *, timeout):
            if params["bld"].endswith("MDCSTAT01501"):
                ticker = "004840" if params["mktId"] == "STK" else "00AB10"
                return {"OutBlock_1": [{"ISU_SRT_CD": ticker}]}
            if params["bld"].endswith("MDCSTAT01901"):
                return {"OutBlock_1": [{"ISU_SRT_CD": "004840", "ISU_CD": "KR7004840004"}]}
            return super().request(params, timeout=timeout)

    frozen = []
    checkpoints = []
    result = NormalizedKRXSource(Discovery(), interval=0, utc_now=lambda: NOW).collect(
        DAY, on_universe=lambda mapping: (checkpoints.append("universe"), frozen.append(mapping)),
        on_universe_evidence=lambda evidence: checkpoints.append(len(evidence)))
    assert frozen == [{"004840": "KR7004840004", "00AB10": None}]
    assert checkpoints == [3, "universe"]
    assert result.market_complete and result.requests == 5
    assert result.items[-1].reason == "identity_unavailable"


def test_empty_market_is_not_successful_empty_collection():
    class Empty:
        def request(self, params, *, timeout):
            return {"OutBlock_1": []}

    result = NormalizedKRXSource(Empty(), interval=0, utc_now=lambda: NOW).collect(DAY)
    assert not result.market_complete
    assert result.error == "market_membership_unavailable"
    assert result.requests == 1


def test_pacing_and_elapsed_budget_do_not_start_late_request():
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    client = Client()
    source = NormalizedKRXSource(client, interval=1, utc_now=lambda: NOW,
                                 monotonic=lambda: clock[0], sleep=sleep)
    result = source.collect(DAY, {"004840": "KR7004840004"}, remaining_seconds=.5)
    assert len(client.calls) == 1 and result.error == "request_or_time_budget_exhausted"
    assert sleeps == []


def test_identity_contradiction_in_response_is_rejected():
    result = collect(Client(raw=[row(ISU_CD="KR7005930003")]))
    assert result.items[0].reason == "identity_mismatch"


def test_current_day_cannot_be_collected_before_close():
    client = Client()
    source = NormalizedKRXSource(client, utc_now=lambda: NOW.replace(hour=5))
    assert source.collect(DAY, {"004840": "KR7004840004"}).error == "current_completed_session_required"
    assert not client.calls


def test_production_client_uses_guarded_session_and_explicit_timeout(monkeypatch):
    import maps.data.ohlcv_source as module
    from pykrx.website.comm import webio
    calls = []

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"output": [row()], "secret": "must not escape"}

    class Auth:
        def __init__(self):
            self.session = self
        def get_headers(self):
            return {"Cookie": "private"}
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(module, "ensure_krx_login_guard", lambda: calls.append("guard"))
    monkeypatch.setattr(module, "get_breaker", lambda: type("Breaker", (), {"status": lambda self: {"blocked": False}})())
    monkeypatch.setattr(webio, "get_session", lambda: Auth())
    response = module.KRXPriceClient().request({"bld": "test"}, timeout=(3, 15))
    assert calls[0] == "guard"
    assert calls[1][1]["timeout"] == (3, 15)
    assert "secret" not in response


def test_production_client_auth_failure_does_not_retry_or_return_error_body(monkeypatch):
    import maps.data.ohlcv_source as module
    from maps.common.exceptions import DataCollectionError
    monkeypatch.setattr(module, "ensure_krx_login_guard", lambda: None)
    monkeypatch.setattr(module, "get_breaker", lambda: type("Breaker", (), {"status": lambda self: {"blocked": True}})())
    with pytest.raises(DataCollectionError, match="authentication_unavailable"):
        module.KRXPriceClient().request({}, timeout=(3, 15))
