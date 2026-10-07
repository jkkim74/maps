"""Public Npay multi-theme snapshot collection with fail-closed completeness."""
from __future__ import annotations

from datetime import date
import math
import time
from typing import Callable
import unicodedata

import requests

from maps.common.exceptions import DataCollectionError
from maps.data.classifications import ClassificationPayload

_BASE = "https://stock.naver.com/api/domestic/market/theme"
_MOBILE_MEMBERS = "https://m.stock.naver.com/front-api/domestic/sector/item/list"
_PAGE_SIZE = 200
_MAX_PAGES = 100


class NaverThemeAdapter:
    """Collect full source manifests before filtering to the trusted universe."""

    def __init__(self, session=None, timeout: float = 10,
                 deadline_seconds: float = 1200,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        """Inject HTTP and clocks for offline tests; bound all network work."""
        if any(not math.isfinite(v) or v <= 0 for v in (timeout, deadline_seconds)):
            raise ValueError("Timeout and deadline must be positive finite numbers")
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.deadline_seconds = deadline_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self._deadline = 0.0
        self._last_request: float | None = None

    def _remaining(self) -> float:
        """Raise when the collection wall deadline has elapsed."""
        remaining = self._deadline - self.monotonic()
        if remaining <= 0:
            raise DataCollectionError("Npay theme collection deadline exceeded")
        return remaining

    def _request(self, url: str, params: dict) -> object:
        """Request JSON with bounded retries and request spacing."""
        for attempt in range(3):
            self._remaining()
            if self._last_request is not None:
                wait = max(0, .25 - (self.monotonic() - self._last_request))
                if wait:
                    self.sleep(min(wait, self._remaining()))
            remaining = self._remaining()
            self._last_request = self.monotonic()
            try:
                response = self.session.get(url, params=params,
                                            timeout=min(self.timeout, remaining))
                self._remaining()
                response.raise_for_status()
                body = response.json()
                self._remaining()
            except requests.RequestException as exc:
                self._remaining()
                status = getattr(getattr(exc, "response", None), "status_code", None)
                transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status in (429, 500, 502, 503, 504)
                if not transient or attempt == 2:
                    raise DataCollectionError("Npay theme HTTP request failed") from exc
                self.sleep(min(.5 * (attempt + 1), self._remaining()))
                continue
            except ValueError as exc:
                raise DataCollectionError("Npay theme malformed JSON") from exc
            return body
        raise DataCollectionError("Npay theme retry limit exceeded")

    def _pages(self, url: str, params: dict) -> list[dict]:
        """Read zero-based page numbers until a short terminal page."""
        rows = []
        for page in range(_MAX_PAGES):
            items = self._request(url, {**params, "startIdx": page, "pageSize": _PAGE_SIZE})
            if not isinstance(items, list) or len(items) > _PAGE_SIZE:
                raise DataCollectionError("Npay theme response must be a paginated list")
            if any(not isinstance(item, dict) for item in items):
                raise DataCollectionError("Npay theme malformed list item")
            rows.extend(items)
            if len(items) < _PAGE_SIZE:
                return rows
        raise DataCollectionError("Npay theme page guard reached before completion")

    @staticmethod
    def _text(value: object, field: str, symbol: bool = False) -> str:
        """Validate original fields without converting nulls to labels."""
        if not isinstance(value, str) or not value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
            raise DataCollectionError(f"Npay theme invalid {field}")
        if symbol and (len(value) > 16 or any(c.isspace() for c in value)):
            raise DataCollectionError(f"Npay theme invalid {field}")
        return value

    def _manifest(self) -> dict[str, tuple[str, int]]:
        """Validate unique theme codes, names, and advertised member counts."""
        manifest = {}
        for item in self._pages(f"{_BASE}/list", {"sortType": "changeRate"}):
            raw_code = item.get("no")
            if isinstance(raw_code, int) and not isinstance(raw_code, bool) and raw_code >= 0:
                raw_code = str(raw_code)
            code = self._text(raw_code, "theme code", symbol=True)
            if not code.isascii() or not code.isdecimal():
                raise DataCollectionError("Npay theme invalid theme code")
            name = self._text(item.get("name"), "theme name")
            count = item.get("totalCnt")
            if isinstance(count, str) and count.isascii() and count.isdecimal():
                count = int(count)
            if type(count) is not int or count < 0 or code in manifest:
                raise DataCollectionError("Npay theme duplicate code or invalid count")
            manifest[code] = (name, count)
        if not manifest:
            raise DataCollectionError("Npay theme empty catalog")
        return manifest

    def _mobile_members(self, code: str, name: str) -> set[str]:
        """Corroborate a stale count using the mobile API's explicit last page."""
        params = {"sectorCode": code, "sectorType": "theme",
                  "sectorSortType": "MARKET_VALUE", "size": 50}
        seen: set[str] = set()
        cursors: set[str] = set()
        for page in range(_MAX_PAGES):
            body = self._request(_MOBILE_MEMBERS, dict(params))
            if not isinstance(body, dict) or body.get("isSuccess") is not True:
                raise DataCollectionError(f"Npay mobile theme request unsuccessful in {code}")
            result = body.get("result")
            if not isinstance(result, dict):
                raise DataCollectionError(f"Npay mobile theme invalid result in {code}")
            info, items, has_next = (result.get(key) for key in ("sectorInfo", "items", "hasNext"))
            # The mobile API includes sectorInfo only on its first cursor page.
            if (page == 0 or info is not None) and (
                    not isinstance(info, dict) or info.get("sectorName") != name):
                raise DataCollectionError(f"Npay mobile theme name mismatch in {code}")
            if (not isinstance(items, list) or not items or len(items) > 50
                    or type(has_next) is not bool):
                raise DataCollectionError(f"Npay mobile theme invalid page in {code}")
            for item in items:
                if not isinstance(item, dict):
                    raise DataCollectionError(f"Npay mobile theme invalid member in {code}")
                ticker = self._text(item.get("itemCode"), "mobile member ticker", symbol=True)
                self._text(item.get("name"), "mobile member name")
                if ticker in seen:
                    raise DataCollectionError(f"Npay mobile theme duplicate member in {code}")
                seen.add(ticker)
            if not has_next:
                return seen
            cursor = self._text(result.get("cursor"), "mobile cursor")
            if cursor in cursors:
                raise DataCollectionError(f"Npay mobile theme repeated cursor in {code}")
            cursors.add(cursor)
            params["cursor"] = cursor
        raise DataCollectionError("Npay mobile theme page guard reached before completion")

    def collect(self, ref_date: date, expected_tickers: list[str]) -> ClassificationPayload:
        """Return explicit multi-memberships and verified empty memberships."""
        self._deadline = self.monotonic() + self.deadline_seconds
        universe = [self._text(ticker, "expected ticker", symbol=True) for ticker in expected_tickers]
        if not universe or len(set(universe)) != len(universe):
            raise DataCollectionError("Npay theme expected universe must be nonempty and unique")
        manifest = self._manifest()
        memberships = {ticker: [] for ticker in universe}
        source_relations = 0
        discrepancies = []
        for code, (name, count) in manifest.items():
            rows = self._pages(f"{_BASE}/{code}/stocklist", {"marketType": "ALL", "orderType": "marketSum"})
            seen = set()
            for row in rows:
                ticker = self._text(row.get("itemcode"), "member ticker", symbol=True)
                self._text(row.get("itemname"), "member name")
                if ticker in seen:
                    raise DataCollectionError(f"Npay theme duplicate member in {code}")
                seen.add(ticker)
            if len(seen) != count:
                if not seen or self._mobile_members(code, name) != seen:
                    raise DataCollectionError(
                        f"Npay theme member count mismatch in {code}: advertised={count}, actual={len(seen)}")
                discrepancies.append({"theme_code": code, "advertised_count": count,
                    "verified_count": len(seen), "verification": "mobile_cursor_complete"})
            source_relations += len(seen)
            for ticker in seen:
                if ticker in memberships:
                    memberships[ticker].append(code)
        if self._manifest() != manifest:
            raise DataCollectionError("Npay theme manifest changed during collection")
        if not any(memberships.values()):
            raise DataCollectionError("Npay theme entire expected universe has zero assignments")
        self._remaining()
        return ClassificationPayload(kind="theme", provider="naver", ref_date=ref_date,
            expected_tickers=universe, catalog={code: name for code, (name, _) in manifest.items()},
            memberships=memberships, metrics={"source_relation_count": source_relations,
                "source_count_discrepancies": discrepancies})
