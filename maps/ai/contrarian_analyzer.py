"""코스톨라니식 역발상 투자 논리 검증 분석기.

AWS Bedrock(Claude)를 호출해 종목이 코스톨라니 역발상 관점에서
투자 가치가 있는지 검증한다.

설계 원칙:
- AI가 매수 신호를 생성하는 것이 아니라 rule-based 결과를 검증(리스크 리뷰)한다.
- REJECT → 후보에서 제외 또는 final_score 감산
- WATCH → position_size 축소 (50%)
- PASS → rule-based risk check 통과 시 진입 허용
- AI 오류(API 타임아웃, JSON 파싱 실패) 시 rule-based fallback 반환
- maps_ai_contrarian_check_enabled=false 시 skip
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CONTRARIAN_PROMPT_TEMPLATE = """당신은 코스톨라니(André Kostolany) 스타일의 가치 투자자입니다.
아래 데이터를 바탕으로 {ticker}({name})에 대한 역발상 투자 타당성을 평가하세요.

[기본 정보 (기준일: {ref_date})]
현재가: {close:,}원
52주 고점 대비 하락률: {drop_pct:.1f}%
거래대금 점수(유동성): {liquidity_score:.1f}/100
추세 강도: {trend_strength:.1f}/100
가치 안전마진 점수: {valuation_margin_score}/100
업종: {sector}
테마: {theme}

[밸류에이션 지표]
PER: {per}
PBR: {pbr}
ROE: {roe}%
부채비율: {debt_ratio}%
영업이익 성장률(YoY): {op_growth}%
컨센서스 수정 방향: {consensus_revision}

[시장 심리]
시장 국면: {market_regime}
섹터 분위기: {sector_sentiment}

다음 10가지 관점에서 분석하고 JSON으로만 응답하세요. 다른 텍스트 불포함:
1. 이 종목을 지금 사면 안 되는 이유
2. 시장 기대가 이미 과도하게 반영됐는가
3. 악재가 주가에 충분히 반영됐는가
4. 이 가격이 가치 기반 안전마진 구간인가 아니면 계속 하락하는 구간인가
5. 업황 사이클이 초입/중반/후반 중 어디인가
6. 이 가격에서 6개월 이상 기다릴 수 있는 종목인가
7. 추가 하락 시 추가 매수 가능한 종목인가
8. 단순 하락 추격인가
9. 실적과 주가의 괴리가 있는가
10. 매도 전제가 단순 차트 이탈인지 투자 thesis 피훼인지 구분 가능한가

{{
  "contrarian_score": <0-100, 역발상 투자 매력도. 높을수록 역발상 기회>,
  "crowd_risk": "<LOW|NORMAL|HIGH, 대중이 몰려있는 위험>",
  "expectation_risk": "<LOW|NORMAL|HIGH, 기대가 이미 과도하게 반영된 위험>",
  "bad_news_priced_in": <true|false, 악재가 주가에 반영됐는지>,
  "can_hold_6m": <true|false, 6개월 이상 보유 가능한지>,
  "can_add_on_drop": <true|false, 추가 하락 시 추가 매수 가능한지>,
  "is_theme_chasing": <true|false, 단순 테마 추격인지>,
  "business_cycle_stage": "<EARLY|MID|LATE|UNKNOWN, 업황 사이클 단계>",
  "thesis_summary": "<투자 thesis 요약 2-3문장>",
  "anti_thesis": "<반론(투자하지 말아야 할 이유) 2-3문장>",
  "invalidation_points": ["<thesis 무효화 조건 1>", "<조건 2>", "<조건 3>"],
  "final_opinion": "<PASS|WATCH|REJECT>",
  "reason": "<최종 판단 근거 1-2문장>"
}}"""


@dataclass(frozen=True)
class ContrarianAnalysisResult:
    """AI 역발상 검증 결과."""

    ticker: str
    contrarian_score: float          # 0-100, 역발상 매력도
    crowd_risk: str                  # LOW|NORMAL|HIGH
    expectation_risk: str            # LOW|NORMAL|HIGH
    bad_news_priced_in: bool
    can_hold_6m: bool
    can_add_on_drop: bool
    is_theme_chasing: bool
    business_cycle_stage: str        # EARLY|MID|LATE|UNKNOWN
    thesis_summary: str
    anti_thesis: str
    invalidation_points: list[str] = field(default_factory=list)
    final_opinion: str = "WATCH"     # PASS|WATCH|REJECT
    reason: str = ""
    is_fallback: bool = False        # True면 rule-based fallback 결과

    def position_size_multiplier(self) -> float:
        """final_opinion에 따른 포지션 비중 조정 배율."""
        if self.final_opinion == "PASS":
            return 1.0
        if self.final_opinion == "WATCH":
            return 0.5
        return 0.0  # REJECT


def _rule_based_fallback(ticker: str, valuation_score: float, drop_pct: float) -> ContrarianAnalysisResult:
    """AI 오류 시 rule-based 결과를 반환한다."""
    if valuation_score >= 65 and drop_pct >= 20:
        opinion = "WATCH"
        score = 55.0
        reason = "rule-based fallback: valuation_margin_score ≥ 65 and drop ≥ 20%"
    elif valuation_score >= 50 and drop_pct >= 15:
        opinion = "WATCH"
        score = 45.0
        reason = "rule-based fallback: partial valuation margin with moderate drop"
    else:
        opinion = "REJECT"
        score = 30.0
        reason = "rule-based fallback: insufficient valuation margin or drop"

    return ContrarianAnalysisResult(
        ticker=ticker,
        contrarian_score=score,
        crowd_risk="NORMAL",
        expectation_risk="NORMAL",
        bad_news_priced_in=drop_pct >= 20,
        can_hold_6m=valuation_score >= 65,
        can_add_on_drop=valuation_score >= 65,
        is_theme_chasing=False,
        business_cycle_stage="UNKNOWN",
        thesis_summary="rule-based analysis (AI unavailable)",
        anti_thesis="insufficient data for AI analysis",
        invalidation_points=["valuation_score_drops_below_50"],
        final_opinion=opinion,
        reason=reason,
        is_fallback=True,
    )


class AIContrarianAnalyzer:
    """AWS Bedrock Claude를 코스톨라니식 투자 논리 검증 엔진으로 활용.

    AI가 매수 신호를 생성하는 역할이 아니라, rule-based 분석 결과를
    역발상 투자 관점에서 검증한다.
    """

    def __init__(
        self,
        *,
        model_id: str = "us.anthropic.claude-sonnet-4-6",
        region: str = "us-east-1",
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._access_key = aws_access_key_id
        self._secret_key = aws_secret_access_key
        self._timeout = timeout
        self._client = None

    @classmethod
    def from_settings(cls) -> "AIContrarianAnalyzer":
        """설정 기반 인스턴스를 생성한다."""
        from maps.common.settings import get_settings
        s = get_settings()
        return cls(
            model_id=s.aws_bedrock_model_id,
            region=s.aws_region,
            aws_access_key_id=s.aws_access_key_id,
            aws_secret_access_key=s.aws_secret_access_key,
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            session_kwargs: dict = {"region_name": self._region}
            if self._access_key and self._secret_key:
                session_kwargs["aws_access_key_id"] = self._access_key
                session_kwargs["aws_secret_access_key"] = self._secret_key
            session = boto3.Session(**session_kwargs)
            self._client = session.client("bedrock-runtime")
            return self._client
        except Exception as exc:
            logger.warning("Bedrock 클라이언트 초기화 실패: %s", exc)
            return None

    def analyze(
        self,
        ticker: str,
        name: str = "",
        *,
        ref_date: str = "",
        close: float = 0.0,
        drop_pct: float = 0.0,
        liquidity_score: float = 50.0,
        trend_strength: float = 50.0,
        valuation_margin_score: float = 50.0,
        sector: str = "",
        theme: str = "",
        per: float | None = None,
        pbr: float | None = None,
        roe: float | None = None,
        debt_ratio: float | None = None,
        op_growth: float | None = None,
        consensus_revision: float | None = None,
        market_regime: str = "mixed",
        sector_sentiment: str = "neutral",
    ) -> ContrarianAnalysisResult:
        """코스톨라니식 역발상 투자 타당성을 AI로 검증한다.

        AWS Bedrock 오류 시 rule-based fallback을 반환한다.
        """
        client = self._get_client()
        if client is None:
            return _rule_based_fallback(ticker, valuation_margin_score, drop_pct)

        prompt = _CONTRARIAN_PROMPT_TEMPLATE.format(
            ticker=ticker,
            name=name or ticker,
            ref_date=ref_date,
            close=close,
            drop_pct=drop_pct,
            liquidity_score=liquidity_score,
            trend_strength=trend_strength,
            valuation_margin_score=valuation_margin_score,
            sector=sector or "미분류",
            theme=theme or "미분류",
            per=f"{per:.1f}" if per is not None else "N/A",
            pbr=f"{pbr:.2f}" if pbr is not None else "N/A",
            roe=f"{roe:.1f}" if roe is not None else "N/A",
            debt_ratio=f"{debt_ratio:.1f}" if debt_ratio is not None else "N/A",
            op_growth=f"{op_growth:.1f}" if op_growth is not None else "N/A",
            consensus_revision=f"{consensus_revision:+.1f}" if consensus_revision is not None else "N/A",
            market_regime=market_regime,
            sector_sentiment=sector_sentiment,
        )

        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            })
            response = client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            raw = json.loads(response["body"].read())
            text = raw["content"][0]["text"].strip()
            # JSON 블록 추출
            if "```" in text:
                text = text.split("```")[-2] if text.count("```") >= 2 else text.replace("```json", "").replace("```", "")
            data = json.loads(text)
            return self._parse_response(ticker, data)
        except Exception as exc:
            logger.warning("AI 역발상 분석 실패 [%s]: %s — rule-based fallback 사용", ticker, exc)
            return _rule_based_fallback(ticker, valuation_margin_score, drop_pct)

    @staticmethod
    def _parse_response(ticker: str, data: dict) -> ContrarianAnalysisResult:
        opinion = str(data.get("final_opinion", "WATCH")).upper()
        if opinion not in ("PASS", "WATCH", "REJECT"):
            opinion = "WATCH"

        return ContrarianAnalysisResult(
            ticker=ticker,
            contrarian_score=float(data.get("contrarian_score", 50)),
            crowd_risk=str(data.get("crowd_risk", "NORMAL")).upper(),
            expectation_risk=str(data.get("expectation_risk", "NORMAL")).upper(),
            bad_news_priced_in=bool(data.get("bad_news_priced_in", False)),
            can_hold_6m=bool(data.get("can_hold_6m", False)),
            can_add_on_drop=bool(data.get("can_add_on_drop", False)),
            is_theme_chasing=bool(data.get("is_theme_chasing", False)),
            business_cycle_stage=str(data.get("business_cycle_stage", "UNKNOWN")).upper(),
            thesis_summary=str(data.get("thesis_summary", "")),
            anti_thesis=str(data.get("anti_thesis", "")),
            invalidation_points=list(data.get("invalidation_points", [])),
            final_opinion=opinion,
            reason=str(data.get("reason", "")),
            is_fallback=False,
        )
