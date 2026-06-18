"""Claude AI 기반 기술적 분석 스코어러.

AWS Bedrock(Claude)를 호출해 OHLCV 데이터로부터 기술적 분석 점수와
적정 매수가·손절가·목표가를 산출한다.

설계 원칙:
- 오류(API 타임아웃, JSON 파싱 실패, AWS 미설정) 시 None 반환 → 기존 점수 유지
- 후보 생성 시점(16:20 KST)에 호출 — 주문 사이클(08:55)과 분리
- 비스트리밍 JSON 응답 (구조화 출력 파싱용)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """당신은 한국 주식시장 기술적 분석 전문가입니다.
아래 데이터를 바탕으로 {ticker}({name})의 단기 매수 적합성을 평가하세요.
전략 유형: {strategy_id}

[기술적 지표 (기준일: {ref_date})]
현재가(전일종가): {close:,}원
MA5: {ma5} / MA20: {ma20} / MA60: {ma60}
정배열 여부: {ma_align}
RSI(14): {rsi14} ({rsi_status})
MACD: {macd} / Signal: {macd_signal} / 히스토그램: {macd_hist}
거래량비율(20일 평균 대비): {vol_ratio:.2f}x
ATR(14): {atr14:,}원
트렌드강도: {ts_score:.1f}/100 ({ts_bucket})
52주 고가: {h52:,}원 / 저가: {l52:,}원

[최근 30거래일 OHLCV]
{recent_ohlcv}

다음 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{
  "technical_score": <0-100 정수, 매수 적합성. 80이상=매우적합, 60-79=적합, 40-59=보통, 20-39=부적합, 0-19=매우부적합>,
  "pattern": "<감지된 주요 기술적 패턴 한 줄 (예: 골든크로스+거래량 확인, 쌍바닥 형성 중)>",
  "support": <주요 지지선 가격 (정수)>,
  "resistance": <주요 저항선 가격 (정수)>,
  "ai_buy_price": <적정 매수가 (정수, 현재가 대비 ±8% 이내)>,
  "ai_stop_price": <손절가 (정수, 주요 지지선 또는 ATR 기반, 반드시 현재가보다 낮게)>,
  "ai_target_price": <3개월 목표가 (정수, 반드시 현재가보다 높게)>,
  "risk_factors": ["<리스크1>", "<리스크2>"],
  "reasoning": "<핵심 근거 2-3문장>"
}}"""


@dataclass(frozen=True)
class AIScore:
    """Claude 기술적 분석 결과."""

    technical_score: float          # 0-100, 매수 적합성 점수
    pattern: str                    # 감지된 주요 패턴
    support: float                  # 지지선
    resistance: float               # 저항선
    ai_buy_price: float             # 적정 매수가
    ai_stop_price: float            # 손절가
    ai_target_price: float          # 목표가
    risk_factors: list[str]         # 리스크 요인
    reasoning: str                  # 핵심 근거
    raw_memo: str                   # DB 저장용 요약 (500자 이내)


class AITechnicalScorer:
    """AWS Bedrock Claude로 기술적 분석 점수와 가격 목표를 산출한다."""

    def __init__(
        self,
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        aws_region: str = "us-east-1",
        model_id: str = "us.anthropic.claude-sonnet-4-6",
    ) -> None:
        self._key_id = aws_access_key_id
        self._key_secret = aws_secret_access_key
        self._region = aws_region
        self._model_id = model_id

    @classmethod
    def from_settings(cls) -> "AITechnicalScorer":
        """MapsSettings에서 인스턴스를 생성한다."""
        from maps.common.settings import get_settings
        s = get_settings()
        return cls(
            aws_access_key_id=s.aws_access_key_id,
            aws_secret_access_key=s.aws_secret_access_key,
            aws_region=s.aws_region,
            model_id=s.aws_bedrock_model_id,
        )

    def score(
        self,
        ticker: str,
        name: str,
        ohlcv_df: pd.DataFrame,
        strategy_id: str,
        ts_score: float,
        ts_bucket: str,
        ref_date: str = "",
    ) -> AIScore | None:
        """OHLCV DataFrame을 분석해 AIScore를 반환한다.

        Args:
            ticker: 종목 코드 (예: "005930")
            name: 종목명 (예: "삼성전자")
            ohlcv_df: HistoricalOHLCVRepository.to_dataframe() 결과.
                      date 인덱스, open/high/low/close/volume 컬럼.
            strategy_id: 전략 ID (예: "pullback_v3")
            ts_score: TrendStrength 점수 (0-100)
            ts_bucket: TrendStrength 버킷 ("S1"~"S5")
            ref_date: 기준일 문자열 (표시용)

        Returns:
            AIScore 또는 None (오류 시)
        """
        if not self._key_id or not self._key_secret:
            logger.debug("[AITechnicalScorer] AWS 자격증명 미설정 — 스킵 (%s)", ticker)
            return None

        if ohlcv_df.empty or len(ohlcv_df) < 30:
            logger.debug("[AITechnicalScorer] OHLCV 데이터 부족 (%s, %d행)", ticker, len(ohlcv_df))
            return None

        try:
            indicators = self._calc_indicators(ohlcv_df)
            prompt = self._build_prompt(
                ticker, name, strategy_id, ref_date, indicators, ohlcv_df, ts_score, ts_bucket
            )
            raw_json = self._call_bedrock(prompt)
            return self._parse_response(raw_json, indicators["close"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AITechnicalScorer] %s 분석 실패: %s", ticker, exc)
            return None

    # ── 내부 메서드 ───────────────────────────────────────────────────────────

    def _calc_indicators(self, df: pd.DataFrame) -> dict:
        """기술적 지표를 사전 계산한다."""
        c = df["close"]
        vol = df["volume"]

        ma5 = c.rolling(5).mean()
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()

        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi14 = (100 - (100 / (1 + rs))).iloc[-1]

        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()

        atr_high_low = df["high"] - df["low"]
        atr_high_prev = (df["high"] - c.shift()).abs()
        atr_low_prev = (df["low"] - c.shift()).abs()
        true_range = pd.concat([atr_high_low, atr_high_prev, atr_low_prev], axis=1).max(axis=1)
        atr14 = true_range.rolling(14).mean().iloc[-1]

        vol_ma20 = vol.rolling(20).mean().iloc[-1]
        vol_ratio = vol.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

        last_close = float(c.iloc[-1])
        last_ma5 = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else 0.0
        last_ma20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else 0.0
        last_ma60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else 0.0
        last_rsi = float(rsi14) if pd.notna(rsi14) else 50.0
        last_macd = float(macd_line.iloc[-1]) if pd.notna(macd_line.iloc[-1]) else 0.0
        last_sig = float(macd_sig.iloc[-1]) if pd.notna(macd_sig.iloc[-1]) else 0.0

        h52 = int(c.tail(252).max())
        l52 = int(c.tail(252).min())

        rsi_status = "과매수" if last_rsi >= 70 else "과매도" if last_rsi <= 30 else "중립"
        ma_align = (
            "정배열" if last_ma5 > last_ma20 > last_ma60 > 0
            else "역배열" if last_ma5 < last_ma20 < last_ma60
            else "혼조"
        )

        return {
            "close": last_close,
            "ma5": f"{last_ma5:,.0f}" if last_ma5 > 0 else "N/A",
            "ma20": f"{last_ma20:,.0f}" if last_ma20 > 0 else "N/A",
            "ma60": f"{last_ma60:,.0f}" if last_ma60 > 0 else "N/A",
            "ma_align": ma_align,
            "rsi14": round(last_rsi, 1),
            "rsi_status": rsi_status,
            "macd": round(last_macd, 1),
            "macd_signal": round(last_sig, 1),
            "macd_hist": round(last_macd - last_sig, 1),
            "vol_ratio": float(vol_ratio),
            "atr14": int(atr14) if pd.notna(atr14) else 0,
            "h52": h52,
            "l52": l52,
        }

    def _build_prompt(
        self,
        ticker: str,
        name: str,
        strategy_id: str,
        ref_date: str,
        ind: dict,
        df: pd.DataFrame,
        ts_score: float,
        ts_bucket: str,
    ) -> str:
        """분석 프롬프트를 생성한다."""
        recent = df.tail(30)[["open", "high", "low", "close", "volume"]].copy()
        ohlcv_lines = ["날짜|시가|고가|저가|종가|거래량"]
        for date_idx, row in recent.iterrows():
            date_str = date_idx.strftime("%m/%d") if hasattr(date_idx, "strftime") else str(date_idx)
            ohlcv_lines.append(
                f"{date_str}|{int(row['open']):,}|{int(row['high']):,}|"
                f"{int(row['low']):,}|{int(row['close']):,}|{int(row['volume']):,}"
            )

        return _PROMPT_TEMPLATE.format(
            ticker=ticker,
            name=name,
            strategy_id=strategy_id,
            ref_date=ref_date,
            close=int(ind["close"]),
            ma5=ind["ma5"],
            ma20=ind["ma20"],
            ma60=ind["ma60"],
            ma_align=ind["ma_align"],
            rsi14=ind["rsi14"],
            rsi_status=ind["rsi_status"],
            macd=ind["macd"],
            macd_signal=ind["macd_signal"],
            macd_hist=ind["macd_hist"],
            vol_ratio=ind["vol_ratio"],
            atr14=ind["atr14"],
            ts_score=ts_score,
            ts_bucket=ts_bucket,
            h52=ind["h52"],
            l52=ind["l52"],
            recent_ohlcv="\n".join(ohlcv_lines),
        )

    def _call_bedrock(self, prompt: str) -> str:
        """AWS Bedrock Claude를 호출하고 응답 텍스트를 반환한다."""
        import boto3

        client_kwargs: dict = {"region_name": self._region}
        if self._key_id and self._key_secret:
            client_kwargs["aws_access_key_id"] = self._key_id
            client_kwargs["aws_secret_access_key"] = self._key_secret

        client = boto3.client("bedrock-runtime", **client_kwargs)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        })

        response = client.invoke_model(
            modelId=self._model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]

    def _parse_response(self, text: str, current_close: float) -> AIScore | None:
        """JSON 응답을 파싱해 AIScore를 반환한다."""
        text = text.strip()
        # JSON 블록 추출 (```json ... ``` 형식 대응)
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("[AITechnicalScorer] JSON 파싱 실패: %s | text=%s", exc, text[:200])
            return None

        score = float(data.get("technical_score", 50))
        score = max(0.0, min(100.0, score))

        def _price(key: str, default: float) -> float:
            val = data.get(key)
            try:
                p = float(val)
                return p if p > 0 else default
            except (TypeError, ValueError):
                return default

        support = _price("support", current_close * 0.95)
        resistance = _price("resistance", current_close * 1.10)
        ai_buy_price = _price("ai_buy_price", current_close)
        ai_stop_price = _price("ai_stop_price", current_close * 0.93)
        ai_target_price = _price("ai_target_price", current_close * 1.15)

        # 이상값 보정
        ai_buy_price = max(current_close * 0.92, min(current_close * 1.08, ai_buy_price))
        ai_stop_price = min(ai_stop_price, current_close * 0.99)
        ai_target_price = max(ai_target_price, current_close * 1.01)

        risk_factors = list(data.get("risk_factors", []))
        reasoning = str(data.get("reasoning", ""))
        pattern = str(data.get("pattern", ""))

        memo = (
            f"score={score:.0f} pattern={pattern[:30]} "
            f"buy={ai_buy_price:.0f} stop={ai_stop_price:.0f} target={ai_target_price:.0f}"
        )[:500]

        return AIScore(
            technical_score=score,
            pattern=pattern,
            support=support,
            resistance=resistance,
            ai_buy_price=ai_buy_price,
            ai_stop_price=ai_stop_price,
            ai_target_price=ai_target_price,
            risk_factors=risk_factors,
            reasoning=reasoning,
            raw_memo=memo,
        )
