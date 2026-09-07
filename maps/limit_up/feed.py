"""KIS market-data normalization and bounded fallback helpers."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: (tr_id, 레코드 폭) 조합별로 한 번만 알린다 — 프레임마다 찍으면 시간당 수만 줄이다.
_WIDTH_NOTICES: set[tuple[str, int]] = set()


KIS_TRADE_COLUMNS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)

KIS_ASK_COLUMNS = (
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE",
    "ASKP1", "ASKP2", "ASKP3", "ASKP4", "ASKP5", "ASKP6", "ASKP7", "ASKP8", "ASKP9", "ASKP10",
    "BIDP1", "BIDP2", "BIDP3", "BIDP4", "BIDP5", "BIDP6", "BIDP7", "BIDP8", "BIDP9", "BIDP10",
    "ASKP_RSQN1", "ASKP_RSQN2", "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5",
    "ASKP_RSQN6", "ASKP_RSQN7", "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10",
    "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3", "BIDP_RSQN4", "BIDP_RSQN5",
    "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9", "BIDP_RSQN10",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN", "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC", "STCK_DEAL_CLS_CODE",
)


@dataclass(frozen=True)
class FeedTrade:
    """Normalized KRX execution used by the V1 state machine."""

    ticker: str
    price: int
    cumulative_turnover_krw: int
    execution_strength: float
    buy_initiated: bool
    received_at: float


@dataclass(frozen=True)
class FeedQuote:
    """Normalized KRX best-level orderbook snapshot."""

    ticker: str
    best_ask_price: int
    best_ask_qty: int
    best_bid_price: int
    best_bid_qty: int
    received_at: float


@dataclass(frozen=True)
class TapeSnapshot:
    """Bounded transition tape ready for an asynchronous DB writer."""

    ticker: str
    transition: str
    payload: tuple[dict, ...]


def parse_kis_ws_message(raw: str, *, received_at: float) -> list[FeedTrade | FeedQuote]:
    """Parse unencrypted H0STCNT0/H0STASP0 rows and ignore control JSON."""
    if not raw.startswith("0|"):
        return []
    parts = raw.split("|", 3)
    if len(parts) != 4:
        raise ValueError("invalid KIS WebSocket envelope")
    tr_id = parts[1]
    try:
        record_count = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid KIS record count") from exc
    if tr_id == "H0STCNT0":
        columns = KIS_TRADE_COLUMNS
    elif tr_id == "H0STASP0":
        columns = KIS_ASK_COLUMNS
    else:
        return []
    values = parts[3].split("^")
    expected = record_count * len(columns)
    # KIS 는 새 컬럼을 레코드 **뒤에** 덧붙인다(NXT·통합 호가의 KMID_*/NMID_* 가 공식
    # 예제에서 STCK_DEAL_CLS_CODE 뒤에 온다). 2026-09-07 H0STASP0 가 59→62 로 늘어나
    # 한 시간에 17,150 프레임이 전부 버려졌다. 레코드 폭이 정확히 나누어지고 우리가
    # 아는 컬럼 수 이상이면 앞부분만 쓰고 나머지는 무시한다. 폭이 모자라거나 나누어
    # 떨어지지 않으면 절단·손상이므로 예전처럼 fail-closed.
    if record_count <= 0 or len(values) % record_count != 0:
        raise ValueError(f"KIS {tr_id} field count {len(values)} != {expected}")
    width = len(values) // record_count
    if width < len(columns):
        raise ValueError(f"KIS {tr_id} field count {len(values)} != {expected}")
    if width > len(columns) and (tr_id, width) not in _WIDTH_NOTICES:
        _WIDTH_NOTICES.add((tr_id, width))
        logger.warning(
            "KIS %s 레코드 폭 %d > 알려진 컬럼 %d — 뒤의 %d개 필드는 무시한다: %s",
            tr_id, width, len(columns), width - len(columns), values[len(columns):width],
        )
    records: list[FeedTrade | FeedQuote] = []
    for offset in range(0, len(values), width):
        row = dict(zip(columns, values[offset:offset + len(columns)], strict=True))
        if tr_id == "H0STCNT0":
            records.append(
                FeedTrade(
                    ticker=row["MKSC_SHRN_ISCD"],
                    price=_as_int(row["STCK_PRPR"]),
                    cumulative_turnover_krw=_as_int(row["ACML_TR_PBMN"]),
                    execution_strength=_as_float(row["CTTR"]),
                    buy_initiated=row["CCLD_DVSN"] == "1",
                    received_at=received_at,
                )
            )
        else:
            records.append(
                FeedQuote(
                    ticker=row["MKSC_SHRN_ISCD"],
                    best_ask_price=_as_int(row["ASKP1"]),
                    best_ask_qty=_as_int(row["ASKP_RSQN1"]),
                    best_bid_price=_as_int(row["BIDP1"]),
                    best_bid_qty=_as_int(row["BIDP_RSQN1"]),
                    received_at=received_at,
                )
            )
    return records


class TapeBuffer:
    """Per-ticker in-memory ring buffer that never performs database I/O."""

    def __init__(self, *, window_seconds: float = 60.0) -> None:
        """Create an empty bounded market tape."""
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds
        self._rows: dict[str, deque[dict]] = defaultdict(deque)

    def append(self, ticker: str, *, at: float, payload: dict) -> None:
        """Append one normalized event and evict data outside the time window."""
        row = {"at": at, **payload}
        rows = self._rows[ticker]
        rows.append(row)
        while rows and at - float(rows[0]["at"]) >= self.window_seconds:
            rows.popleft()

    def snapshot(self, ticker: str, *, transition: str) -> TapeSnapshot:
        """Copy current evidence for an asynchronous forced transition dump."""
        return TapeSnapshot(ticker, transition, tuple(dict(row) for row in self._rows[ticker]))


class RestFallbackLimiter:
    """Minimal shared pacing state for degraded REST polling."""

    def __init__(self, *, min_interval_seconds: float = 0.5) -> None:
        """Create a limiter with the required V1 polling floor."""
        if min_interval_seconds < 0.5:
            raise ValueError("REST fallback interval must be at least 0.5 seconds")
        self.min_interval_seconds = min_interval_seconds
        self.last_call_at: float | None = None
        self.backoff_seconds = 0.0

    def delay(self, *, now: float) -> float:
        """Return required delay before the next REST request."""
        interval_delay = 0.0
        if self.last_call_at is not None:
            interval_delay = max(0.0, self.min_interval_seconds - (now - self.last_call_at))
        return max(interval_delay, self.backoff_seconds)

    def record_call(self, *, now: float) -> None:
        """Record an issued REST request."""
        self.last_call_at = now

    def record_rate_limit(self) -> None:
        """Increase 429/EGW00201 backoff exponentially up to 30 seconds."""
        self.backoff_seconds = min(30.0, 1.0 if self.backoff_seconds == 0 else self.backoff_seconds * 2)

    def record_success(self) -> None:
        """Clear transient rate-limit backoff after a successful request."""
        self.backoff_seconds = 0.0


def _as_int(value: str) -> int:
    """Parse blank-safe signed KIS integer text."""
    try:
        return int(value or 0)
    except ValueError as exc:
        raise ValueError(f"invalid KIS integer: {value!r}") from exc


def _as_float(value: str) -> float:
    """Parse blank-safe KIS decimal text."""
    try:
        return float(value or 0)
    except ValueError as exc:
        raise ValueError(f"invalid KIS decimal: {value!r}") from exc

