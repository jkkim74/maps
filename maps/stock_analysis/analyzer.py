"""주식 종합 분석기 — pykrx 시세/기술적지표 + DART 재무제표."""

from __future__ import annotations

import datetime
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from typing import Any

import pandas as pd
import requests
from pykrx import stock

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
