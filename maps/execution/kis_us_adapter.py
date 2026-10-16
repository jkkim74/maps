"""Dedicated KIS US paper broker; no production hosts or domestic credentials.

Endpoint contracts follow Korea Investment's official open-trading-api examples.
REST current-price has no quote timestamp/delay evidence; execution uses HDFSCNT0.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
import re
import json
import time
from typing import Any

import requests
from websockets.sync.client import connect
from maps.common.exceptions import BrokerAdapterError, BrokerOrderUnknownError
from maps.common.settings import get_settings
from maps.execution.kis_adapter import KISAdapter
from maps.execution.broker_adapter import (Order, OrderResult, OrderStatus, OrderSide, OrderType,
    Position, AccountBalance, PositionSnapshot, BuyingPower, CancelResult, PendingOrder)
from maps.execution.us_contracts import USQuote
from maps.market.us_trading_rules import NEW_YORK, is_us_market_open, validate_us_limit_price

PAPER_URL = 'https://openapivts.koreainvestment.com:29443'
_EXCHANGES = {'NASD':'NAS', 'NYSE':'NYS', 'AMEX':'AMS'}
_PATH = '/uapi/overseas-stock/v1/trading/'
_ALLOWED = {'VTTS3012R','VTTS3035R','VTTS3007R','VTTT1002U','VTTT1001U','VTTT1004U','VTRP6504R','HHDFS00000300'}


class _SafeResponse:
    """Prevent inherited transport diagnostics from echoing account-bearing bodies."""
    def __init__(self, response):
        self.status_code = response.status_code
        self.headers = {key:value for key,value in response.headers.items() if key.lower() != 'gt_uid'}
        self.text = '[US broker response redacted]'
        self._response = response

    def json(self):
        return self._response.json()


class _SafeHTTP:
    """Preserve HTTP behavior while keeping credentials out of diagnostics."""
    def __init__(self, session):
        self._session = session

    def _call(self, method, *args, **kwargs):
        try:
            return _SafeResponse(getattr(self._session,method)(*args,**kwargs))
        except requests.RequestException as exc:
            raise type(exc)('US broker HTTP request failed') from None

    def request(self, *args, **kwargs):
        return self._call('request',*args,**kwargs)

    def post(self, *args, **kwargs):
        return self._call('post',*args,**kwargs)

    def close(self) -> None:
        """Release the wrapped HTTP pool at the end of a request or sync job."""
        self._session.close()


def _decimal(value: Any, label: str) -> Decimal:
    """Require finite nonnegative broker numbers without defaulting missing data."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BrokerAdapterError(f'US broker missing/invalid {label}') from exc
    if not result.is_finite() or result < 0:
        raise BrokerAdapterError(f'US broker invalid {label}')
    return result


def _integer(value: Any, label: str) -> int:
    """Reject fractional quantities instead of truncating them."""
    result = _decimal(value, label)
    if result != result.to_integral_value():
        raise BrokerAdapterError(f'US broker fractional {label}')
    return int(result)


class KISUSAdapter(KISAdapter):
    """Reuse KIS throttling/retry transport with paper-only isolated identity."""

    def __init__(self, settings=None, *, http: requests.Session | None = None) -> None:
        settings = settings or get_settings()
        if settings.maps_us_broker_mode != 'kis_paper' or settings.maps_us_base_url.rstrip('/') != PAPER_URL:
            raise BrokerAdapterError('US broker supports the fixed KIS paper host only')
        self._settings = settings
        self._app_key = settings.maps_us_app_key
        self._app_secret = settings.maps_us_app_secret
        self._account_no = settings.maps_us_account_no
        self._account_prefix = self._account_no.split('-')[0]
        self._account_product_code = settings.maps_us_account_product_code
        if not self._app_key or not self._app_secret or not re.fullmatch(r'\d{8}', self._account_prefix) or not re.fullmatch(r'\d{2}', self._account_product_code):
            raise BrokerAdapterError('Dedicated US paper credentials/account required')
        self._base_url = PAPER_URL
        self._real = False
        self._http = _SafeHTTP(http or requests.Session())
        self._timeout = settings.maps_kis_timeout
        self._token_cache_key = ('US', PAPER_URL, self._app_key, self._account_no, False)
        self._request_pace_key = (PAPER_URL, self._account_no, False)
        self._last_tr_cont = ''

    def _raise_api_error(self, payload, prefix) -> None:
        """Business rejections expose no account-bearing broker message."""
        from maps.common.exceptions import BrokerOrderRejectedError
        raise BrokerOrderRejectedError('US paper broker rejected request')

    def _read_file_cached_token(self, now):
        """US credentials never touch the shared domestic token file."""
        return None

    def _write_file_cached_token(self, access_token, expires_at) -> None:
        """Keep US tokens in memory only."""
        return None

    def _invalidate_token_cache(self) -> None:
        """Invalidate only the isolated in-memory US token."""
        from maps.execution.kis_adapter import _TOKEN_CACHE, _TOKEN_CACHE_LOCK
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE.pop(self._token_cache_key, None)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Refuse inherited domestic or production transaction IDs."""
        if self._base_url != PAPER_URL or kwargs.get('tr_id') not in _ALLOWED:
            raise BrokerAdapterError('US paper endpoint/TR ID rejected')
        if not (path.startswith(_PATH) or path == '/uapi/overseas-price/v1/quotations/price'):
            raise BrokerAdapterError('US paper endpoint rejected')
        return super()._request(method, path, **kwargs)

    def _account_params(self) -> dict[str, str]:
        return {'CANO':self._account_prefix, 'ACNT_PRDT_CD':self._account_product_code}

    @staticmethod
    def _symbol(ticker: str, exchange: str) -> None:
        if exchange not in _EXCHANGES or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', ticker):
            raise BrokerAdapterError('Unsupported US ticker/exchange')

    def _pages(self, endpoint: str, tr_id: str, params: dict, keys: tuple[str,...]) -> dict:
        """Consume every page or fail; repeated/missing cursors never yield success."""
        params = {**self._account_params(), **params}
        cursor = ('','')
        seen = set()
        result = {key:[] for key in keys}
        for page in range(100):
            if endpoint != 'inquire-present-balance':
                params.update(CTX_AREA_FK200=cursor[0], CTX_AREA_NK200=cursor[1])
            payload = self._request('GET', _PATH+endpoint, tr_id=tr_id, params=dict(params), tr_cont='N' if page else '')
            for key in keys:
                if key not in payload or not isinstance(payload[key], (list,dict)):
                    raise BrokerAdapterError('US broker malformed page')
                result[key].extend(payload[key] if isinstance(payload[key],list) else [payload[key]])
            if self._last_tr_cont in ('','D','E'):
                return result
            if self._last_tr_cont not in ('M','F'):
                raise BrokerAdapterError('US pagination unknown continuation')
            cursor = (str(payload.get('ctx_area_fk200') or ''), str(payload.get('ctx_area_nk200') or ''))
            # Present balance has header-only continuation; identical pages are unsafe.
            marker = repr(payload) if endpoint == 'inquire-present-balance' else cursor
            if marker in seen or (endpoint != 'inquire-present-balance' and not any(cursor)):
                raise BrokerAdapterError('US pagination incomplete/repeated cursor')
            seen.add(marker)
        raise BrokerAdapterError('US pagination page limit exceeded')

    def get_rest_quote(self, ticker: str, exchange: str) -> USQuote:
        """Report price with unknown timestamp/delay, never poll-time evidence."""
        self._symbol(ticker, exchange)
        data = self._request('GET','/uapi/overseas-price/v1/quotations/price',tr_id='HHDFS00000300',params={'AUTH':'','EXCD':_EXCHANGES[exchange],'SYMB':ticker})
        return USQuote(ticker, exchange, _decimal(data.get('output',{}).get('last'),'quote price'), None, None)

    def issue_websocket_approval_key(self) -> str:
        """Obtain approval only from the fixed paper authentication host."""
        self._pace_request()
        response = self._http.post(PAPER_URL+'/oauth2/Approval',json={'grant_type':'client_credentials','appkey':self._app_key,'secretkey':self._app_secret},timeout=self._timeout)
        payload = self._decode_response(response)
        if not payload.get('approval_key'):
            raise BrokerAdapterError('US websocket approval unavailable')
        return str(payload['approval_key'])

    def _parse_trade(self, message: str, ticker: str, exchange: str) -> USQuote:
        """Decode HDFSCNT0 LAST and exchange XYMD/XHMS, US zero-minute feed."""
        parts = message.split('|',3)
        if len(parts) != 4 or parts[:3] != ['0','HDFSCNT0','001']:
            raise BrokerAdapterError('US malformed realtime quote')
        fields = parts[3].split('^')
        expected = 'D'+_EXCHANGES[exchange]+ticker
        if len(fields) != 26 or fields[0] != expected:
            raise BrokerAdapterError('US realtime quote identity mismatch')
        try:
            observed = dt.datetime.strptime(fields[3]+fields[4],'%Y%m%d%H%M%S').replace(tzinfo=NEW_YORK).astimezone(dt.timezone.utc)
        except ValueError as exc:
            raise BrokerAdapterError('US invalid realtime quote timestamp') from exc
        price = _decimal(fields[10],'realtime trade price')
        if not price:
            raise BrokerAdapterError('US zero realtime trade price')
        return USQuote(ticker,exchange,price,observed,False)

    def get_quote(self, ticker: str, exchange: str) -> USQuote:
        """Wait at most five seconds for an evidenced US trade; never REST-fallback."""
        self._symbol(ticker,exchange)
        approval = self.issue_websocket_approval_key()
        request = {'header':{'approval_key':approval,'custtype':'P','tr_type':'1','content-type':'utf-8'},'body':{'input':{'tr_id':'HDFSCNT0','tr_key':'D'+_EXCHANGES[exchange]+ticker}}}
        deadline = time.monotonic()+5
        try:
            with connect('ws://ops.koreainvestment.com:31000/tryitout',open_timeout=5,close_timeout=1,proxy=None) as socket:
                socket.send(json.dumps(request))
                for _ in range(20):
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        break
                    message = socket.recv(timeout=remaining)
                    if not isinstance(message,str):
                        raise BrokerAdapterError('US binary quote unsupported')
                    if message.startswith('0|'):
                        return self._parse_trade(message,ticker,exchange)
                    control = json.loads(message)
                    if control.get('header',{}).get('tr_id') == 'PINGPONG':
                        socket.send(message)
                    elif str(control.get('body',{}).get('rt_cd','0')) != '0':
                        raise BrokerAdapterError('US realtime quote subscription rejected')
        except BrokerAdapterError:
            raise
        except Exception:
            raise BrokerAdapterError('US realtime quote unavailable') from None
        raise BrokerAdapterError('US realtime quote timeout')

    def get_buying_power(self, order: Order) -> BuyingPower:
        """Use symbol/limit-price-bound paper buying power, without FX fallback."""
        self._symbol(order.ticker, order.exchange)
        price = validate_us_limit_price(Decimal(str(order.limit_price)))
        payload = self._request('GET',_PATH+'inquire-psamount',tr_id='VTTS3007R',params={**self._account_params(),'OVRS_EXCG_CD':order.exchange,'OVRS_ORD_UNPR':str(price),'ITEM_CD':order.ticker})
        row = payload.get('output',{})
        return BuyingPower(_decimal(row.get('ovrs_ord_psbl_amt'),'buying power'), _integer(row.get('max_ord_psbl_qty'),'buying quantity'),price,dt.datetime.now(dt.timezone.utc))

    def get_execution_snapshot(self) -> PositionSnapshot:
        """Read complete USD positions and USD cash; reject ambiguous currency."""
        data = self._pages('inquire-balance','VTTS3012R',{'OVRS_EXCG_CD':'NASD','TR_CRCY_CD':'USD'},('output1',))
        positions = {}
        for row in data['output1']:
            if row.get('tr_crcy_cd') != 'USD' or row.get('ovrs_excg_cd') not in _EXCHANGES:
                raise BrokerAdapterError('US balance currency/exchange unknown')
            ticker = str(row.get('ovrs_pdno') or '')
            self._symbol(ticker,row['ovrs_excg_cd'])
            if ticker in positions:
                raise BrokerAdapterError('US duplicate position rows require manual review')
            positions[ticker] = Position(ticker,_integer(row.get('ovrs_cblc_qty'),'position quantity'),_decimal(row.get('pchs_avg_pric'),'average price'),current_price=_decimal(row.get('now_pric2'),'position price'),evaluation_value=_decimal(row.get('ovrs_stck_evlu_amt'),'position value'),sellable_quantity=_integer(row.get('ord_psbl_qty'),'sellable quantity'))
        cash_data = self._pages('inquire-present-balance','VTRP6504R',{'WCRC_FRCR_DVSN_CD':'02','NATN_CD':'840','TR_MKET_CD':'00','INQR_DVSN_CD':'01'},('output2',))
        usd = [row for row in cash_data['output2'] if row.get('crcy_cd') == 'USD']
        if len(usd) != 1:
            raise BrokerAdapterError('US USD cash missing/ambiguous')
        cash = _decimal(usd[0].get('frcr_dncl_amt_2'),'USD cash')
        value = sum((p.evaluation_value for p in positions.values()), Decimal(0))
        return PositionSnapshot(positions,AccountBalance(cash,value),dt.datetime.now(dt.timezone.utc))

    def get_position(self, ticker: str) -> Position | None:
        return self.get_execution_snapshot().positions.get(ticker)

    def get_position_details(self) -> dict[str, Position]:
        return self.get_execution_snapshot().positions

    def get_positions(self) -> dict[str,int]:
        return {ticker:p.quantity for ticker,p in self.get_position_details().items()}

    def get_account_balance(self) -> AccountBalance:
        return self.get_execution_snapshot().balance

    def get_position_snapshot(self, max_age_seconds: float) -> PositionSnapshot:
        """Use US USD snapshot only; never inherited domestic balance cache."""
        return self.get_execution_snapshot()

    def is_market_open(self) -> bool:
        return is_us_market_open()

    def place_order(self, order: Order) -> OrderResult:
        """Submit a whole-share USD regular limit order exactly once per transport policy."""
        self._symbol(order.ticker, order.exchange)
        if order.market != 'US' or order.currency != 'USD' or order.side not in (OrderSide.BUY,OrderSide.SELL) or order.order_type != OrderType.LIMIT or type(order.quantity) is not int or order.quantity <= 0:
            raise BrokerAdapterError('US orders require USD whole-share limit contract')
        price = validate_us_limit_price(Decimal(str(order.limit_price)))
        submitted = dt.datetime.now(dt.timezone.utc)
        if not self.is_market_open():
            raise BrokerAdapterError('US regular session is closed')
        body = {**self._account_params(),'OVRS_EXCG_CD':order.exchange,'PDNO':order.ticker,'ORD_QTY':str(order.quantity),'OVRS_ORD_UNPR':str(price),'CTAC_TLNO':'','MGCO_APTM_ODNO':'','SLL_TYPE':'00','ORD_SVR_DVSN_CD':'0','ORD_DVSN':'00'}
        payload = self._request('POST',_PATH+'order',tr_id='VTTT1002U' if order.side == OrderSide.BUY else 'VTTT1001U',json=body,hash_body=body,idempotent=False)
        output = payload.get('output',{})
        order_id = str(output.get('ODNO') or output.get('odno') or '')
        if not order_id:
            raise BrokerOrderUnknownError('US order accepted without exact broker ID')
        scoped_id = f'{submitted.astimezone(NEW_YORK):%Y%m%d}:{order.exchange}:{order_id}'
        return OrderResult(scoped_id,order.strategy_id,order.ticker,order.side,OrderStatus.PENDING,commission=None,submitted_at=submitted,quantity=order.quantity,order_price=price,remaining_quantity=order.quantity,exchange=order.exchange)

    def get_order_history(self, start: dt.date, end: dt.date) -> list[OrderResult]:
        """Query broker local US trading dates, preserving exact IDs and unknown costs."""
        if start > end:
            raise BrokerAdapterError('US invalid history range')
        data = self._pages('inquire-ccnl','VTTS3035R',{'PDNO':'','ORD_STRT_DT':start.strftime('%Y%m%d'),'ORD_END_DT':end.strftime('%Y%m%d'),'SLL_BUY_DVSN':'00','CCLD_NCCS_DVSN':'00','OVRS_EXCG_CD':'','SORT_SQN':'','ORD_DT':'','ORD_GNO_BRNO':'','ODNO':''},('output',))
        result = []
        seen = set()
        for row in data['output']:
            order_id = str(row.get('odno') or '')
            ticker, exchange = str(row.get('pdno') or ''), str(row.get('ovrs_excg_cd') or '')
            self._symbol(ticker,exchange)
            side = {'01':OrderSide.SELL,'02':OrderSide.BUY}.get(row.get('sll_buy_dvsn_cd'))
            if not order_id or side is None:
                raise BrokerAdapterError('US history missing order identity/side')
            try:
                submitted = dt.datetime.strptime(str(row['ord_dt'])+str(row['ord_tmd']), '%Y%m%d%H%M%S').replace(tzinfo=NEW_YORK).astimezone(dt.timezone.utc)
            except (ValueError,KeyError) as exc:
                raise BrokerAdapterError('US history timestamp missing/invalid') from exc
            identity = (row['ord_dt'],order_id)
            if identity in seen or not start <= submitted.astimezone(NEW_YORK).date() <= end:
                raise BrokerAdapterError('US history duplicated/out-of-range order')
            seen.add(identity)
            order_id = f'{row["ord_dt"]}:{exchange}:{order_id}'
            qty = _integer(row.get('ft_ord_qty'),'order quantity')
            filled = _integer(row.get('ft_ccld_qty'),'filled quantity')
            remaining = _integer(row.get('nccs_qty'),'remaining quantity')
            if filled+remaining > qty:
                raise BrokerAdapterError('US inconsistent order quantities')
            status = {'접수':OrderStatus.PENDING,'미체결':OrderStatus.PENDING,'체결':OrderStatus.FILLED,'전량체결':OrderStatus.FILLED,'부분체결':OrderStatus.PARTIALLY_FILLED,'취소':OrderStatus.CANCELLED,'취소완료':OrderStatus.CANCELLED,'거부':OrderStatus.REJECTED}.get(str(row.get('prcs_stat_name') or ''),OrderStatus.UNKNOWN)
            if status == OrderStatus.FILLED and (filled != qty or remaining):
                status = OrderStatus.UNKNOWN
            result.append(OrderResult(order_id,'',ticker,side,status,filled_quantity=filled,avg_price=_decimal(row.get('ft_ccld_unpr3'),'fill price'),commission=None,submitted_at=submitted,quantity=qty,order_price=_decimal(row.get('ft_ord_unpr3'),'order price'),remaining_quantity=remaining,cumulative_gross=_decimal(row.get('ft_ccld_amt3'),'fill gross'),tax=None,costs_complete=False,exchange=exchange))
        return result

    def get_daily_order_results(self) -> list[OrderResult]:
        today = dt.datetime.now(NEW_YORK).date()
        return self.get_order_history(today,today)

    def get_open_orders(self) -> list[PendingOrder]:
        return [PendingOrder(r.order_id,r.ticker,r.side,r.quantity,r.remaining_quantity,r.order_price,r.submitted_at) for r in self.get_daily_order_results() if r.status in (OrderStatus.PENDING,OrderStatus.PARTIALLY_FILLED,OrderStatus.UNKNOWN)]

    def cancel_order(self, order_id: str) -> CancelResult:
        """Cancel exact date/exchange/raw identity; acceptance is not completion."""
        try:
            day_text, exchange, raw_id = order_id.split(':')
            day = dt.datetime.strptime(day_text,'%Y%m%d').date()
            if exchange not in _EXCHANGES or not raw_id:
                raise ValueError('invalid identity')
        except ValueError as exc:
            raise BrokerAdapterError('US cancel requires scoped date/exchange/order ID') from exc
        if day != dt.datetime.now(NEW_YORK).date():
            raise BrokerAdapterError('US cancel cannot safely disambiguate a prior-day broker ID')
        matches = [r for r in self.get_order_history(day,day) if r.order_id == order_id]
        if len(matches) != 1:
            raise BrokerAdapterError('US cancel exact order not found')
        order = matches[0]
        if order.remaining_quantity is None or order.remaining_quantity <= 0 or order.status not in (OrderStatus.PENDING,OrderStatus.PARTIALLY_FILLED):
            raise BrokerAdapterError('US cancel order is not demonstrably open')
        body = {**self._account_params(),'OVRS_EXCG_CD':order.exchange,'PDNO':order.ticker,'ORGN_ODNO':raw_id,'RVSE_CNCL_DVSN_CD':'02','ORD_QTY':str(order.remaining_quantity),'OVRS_ORD_UNPR':'0','MGCO_APTM_ODNO':'','ORD_SVR_DVSN_CD':'0'}
        payload = self._request('POST',_PATH+'order-rvsecncl',tr_id='VTTT1004U',json=body,hash_body=body,idempotent=False)
        output = payload.get('output',{})
        if not (output.get('ODNO') or output.get('odno')):
            raise BrokerOrderUnknownError('US cancellation response lacks broker ID')
        return CancelResult(accepted=True,confirmed=False)
