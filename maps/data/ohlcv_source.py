"""Bounded current-session KRX prices with explicit price basis and source evidence.

Bulk prices are used ONLY for dated membership. Price rows always come from the
per-security endpoint. This module neither writes a database nor fills missing bars.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime as dt
from decimal import Decimal
import hashlib
import json
import math
import re
import time
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

import requests

from maps.common.exceptions import DataCollectionError, DataQualityError
from maps.data.krx_auth import ensure_krx_login_guard, get_breaker

KST = ZoneInfo("Asia/Seoul")
PRICE_BLD = "dbms/MDC/STAT/standard/MDCSTAT01701"
MEMBERS_BLD = "dbms/MDC/STAT/standard/MDCSTAT01501"
BASIC_BLD = "dbms/MDC/STAT/standard/MDCSTAT01901"
URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
FIELDS = {"open": "TDD_OPNPRC", "high": "TDD_HGPRC", "low": "TDD_LWPRC",
          "close": "TDD_CLSPRC", "volume": "ACC_TRDVOL"}


@dataclass
class SourceBar:
    """Regular-session prices, observed total volume and independently read adjustment."""
    date: dt.date
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float | None = None

    @property
    def has_adjusted(self) -> bool:
        """Only an actual adjustment response establishes availability."""
        return self.adj_close is not None


@dataclass
class SourceItem:
    """One terminal symbol observation, including non-price outcomes."""
    ticker: str
    date: dt.date
    status: str
    bar: SourceBar | None = None
    reason: str | None = None
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Return JSON-compatible evidence without credentials or exception bodies."""
        result = asdict(self)
        result["date"] = self.date.isoformat()
        if result["bar"] is not None:
            result["bar"]["date"] = self.date.isoformat()
        return result


@dataclass
class SourceBatch:
    """A bounded attempt, not an assertion of final exchange daily volume."""
    ref_date: dt.date
    items: list[SourceItem] = field(default_factory=list)
    expected: dict[str, str | None] = field(default_factory=dict)
    requests: int = 0
    market_complete: bool = False
    error: str | None = None
    universe_evidence: list[dict] = field(default_factory=list)

    @property
    def bars(self) -> list[SourceBar]:
        """Return only validated regular bars; adjustment may remain unavailable."""
        return [item.bar for item in self.items if item.bar is not None]

    def quality(self) -> dict:
        """Separate expected no-trade observations from failed source observations."""
        counts = {status: sum(item.status == status for item in self.items) for status in
                  ("valid", "no_trade", "failed", "adjustment_missing", "unprocessed")}
        incomplete = self.error or not self.market_complete or any(counts[s] for s in
                     ("failed", "adjustment_missing", "unprocessed"))
        return dict(status="partial" if incomplete else "complete",
                    expected_count=len(self.expected), received_count=len(self.items),
                    saved_count=len(self.bars), rejected_count=counts["failed"],
                    no_trade_count=counts["no_trade"], failed_count=counts["failed"],
                    adjustment_missing_count=counts["adjustment_missing"],
                    unprocessed_count=counts["unprocessed"], request_count=self.requests,
                    market_complete=self.market_complete, error=self.error,
                    price_basis="regular_unadjusted", volume_basis="observed_total",
                    rejected_rows=[dict(ticker=i.ticker, date=i.date.isoformat(), reason=i.reason)
                                   for i in self.items if i.status in ("failed", "unprocessed")])


class KRXPriceClient:
    """Use the guarded authenticated pykrx session with finite per-call timeouts."""

    def request(self, params: dict, *, timeout: tuple[float, float]) -> dict:
        """Read only public price fields; never return auth or HTTP error bodies."""
        ensure_krx_login_guard()
        if get_breaker().status()["blocked"]:
            raise DataCollectionError("authentication_unavailable")
        from pykrx.website.comm.webio import get_session
        authenticated = get_session()
        if authenticated is None:
            raise DataCollectionError("authentication_unavailable")
        response = authenticated.session.post(URL, data=params,
                    headers=authenticated.get_headers(), timeout=timeout)
        if response.status_code in (401, 403):
            raise DataCollectionError("authentication_unavailable")
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise DataCollectionError("invalid_json_response") from exc
        if not isinstance(body, dict):
            raise DataCollectionError("invalid_response_shape")
        if body.get("_error_code") not in (None, "", "CD001"):
            raise DataCollectionError("authentication_or_source_rejected")
        # Only public source rows leave this boundary. Session keys and auth data
        # are neither persisted nor included in evidence/error messages.
        return {key: body[key] for key in ("output", "OutBlock_1") if key in body}


def _number(value: object, *, volume: bool = False) -> float | int:
    """Parse explicit nonnegative decimal source values without dash/NaN coercion."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise DataQualityError("invalid_number")
    exact = Decimal(value.replace(",", ""))
    if volume:
        if exact != exact.to_integral_value() or exact > 2_147_483_647:
            raise DataQualityError("invalid_volume")
        return int(exact)
    result = float(exact)
    if not math.isfinite(result):
        raise DataQualityError("invalid_number")
    return result


def _row(body: dict, day: dt.date, isin: str) -> dict:
    """Require exactly one matching date and reject contradictory returned identities."""
    rows = body.get("output")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise DataQualityError("missing_or_duplicate_day")
    row = rows[0]
    if row.get("TRD_DD") != day.strftime("%Y/%m/%d"):
        raise DataQualityError("date_mismatch")
    if row.get("ISU_CD", isin) != isin:
        raise DataQualityError("identity_mismatch")
    return row


class NormalizedKRXSource:
    """Serial request-budgeted current-day collection with injectable clocks/client."""

    def __init__(self, client=None, *, interval: float | None = None, max_requests: int | None = None,
                 max_seconds: float | None = None, connect_timeout: float | None = None,
                 read_timeout: float | None = None, utc_now: Callable = lambda: dt.datetime.now(dt.timezone.utc),
                 monotonic: Callable = time.monotonic, sleep: Callable = time.sleep):
        """Defaults are operational bounds; tests inject clocks without real sleeping."""
        from maps.common.settings import get_settings
        settings = get_settings()
        interval = settings.maps_ohlcv_request_interval_seconds if interval is None else interval
        max_requests = settings.maps_ohlcv_max_requests if max_requests is None else max_requests
        max_seconds = settings.maps_ohlcv_max_seconds if max_seconds is None else max_seconds
        connect_timeout = settings.maps_ohlcv_connect_timeout if connect_timeout is None else connect_timeout
        read_timeout = settings.maps_ohlcv_read_timeout if read_timeout is None else read_timeout
        if interval < 0 or max_requests < 0 or min(max_seconds, connect_timeout, read_timeout) <= 0:
            raise ValueError("invalid_source_bounds")
        self.client = client if client is not None else KRXPriceClient()
        self.interval, self.max_requests, self.max_seconds = interval, max_requests, max_seconds
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self.utc_now, self.monotonic, self.sleep = utc_now, monotonic, sleep
        self.requests = 0
        self._last_started = None
        self._deadline = 0.0
        self._day = None
        self._on_request = None

    def _request(self, params: dict) -> tuple[dict, dict]:
        """Charge every attempt, retry transient errors once, stop on authentication."""
        for attempt in range(2):
            if self.utc_now().astimezone(KST).date() != self._day:
                raise DataCollectionError("session_date_changed")
            delay = max(0, self.interval - (self.monotonic() - self._last_started)) if self._last_started is not None else 0
            if self.requests >= self.max_requests or self.monotonic() + delay >= self._deadline:
                raise DataCollectionError("request_or_time_budget_exhausted")
            if delay:
                self.sleep(delay)
            remaining = self._deadline - self.monotonic()
            if remaining <= 0:
                raise DataCollectionError("request_or_time_budget_exhausted")
            self._last_started = self.monotonic()
            self.requests += 1
            if self._on_request:
                self._on_request(self.requests)
            try:
                body = self.client.request(params, timeout=(min(self.connect_timeout, remaining), min(self.read_timeout, remaining)))
                if not isinstance(body, dict):
                    raise DataCollectionError("invalid_response_shape")
                if self.monotonic() > self._deadline or self.utc_now().astimezone(KST).date() != self._day:
                    raise DataCollectionError("request_or_time_budget_exhausted")
                try:
                    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
                except (TypeError, ValueError) as exc:
                    raise DataCollectionError("invalid_response_shape") from exc
                if len(encoded) > 10_000_000:
                    raise DataCollectionError("source_response_too_large")
                return body, dict(request=dict(params), observed_at=self.utc_now().isoformat(),
                                 sha256=hashlib.sha256(encoded).hexdigest(), response=body)
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise DataCollectionError("source_transport_failed") from None
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (401, 403):
                    raise DataCollectionError("authentication_unavailable") from None
                if attempt or status < 500:
                    raise DataCollectionError("source_http_failed") from None
        raise DataCollectionError("source_transport_failed")

    def _universe(self, day: dt.date, batch: SourceBatch) -> dict[str, str | None]:
        """Establish two nonempty dated markets, binding identities from basic info."""
        tickers = set()
        for market in ("STK", "KSQ"):
            body, evidence = self._request(dict(bld=MEMBERS_BLD, mktId=market, trdDd=day.strftime("%Y%m%d")))
            batch.universe_evidence.append(evidence)
            rows = body.get("OutBlock_1")
            if not isinstance(rows, list) or not rows:
                raise DataCollectionError("market_membership_unavailable")
            for row in rows:
                ticker = row.get("ISU_SRT_CD") if isinstance(row, dict) else None
                if not isinstance(ticker, str) or not re.fullmatch(r"[0-9A-Z]{6}", ticker) or ticker in tickers:
                    raise DataCollectionError("market_membership_invalid")
                tickers.add(ticker)
        body, evidence = self._request(dict(bld=BASIC_BLD, mktId="ALL"))
        batch.universe_evidence.append(evidence)
        rows = body.get("OutBlock_1")
        if not isinstance(rows, list):
            raise DataCollectionError("identity_source_unavailable")
        identities = {}
        for row in rows:
            if not isinstance(row, dict):
                raise DataCollectionError("identity_source_invalid")
            ticker = row.get("ISU_SRT_CD")
            if ticker not in tickers:
                continue
            isin = row.get("ISU_CD")
            if ticker in identities:
                identities[ticker] = None
            else:
                identities[ticker] = isin if isinstance(isin, str) and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) else None
        return {ticker: identities.get(ticker) for ticker in sorted(tickers)}

    def collect(self, ref_date: dt.date, expected: Mapping[str, str | None] | None = None,
                on_item: Callable | None = None, *, on_universe: Callable | None = None,
                on_universe_evidence: Callable | None = None,
                on_request: Callable | None = None,
                skip_tickers=(), initial_requests: int = 0,
                remaining_seconds: float | None = None) -> SourceBatch:
        """Collect once; callbacks may persist completed items and the frozen universe.

        A supplied universe is a trusted previously verified identity snapshot.
        Skip/resume callers merge prior items and preserve the original time budget.
        """
        batch = SourceBatch(ref_date)
        now = self.utc_now().astimezone(KST)
        if ref_date != now.date() or now.time() < dt.time(15, 30):
            batch.error = "current_completed_session_required"
            return batch
        self._day = ref_date
        self._on_request = on_request
        self.requests = initial_requests
        self._last_started = None
        self._deadline = self.monotonic() + min(self.max_seconds, remaining_seconds if remaining_seconds is not None else self.max_seconds)
        try:
            batch.expected = dict(expected) if expected is not None else self._universe(ref_date, batch)
            if not batch.expected or len(batch.expected) > 5000:
                raise DataCollectionError("market_membership_unavailable")
            if any(not isinstance(t, str) or not re.fullmatch(r"[0-9A-Z]{6}", t) for t in batch.expected):
                raise DataCollectionError("market_membership_invalid")
            batch.market_complete = True
        except DataCollectionError as exc:
            batch.error = str(exc)
            batch.requests = self.requests
            return batch
        if on_universe_evidence and batch.universe_evidence:
            on_universe_evidence(batch.universe_evidence)
        if on_universe:
            on_universe(batch.expected)
        stopped = None
        for ticker, isin in sorted(batch.expected.items()):
            if ticker in skip_tickers:
                continue
            item = SourceItem(ticker, ref_date, "failed")
            if stopped:
                item.status, item.reason = "unprocessed", stopped
            elif not isinstance(isin, str) or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
                item.reason = "identity_unavailable"
            else:
                params = dict(bld=PRICE_BLD, isuCd=isin, strtDd=ref_date.strftime("%Y%m%d"),
                              endDd=ref_date.strftime("%Y%m%d"), adjStkPrc="1")
                try:
                    body, evidence = self._request(params)
                    item.evidence["regular"] = evidence
                    row = _row(body, ref_date, isin)
                    values = {name: _number(row.get(key), volume=name == "volume") for name, key in FIELDS.items()}
                    if all(values[name] == 0 for name in ("open", "high", "low", "volume")):
                        item.status = "no_trade"
                    else:
                        if min(values[name] for name in ("open", "high", "low", "close")) <= 0 or not (
                            values["low"] <= values["open"] <= values["high"] and values["low"] <= values["close"] <= values["high"]):
                            raise DataQualityError("invalid_ohlcv_range")
                        item.bar = SourceBar(ref_date, ticker, **values)
                        item.status = "adjustment_missing"
                        body, evidence = self._request(params | {"adjStkPrc": "2"})
                        item.evidence["adjusted"] = evidence
                        adjusted = _number(_row(body, ref_date, isin).get("TDD_CLSPRC"))
                        if adjusted <= 0:
                            raise DataQualityError("invalid_adjusted_close")
                        item.bar.adj_close = adjusted
                        item.status = "valid"
                except (DataCollectionError, DataQualityError) as exc:
                    item.reason = str(exc)
                    if str(exc) in ("authentication_unavailable", "authentication_or_source_rejected",
                                    "request_or_time_budget_exhausted", "session_date_changed"):
                        stopped = str(exc)
                        batch.error = stopped
                        if item.bar is None:
                            item.status = "unprocessed"
            batch.items.append(item)
            batch.requests = self.requests
            if on_item:
                on_item(item)
        return batch
