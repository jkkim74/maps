from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from maps.common.exceptions import BrokerAdapterError
from maps.execution.kis_us_adapter import KISUSAdapter, PAPER_URL
from maps.execution.broker_adapter import Order, OrderType, OrderSide, OrderStatus

@pytest.fixture(autouse=True)
def regular_session(monkeypatch):
    monkeypatch.setattr(KISUSAdapter,'is_market_open',lambda self:True)


def settings(**changes):
    values = dict(maps_us_app_key='us-key', maps_us_app_secret='us-secret', maps_us_account_no='12345678', maps_us_account_product_code='01', maps_us_broker_mode='kis_paper', maps_us_base_url=PAPER_URL, maps_kis_timeout=30, maps_kis_connect_timeout=3, maps_kis_read_timeout=8, maps_kis_paper_min_interval_seconds=0, maps_order_retry_attempts=1, maps_order_retry_backoff_seconds=0)
    values.update(changes)
    return SimpleNamespace(**values)


def test_runtime_cleanup_closes_kis_transport():
    from maps.ops.us_trading import _LazyBroker, close_service
    closed = []
    transport = SimpleNamespace(close=lambda: closed.append(True))
    config = settings()
    lazy = _LazyBroker(None, config)
    lazy._broker = KISUSAdapter(config, http=transport)
    close_service(SimpleNamespace(broker=lazy))
    assert closed == [True]


def row(**changes):
    value = dict(odno='0000123', pdno='AAPL', ovrs_excg_cd='NASD', sll_buy_dvsn_cd='02', ft_ord_qty='3', ft_ord_unpr3='100.01', ft_ccld_qty='1', ft_ccld_unpr3='100.00', ft_ccld_amt3='100.00', nccs_qty='2', ord_dt='20261015', ord_tmd='103000', prcs_stat_name='부분체결', rjct_rson='')
    value.update(changes)
    return value


def stub_pages(adapter, pages):
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        payload, adapter._last_tr_cont = pages.pop(0)
        return payload
    adapter._request = request
    return calls


def test_real_host_and_real_mode_rejected():
    for change in (dict(maps_us_broker_mode='kis'), dict(maps_us_base_url='https://openapi.koreainvestment.com:9443')):
        with pytest.raises(BrokerAdapterError):
            KISUSAdapter(settings(**change))


def test_pagination_preserves_exact_id_local_date_and_unknown_costs():
    broker = KISUSAdapter(settings())
    calls = stub_pages(broker, [({'output':[row()], 'ctx_area_fk200':'f', 'ctx_area_nk200':'n'}, 'M'), ({'output':[row(odno='0000124') ]}, 'D')])
    results = broker.get_order_history(date(2026,10,15), date(2026,10,15))
    assert [r.order_id for r in results] == ['20261015:NASD:0000123', '20261015:NASD:0000124']
    assert results[0].status == OrderStatus.PARTIALLY_FILLED
    assert results[0].commission is None and not results[0].costs_complete
    assert results[0].avg_price == Decimal('100.00')
    assert results[0].submitted_at.hour == 14
    assert calls[1][2]['params']['CTX_AREA_NK200'] == 'n'
    assert calls[0][2]['params']['OVRS_EXCG_CD'] == ''


def test_broken_continuation_does_not_return_partial_success():
    broker = KISUSAdapter(settings())
    stub_pages(broker, [({'output':[row()]}, 'M')])
    with pytest.raises(BrokerAdapterError, match='pagination'):
        broker.get_order_history(date(2026,10,15), date(2026,10,15))


def test_unknown_status_is_not_guessed_from_quantities():
    broker = KISUSAdapter(settings())
    stub_pages(broker, [({'output':[row(prcs_stat_name='새상태')]}, 'D')])
    assert broker.get_order_history(date(2026,10,15), date(2026,10,15))[0].status == OrderStatus.UNKNOWN


def test_place_limit_paper_order_with_exact_id_and_no_zero_costs():
    broker = KISUSAdapter(settings())
    calls = stub_pages(broker, [({'output':{'ODNO':'0000011'}}, '')])
    result = broker.place_order(Order('manual', 'AAPL', OrderSide.BUY, OrderType.LIMIT, 2, Decimal('100.01'), exchange='NASD', market='US', currency='USD'))
    assert result.order_id.endswith(':NASD:0000011') and result.commission is None
    assert calls[0][2]['tr_id'] == 'VTTT1002U'
    assert calls[0][2]['idempotent'] is False


def test_quote_without_timestamp_is_explicitly_unknown():
    broker = KISUSAdapter(settings())
    stub_pages(broker, [({'output':{'last':'100.01'}}, '')])
    quote = broker.get_rest_quote('AAPL','NASD')
    assert quote.price == Decimal('100.01')
    assert quote.as_of is None and quote.delayed is None


def test_buying_power_is_price_bound_decimal():
    broker = KISUSAdapter(settings())
    stub_pages(broker, [({'output':{'ovrs_ord_psbl_amt':'125.60','max_ord_psbl_qty':'1'}}, '')])
    power = broker.get_buying_power(Order('manual','AAPL',OrderSide.BUY,OrderType.LIMIT,1,Decimal('100.01'),exchange='NASD',market='US',currency='USD'))
    assert power.amount == Decimal('125.60') and power.price_bound == Decimal('100.01')


def test_transport_rejects_real_tr_id_without_http():
    broker = KISUSAdapter(settings())
    with pytest.raises(BrokerAdapterError, match='TR ID'):
        broker._request('GET','/uapi/overseas-stock/v1/trading/inquire-balance',tr_id='TTTS3012R')


def test_us_tokens_never_read_or_write_domestic_file(tmp_path):
    broker = KISUSAdapter(settings())
    broker._token_cache_file = tmp_path/'domestic.json'
    broker._write_file_cached_token('secret',None)
    assert not broker._token_cache_file.exists()


def test_balance_uses_explicit_usd_cash_and_no_float():
    broker = KISUSAdapter(settings())
    stub_pages(broker, [({'output1':[dict(ovrs_pdno='AAPL',tr_crcy_cd='USD',ovrs_excg_cd='NASD',ovrs_cblc_qty='2',pchs_avg_pric='100.01',now_pric2='101.01',ovrs_stck_evlu_amt='202.02',ord_psbl_qty='2')]},'D'),({'output2':[dict(crcy_cd='USD',frcr_dncl_amt_2='1000.01')]},'D')])
    snapshot = broker.get_execution_snapshot()
    assert snapshot.balance.cash == Decimal('1000.01')
    assert snapshot.balance.positions_value == Decimal('202.02')


def test_cancel_exact_id_acceptance_is_not_confirmation():
    broker = KISUSAdapter(settings())
    calls = stub_pages(broker,[({'output':[row(ord_dt=__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/New_York')).strftime('%Y%m%d'))]},'D'),({'output':{'ODNO':'0000125'}},'')])
    result = broker.cancel_order(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/New_York')).strftime('%Y%m%d')+':NASD:0000123')
    assert result.accepted and not result.confirmed
    assert calls[-1][2]['json']['ORGN_ODNO'] == '0000123'
    assert calls[-1][2]['json']['ORD_QTY'] == '2'


def test_transport_errors_do_not_log_account_or_response_body(caplog):
    import requests
    class HTTP:
        def request(self,*args,**kwargs):
            response = requests.Response()
            response.status_code = 503
            response._content = b'{"rt_cd":"1","msg1":"12345678 us-secret"}'
            return response
    broker = KISUSAdapter(settings(),http=HTTP())
    broker._ensure_token = lambda:'token'
    with pytest.raises(BrokerAdapterError) as error:
        broker._request('GET','/uapi/overseas-stock/v1/trading/inquire-balance',tr_id='VTTS3012R')
    assert '12345678' not in str(error.value)+caplog.text
    assert 'us-secret' not in str(error.value)+caplog.text



def test_realtime_quote_uses_exchange_trade_timestamp(monkeypatch):
    import maps.execution.kis_us_adapter as module
    fields = ['DNASAAPL','2','20261015','20261015','103045','20261015','233045','100','101','99','100.12']+['0']*15
    class Socket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def send(self,value): self.sent = value
        def recv(self,timeout): return '0|HDFSCNT0|001|'+'^'.join(fields)
    monkeypatch.setattr(module,'connect',lambda *args,**kwargs:Socket())
    broker = KISUSAdapter(settings())
    broker.issue_websocket_approval_key = lambda:'approval'
    quote = broker.get_quote('AAPL','NASD')
    assert quote.price == Decimal('100.12')
    assert quote.as_of.isoformat() == '2026-10-15T14:30:45+00:00'
    assert quote.delayed is False


def test_realtime_quote_wrong_symbol_fails_closed(monkeypatch):
    import maps.execution.kis_us_adapter as module
    broker = KISUSAdapter(settings())
    with pytest.raises(BrokerAdapterError):
        broker._parse_trade('0|HDFSCNT0|001|'+'^'.join(['DNASMSFT','2','20261015','20261015','103045','20261015','233045','100','101','99','100.12']+['0']*15),'AAPL','NASD')




def test_submit_timeout_is_unknown_and_never_retried():
    import requests
    from maps.common.exceptions import BrokerOrderUnknownError
    class HTTP:
        calls = 0
        def request(self,*args,**kwargs):
            self.calls += 1
            raise requests.ReadTimeout('12345678 secret in URL')
    http = HTTP()
    broker = KISUSAdapter(settings(),http=http)
    broker._ensure_token=lambda:'token'
    broker._hashkey=lambda body:'hash'
    with pytest.raises(BrokerOrderUnknownError) as error:
        broker.place_order(Order('manual','AAPL',OrderSide.BUY,OrderType.LIMIT,1,Decimal('10.01'),exchange='NASD',market='US',currency='USD'))
    assert http.calls == 1 and '12345678' not in str(error.value)


def test_invalid_order_side_rejected_before_transport():
    broker = KISUSAdapter(settings())
    def no_request(*args,**kwargs): raise AssertionError('invalid side reached broker')
    broker._request=no_request
    with pytest.raises(BrokerAdapterError):
        broker.place_order(Order('manual','AAPL','bad',OrderType.LIMIT,1,Decimal('10.01'),exchange='NASD',market='US',currency='USD'))



def test_same_raw_id_different_days_is_distinct():
    broker = KISUSAdapter(settings())
    stub_pages(broker,[({'output':[row(),row(ord_dt='20261016')]},'D')])
    results = broker.get_order_history(date(2026,10,15),date(2026,10,16))
    assert results[0].order_id != results[1].order_id



def test_prior_day_cancel_cannot_target_reused_current_id():
    broker = KISUSAdapter(settings())
    with pytest.raises(BrokerAdapterError,match='prior-day'):
        broker.cancel_order('20000103:NASD:0000123')


def test_regular_session_rechecked_before_submission(monkeypatch):
    broker = KISUSAdapter(settings())
    monkeypatch.setattr(broker,'is_market_open',lambda:False)
    with pytest.raises(BrokerAdapterError,match='session is closed'):
        broker.place_order(Order('manual','AAPL',OrderSide.BUY,OrderType.LIMIT,1,Decimal('10.01'),exchange='NASD',market='US',currency='USD'))
