"""KIS 주식잔고조회(inquire-balance) 원시 HTTP 응답 진단 스크립트.

운영 중인 broker_sync가 `inquire-balance`에서 HTTP 500을 받는데, 정상 경로
(`KISAdapter._send_with_retry`)는 500 응답의 **본문과 헤더를 버린다**. 고객의소리
접수에는 응답 Body가 필요하므로, 이 스크립트는 어댑터와 동일한 headers/params로
호출하되 재시도/파싱을 우회해 **원시 status·headers·body**를 그대로 출력한다.

순수 조회만 수행한다 — 주문/취소 등 매매성 호출은 일절 없다.

실행(운영 서버):
    cd /opt/maps && .venv/bin/python -m scripts.diag_kis_balance

선택: 파라미터 변형 비교(서버측 장애 확정용) — 환경변수 DIAG_VARIANTS=1
"""

from __future__ import annotations

import datetime as dt
import os

from maps.execution.kis_adapter import _BALANCE_PATH, KISAdapter

_KST = dt.timezone(dt.timedelta(hours=9))

# KIS 추적/진단에 유용한 응답 헤더 키 (대소문자 무시 매칭)
_TRACE_HEADERS = ("gt_uid", "tr_cont", "tr_id", "content-type", "date")


def _base_params(adapter: KISAdapter) -> dict[str, str]:
    """`KISAdapter._fetch_balance_data`와 동일한 잔고조회 파라미터."""
    return {
        "CANO": adapter._account_prefix,
        "ACNT_PRDT_CD": adapter._account_product_code,
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


def _headers(adapter: KISAdapter) -> dict[str, str]:
    """`KISAdapter._request`와 동일한 헤더(잔고조회 TR_ID)."""
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {adapter._ensure_token()}",
        "appkey": adapter._app_key,
        "appsecret": adapter._app_secret,
        "tr_id": adapter._tr_id("balance"),
        "custtype": "P",
    }


def _call_once(adapter: KISAdapter, params: dict[str, str], label: str) -> None:
    """재시도/파싱 우회 — requests로 직접 1회 호출 후 원시 응답 출력."""
    url = adapter._url(_BALANCE_PATH)
    sent_at = dt.datetime.now(_KST)
    print("=" * 78)
    print(f"[{label}] 요청시각: {sent_at.isoformat()}")
    print(f"  URL     : {url}")
    print(f"  TR_ID   : {adapter._tr_id('balance')} (모의={'N' if adapter._real else 'Y'})")
    print(f"  PARAMS  : {params}")
    try:
        resp = adapter._http.get(
            url,
            headers=_headers(adapter),
            params=params,
            timeout=adapter._timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 진단 목적
        print(f"  >>> 요청 예외: {type(exc).__name__}: {exc}")
        return

    print(f"  HTTP    : {resp.status_code} {resp.reason}")
    print("  RESP HEADERS (추적용):")
    for key in _TRACE_HEADERS:
        for hk, hv in resp.headers.items():
            if hk.lower() == key:
                print(f"    {hk}: {hv}")
    print("  RESP BODY (원문):")
    body = resp.text or ""
    print(body if len(body) <= 4000 else body[:4000] + " ...[truncated]")
    print()


def main() -> None:
    adapter = KISAdapter()
    print(
        f"KIS 잔고조회 진단 시작 — account={adapter._account_no} "
        f"base_url={adapter._base_url}"
    )
    _call_once(adapter, _base_params(adapter), "표준 호출 (broker_sync와 동일)")

    if os.getenv("DIAG_VARIANTS") == "1":
        # 서버측 장애 확정용: 파라미터를 바꿔도 동일 500인지 비교
        v1 = _base_params(adapter)
        v1["INQR_DVSN"] = "01"  # 대출일별
        _call_once(adapter, v1, "변형1 INQR_DVSN=01")

        v2 = _base_params(adapter)
        v2["PRCS_DVSN"] = "00"  # 전일매매포함
        _call_once(adapter, v2, "변형2 PRCS_DVSN=00")


if __name__ == "__main__":
    main()
