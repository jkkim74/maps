"""KRX Open API 어댑터.

Phase 1.1: pykrx 라이브러리로 KRX 데이터를 수집한다.
  pip install pykrx
API 키 없이 KRX 데이터 포털을 직접 접근한다.

KRX_API_KEY 환경변수가 없어도 동작한다.
테스트 환경에서는 MockKRXAdapter를 사용한다.
"""

from __future__ import annotations

import datetime
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from maps.common.exceptions import DataCollectionError

logger = logging.getLogger(__name__)


def _classify_security_type(name: str) -> str:
    text = name.upper()
    if "스팩" in name or "SPAC" in text:
        return "SPAC"
    if "ETF" in text or "ETN" in text:
        return "ETF"
    return "STOCK"


def _env_tickers(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


@dataclass
class OHLCVData:
    """일별 OHLCV 데이터."""

    date: datetime.date
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float | None = None

    @property
    def has_adjusted(self) -> bool:
        return self.adj_close is not None


@dataclass
class SecurityMeta:
    """종목 메타 정보."""

    ticker: str
    name: str
    market: str          # KOSPI | KOSDAQ
    security_type: str   # STOCK | ETF | SPAC
    listing_date: datetime.date | None = None
    delisting_date: datetime.date | None = None


@dataclass
class CollectionResult:
    """수집 결과 묶음."""

    ref_date: datetime.date
    ohlcv: list[OHLCVData] = field(default_factory=list)
    meta: list[SecurityMeta] = field(default_factory=list)
    halts: list[str] = field(default_factory=list)       # 정지 ticker 목록
    managed: list[str] = field(default_factory=list)     # 관리종목 ticker 목록
    source: str = "krx"


class KRXAdapterBase(ABC):
    """KRX 어댑터 추상 인터페이스."""

    @abstractmethod
    def get_ohlcv(self, ref_date: datetime.date) -> list[OHLCVData]:
        """ref_date 기준 전체 종목 OHLCV를 반환한다."""

    @abstractmethod
    def get_security_meta(self, ref_date: datetime.date) -> list[SecurityMeta]:
        """ref_date 기준 상장 종목 메타를 반환한다."""

    @abstractmethod
    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 거래정지 중인 ticker 목록을 반환한다."""

    @abstractmethod
    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 관리종목 지정 중인 ticker 목록을 반환한다."""


class KRXAdapter(KRXAdapterBase):
    """KRX 데이터 어댑터 — pykrx 기반 Phase 1.1 구현.

    pykrx 는 API 키 없이 KRX 데이터 포털을 스크래핑한다.
    설치: pip install pykrx

    KRX_API_KEY 환경변수는 더 이상 필요하지 않다.
    pykrx 미설치 시 DataCollectionError가 발생한다.
    """

    def __init__(self) -> None:
        try:
            import pykrx  # noqa: F401
        except ImportError as e:
            raise DataCollectionError(
                "pykrx 라이브러리가 필요합니다: pip install pykrx"
            ) from e

    def get_ohlcv(self, ref_date: datetime.date) -> list[OHLCVData]:
        """ref_date 기준 KOSPI + KOSDAQ 전 종목 OHLCV를 반환한다."""
        try:
            from pykrx import stock as _krx
        except ImportError as e:
            raise DataCollectionError("pykrx 라이브러리가 필요합니다: pip install pykrx") from e

        date_str = ref_date.strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = _krx.get_market_ohlcv(date_str, market=market)
                if df is not None and not df.empty:
                    df["_market"] = market
                    frames.append(df)
            except Exception as exc:
                logger.warning("KRX OHLCV 수집 실패 [%s %s]: %s", market, date_str, exc)

        if not frames:
            return []

        combined = pd.concat(frames)
        result: list[OHLCVData] = []
        for ticker, row in combined.iterrows():
            try:
                close = float(row.get("종가", 0) or 0)
                result.append(OHLCVData(
                    date=ref_date,
                    ticker=str(ticker),
                    open=float(row.get("시가", 0) or 0),
                    high=float(row.get("고가", 0) or 0),
                    low=float(row.get("저가", 0) or 0),
                    close=close,
                    volume=int(row.get("거래량", 0) or 0),
                    # pykrx daily OHLCV is adjusted for split/rights events in
                    # the common endpoint. Store close as adj_close so the
                    # downstream adjusted-price gate can make an explicit
                    # decision instead of treating every live bar as missing.
                    adj_close=close if close > 0 else None,
                ))
            except (TypeError, ValueError) as exc:
                logger.debug("OHLCV 행 변환 오류 [%s]: %s", ticker, exc)
        logger.info("KRX OHLCV 수집 완료 [%s]: %d종목", date_str, len(result))
        return result

    def get_security_meta(self, ref_date: datetime.date) -> list[SecurityMeta]:
        """ref_date 기준 KOSPI + KOSDAQ 상장 종목 메타를 반환한다."""
        try:
            from pykrx import stock as _krx
        except ImportError as e:
            raise DataCollectionError("pykrx 라이브러리가 필요합니다: pip install pykrx") from e

        date_str = ref_date.strftime("%Y%m%d")
        result: list[SecurityMeta] = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                tickers = _krx.get_market_ticker_list(date_str, market=market)
                for ticker in tickers:
                    try:
                        name = _krx.get_market_ticker_name(ticker)
                    except Exception:
                        name = ticker
                    result.append(SecurityMeta(
                        ticker=ticker,
                        name=name,
                        market=market,
                        security_type=_classify_security_type(name),
                        listing_date=None,
                        delisting_date=None,
                    ))
            except Exception as exc:
                logger.warning("KRX 종목 메타 수집 실패 [%s]: %s", market, exc)
        logger.info("KRX 종목 메타 수집 완료 [%s]: %d종목", date_str, len(result))
        return result

    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 거래정지 중인 ticker 목록을 반환한다.

        pykrx 는 거래정지 전용 API를 제공하지 않는다.
        거래량 0 을 heuristic으로 사용한다 (공식 정지 목록과 다를 수 있음).
        Phase 5에서 KRX 공시 API로 교체 예정.
        """
        ohlcv = self.get_ohlcv(ref_date)
        halts = [d.ticker for d in ohlcv if d.volume == 0]
        halts = sorted(set(halts) | _env_tickers("MAPS_HALTED_TICKERS"))
        if halts:
            logger.info("거래정지 추정 [%s]: %d종목 (거래량 0 + manual override)", ref_date, len(halts))
        return halts

    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 관리종목으로 지정된 ticker 목록을 반환한다.

        pykrx 는 관리종목 전용 API를 제공하지 않는다.
        Phase 5에서 KRX DART 공시 API로 교체 예정.
        현재는 빈 목록을 반환한다 (보수적: 관리종목 필터 없음).
        """
        managed = sorted(_env_tickers("MAPS_MANAGED_TICKERS"))
        if managed:
            logger.warning("관리종목 manual override 적용 [%s]: %d종목", ref_date, len(managed))
        else:
            logger.warning(
                "get_managed_list: pykrx 미지원 — MAPS_MANAGED_TICKERS 비어 있음 (%s). "
                "DART/KRX 공식 상태 API 연동 전까지 manual override를 사용하세요.",
                ref_date,
            )
        return managed


class MockKRXAdapter(KRXAdapterBase):
    """테스트용 더미 KRX 어댑터.

    seed_tickers를 지정하면 그 종목들만 반환한다.
    ohlcv_override / halt_override / managed_override 로 개별 날짜 데이터를 주입할 수 있다.
    """

    def __init__(
        self,
        seed_tickers: list[str] | None = None,
        base_price: float = 50_000.0,
    ) -> None:
        self._tickers = seed_tickers or ["005930", "000660", "035420"]
        self._base_price = base_price
        self._halt_override: dict[datetime.date, list[str]] = {}
        self._managed_override: dict[datetime.date, list[str]] = {}
        self._meta_override: dict[str, SecurityMeta] = {}

    def set_halts(self, date: datetime.date, tickers: list[str]) -> None:
        self._halt_override[date] = tickers

    def set_managed(self, date: datetime.date, tickers: list[str]) -> None:
        self._managed_override[date] = tickers

    def set_meta(self, ticker: str, meta: SecurityMeta) -> None:
        self._meta_override[ticker] = meta

    def get_ohlcv(self, ref_date: datetime.date) -> list[OHLCVData]:
        result = []
        for ticker in self._tickers:
            p = self._base_price
            result.append(
                OHLCVData(
                    date=ref_date,
                    ticker=ticker,
                    open=p * 0.99,
                    high=p * 1.01,
                    low=p * 0.98,
                    close=p,
                    volume=100_000,
                    adj_close=p,
                )
            )
        return result

    def get_security_meta(self, ref_date: datetime.date) -> list[SecurityMeta]:
        result = []
        for ticker in self._tickers:
            if ticker in self._meta_override:
                result.append(self._meta_override[ticker])
            else:
                result.append(
                    SecurityMeta(
                        ticker=ticker,
                        name=f"종목_{ticker}",
                        market="KOSPI",
                        security_type="STOCK",
                        listing_date=datetime.date(2000, 1, 1),
                        delisting_date=None,
                    )
                )
        return result

    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        return self._halt_override.get(ref_date, [])

    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        return self._managed_override.get(ref_date, [])
