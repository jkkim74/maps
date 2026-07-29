"""주식 종합 분석기 — pykrx 시세/기술적지표 + DART 재무제표."""

from __future__ import annotations

import datetime
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Generator
from typing import Any

import pandas as pd
import requests
from pykrx import stock

from maps.data.krx_auth import ensure_krx_login_guard

# pykrx 는 임포트 시점에 1회, 이후 요청마다 재로그인을 시도한다. 임포트 시점
# 1회는 가드보다 앞서므로 막을 수 없지만, 여기서 설치해두면 이후의 무한 재시도가
# 끊긴다 — 계정 잠금을 유발한 것은 그 반복분이다.
ensure_krx_login_guard()

ProgressFn = Callable[[str, int], None]  # (step_label, pct 0-100)


# ── 1. 종목코드 해석 ──────────────────────────────────────────────────────────

def resolve_ticker(arg: str) -> tuple[str, str]:
    """종목명 또는 6자리 코드를 (코드, 종목명) 튜플로 반환한다."""
    arg = arg.strip()
    today = datetime.datetime.now().strftime("%Y%m%d")
    if arg.isdigit() and len(arg) == 6:
        name = stock.get_market_ticker_name(arg)
        if not name:
            raise ValueError(f"종목코드를 찾을 수 없습니다: {arg}")
        return arg, name
    for mkt in ("KOSPI", "KOSDAQ"):
        for t in stock.get_market_ticker_list(today, market=mkt):
            if stock.get_market_ticker_name(t) == arg:
                return t, arg
    raise ValueError(f"종목을 찾을 수 없습니다: {arg}")


# ── 2. 기술적 지표 ────────────────────────────────────────────────────────────

def calc_technicals(code: str) -> dict[str, Any]:
    """RSI·MACD·이동평균선·6개월 차트 데이터를 계산한다."""
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=400)
    df = stock.get_market_ohlcv(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code
    )
    df = df.rename(columns={
        "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume",
    })
    if df.empty:
        return {"error": "시세 데이터 없음"}

    c = df["close"]
    for w in (5, 20, 60, 120):
        df[f"ma{w}"] = c.rolling(w).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    last = df.iloc[-1]
    prev = df.iloc[-2]

    six_m = df.tail(126)
    chart = [
        {"date": d.strftime("%Y-%m-%d"), "close": int(row["close"]), "volume": int(row["volume"])}
        for d, row in six_m.iloc[::5].iterrows()
    ]

    def cross(s_now: float, l_now: float, s_prev: float, l_prev: float) -> str:
        if s_prev <= l_prev and s_now > l_now:
            return "golden_cross"
        if s_prev >= l_prev and s_now < l_now:
            return "dead_cross"
        return "none"

    return {
        "기준일": df.index[-1].strftime("%Y-%m-%d"),
        "현재가": int(last["close"]),
        "전일대비_pct": round((last["close"] / prev["close"] - 1) * 100, 2),
        "52주_고가": int(c.tail(252).max()),
        "52주_저가": int(c.tail(252).min()),
        "이동평균선": {
            f"MA{w}": round(float(last[f"ma{w}"]), 1)
            for w in (5, 20, 60, 120)
            if pd.notna(last[f"ma{w}"])
        },
        "정배열_여부": (
            bool(last["ma20"] > last["ma60"] > last["ma120"])
            if pd.notna(last["ma120"]) else None
        ),
        "20_60_크로스": cross(last["ma20"], last["ma60"], prev["ma20"], prev["ma60"]),
        "RSI14": round(float(last["rsi"]), 1),
        "RSI_상태": (
            "과매수" if last["rsi"] >= 70 else
            "과매도" if last["rsi"] <= 30 else "중립"
        ),
        "MACD": round(float(last["macd"]), 1),
        "MACD_signal": round(float(last["macd_signal"]), 1),
        "MACD_히스토그램": round(float(last["macd_hist"]), 1),
        "MACD_방향": (
            "상승전환" if last["macd_hist"] > 0 and prev["macd_hist"] <= 0 else
            "하락전환" if last["macd_hist"] < 0 and prev["macd_hist"] >= 0 else
            ("상승" if last["macd_hist"] > 0 else "하락")
        ),
        "차트_6개월": chart,
    }


# ── 3. 밸류에이션 ─────────────────────────────────────────────────────────────

def fundamentals(code: str) -> dict[str, Any]:
    """PER·PBR·EPS·시가총액 등 기본 밸류에이션 지표를 조회한다."""
    today = datetime.datetime.now().strftime("%Y%m%d")
    start = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    try:
        f = stock.get_market_fundamental(start, today, code)
        f = f[f.index <= today].iloc[-1]
        cap = stock.get_market_cap(start, today, code).iloc[-1]
        return {
            "PER": round(float(f.get("PER", 0)), 2),
            "PBR": round(float(f.get("PBR", 0)), 2),
            "EPS": round(float(f.get("EPS", 0)), 0),
            "BPS": round(float(f.get("BPS", 0)), 0),
            "DIV_배당수익률": round(float(f.get("DIV", 0)), 2),
            "시가총액_억원": round(float(cap.get("시가총액", 0)) / 1e8, 0),
            "상장주식수": int(cap.get("상장주식수", 0)),
        }
    except Exception as e:
        return {"error": str(e)}


# ── 4. DART 재무제표 ──────────────────────────────────────────────────────────

_DART_BASE = "https://opendart.fss.or.kr/api"

_ACCOUNT_KEYWORDS: dict[str, list[str]] = {
    "매출액": ["매출액", "수익(매출액)", "영업수익"],
    "영업이익": ["영업이익", "영업이익(손실)"],
    "당기순이익": ["당기순이익", "당기순이익(손실)"],
    "자산총계": ["자산총계"],
    "부채총계": ["부채총계"],
    "자본총계": ["자본총계"],
    "영업활동현금흐름": ["영업활동현금흐름", "영업활동으로인한현금흐름"],
}


def dart_corp_code(api_key: str, stock_code: str) -> str | None:
    """corpCode.xml(zip)에서 종목코드 → DART 고유번호를 반환한다."""
    r = requests.get(
        f"{_DART_BASE}/corpCode.xml",
        params={"crtfc_key": api_key},
        timeout=30,
    )
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    for el in root.iter("list"):
        if (el.findtext("stock_code") or "").strip() == stock_code:
            return el.findtext("corp_code")
    return None


def dart_financials(
    api_key: str,
    corp_code: str,
    _report: ProgressFn | None = None,
) -> dict[str, Any]:
    """최근 3개년 주요 재무계정(연결 우선)을 반환한다."""
    this_year = datetime.datetime.now().year
    result: dict[str, Any] = {}
    years = list(range(this_year - 1, this_year - 4, -1))

    for i, yr in enumerate(years):
        if _report:
            # 70 → 95% 구간을 3개년에 균등 분배
            _report(f"재무제표 {yr}년 수집 중…", 70 + i * 9)
        for fs in ("CFS", "OFS"):
            r = requests.get(
                f"{_DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(yr),
                    "reprt_code": "11011",
                    "fs_div": fs,
                },
                timeout=30,
            ).json()
            if r.get("status") != "000":
                continue
            year_data: dict[str, Any] = {}
            for item in r.get("list", []):
                nm = item.get("account_nm", "").replace(" ", "")
                for key, kws in _ACCOUNT_KEYWORDS.items():
                    if any(k.replace(" ", "") == nm for k in kws):
                        val = item.get("thstrm_amount", "").replace(",", "")
                        try:
                            year_data[key] = int(val)
                        except ValueError:
                            pass
            if year_data:
                try:
                    year_data["부채비율_pct"] = round(
                        year_data["부채총계"] / year_data["자본총계"] * 100, 1
                    )
                    year_data["ROE_pct"] = round(
                        year_data["당기순이익"] / year_data["자본총계"] * 100, 1
                    )
                    year_data["영업이익률_pct"] = round(
                        year_data["영업이익"] / year_data["매출액"] * 100, 1
                    )
                except (KeyError, ZeroDivisionError):
                    pass
                result[str(yr)] = year_data
                break
    return result


# ── 5. 통합 분석 진입점 ───────────────────────────────────────────────────────

def _json_default(o: Any) -> Any:
    """pandas/numpy 타입을 JSON 직렬화 가능한 기본 타입으로 변환한다."""
    import numpy as np

    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, pd.DataFrame):
        return o.to_dict()
    return str(o)


def analyze(
    ticker: str,
    dart_api_key: str = "",
    progress_callback: ProgressFn | None = None,
) -> dict[str, Any]:
    """종목명 또는 종목코드를 받아 종합 분석 결과 dict를 반환한다.

    progress_callback(step_label, pct): 각 단계 완료 시 호출된다.
    """
    def report(step: str, pct: int) -> None:
        if progress_callback:
            progress_callback(step, pct)

    report("종목코드 해석 중…", 5)
    code, name = resolve_ticker(ticker)
    report(f"{name} ({code}) 확인", 10)

    report("시세 데이터 수집 중… (pykrx)", 15)
    tech = calc_technicals(code)
    report("기술적 지표 계산 완료", 40)

    report("밸류에이션 데이터 조회 중…", 45)
    val = fundamentals(code)
    report("밸류에이션 조회 완료", 55)

    data: dict[str, Any] = {
        "종목명": name,
        "종목코드": code,
        "수집시각": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "기술적분석": tech,
        "밸류에이션": val,
    }

    if dart_api_key:
        try:
            report("DART 법인코드 조회 중… (10~20초 소요)", 60)
            corp = dart_corp_code(dart_api_key, code)
            if corp:
                data["재무제표_3개년"] = dart_financials(dart_api_key, corp, _report=report)
            else:
                data["재무제표_3개년"] = {"error": "DART corp_code 매핑 실패"}
        except Exception as e:
            data["재무제표_3개년"] = {"error": f"DART 조회 실패: {e}"}
    else:
        data["재무제표_3개년"] = {"error": "DART_API_KEY 미설정"}

    report("데이터 정리 중…", 97)
    return json.loads(json.dumps(data, default=_json_default, ensure_ascii=False))


# ── 6. LLM 종합 분석 (Claude Opus via AWS Bedrock) ───────────────────────────

def stream_llm_analysis(
    data: dict[str, Any],
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
    model_id: str = "us.anthropic.claude-opus-4-8-20251101-v1:0",
) -> Generator[str, None, None]:
    """AWS Bedrock Claude claude-opus-4-8으로 7단계 종합 분석 텍스트를 스트리밍한다.

    차트 시계열 데이터는 LLM 프롬프트에서 제외해 토큰을 절약한다.
    """
    import boto3

    name = data.get("종목명", "")
    code = data.get("종목코드", "")
    collect_time = data.get("수집시각", "")

    ta_raw = data.get("기술적분석", {})
    ta_trimmed = {k: v for k, v in ta_raw.items() if k != "차트_6개월"}
    data_for_llm: dict[str, Any] = {
        k: v for k, v in data.items() if k != "기술적분석"
    }
    data_for_llm["기술적분석"] = ta_trimmed

    data_json = json.dumps(data_for_llm, ensure_ascii=False, indent=2)

    prompt = f"""당신은 한국 주식 시장 전문 애널리스트입니다. 아래 JSON 데이터를 바탕으로 **{name}({code})** 종목에 대한 심층 종합 분석을 수행하세요.

## 기초 데이터 (기준: {collect_time})

```json
{data_json}
```

다음 7단계 분석을 순서대로 작성하세요:

---

### STEP 1: 기업 개요 및 주가 동향
- 기업 특성, 주요 사업, 시장 포지셔닝
- 현재 주가 위치 (52주 고/저가 대비), 최근 가격 흐름

### STEP 2: 기본적 분석
- PER/PBR/EPS/BPS 분석 및 업종·시장 평균 대비 평가 (학습 데이터 기반 업종 평균 활용)
- 3개년 재무제표 YoY 변화율, 매출 CAGR, ROE 추이, 부채비율 적정성

### STEP 3: 최근 실적 및 컨센서스
- 제공된 재무 데이터 기반 실적 분석 (매출, 영업이익, 순이익 추이)
- 재무지표로 추론한 실적 방향성 및 성장성 평가
- 일반적인 실적 발표 시즌 기준 다음 발표 예상 시기

### STEP 4: 기술적 분석
- 이동평균선 배열 (정배열/역배열), 주요 지지/저항선
- RSI·MACD 신호 해석
- 매수/매도/관망 신호 (표 형식):

| 지표 | 현재값 | 신호 | 강도 |
|------|--------|------|------|
| MA20/60 크로스 | | | |
| RSI(14) | | | |
| MACD | | | |

### STEP 5: 업종·산업 분석
- 해당 기업의 사업 특성 기반 업종 트렌드 분석
- 거시 환경(금리, 환율, 무역 정책) 영향 평가
- 동종업계 내 경쟁력 포지션 (재무지표 기반)

### STEP 6: 향후 6개월 주요 임팩트 요인
- 상승 촉매 (3-4가지)
- 하락 리스크 (3-4가지)
- 주요 이벤트 캘린더 (실적, IR, 배당 등)

### STEP 7: 장기 투자 매력도 및 분할매수 전략
- 장기 투자 관점의 매력도 평가 (★★★★☆ 형식)
- 분할매수 목표가 3단계 제시 (1차/2차/3차)
- 목표주가 및 기대 수익률 (6개월~1년)
- 3~5문장의 핵심 투자 결론

---

**주의사항:**
- 핵심 수치는 반드시 표 형식으로 정리하세요
- 기초 데이터의 기준일({collect_time})을 명시하세요
- 마지막에 반드시 다음을 추가하세요:

> *본 분석은 투자 참고용이며 투자 결정의 최종 책임은 투자자 본인에게 있습니다.*
"""

    client_kwargs: dict[str, Any] = {"region_name": aws_region}
    if aws_access_key_id and aws_secret_access_key:
        client_kwargs["aws_access_key_id"] = aws_access_key_id
        client_kwargs["aws_secret_access_key"] = aws_secret_access_key

    client = boto3.client("bedrock-runtime", **client_kwargs)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    })

    response = client.invoke_model_with_response_stream(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"].decode())
        if chunk.get("type") == "content_block_delta":
            delta = chunk.get("delta", {})
            if delta.get("type") == "text_delta":
                yield delta.get("text", "")
