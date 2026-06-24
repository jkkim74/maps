"""Naver 모바일 API 기반 펀더멘털 수집 어댑터 (KRX MDC 대체 소스).

KRX 데이터 포털(`data.krx.co.kr`)의 ``getJsonData.cmd`` 펀더멘털 엔드포인트는
세션·anti-scraping 정책으로 ``400 LOGOUT`` 을 반환해 pykrx
``get_market_fundamental`` 이 빈 프레임으로 무력화되는 경우가 있다. 이때 동일한
지표(PER/PBR/EPS/BPS/DIV/DPS)를 Naver 모바일 통합 API에서 종목 단위로 가져온다.

소스: ``https://m.stock.naver.com/api/stock/{code}/integration``
  → ``totalInfos`` 배열에 per/eps/pbr/bps/dividendYieldRatio/dividend 및
    컨센서스(cnsPer/cnsEps)가 문자열("26.57배", "12,372원", "0.51%")로 들어있다.

한계: 이 API는 **현재 스냅샷**만 제공한다. 과거 PER/PBR 시계열은 얻을 수 없으므로
``FundamentalRepository.historical_band``/``historical_avg`` 는 단일 일자 백필만으로는
결측(중립 처리)이 된다. 일별 누적 적재로만 히스토리가 쌓인다.
"""

from __future__ import annotations

import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from maps.data.krx_adapter import FundamentalData, _nonneg_or_none, _pos_or_none

logger = logging.getLogger(__name__)

_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Referer": "https://m.stock.naver.com/",
}


def _to_number(value: object) -> float | None:
    """"26.57배"·"12,372원"·"0.51%" 같은 Naver 문자열을 float로 변환한다.

    결측("-", "N/A", "", None)은 None을 반환한다.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "N/A"}:
        return None
    cleaned = (
        text.replace(",", "")
        .replace("배", "")
        .replace("원", "")
        .replace("%", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


class NaverFundamentalAdapter:
    """Naver 모바일 통합 API에서 종목별 펀더멘털을 가져오는 어댑터.

    pykrx/KRX와 동일한 `FundamentalData` 계약을 반환하므로 collector 의
    upsert 경로를 그대로 재사용할 수 있다.
    """

    def __init__(self, timeout: float = 10.0, max_workers: int = 8) -> None:
        """:param timeout: HTTP 타임아웃(초). :param max_workers: 병렬 요청 수."""
        self._timeout = timeout
        self._max_workers = max_workers
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def get_one(
        self, ticker: str, ref_date: datetime.date
    ) -> FundamentalData | None:
        """단일 종목의 펀더멘털을 반환한다. 실패 시 None."""
        url = _INTEGRATION_URL.format(code=ticker)
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Naver 펀더멘털 수집 실패 [%s]: %s", ticker, exc)
            return None

        infos = {
            str(item.get("code")): item.get("value")
            for item in (payload.get("totalInfos") or [])
        }
        if not infos:
            return None

        return FundamentalData(
            date=ref_date,
            ticker=ticker,
            per=_pos_or_none(_to_number(infos.get("per"))),
            pbr=_pos_or_none(_to_number(infos.get("pbr"))),
            eps=_pos_or_none(_to_number(infos.get("eps"))),
            bps=_pos_or_none(_to_number(infos.get("bps"))),
            div=_nonneg_or_none(_to_number(infos.get("dividendYieldRatio"))),
            dps=_nonneg_or_none(_to_number(infos.get("dividend"))),
        )

    def get_many(
        self, tickers: list[str], ref_date: datetime.date
    ) -> list[FundamentalData]:
        """여러 종목의 펀더멘털을 병렬로 가져온다. 실패 종목은 결과에서 제외된다."""
        results: list[FundamentalData] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(self.get_one, ticker, ref_date): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                row = future.result()
                if row is not None:
                    results.append(row)
        logger.info(
            "Naver 펀더멘털 수집 완료 [%s]: %d/%d종목",
            ref_date, len(results), len(tickers),
        )
        return results
