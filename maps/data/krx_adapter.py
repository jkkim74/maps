"""KRX Open API 어댑터.

실제 API 키는 환경변수 KRX_API_KEY.
키가 없으면 MockKRXAdapter(테스트용 더미 데이터)를 사용한다.
"""

from __future__ import annotations

import datetime
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from maps.common.exceptions import DataCollectionError


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
    """KRX Open API 실 연동 어댑터.

    환경변수 KRX_API_KEY 가 필요하다.
    Phase 1 구현 전까지는 NotImplementedError를 발생시킨다.
    """

    BASE_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

    def __init__(self) -> None:
        self._api_key = os.getenv("KRX_API_KEY", "")
        if not self._api_key:
            raise DataCollectionError(
                "KRX_API_KEY 환경변수가 없습니다. MockKRXAdapter를 사용하세요."
            )

    def get_ohlcv(self, ref_date: datetime.date) -> list[OHLCVData]:
        raise NotImplementedError("KRX OHLCV 수집 — Phase 1.1 구현 예정")

    def get_security_meta(self, ref_date: datetime.date) -> list[SecurityMeta]:
        raise NotImplementedError("KRX 종목 메타 수집 — Phase 1.1 구현 예정")

    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        raise NotImplementedError("KRX 거래정지 목록 — Phase 1.1 구현 예정")

    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        raise NotImplementedError("KRX 관리종목 목록 — Phase 1.1 구현 예정")


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
