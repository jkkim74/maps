"""Korea Investment & Securities Open API broker adapter.

This adapter implements the domestic stock REST flow used by MAPS:
token issuance, hashkey issuance, cash order, cancel, balance, positions,
and open-order lookup.  Keep all KIS endpoint and TR-ID details in this
module so broker-specific drift is easy to isolate.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from maps.common.exceptions import BrokerAdapterError
from maps.common.settings import MapsSettings, get_settings
from maps.market.trading_rules import is_krx_closed_date, krx_tick_size
from maps.execution.broker_adapter import (
    AccountBalance,
    AfterHoursQuote,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PendingOrder,
    Position,
    SameDayBuy,
    raw_broker_order_id,
)

logger = logging.getLogger(__name__)

_KST = dt.timezone(dt.timedelta(hours=9))
_MARKET_OPEN = dt.time(9, 0)
_MARKET_CLOSE = dt.time(15, 30)

_TOKEN_PATH = "/oauth2/tokenP"
_HASHKEY_PATH = "/uapi/hashkey"
_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_VOLUME_RANK_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
_INDEX_TIME_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-timeprice"
_INDEX_TIME_INTERVAL_SECONDS = "60"
_WS_APPROVAL_PATH = "/oauth2/Approval"


def _kst_now_naive() -> dt.datetime:
    """Return current KST in the naïve form used by KIS order timestamps."""
    return dt.datetime.now(_KST).replace(tzinfo=None)
# 시세 조회 TR_ID는 모의/실거래 공통 (FHKST01010100).
_PRICE_TR_ID = "FHKST01010100"
# 🟡 시간외 단일가 주문구분. KIS 문서 대조 + 모의계좌 실주문으로 확정하기 전까지는
# **가정값**이다. 틀리면 주문이 거부되거나 정규장 주문으로 나가므로, 확정 전에는
# AUTOMATIC 모드로 시간외 탈출을 신뢰하지 말 것.
_AFTER_HOURS_ORD_DVSN = "21"
_VOLUME_RANK_TR_ID = "FHPST01710000"
_INDEX_TIME_PRICE_TR_ID = "FHPUP02110200"

_TR_IDS = {
    "paper": {
        "buy": "VTTC0802U",
        "sell": "VTTC0801U",
        "cancel": "VTTC0803U",
        "balance": "VTTC8434R",
        "daily_ccld": "VTTC0081R",
    },
    "real": {
        "buy": "TTTC0802U",
        "sell": "TTTC0801U",
        "cancel": "TTTC0803U",
        "balance": "TTTC8434R",
        "daily_ccld": "TTTC0081R",
    },
}

_KIS_ERROR_HINTS = {
    "APBK0013": "Order quantity or price is invalid.",
    "APBK0919": "Insufficient orderable cash or quantity.",
    "APBK0651": "Account product code or account number is invalid.",
    "EGW00123": "Access token is missing, expired, or invalid.",
    "EGW00201": "API rate limit was exceeded.",
    "EGW00202": "Invalid hashkey for POST body.",
    "90020000": "KIS session expired — token will be refreshed automatically.",
}

# 토큰 만료로 인한 서버측 세션 오류 코드 (자동 재발급 대상)
_TOKEN_EXPIRED_CODES: frozenset[str] = frozenset({"90020000", "EGW00123"})

# 연속조회(tr_cont) 페이지 상한 — 잔고·일별체결은 페이지당 약 20행이므로
# 100페이지(약 2,000행)면 개인 계좌에서는 사실상 무한. 무한루프 방어용.
_MAX_TR_CONT_PAGES = 100

# 응답 헤더 tr_cont 가 이 값이면 다음 페이지가 남아 있다 (D/E/공백 = 마지막)
_TR_CONT_HAS_MORE = frozenset({"F", "M"})


@dataclass
class _TokenCacheEntry:
    access_token: str
    expires_at: dt.datetime


_TOKEN_CACHE: dict[tuple[str, str, str, bool], _TokenCacheEntry] = {}
_TOKEN_CACHE_LOCK = threading.Lock()

# 잔고 응답 단기 캐시 — 화면 한 번 로드에 잔고 API가 여러 번 호출돼
# 모의투자 초당 호출한도(EGW00201)를 넘는 것을 막는다. 주문·취소 직후에는
# 포지션이 바뀌므로 무효화한다. 어댑터가 호출마다 새로 생성되므로 모듈 레벨로 공유한다.
_BALANCE_CACHE_TTL_SEC = 5.0
_BALANCE_CACHE: dict[tuple[str, str, str, bool], tuple[float, dict[str, Any]]] = {}
_BALANCE_CACHE_LOCK = threading.Lock()


@dataclass
class _RequestPaceState:
    """One shared KIS REST lane for an app/account/environment tuple."""

    lock: threading.Lock
    last_request_at: float = float("-inf")


_REQUEST_PACE_STATES: dict[tuple[str, str, str, bool], _RequestPaceState] = {}
_REQUEST_PACE_STATES_LOCK = threading.Lock()


def _quote_is_halted(quote: dict[str, Any]) -> bool:
    """Return whether an inquire-price payload reports a halted or flagged stock.

    KIS spells this differently across endpoints, so every known spelling is
    checked. An unknown payload reads as "not halted", which is the behaviour
    that already exists — this only ever tightens the gate.

    Args:
        quote: ``output`` object from inquire-price.

    Returns:
        ``True`` when the stock is suspended or under a volatility interruption.
    """
    for key in ("temp_stop_yn", "tr_stop_yn", "trht_yn"):
        if str(quote.get(key) or "").strip().upper() == "Y":
            return True
    # 종목상태구분코드: 정상(00) 외에는 관리·경고·정지 등 비정상이다.
    status = str(
        quote.get("iscd_stat_cls_code") or quote.get("iscd_stat_cls_cd") or ""
    ).strip()
    return bool(status) and status not in {"00", "55"}


class KISAdapter(BrokerAdapter):
    """KIS domestic-stock REST broker adapter."""

    def __init__(
        self,
        settings: MapsSettings | None = None,
        *,
        http: requests.Session | None = None,
        timeout: float | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._app_key = self._settings.kis_app_key
        self._app_secret = self._settings.kis_app_secret
        self._account_no = self._settings.kis_account_no
        self._account_prefix = self._settings.kis_account_prefix
        self._account_product_code = self._settings.kis_account_product_code
        self._real = self._settings.kis_real_trading
        self._base_url = self._settings.kis_base_url.rstrip("/")
        self._http = http or requests.Session()
        # timeout: 인자 > .env MAPS_KIS_TIMEOUT > 기본값 30s
        # (기존 기본값 10s는 KIS 모의서버 지연 시 자주 timeout 유발)
        self._timeout = timeout if timeout is not None else self._settings.maps_kis_timeout
        self._token_cache_key = (self._base_url, self._app_key, self._account_no, self._real)
        self._token_cache_file = Path(self._settings.maps_log_dir) / ".kis_token_cache.json"
        # 직전 _request 응답의 tr_cont 헤더 — _fetch_paged 의 연속조회 판정용
        self._last_tr_cont = ""

        missing = [
            name
            for name, value in {
                "KIS_APP_KEY": self._app_key,
                "KIS_APP_SECRET": self._app_secret,
                "KIS_ACCOUNT_NO": self._account_no,
            }.items()
            if not value
        ]
        if missing:
            raise BrokerAdapterError(f"KIS required settings are missing: {', '.join(missing)}")
        if not self._account_prefix or not self._account_product_code:
            raise BrokerAdapterError("KIS_ACCOUNT_NO must look like '12345678-01'.")

        mode = "real" if self._real else "paper"
        logger.info("KISAdapter initialized [%s], account=%s", mode, self._account_no)

    def _pace_request(self) -> None:
        """Serialize KIS REST calls at the venue's paper/production interval."""
        with _REQUEST_PACE_STATES_LOCK:
            state = _REQUEST_PACE_STATES.setdefault(
                self._token_cache_key,
                _RequestPaceState(lock=threading.Lock()),
            )
        interval = 0.05 if self._real else 0.5
        scheduled_at: float
        with state.lock:
            now = time.monotonic()
            scheduled_at = max(now, state.last_request_at + interval)
            state.last_request_at = scheduled_at
        remaining = scheduled_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def place_order(self, order: Order) -> OrderResult:
        """Submit a domestic cash stock order through KIS."""
        tr_id = self._tr_id("buy" if order.side == OrderSide.BUY else "sell")
        order_price = self._resolve_order_price(order)
        body = {
            "CANO": self._account_prefix,
            "ACNT_PRDT_CD": self._account_product_code,
            "PDNO": order.ticker,
            "ORD_DVSN": self._order_division(order),
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(order_price),
        }
        data = self._request("POST", _ORDER_PATH, tr_id=tr_id, json=body, hash_body=body)
        self._invalidate_balance_cache()
        output = self._output(data)
        order_id = str(output.get("ODNO") or output.get("odno") or "")
        submitted = self._parse_kis_datetime(
            output.get("ORD_TMD") or output.get("ord_tmd"),
            fallback=_kst_now_naive(),
        )

        return OrderResult(
            order_id=order_id,
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
            filled_quantity=0,
            avg_price=0.0,
            commission=0.0,
            submitted_at=submitted,
            filled_at=None,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open KIS domestic-stock order.

        KIS requires the original order number.  Some accounts also require
        the exchange-forwarding organization number; " " is accepted by the
        common domestic stock flow when that value is not supplied.
        """
        body = {
            "CANO": self._account_prefix,
            "ACNT_PRDT_CD": self._account_product_code,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": raw_broker_order_id(order_id),
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        self._request("POST", _CANCEL_PATH, tr_id=self._tr_id("cancel"), json=body, hash_body=body)
        self._invalidate_balance_cache()
        return True

    def place_opening_auction_sell(
        self, *, ticker: str, quantity: int, strategy_id: str
    ) -> OrderResult:
        """Submit an explicit 01 market sell for the 08:30-09:00 call auction."""
        if quantity <= 0:
            raise BrokerAdapterError("Opening-auction sell quantity must be positive.")
        return self.place_order(
            Order(
                strategy_id=strategy_id,
                ticker=ticker,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )
        )

    def get_after_hours_quote(self, ticker: str) -> AfterHoursQuote:
        """Return the after-hours single-price quote for one ticker.

        Uses the already-proven inquire-price call rather than guessing at a
        separate after-hours TR id, and returns price and cumulative volume from
        that single response so the volume gate qualifies the same sample.

        🟡 Whether ``stck_prpr``/``acml_vol`` reflect the after-hours session
        during 16:00-18:00 is unverified against the live venue. The caller's
        volume gate compares against the *previous* round rather than testing
        for zero, so a whole-day counter still behaves correctly.
        """
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        data = self._request("GET", _PRICE_PATH, tr_id=_PRICE_TR_ID, params=params)
        output = data.get("output") or {}
        return AfterHoursQuote(
            price=self._to_int(output.get("stck_prpr")),
            cumulative_volume=self._to_int(output.get("acml_vol")),
        )

    def issue_websocket_approval_key(self) -> str:
        """Issue the KIS approval key required by real-time subscriptions."""
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._app_secret,
        }
        try:
            self._pace_request()
            response = self._http.post(
                f"{self._base_url}{_WS_APPROVAL_PATH}",
                json=body,
                timeout=self._timeout,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BrokerAdapterError("KIS websocket approval request failed") from exc
        approval_key = str(data.get("approval_key") or "")
        if response.status_code >= 400 or not approval_key:
            raise BrokerAdapterError(
                f"KIS websocket approval failed: {data.get('msg1') or response.text}"
            )
        return approval_key

    def get_kosdaq_index(self) -> float:
        """Return the current KOSDAQ composite (official index code 1001)."""
        data = self._request(
            "GET",
            _INDEX_TIME_PRICE_PATH,
            tr_id=_INDEX_TIME_PRICE_TR_ID,
            params={
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": "1001",
                "FID_INPUT_HOUR_1": _INDEX_TIME_INTERVAL_SECONDS,
            },
        )
        rows = self._as_list(data.get("output"))
        if not rows:
            raise BrokerAdapterError("KIS KOSDAQ index response was empty")
        value = self._to_float(
            rows[0].get("bstp_nmix_prpr")
            or rows[0].get("bstp_nmix_prpr_1")
            or rows[0].get("value")
        )
        if value <= 0:
            raise BrokerAdapterError("KIS KOSDAQ index response had no current value")
        return value

    def get_limit_up_candidates(self) -> list[dict[str, Any]]:
        """Return +25% KRX volume-rank rows enriched with broker quote facts."""
        rank_data = self._request(
            "GET",
            _VOLUME_RANK_PATH,
            tr_id=_VOLUME_RANK_TR_ID,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_INPUT_CNT_1": "0",
                "FID_PRC_CLS_CODE": "0",
                "FID_INPUT_PRICE_3": "",
                "FID_INPUT_PRICE_4": "",
            },
        )
        candidates: list[dict[str, Any]] = []
        for ranked in self._as_list(rank_data.get("output")):
            ticker = str(
                ranked.get("mksc_shrn_iscd")
                or ranked.get("stck_shrn_iscd")
                or ranked.get("code")
                or ""
            )
            if not ticker or self._to_float(ranked.get("prdy_ctrt") or ranked.get("chgrate")) < 25.0:
                continue
            quote_data = self._request(
                "GET",
                _PRICE_PATH,
                tr_id=_PRICE_TR_ID,
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            )
            quote = quote_data.get("output") or {}
            upper = self._to_int(quote.get("stck_mxpr") or quote.get("uplmtprice"))
            listed = self._to_int(quote.get("lstn_stcn"))
            current = self._to_int(quote.get("stck_prpr") or quote.get("price"))
            if upper <= 0 or listed <= 0 or current <= 0:
                continue
            market_name = str(
                quote.get("rprs_mrkt_kor_name")
                or ranked.get("rprs_mrkt_kor_name")
                or ""
            ).upper()
            market = "KOSDAQ" if "KOSDAQ" in market_name or "코스닥" in market_name else "KOSPI"
            candidates.append(
                {
                    "ticker": ticker,
                    "market": market,
                    "current_price": current,
                    "change_rate": self._to_float(quote.get("prdy_ctrt") or ranked.get("prdy_ctrt")),
                    "cumulative_turnover_krw": self._to_int(
                        quote.get("acml_tr_pbmn") or ranked.get("acml_tr_pbmn")
                    ),
                    "execution_strength": self._to_float(
                        quote.get("cttr") or ranked.get("cttr")
                    ),
                    "upper_limit_price": upper,
                    "total_listed_shares": listed,
                    # 거래정지·VI 여부는 이미 이 inquire-price 응답에 들어 있다.
                    # 담지 않으면 스캐너의 정지 게이트가 늘 False 를 읽는 죽은 코드가 된다.
                    "trading_halted": _quote_is_halted(quote),
                }
            )
        return candidates

    def get_position(self, ticker: str) -> Position | None:
        positions, _balance = self._fetch_positions_and_balance()
        return positions.get(ticker)

    def get_account_balance(self) -> AccountBalance:
        _positions, balance = self._fetch_positions_and_balance()
        return balance

    def get_open_orders(self) -> list[PendingOrder]:
        """Return same-day orders with remaining quantity greater than zero."""
        rows = self._fetch_daily_order_rows(ccld_dvsn="02")
        pending: list[PendingOrder] = []
        for row in rows:
            order_qty = self._row_order_qty(row)
            remaining = self._row_remaining_qty(row)
            if remaining <= 0:
                continue
            pending.append(
                PendingOrder(
                    order_id=self._row_order_id(row),
                    ticker=self._row_ticker(row),
                    side=self._row_side(row),
                    quantity=order_qty,
                    remaining_quantity=remaining,
                    order_price=self._to_float_or_none(row.get("ord_unpr")),
                    submitted_at=self._parse_kis_datetime(row.get("ord_tmd"), fallback=None),
                    raw=row,
                )
            )
        return pending

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return same-day KIS order/fill states, including partial fills."""
        results: list[OrderResult] = []
        for row in self._fetch_daily_order_rows():
            order_qty = self._row_order_qty(row)
            filled_qty = self._row_filled_qty(row)
            remaining = self._row_remaining_qty(row)
            status = self._row_status(row, order_qty=order_qty, filled_qty=filled_qty, remaining=remaining)
            submitted_at = self._parse_kis_datetime(row.get("ord_tmd"), fallback=_kst_now_naive())
            results.append(
                OrderResult(
                    order_id=self._row_order_id(row),
                    strategy_id="",
                    ticker=self._row_ticker(row),
                    side=self._row_side(row),
                    status=status,
                    filled_quantity=filled_qty,
                    avg_price=(
                        self._to_float(row.get("avg_prvs") or row.get("avg_pric") or row.get("ccld_avg_prvs"))
                        if filled_qty > 0 else 0.0
                    ),
                    commission=0.0,
                    submitted_at=submitted_at or _kst_now_naive(),
                    filled_at=submitted_at if filled_qty > 0 else None,
                )
            )
        return results

    def _fetch_daily_order_rows(self, *, ccld_dvsn: str = "00") -> list[dict[str, Any]]:
        # ccld_dvsn: "00"=전체, "01"=체결, "02"=미체결
        params = {
            "CANO": self._account_prefix,
            "ACNT_PRDT_CD": self._account_product_code,
            "INQR_STRT_DT": dt.datetime.now(_KST).strftime("%Y%m%d"),
            "INQR_END_DT": dt.datetime.now(_KST).strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": ccld_dvsn,
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._fetch_paged(_DAILY_CCLD_PATH, tr_id=self._tr_id("daily_ccld"), params=params)
        return self._as_list(data.get("output1"))

    def get_positions(self) -> dict[str, int]:
        positions, _balance = self._fetch_positions_and_balance()
        return {ticker: pos.quantity for ticker, pos in positions.items()}

    def get_position_details(self) -> dict[str, Position]:
        positions, _balance = self._fetch_positions_and_balance()
        return positions

    def get_same_day_buys(self) -> dict[str, SameDayBuy]:
        """Return quantities bought today from the KIS balance response."""
        data = self._fetch_balance_data()
        buys: dict[str, SameDayBuy] = {}
        for row in self._as_list(data.get("output1")):
            ticker = str(row.get("pdno") or "")
            quantity = self._to_int(row.get("thdt_buyqty") or 0)
            if not ticker or quantity <= 0:
                continue
            holding_qty = self._to_int(row.get("hldg_qty") or 0)
            buys[ticker] = SameDayBuy(
                ticker=ticker,
                quantity=quantity,
                avg_price=(
                    self._to_float(row.get("pchs_avg_pric") or row.get("avg_prvs"))
                    if holding_qty == quantity else None
                ),
            )
        return buys

    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """종목별 실시간 현재가를 조회한다(미보유 포함). 실패한 종목은 결과에서 빠진다.

        KIS inquire-price(FHKST01010100)를 종목당 1회 호출한다. 보유 종목만 시세를 주는
        잔고 조회와 달리 임의 종목의 현재가(stck_prpr)를 얻을 수 있다. 개별 조회 실패는
        로깅 후 건너뛰어(상위에서 일봉 종가 폴백) 화면 조회가 죽지 않게 한다.
        """
        prices: dict[str, float] = {}
        for ticker in {t for t in tickers if t}:
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
            try:
                data = self._request("GET", _PRICE_PATH, tr_id=_PRICE_TR_ID, params=params)
            except (BrokerAdapterError, requests.RequestException) as exc:
                logger.warning("KIS 현재가 조회 실패 [%s]: %s", ticker, exc)
                continue
            output = data.get("output") or {}
            price = self._to_float(output.get("stck_prpr"))
            if price > 0:
                prices[ticker] = price
        return prices

    def is_market_open(self) -> bool:
        now = dt.datetime.now(_KST)
        # 주말 + 한국 공휴일(holidays.KR) + 고정 휴장일을 모두 반영한다.
        if is_krx_closed_date(now.date()):
            return False
        return _MARKET_OPEN <= now.time() <= _MARKET_CLOSE

    def _fetch_positions_and_balance(self) -> tuple[dict[str, Position], AccountBalance]:
        data = self._fetch_balance_data()
        positions: dict[str, Position] = {}
        for row in self._as_list(data.get("output1")):
            qty = self._to_int(row.get("hldg_qty") or row.get("ord_psbl_qty") or 0)
            ticker = str(row.get("pdno") or "")
            if not ticker or qty <= 0:
                continue
            positions[ticker] = Position(
                ticker=ticker,
                quantity=qty,
                avg_price=self._to_float(row.get("pchs_avg_pric") or row.get("avg_prvs")),
                name=str(row.get("prdt_name") or ""),
                current_price=self._to_float(row.get("prpr") or row.get("stck_prpr")),
                evaluation_value=self._to_float(row.get("evlu_amt")) or None,
            )

        summary = self._first(data.get("output2"))
        cash = self._to_float(
            summary.get("prvs_rcdl_excc_amt")
            or summary.get("dnca_tot_amt")
            or summary.get("nass_amt")
        )
        positions_value = self._to_float(
            summary.get("scts_evlu_amt")
            or summary.get("evlu_amt_smtl_amt")
            or sum(p.market_value for p in positions.values())
        )
        total_assets = self._to_float(
            summary.get("tot_evlu_amt")
            or summary.get("nass_amt")
            or cash + positions_value
        )
        return positions, AccountBalance(
            cash=cash,
            positions_value=positions_value,
            total_assets=total_assets,
        )

    def _fetch_balance_data(self) -> dict[str, Any]:
        now = time.monotonic()
        with _BALANCE_CACHE_LOCK:
            cached = _BALANCE_CACHE.get(self._token_cache_key)
            if cached and now - cached[0] < _BALANCE_CACHE_TTL_SEC:
                return cached[1]
        params = {
            "CANO": self._account_prefix,
            "ACNT_PRDT_CD": self._account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._fetch_paged(_BALANCE_PATH, tr_id=self._tr_id("balance"), params=params)
        with _BALANCE_CACHE_LOCK:
            _BALANCE_CACHE[self._token_cache_key] = (time.monotonic(), data)
        return data

    def _fetch_paged(
        self,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any],
        list_key: str = "output1",
    ) -> dict[str, Any]:
        """KIS 연속조회(tr_cont)를 따라가 전체 페이지의 `list_key` 행을 병합해 반환한다.

        잔고·일별체결은 페이지당 약 20행만 내려온다. 응답 헤더 `tr_cont`가 F/M이면
        body의 `ctx_area_fk100/nk100`을 다음 요청 파라미터에 싣고 요청 헤더
        `tr_cont: N`으로 재호출한다. 반환값은 첫 페이지 payload에 이후 페이지의
        `list_key` 행을 이어 붙인 것 (output2 등 요약 필드는 첫 페이지 값 유지).

        연속조회가 실제로 발동한 경우(2페이지 이상)에만 INFO를 남긴다. 1페이지로 끝나는
        평상시(broker_sync 60초 간격 = 거래일 ~1,400회)에는 로그가 늘지 않으므로, 이
        INFO 한 줄이 "페이지네이션이 실제로 동작했다"는 유일한 증거가 된다.
        """
        params = dict(params)
        merged: dict[str, Any] | None = None
        tr_cont = ""
        for page in range(1, _MAX_TR_CONT_PAGES + 1):
            data = self._request("GET", path, tr_id=tr_id, params=params, tr_cont=tr_cont)
            if merged is None:
                merged = data
            else:
                merged[list_key] = self._as_list(merged.get(list_key)) + self._as_list(
                    data.get(list_key)
                )
            if self._last_tr_cont not in _TR_CONT_HAS_MORE:
                if page > 1:
                    logger.info(
                        "KIS 연속조회 %d페이지 병합: %s (%s %d행)",
                        page, path, list_key, len(self._as_list(merged.get(list_key))),
                    )
                return merged
            params["CTX_AREA_FK100"] = str(data.get("ctx_area_fk100") or "")
            params["CTX_AREA_NK100"] = str(data.get("ctx_area_nk100") or "")
            tr_cont = "N"
        logger.warning("KIS 연속조회가 %d페이지 상한에 도달: %s", _MAX_TR_CONT_PAGES, path)
        return merged if merged is not None else {}

    def _invalidate_balance_cache(self) -> None:
        """주문·취소로 포지션이 바뀐 뒤 오래된 잔고 캐시를 제거한다."""
        with _BALANCE_CACHE_LOCK:
            _BALANCE_CACHE.pop(self._token_cache_key, None)

    def _ensure_token(self) -> str:
        now = dt.datetime.now(dt.timezone.utc)
        cached = self._get_cached_token(now)
        if cached:
            return cached

        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            self._pace_request()
            response = self._http.post(
                self._url(_TOKEN_PATH),
                json=body,
                headers={"content-type": "application/json; charset=utf-8"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            # KIS 모의투자(VTS) 서버는 주말·야간에 접속을 거부한다. 원시 requests 예외가
            # 새어나가면 상위의 BrokerAdapterError 폴백 처리가 작동하지 못하므로 래핑한다.
            raise BrokerAdapterError(f"KIS token request failed: {exc}") from exc
        payload = self._decode_response(response)
        token = payload.get("access_token")
        if not token:
            self._raise_api_error(payload, "KIS token issuance failed")
        expires_in = self._to_int(payload.get("expires_in") or 86400)
        access_token = str(token)
        expires_at = now + dt.timedelta(seconds=max(expires_in - 60, 60))
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE[self._token_cache_key] = _TokenCacheEntry(access_token, expires_at)
            self._write_file_cached_token(access_token, expires_at)
        return access_token

    def _get_cached_token(self, now: dt.datetime) -> str | None:
        with _TOKEN_CACHE_LOCK:
            entry = _TOKEN_CACHE.get(self._token_cache_key)
            if entry and now < entry.expires_at:
                return entry.access_token
            if entry:
                _TOKEN_CACHE.pop(self._token_cache_key, None)
            file_entry = self._read_file_cached_token(now)
            if file_entry:
                _TOKEN_CACHE[self._token_cache_key] = file_entry
                return file_entry.access_token
        return None

    def _token_file_key(self) -> str:
        raw = "|".join(map(str, self._token_cache_key))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_file_cached_token(self, now: dt.datetime) -> _TokenCacheEntry | None:
        try:
            if not self._token_cache_file.exists():
                return None
            payload = json.loads(self._token_cache_file.read_text(encoding="utf-8"))
            item = payload.get(self._token_file_key()) if isinstance(payload, dict) else None
            if not isinstance(item, dict):
                return None
            token = item.get("access_token")
            expires_at_text = item.get("expires_at")
            if not token or not expires_at_text:
                return None
            expires_at = dt.datetime.fromisoformat(str(expires_at_text))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
            if now >= expires_at:
                return None
            return _TokenCacheEntry(str(token), expires_at)
        except Exception as exc:  # pragma: no cover - cache corruption should not block trading ops
            logger.warning("Ignoring unreadable KIS token cache file: %s", exc)
            return None

    def _write_file_cached_token(self, access_token: str, expires_at: dt.datetime) -> None:
        try:
            self._token_cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {}
            if self._token_cache_file.exists():
                existing = json.loads(self._token_cache_file.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing
            payload[self._token_file_key()] = {
                "access_token": access_token,
                "expires_at": expires_at.isoformat(),
            }
            self._token_cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - file cache is best-effort
            logger.warning("Could not write KIS token cache file: %s", exc)

    def _hashkey(self, body: dict[str, Any]) -> str:
        try:
            self._pace_request()
            response = self._http.post(
                self._url(_HASHKEY_PATH),
                json=body,
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise BrokerAdapterError(f"KIS hashkey request failed: {exc}") from exc
        payload = self._decode_response(response)
        hashkey = payload.get("HASH") or payload.get("hash")
        if not hashkey:
            self._raise_api_error(payload, "KIS hashkey issuance failed")
        return str(hashkey)

    def _request(
        self,
        method: str,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        hash_body: dict[str, Any] | None = None,
        tr_cont: str = "",
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont
        if hash_body is not None:
            headers["hashkey"] = self._hashkey(hash_body)

        response = self._send_with_retry(method, path, headers=headers, params=params, json=json)
        self._last_tr_cont = str(response.headers.get("tr_cont") or "").strip().upper()
        payload = self._decode_response(response)
        rt_cd = str(payload.get("rt_cd", "0"))
        if rt_cd not in {"0", ""}:
            code = str(payload.get("msg_cd") or payload.get("error_code") or "")
            # 토큰 만료(90020000 / EGW00123): 캐시 무효화 후 새 토큰으로 1회 재시도
            if code in _TOKEN_EXPIRED_CODES:
                logger.warning("KIS token expired (%s) — invalidating cache and retrying: %s", code, path)
                self._invalidate_token_cache()
                headers["authorization"] = f"Bearer {self._ensure_token()}"
                if hash_body is not None:
                    headers["hashkey"] = self._hashkey(hash_body)
                response = self._send_with_retry(method, path, headers=headers, params=params, json=json)
                self._last_tr_cont = str(response.headers.get("tr_cont") or "").strip().upper()
                payload = self._decode_response(response)
                rt_cd = str(payload.get("rt_cd", "0"))
                if rt_cd not in {"0", ""}:
                    self._raise_api_error(payload, f"KIS API failed after token refresh: {path}")
                return payload
            self._raise_api_error(payload, f"KIS API failed: {path}")
        return payload

    def _invalidate_token_cache(self) -> None:
        """인메모리 및 파일 토큰 캐시를 즉시 무효화한다."""
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE.pop(self._token_cache_key, None)
            try:
                if self._token_cache_file.exists():
                    payload = json.loads(self._token_cache_file.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload.pop(self._token_file_key(), None)
                        self._token_cache_file.write_text(
                            json.dumps(payload), encoding="utf-8"
                        )
            except Exception as exc:
                logger.warning("토큰 캐시 파일 무효화 실패 (무시): %s", exc)

    def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
    ) -> requests.Response:
        attempts = max(1, self._settings.maps_order_retry_attempts)
        backoff = self._settings.maps_order_retry_backoff_seconds
        last_exc: Exception | None = None
        token_refreshed = False
        attempt = 0
        while attempt < attempts:
            attempt += 1
            try:
                self._pace_request()
                response = self._http.request(
                    method,
                    self._url(path),
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=self._timeout,
                )
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                # KIS는 토큰 만료(EGW00123/90020000)를 HTTP 200이 아니라 5xx로 내려주기도 한다.
                # 이 경우 _request의 토큰 재발급 분기(rt_cd 검사)에 도달하지 못하므로, 여기서
                # 본문의 msg_cd를 직접 확인해 토큰을 재발급하고 1회 무료 재시도한다.
                msg_cd = self._peek_msg_cd(response)
                if msg_cd in _TOKEN_EXPIRED_CODES and not token_refreshed:
                    token_refreshed = True
                    attempt -= 1  # 토큰 재발급 재시도는 재시도 횟수에서 제외
                    logger.warning(
                        "KIS 토큰 만료가 HTTP %s로 반환됨 (%s) — 토큰 재발급 후 재시도: %s",
                        response.status_code, msg_cd, path,
                    )
                    self._invalidate_token_cache()
                    headers["authorization"] = f"Bearer {self._ensure_token()}"
                    continue
                # 그 외 429/5xx는 재시도 대상. 대부분(예: EGW00201 초당 호출한도)은 재시도로
                # 자가복구되므로 매 시도는 DEBUG로만 남겨 로그 노이즈를 줄이고, 모든 시도 소진
                # 시에만 WARNING(아래)을 남긴다. KIS가 5xx 본문에 실어 보내는 진단 메시지와
                # 추적키(gt_uid)는 예외 메시지에 보존한다(고객센터 접수·디버깅용).
                gt_uid = response.headers.get("gt_uid", "")
                body_snippet = (response.text or "")[:500]
                logger.debug(
                    "KIS transient HTTP %s: %s (attempt %d/%d) gt_uid=%s body=%s",
                    response.status_code, path, attempt, attempts, gt_uid, body_snippet,
                )
                last_exc = BrokerAdapterError(
                    f"KIS transient HTTP {response.status_code}: {path}"
                    + (f" gt_uid={gt_uid}" if gt_uid else "")
                    + (f" body={body_snippet}" if body_snippet else "")
                )
            except requests.RequestException as exc:
                last_exc = exc
            if attempt < attempts:
                time.sleep(backoff * (2 ** (attempt - 1)))
        # 모든 재시도 소진 = 실제 실패. 이때만 한 번 WARNING으로 남긴다(본문·gt_uid 포함).
        logger.warning("KIS 요청 %d회 재시도 모두 실패: %s", attempts, last_exc)
        if isinstance(last_exc, BrokerAdapterError):
            raise last_exc
        raise BrokerAdapterError(f"KIS request failed after {attempts} attempts: {last_exc}") from last_exc

    @staticmethod
    def _peek_msg_cd(response: requests.Response) -> str:
        """응답 본문에서 KIS 오류코드(msg_cd)를 안전하게 추출한다(파싱 실패 시 빈 문자열)."""
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("msg_cd") or payload.get("error_code") or "")

    def _decode_response(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerAdapterError(
                f"KIS HTTP {response.status_code}: non-JSON response"
            ) from exc
        if response.status_code >= 400:
            self._raise_api_error(payload, f"KIS HTTP {response.status_code}")
        if not isinstance(payload, dict):
            raise BrokerAdapterError(f"KIS returned unexpected payload: {type(payload).__name__}")
        return payload

    def _raise_api_error(self, payload: dict[str, Any], prefix: str) -> None:
        code = str(payload.get("msg_cd") or payload.get("error_code") or "")
        message = str(payload.get("msg1") or payload.get("error_description") or payload)
        hint = _KIS_ERROR_HINTS.get(code)
        suffix = f" ({hint})" if hint else ""
        raise BrokerAdapterError(f"{prefix}: {code} {message}{suffix}".strip())

    def _tr_id(self, key: str) -> str:
        return _TR_IDS["real" if self._real else "paper"][key]

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _order_division(self, order: Order) -> str:
        if order.order_type == OrderType.MARKET:
            return "01"
        if order.order_type == OrderType.LIMIT:
            return "00"
        if order.order_type == OrderType.AFTER_HOURS_SINGLE:
            return _AFTER_HOURS_ORD_DVSN
        raise BrokerAdapterError(f"Unsupported KIS order type: {order.order_type}")

    @staticmethod
    def _tick_size(price: float) -> int:
        """KRX 국내주식 호가단위.

        2023-01-25 개편 반영 공용 규칙(`maps.market.trading_rules.krx_tick_size`)을
        재사용해 백테스트·주문·미리보기 전반에서 단일 기준을 유지한다.
        """
        return krx_tick_size(price)

    def _resolve_order_price(self, order: Order) -> int:
        if order.order_type == OrderType.MARKET:
            return 0
        price = order.limit_price or order.current_price
        if price is None or price <= 0:
            raise BrokerAdapterError("KIS limit orders require limit_price or current_price.")
        tick = self._tick_size(price)
        return int(round(price / tick) * tick)

    @staticmethod
    def _row_order_id(row: dict[str, Any]) -> str:
        return str(row.get("odno") or row.get("ODNO") or "")

    @staticmethod
    def _row_ticker(row: dict[str, Any]) -> str:
        return str(row.get("pdno") or row.get("PDNO") or "")

    @classmethod
    def _row_order_qty(cls, row: dict[str, Any]) -> int:
        return cls._to_int(row.get("ord_qty") or row.get("qty") or 0)

    @classmethod
    def _row_filled_qty(cls, row: dict[str, Any]) -> int:
        filled = cls._to_int(row.get("tot_ccld_qty") or row.get("ccld_qty") or 0)
        if filled == 0:
            order_qty = cls._row_order_qty(row)
            explicit_remaining = cls._to_int(row.get("rmn_qty") or row.get("ord_unprcs_qty"))
            if order_qty > 0 and explicit_remaining > 0:
                return max(order_qty - explicit_remaining, 0)
        return filled

    @classmethod
    def _row_remaining_qty(cls, row: dict[str, Any]) -> int:
        order_qty = cls._row_order_qty(row)
        remaining = cls._to_int(row.get("rmn_qty") or row.get("ord_unprcs_qty"))
        if remaining == 0 and order_qty > 0:
            remaining = max(order_qty - cls._row_filled_qty(row), 0)
        return remaining

    @staticmethod
    def _row_side(row: dict[str, Any]) -> OrderSide:
        side_code = str(row.get("sll_buy_dvsn_cd") or row.get("trad_dvsn_name") or "")
        return OrderSide.SELL if side_code in {"01", "sell", "SELL", "매도"} else OrderSide.BUY

    @staticmethod
    def _row_status(
        row: dict[str, Any],
        *,
        order_qty: int,
        filled_qty: int,
        remaining: int,
    ) -> OrderStatus:
        cancel_flag = str(row.get("cncl_yn") or row.get("rvse_cncl_dvsn_name") or "").upper()
        if cancel_flag in {"Y", "CANCEL", "CANCELLED", "취소"}:
            return OrderStatus.CANCELLED
        if filled_qty > 0 and remaining > 0:
            return OrderStatus.PARTIALLY_FILLED
        if filled_qty > 0 and remaining == 0:
            return OrderStatus.FILLED
        if order_qty > 0 and remaining > 0:
            return OrderStatus.PENDING
        return OrderStatus.REJECTED

    @staticmethod
    def _output(data: dict[str, Any]) -> dict[str, Any]:
        output = data.get("output")
        if isinstance(output, dict):
            return output
        if isinstance(output, list) and output and isinstance(output[0], dict):
            return output[0]
        return {}

    @staticmethod
    def _first(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(float(str(value).replace(",", "").strip() or "0"))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(str(value).replace(",", "").strip() or "0")
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _to_float_or_none(cls, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return cls._to_float(value)

    @staticmethod
    def _parse_kis_datetime(value: Any, *, fallback: dt.datetime | None) -> dt.datetime | None:
        if not value:
            return fallback
        text = str(value).strip()
        for fmt in ("%H%M%S", "%Y%m%d%H%M%S"):
            try:
                parsed = dt.datetime.strptime(text, fmt)
                if fmt == "%H%M%S":
                    today = dt.datetime.now(_KST).date()
                    return dt.datetime.combine(today, parsed.time())
                return parsed
            except ValueError:
                continue
        return fallback
