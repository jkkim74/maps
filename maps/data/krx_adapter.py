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
from maps.data.krx_auth import ensure_krx_login_guard

logger = logging.getLogger(__name__)


def _classify_security_type(name: str) -> str:
    text = name.upper()
    if "스팩" in name or "SPAC" in text:
        return "SPAC"
    if "ETF" in text or "ETN" in text:
        return "ETF"
    return "STOCK"


def _listing_dates_from_frame(frame: pd.DataFrame) -> dict[str, datetime.date]:
    """KRX [12005] 전종목 기본정보 프레임에서 ``{ticker: 상장일}`` 을 뽑는다.

    ``LIST_DD`` 는 ``YYYY/MM/DD`` 문자열이다. 빈 값·파싱 불가 행은 건너뛴다 —
    상장일을 모르는 종목은 None 으로 남겨 하류(상한가 자격 판정)가 fail-closed 로
    막게 두는 편이 잘못된 날짜보다 안전하다.

    :param frame: ``ISU_SRT_CD`` / ``LIST_DD`` 컬럼을 가진 DataFrame.
    :return: 단축코드 → 상장일.
    """
    result: dict[str, datetime.date] = {}
    if frame is None or frame.empty:
        return result
    for _, row in frame.iterrows():
        ticker = row.get("ISU_SRT_CD")
        if not isinstance(ticker, str):
            continue
        ticker = ticker.strip()
        raw = row.get("LIST_DD")
        if not ticker or raw is None:
            continue
        try:
            result[ticker] = datetime.datetime.strptime(str(raw).strip(), "%Y/%m/%d").date()
        except (TypeError, ValueError):
            continue
    return result


def fetch_basic_information() -> pd.DataFrame:
    """Fresh KRX names/listing dates; membership remains date-specific.

    This endpoint is current, not a historical snapshot: historical names and
    delisted securities cannot be reconstructed from it.
    """
    ensure_krx_login_guard()
    from pykrx.website.krx.market import core

    return core.전종목기본정보().fetch("ALL")


def fetch_listing_dates() -> dict[str, datetime.date]:
    """Compatibility helper for listing-date backfills; failures remain missing."""
    try:
        return _listing_dates_from_frame(fetch_basic_information())
    except Exception as exc:
        logger.warning("KRX 전종목 기본정보 조회 실패 — 상장일 미적재: %s", exc)
        return {}


def _valid_name(value, ticker: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value != ticker and value.lower() not in {"nan", "none", "null"} else None


def _env_tickers(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _pos_or_none(value) -> float | None:
    """양수면 float, 그 외(0·결측·음수)는 None. pykrx 0=미산출 관례 대응."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _nonneg_or_none(value) -> float | None:
    """0 이상이면 float, 결측/음수는 None (배당 지표는 0이 유효)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


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
class FundamentalData:
    """일별 펀더멘털 지표 (pykrx get_market_fundamental 기준)."""

    date: datetime.date
    ticker: str
    per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    bps: float | None = None
    div: float | None = None   # 배당수익률(%)
    dps: float | None = None   # 주당배당금


@dataclass
class InvestorFlowData:
    """One ticker's exact-date investor net-purchase values in KRW."""

    date: datetime.date
    ticker: str
    market: str
    foreign_net_value: float | None = None
    institutional_net_value: float | None = None
    individual_net_value: float | None = None


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
class MetadataCollection:
    """Measured metadata coverage. Mock scope represents an explicitly seeded fixture."""

    items: list[SecurityMeta]
    markets: dict[str, dict]
    status: str
    scope: str = "market"

    @property
    def candidate_ready(self) -> bool:
        if not self.items or self.status == "unavailable":
            return False
        for market in ("KOSPI", "KOSDAQ"):
            quality = self.markets.get(market, {})
            expected = quality.get("expected_count", 0)
            valid = quality.get("valid_count", 0)
            if expected == 0 and self.scope == "mock":
                continue
            if (expected <= 0 or not 0 <= valid <= expected or valid / expected < .95
                    or quality.get("coverage_ratio", 0) != valid / expected or quality.get("error")):
                return False
        return True

    def as_dict(self) -> dict:
        return {"status": self.status, "markets": self.markets,
                "candidate_ready": self.candidate_ready, "scope": self.scope}

    @classmethod
    def unavailable(cls, error: str) -> MetadataCollection:
        return cls([], {market: {
            "expected_count": 0, "valid_count": 0, "coverage_ratio": 0.0,
            "missing_tickers": [], "listing_date_missing_tickers": [], "error": error,
        } for market in ("KOSPI", "KOSDAQ")}, "unavailable")


@dataclass
class CollectionResult:
    """수집 결과 묶음."""

    ref_date: datetime.date
    ohlcv: list[OHLCVData] = field(default_factory=list)
    meta: list[SecurityMeta] = field(default_factory=list)
    halts: list[str] = field(default_factory=list)       # 정지 ticker 목록
    managed: list[str] = field(default_factory=list)     # 관리종목 ticker 목록
    source: str = "krx"
    # 수급 0건이면 다음 거래일 신규 매수가 전량 막힌다 — 조용히 넘어가면 안 된다.
    investor_flow_count: int = 0
    investor_flow_error: str | None = None
    metadata_quality: dict | None = None


class KRXAdapterBase(ABC):
    """KRX 어댑터 추상 인터페이스."""

    @abstractmethod
    def get_ohlcv(self, ref_date: datetime.date) -> list[OHLCVData]:
        """ref_date 기준 전체 종목 OHLCV를 반환한다."""

    @abstractmethod
    def get_security_meta(self, ref_date: datetime.date) -> list[SecurityMeta]:
        """ref_date 기준 상장 종목 메타를 반환한다."""

    def get_security_meta_result(self, ref_date: datetime.date) -> MetadataCollection:
        items = self.get_security_meta(ref_date)
        markets = {}
        for market in ("KOSPI", "KOSDAQ"):
            members = [item for item in items if item.market == market]
            valid = [item for item in members if _valid_name(item.name, item.ticker)]
            markets[market] = {
                "expected_count": len(members), "valid_count": len(valid),
                "coverage_ratio": len(valid) / len(members) if members else 0.0,
                "missing_tickers": [item.ticker for item in members if item not in valid],
                "listing_date_missing_tickers": [item.ticker for item in valid if item.listing_date is None],
                "error": None if members else "empty membership",
            }
        valid_items = [item for item in items if _valid_name(item.name, item.ticker)]
        status = "unavailable" if any(q["error"] for q in markets.values()) else (
            "partial" if any(q["missing_tickers"] or q["listing_date_missing_tickers"] for q in markets.values()) else "complete")
        return MetadataCollection(valid_items, markets, status)

    @abstractmethod
    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 거래정지 중인 ticker 목록을 반환한다."""

    @abstractmethod
    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        """ref_date 에 관리종목 지정 중인 ticker 목록을 반환한다."""

    @abstractmethod
    def get_sector_classifications(self, ref_date: datetime.date) -> dict[str, str]:
        """ref_date 기준 ticker → WICS 업종명 매핑을 반환한다."""

    @abstractmethod
    def get_fundamental(self, ref_date: datetime.date) -> list[FundamentalData]:
        """ref_date 기준 전체 종목 펀더멘털(PER/PBR/EPS/BPS/DIV/DPS)을 반환한다."""

    def get_investor_flows(self, ref_date: datetime.date) -> list[InvestorFlowData]:
        """Return exact-date KRX net purchases by foreign/institution/individual."""
        return []


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
        # pykrx 는 요청마다 재로그인을 시도한다 — 실패 누적이 KRX 계정을 잠그지
        # 않도록 회로차단기를 설치한다(멱등).
        ensure_krx_login_guard()

    # pykrx 버전에 따라 컬럼명이 한글 또는 영문으로 반환될 수 있음 — 영문→한글 매핑
    _OHLCV_COL_MAP: dict[str, str] = {
        "Open":   "시가",
        "High":   "고가",
        "Low":    "저가",
        "Close":  "종가",
        "Volume": "거래량",
        "open":   "시가",
        "high":   "고가",
        "low":    "저가",
        "close":  "종가",
        "volume": "거래량",
    }

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
                if df is None or df.empty:
                    continue
                # pykrx 버전·날짜에 따라 영문 컬럼이 반환되는 경우 한글로 통일
                df = df.rename(columns=self._OHLCV_COL_MAP)
                # 필수 컬럼 중 하나라도 없으면 경고 후 스킵
                missing_cols = [c for c in ("시가", "고가", "저가", "종가", "거래량") if c not in df.columns]
                if missing_cols:
                    logger.warning(
                        "KRX OHLCV 컬럼 누락 [%s %s] — %s (실제 컬럼: %s)",
                        market, date_str, missing_cols, list(df.columns),
                    )
                    continue
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
        return self.get_security_meta_result(ref_date).items

    def get_security_meta_result(self, ref_date: datetime.date) -> MetadataCollection:
        ensure_krx_login_guard()
        from pykrx import stock

        basic_error = None
        names = {}
        listing_dates = {}
        try:
            frame = fetch_basic_information()
            listing_dates = _listing_dates_from_frame(frame)
            if frame is not None:
                for _, row in frame.iterrows():
                    ticker = row.get("ISU_SRT_CD")
                    if not isinstance(ticker, str) or not ticker.strip():
                        continue
                    ticker = ticker.strip()
                    name = _valid_name(row.get("ISU_ABBRV"), ticker) or _valid_name(row.get("ISU_NM"), ticker)
                    if name:
                        names[ticker] = name
        except Exception as exc:
            basic_error = f"basic information: {type(exc).__name__}: {exc}"
            logger.warning("KRX basic information unavailable: %s", exc)

        items = []
        markets = {}
        unavailable = False
        for market in ("KOSPI", "KOSDAQ"):
            try:
                tickers = []
                seen = set()
                for ticker in stock.get_market_ticker_list(ref_date.strftime("%Y%m%d"), market=market):
                    if isinstance(ticker, str):
                        if ticker in seen:
                            continue
                        seen.add(ticker)
                    tickers.append(ticker)
                if not tickers:
                    raise ValueError("empty membership")
            except Exception as exc:
                markets[market] = MetadataCollection.unavailable(str(exc)).markets[market]
                unavailable = True
                continue
            missing = []
            listing_missing = []
            for ticker in tickers:
                try:
                    if not isinstance(ticker, str) or not ticker.strip():
                        raise ValueError("invalid ticker")
                    name = names.get(ticker)
                    if name is None:
                        name = _valid_name(stock.get_market_ticker_name(ticker), ticker)
                    if name is None:
                        raise ValueError("missing or malformed name")
                    listing_date = listing_dates.get(ticker)
                    items.append(SecurityMeta(ticker, name, market, _classify_security_type(name), listing_date))
                    if listing_date is None:
                        listing_missing.append(ticker)
                except Exception as exc:
                    missing.append(str(ticker))
                    logger.warning("KRX metadata missing [%s %s]: %s", market, ticker, exc)
            valid_count = len(tickers) - len(missing)
            markets[market] = {
                "expected_count": len(tickers), "valid_count": valid_count,
                "coverage_ratio": valid_count / len(tickers), "missing_tickers": missing,
                "listing_date_missing_tickers": listing_missing, "error": None,
            }
            if basic_error:
                markets[market]["basic_information_error"] = basic_error
        status = "unavailable" if unavailable else (
            "partial" if basic_error or any(q["missing_tickers"] or q["listing_date_missing_tickers"] for q in markets.values()) else "complete")
        return MetadataCollection(items, markets, status)

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

    def get_sector_classifications(self, ref_date: datetime.date) -> dict[str, str]:
        """KOSPI + KOSDAQ 전 종목의 WICS 업종 분류를 반환한다."""
        try:
            from pykrx import stock as _krx
        except ImportError as e:
            raise DataCollectionError("pykrx 라이브러리가 필요합니다: pip install pykrx") from e

        date_str = ref_date.strftime("%Y%m%d")
        result: dict[str, str] = {}
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = _krx.get_market_sector_classifications(date_str, market=market)
                if df is None or df.empty:
                    continue
                # 컬럼명: '종목코드', '종목명', '시가총액', ..., '업종명'
                sector_col = next(
                    (c for c in df.columns if "업종" in c and "명" in c), None
                )
                ticker_col = next(
                    (c for c in df.columns if "종목코드" in c or "티커" in c or c == "Symbol"), None
                )
                if sector_col is None:
                    logger.warning("업종명 컬럼 없음 [%s %s] 컬럼=%s", market, date_str, list(df.columns))
                    continue
                if ticker_col is not None:
                    for _, row in df.iterrows():
                        result[str(row[ticker_col])] = str(row[sector_col])
                else:
                    # 인덱스가 ticker인 경우
                    for ticker, row in df.iterrows():
                        result[str(ticker)] = str(row[sector_col])
            except Exception as exc:
                logger.warning("업종 수집 오류 [%s %s]: %s", market, date_str, exc)
        logger.info("업종 분류 수집 완료 [%s]: %d종목", date_str, len(result))
        return result

    def get_fundamental(self, ref_date: datetime.date) -> list[FundamentalData]:
        """KOSPI + KOSDAQ 전 종목 펀더멘털을 반환한다.

        pykrx ``get_market_fundamental`` 은 ticker 인덱스 + [BPS, PER, PBR, EPS, DIV, DPS]
        컬럼을 반환한다. 값 0 은 결측(휴장/미산출)으로 보고 None 처리한다.
        """
        try:
            from pykrx import stock as _krx
        except ImportError as e:
            raise DataCollectionError("pykrx 라이브러리가 필요합니다: pip install pykrx") from e

        date_str = ref_date.strftime("%Y%m%d")
        result: list[FundamentalData] = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = _krx.get_market_fundamental(date_str, market=market)
            except Exception as exc:
                logger.warning("KRX 펀더멘털 수집 실패 [%s %s]: %s", market, date_str, exc)
                continue
            if df is None or df.empty:
                continue
            for ticker, row in df.iterrows():
                result.append(FundamentalData(
                    date=ref_date,
                    ticker=str(ticker),
                    per=_pos_or_none(row.get("PER")),
                    pbr=_pos_or_none(row.get("PBR")),
                    eps=_pos_or_none(row.get("EPS")),
                    bps=_pos_or_none(row.get("BPS")),
                    div=_nonneg_or_none(row.get("DIV")),
                    dps=_nonneg_or_none(row.get("DPS")),
                ))
        logger.info("KRX 펀더멘털 수집 완료 [%s]: %d종목", date_str, len(result))
        return result

    def get_investor_flows(self, ref_date: datetime.date) -> list[InvestorFlowData]:
        """Fetch three investor groups using pykrx's current net-purchase API."""
        from pykrx import stock as _krx

        date_str = ref_date.strftime("%Y%m%d")
        output: list[InvestorFlowData] = []
        for market in ("KOSPI", "KOSDAQ"):
            values: dict[str, dict[str, float]] = {}
            for investor, target in (
                ("외국인", "foreign_net_value"),
                ("기관합계", "institutional_net_value"),
                ("개인", "individual_net_value"),
            ):
                frame = _krx.get_market_net_purchases_of_equities_by_ticker(
                    date_str, date_str, market=market, investor=investor
                )
                if frame is None or frame.empty:
                    raise DataCollectionError(
                        f"KRX investor flow empty [{market} {investor} {date_str}]"
                    )
                value_col = next(
                    (
                        col for col in frame.columns
                        if "순매수" in str(col) and "거래대금" in str(col)
                    ),
                    None,
                )
                if value_col is None:
                    raise DataCollectionError(
                        f"KRX investor flow value column missing: {list(frame.columns)}"
                    )
                for ticker, row in frame.iterrows():
                    values.setdefault(str(ticker), {})[target] = float(row[value_col])
            output.extend(
                InvestorFlowData(date=ref_date, ticker=ticker, market=market, **item)
                for ticker, item in values.items()
            )
        logger.info("KRX 투자자 수급 수집 완료 [%s]: %d종목", date_str, len(output))
        return output


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
        self._sector_override: dict[str, str] = {}
        self._fundamental_override: dict[str, FundamentalData] = {}
        self._flow_override: dict[str, InvestorFlowData] = {}

    def set_halts(self, date: datetime.date, tickers: list[str]) -> None:
        self._halt_override[date] = tickers

    def set_managed(self, date: datetime.date, tickers: list[str]) -> None:
        self._managed_override[date] = tickers

    def set_meta(self, ticker: str, meta: SecurityMeta) -> None:
        self._meta_override[ticker] = meta

    def set_sectors(self, sectors: dict[str, str]) -> None:
        """ticker → 업종명 매핑을 주입한다."""
        self._sector_override = sectors

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

    def get_security_meta_result(self, ref_date: datetime.date) -> MetadataCollection:
        result = super().get_security_meta_result(ref_date)
        result.scope = "mock"
        # Seeded fixtures need not represent both exchanges. Counts stay actual;
        # only this explicit mock scope permits an empty simulated exchange.
        for quality in result.markets.values():
            if quality["expected_count"] == 0:
                quality["error"] = None
        result.status = ("unavailable" if not result.items else "partial" if any(
            q["missing_tickers"] or q["listing_date_missing_tickers"] for q in result.markets.values()
        ) else "complete")
        return result

    def get_halt_list(self, ref_date: datetime.date) -> list[str]:
        return self._halt_override.get(ref_date, [])

    def get_managed_list(self, ref_date: datetime.date) -> list[str]:
        return self._managed_override.get(ref_date, [])

    def get_sector_classifications(self, ref_date: datetime.date) -> dict[str, str]:
        return dict(self._sector_override)

    def set_fundamentals(self, fundamentals: dict[str, FundamentalData]) -> None:
        """ticker → FundamentalData 매핑을 주입한다."""
        self._fundamental_override = fundamentals

    def get_fundamental(self, ref_date: datetime.date) -> list[FundamentalData]:
        result: list[FundamentalData] = []
        for ticker in self._tickers:
            if ticker in self._fundamental_override:
                fd = self._fundamental_override[ticker]
                result.append(FundamentalData(
                    date=ref_date,
                    ticker=ticker,
                    per=fd.per, pbr=fd.pbr, eps=fd.eps,
                    bps=fd.bps, div=fd.div, dps=fd.dps,
                ))
        return result

    def set_investor_flows(self, flows: dict[str, InvestorFlowData]) -> None:
        """Inject ticker-keyed investor flows."""
        self._flow_override = flows

    def get_investor_flows(self, ref_date: datetime.date) -> list[InvestorFlowData]:
        """Return injected exact-date flows only."""
        return [
            InvestorFlowData(
                date=ref_date,
                ticker=ticker,
                market=row.market,
                foreign_net_value=row.foreign_net_value,
                institutional_net_value=row.institutional_net_value,
                individual_net_value=row.individual_net_value,
            )
            for ticker, row in self._flow_override.items()
        ]
