"""KISAdapter unit tests with a fake HTTP session."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maps.common.exceptions import BrokerAdapterError
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import Order, OrderSide, OrderStatus, OrderType
from maps.execution import kis_adapter
from maps.execution.kis_adapter import KISAdapter


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.order_payload = {"rt_cd": "0", "output": {"ODNO": "12345", "ORD_TMD": "091501"}}
        self.balance_payload = {
            "rt_cd": "0",
            "output1": [{
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "10",
                "thdt_buyqty": "10",
                "pchs_avg_pric": "70000",
                "prpr": "71000",
                "evlu_amt": "710000",
            }],
            "output2": [{
                "dnca_tot_amt": "1000000",
                "prvs_rcdl_excc_amt": "300000",
                "scts_evlu_amt": "700000",
                "tot_evlu_amt": "1000000",
            }],
        }
        self.open_orders_payload = {
            "rt_cd": "0",
            "output1": [
                {
                    "odno": "12345",
                    "pdno": "005930",
                    "sll_buy_dvsn_cd": "02",
                    "ord_qty": "10",
                    "rmn_qty": "4",
                    "ord_unpr": "70000",
                    "ord_tmd": "091501",
                }
            ],
        }
        self.fail_next_requests = 0
        self.expire_token_500_next = 0

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if url.endswith("/oauth2/tokenP"):
            return FakeResponse({"access_token": "token", "expires_in": 86400})
        if url.endswith("/uapi/hashkey"):
            return FakeResponse({"HASH": "hash"})
        if url.endswith("/order-cash"):
            return FakeResponse(self.order_payload)
        if url.endswith("/order-rvsecncl"):
            return FakeResponse({"rt_cd": "0", "output": {"ODNO": "12345"}})
        raise AssertionError(f"unexpected POST {url}")

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail_next_requests > 0:
            self.fail_next_requests -= 1
            return FakeResponse({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate"}, status_code=503)
        if url.endswith("/inquire-balance") and self.expire_token_500_next > 0:
            # KIS가 토큰 만료(EGW00123)를 HTTP 200이 아니라 500으로 내려주는 케이스 재현
            self.expire_token_500_next -= 1
            return FakeResponse(
                {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."},
                status_code=500,
            )
        if url.endswith("/inquire-balance"):
            return FakeResponse(self.balance_payload)
        if url.endswith("/inquire-daily-ccld"):
            return FakeResponse(self.open_orders_payload)
        if url.endswith("/order-cash"):
            return FakeResponse(self.order_payload)
        if url.endswith("/order-rvsecncl"):
            return FakeResponse({"rt_cd": "0", "output": {"ODNO": "12345"}})
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def settings(tmp_path: Path) -> MapsSettings:
    kis_adapter._TOKEN_CACHE.clear()
    kis_adapter._BALANCE_CACHE.clear()
    return MapsSettings(
        maps_broker_mode="kis",
        maps_log_dir=str(tmp_path),
        kis_app_key="app-key",
        kis_app_secret="app-secret",
        kis_account_no="12345678-01",
        kis_real_trading=False,
        kis_paper_base_url="https://paper.example",
    )


def test_token_is_shared_across_adapter_instances(settings: MapsSettings) -> None:
    http1 = FakeSession()
    broker1 = KISAdapter(settings, http=http1)
    broker1.get_account_balance()

    http2 = FakeSession()
    broker2 = KISAdapter(settings, http=http2)
    broker2.get_open_orders()

    token_calls_1 = [call for call in http1.calls if call["url"].endswith("/oauth2/tokenP")]
    token_calls_2 = [call for call in http2.calls if call["url"].endswith("/oauth2/tokenP")]
    assert len(token_calls_1) == 1
    assert token_calls_2 == []


def test_token_is_reused_from_file_cache_after_memory_cache_clear(settings: MapsSettings) -> None:
    http1 = FakeSession()
    broker1 = KISAdapter(settings, http=http1)
    broker1.get_account_balance()

    kis_adapter._TOKEN_CACHE.clear()

    http2 = FakeSession()
    broker2 = KISAdapter(settings, http=http2)
    broker2.get_open_orders()

    token_calls_1 = [call for call in http1.calls if call["url"].endswith("/oauth2/tokenP")]
    token_calls_2 = [call for call in http2.calls if call["url"].endswith("/oauth2/tokenP")]
    assert len(token_calls_1) == 1
    assert token_calls_2 == []


def test_place_order_uses_paper_buy_tr_id_and_hashkey(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    result = broker.place_order(
        Order(
            strategy_id="s1",
            ticker="005930",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
        )
    )

    order_call = next(call for call in http.calls if call["url"].endswith("/order-cash"))
    assert order_call["headers"]["tr_id"] == "VTTC0802U"
    assert order_call["headers"]["hashkey"] == "hash"
    assert order_call["json"]["CANO"] == "12345678"
    assert order_call["json"]["ACNT_PRDT_CD"] == "01"
    assert order_call["json"]["ORD_DVSN"] == "01"
    assert order_call["json"]["ORD_UNPR"] == "0"
    assert result.order_id == "12345"
    assert result.status == OrderStatus.PENDING


def test_get_account_balance_and_position(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    balance = broker.get_account_balance()
    position = broker.get_position("005930")

    assert balance.cash == 300_000
    assert balance.positions_value == 700_000
    assert balance.total_value == 1_000_000
    assert position is not None
    assert position.quantity == 10
    assert position.avg_price == 70_000
    assert position.name == "삼성전자"
    assert position.current_price == 71_000
    assert position.market_value == 710_000


def test_balance_data_is_cached_within_ttl(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    broker.get_account_balance()
    broker.get_positions()
    broker.get_position("005930")

    balance_calls = [call for call in http.calls if call["url"].endswith("/inquire-balance")]
    assert len(balance_calls) == 1  # 단기 캐시로 잔고 API는 1회만 호출


def test_balance_cache_is_invalidated_after_order(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    broker.get_account_balance()
    broker.place_order(
        Order(
            strategy_id="s1",
            ticker="005930",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
        )
    )
    broker.get_account_balance()

    balance_calls = [call for call in http.calls if call["url"].endswith("/inquire-balance")]
    assert len(balance_calls) == 2  # 주문 직후 캐시 무효화 → 재조회


def test_get_open_orders(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    orders = broker.get_open_orders()

    assert len(orders) == 1
    assert orders[0].order_id == "12345"
    assert orders[0].ticker == "005930"
    assert orders[0].remaining_quantity == 4


def test_get_same_day_buys(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    buys = broker.get_same_day_buys()

    assert buys["005930"].quantity == 10
    assert buys["005930"].avg_price == 70_000


def test_get_daily_order_results_maps_partial_fill(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    results = broker.get_daily_order_results()

    assert len(results) == 1
    assert results[0].order_id == "12345"
    assert results[0].status == OrderStatus.PARTIALLY_FILLED
    assert results[0].filled_quantity == 6


def test_request_retries_transient_http(settings: MapsSettings) -> None:
    http = FakeSession()
    http.fail_next_requests = 1
    settings = settings.model_copy(update={"maps_order_retry_backoff_seconds": 0.0})
    broker = KISAdapter(settings, http=http)

    balance = broker.get_account_balance()

    assert balance.cash == 300_000
    balance_calls = [call for call in http.calls if call["url"].endswith("/inquire-balance")]
    assert len(balance_calls) == 2


def test_request_refreshes_token_when_expiry_returned_as_http500(
    settings: MapsSettings, caplog: pytest.LogCaptureFixture
) -> None:
    """KIS가 토큰 만료(EGW00123)를 HTTP 500으로 반환해도 토큰을 재발급하고 재시도한다."""
    http = FakeSession()
    http.expire_token_500_next = 1
    settings = settings.model_copy(update={"maps_order_retry_backoff_seconds": 0.0})
    broker = KISAdapter(settings, http=http)

    with caplog.at_level("WARNING"):
        balance = broker.get_account_balance()

    assert balance.cash == 300_000  # 재발급 후 성공적으로 잔고 수신
    assert any("토큰 재발급" in r.getMessage() for r in caplog.records)
    balance_calls = [call for call in http.calls if call["url"].endswith("/inquire-balance")]
    assert len(balance_calls) == 2  # 500(만료) → 재발급 → 재시도


def test_cancel_order_uses_cancel_tr_id(settings: MapsSettings) -> None:
    http = FakeSession()
    broker = KISAdapter(settings, http=http)

    assert broker.cancel_order("12345") is True

    cancel_call = next(call for call in http.calls if call["url"].endswith("/order-rvsecncl"))
    assert cancel_call["headers"]["tr_id"] == "VTTC0803U"
    assert cancel_call["json"]["ORGN_ODNO"] == "12345"
    assert cancel_call["json"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert cancel_call["json"]["QTY_ALL_ORD_YN"] == "Y"


def test_kis_error_is_mapped(settings: MapsSettings) -> None:
    http = FakeSession()
    http.order_payload = {"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "insufficient"}
    broker = KISAdapter(settings, http=http)

    with pytest.raises(BrokerAdapterError) as exc_info:
        broker.place_order(
            Order(
                strategy_id="s1",
                ticker="005930",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=999_999,
            )
        )

    assert "APBK0919" in str(exc_info.value)
    assert "Insufficient" in str(exc_info.value)
