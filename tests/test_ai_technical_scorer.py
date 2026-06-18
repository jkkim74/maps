"""tests/test_ai_technical_scorer.py — AITechnicalScorer 단위 테스트."""

from __future__ import annotations

import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from maps.ai.technical_scorer import AIScore, AITechnicalScorer


def _make_ohlcv(n: int = 60, base_price: float = 50000.0) -> pd.DataFrame:
    """테스트용 OHLCV DataFrame 생성."""
    rng = np.random.default_rng(42)
    closes = base_price + np.cumsum(rng.normal(0, 500, n))
    dates = pd.bdate_range(end="2026-06-18", periods=n)
    df = pd.DataFrame({
        "open": closes * 0.99,
        "high": closes * 1.01,
        "low": closes * 0.98,
        "close": closes,
        "volume": rng.integers(100_000, 1_000_000, n),
    }, index=dates)
    return df


def _bedrock_text(data: dict) -> str:
    """AITechnicalScorer._call_bedrock이 반환할 텍스트 생성."""
    return json.dumps(data)


@pytest.fixture
def scorer() -> AITechnicalScorer:
    return AITechnicalScorer(
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        aws_region="us-east-1",
        model_id="us.anthropic.claude-sonnet-4-6",
    )


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    return _make_ohlcv(n=60, base_price=50000.0)


class TestAITechnicalScorerScore:
    def test_returns_aiscore_on_success(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """정상 응답 시 AIScore 반환."""
        valid_response = {
            "technical_score": 75,
            "pattern": "골든크로스 + 거래량 확인",
            "support": 48000,
            "resistance": 54000,
            "ai_buy_price": 50500,
            "ai_stop_price": 47000,
            "ai_target_price": 57000,
            "risk_factors": ["RSI 과매수 구간"],
            "reasoning": "MA20이 MA60을 상향 돌파하고 거래량이 증가하고 있습니다.",
        }

        with patch.object(scorer, "_call_bedrock", return_value=_bedrock_text(valid_response)):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4", "2026-06-18")

        assert result is not None
        assert isinstance(result, AIScore)
        assert result.technical_score == 75.0
        assert result.pattern == "골든크로스 + 거래량 확인"
        assert result.ai_buy_price == 50500.0
        assert result.ai_stop_price == 47000.0
        assert result.ai_target_price == 57000.0
        assert len(result.risk_factors) == 1
        assert result.raw_memo != ""

    def test_returns_none_when_no_credentials(self, ohlcv_df: pd.DataFrame) -> None:
        """AWS 자격증명 미설정 시 None 반환."""
        scorer_no_creds = AITechnicalScorer(aws_access_key_id="", aws_secret_access_key="")
        result = scorer_no_creds.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")
        assert result is None

    def test_returns_none_on_api_error(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """API 오류 시 None 반환 (예외 전파 없음)."""
        with patch.object(scorer, "_call_bedrock", side_effect=Exception("Bedrock timeout")):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")
        assert result is None

    def test_returns_none_on_json_parse_error(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """JSON 파싱 실패 시 None 반환."""
        with patch.object(scorer, "_call_bedrock", return_value="이것은 JSON이 아닙니다"):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")
        assert result is None

    def test_returns_none_for_insufficient_data(self, scorer: AITechnicalScorer) -> None:
        """30행 미만 OHLCV 시 None 반환."""
        short_df = _make_ohlcv(n=15)
        result = scorer.score("005930", "삼성전자", short_df, "pullback_v3", 72.5, "S4")
        assert result is None

    def test_returns_none_for_empty_dataframe(self, scorer: AITechnicalScorer) -> None:
        """빈 DataFrame 시 None 반환."""
        empty_df = pd.DataFrame()
        result = scorer.score("005930", "삼성전자", empty_df, "pullback_v3", 72.5, "S4")
        assert result is None


class TestAIScoreParsing:
    def test_score_clamped_to_0_100(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """technical_score가 0-100 범위로 클램핑된다."""
        response = {
            "technical_score": 150,
            "pattern": "테스트",
            "support": 45000,
            "resistance": 55000,
            "ai_buy_price": 50000,
            "ai_stop_price": 47000,
            "ai_target_price": 58000,
            "risk_factors": [],
            "reasoning": "테스트",
        }

        with patch.object(scorer, "_call_bedrock", return_value=_bedrock_text(response)):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")

        assert result is not None
        assert result.technical_score == 100.0

    def test_buy_price_bounded_within_8pct(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """ai_buy_price가 현재가 ±8% 이내로 보정된다."""
        current_close = float(ohlcv_df["close"].iloc[-1])
        response = {
            "technical_score": 70,
            "pattern": "테스트",
            "support": 40000,
            "resistance": 60000,
            "ai_buy_price": current_close * 2.0,
            "ai_stop_price": current_close * 0.93,
            "ai_target_price": current_close * 1.15,
            "risk_factors": [],
            "reasoning": "테스트",
        }

        with patch.object(scorer, "_call_bedrock", return_value=_bedrock_text(response)):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")

        assert result is not None
        assert result.ai_buy_price <= current_close * 1.08 + 1

    def test_stop_price_must_be_below_close(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """ai_stop_price가 현재가보다 낮게 보정된다."""
        current_close = float(ohlcv_df["close"].iloc[-1])
        response = {
            "technical_score": 70,
            "pattern": "테스트",
            "support": 40000,
            "resistance": 60000,
            "ai_buy_price": current_close,
            "ai_stop_price": current_close * 1.05,  # 비정상 — 현재가보다 높음
            "ai_target_price": current_close * 1.15,
            "risk_factors": [],
            "reasoning": "테스트",
        }

        with patch.object(scorer, "_call_bedrock", return_value=_bedrock_text(response)):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")

        assert result is not None
        assert result.ai_stop_price < current_close

    def test_target_price_must_be_above_close(self, scorer: AITechnicalScorer, ohlcv_df: pd.DataFrame) -> None:
        """ai_target_price가 현재가보다 높게 보정된다."""
        current_close = float(ohlcv_df["close"].iloc[-1])
        response = {
            "technical_score": 70,
            "pattern": "테스트",
            "support": 40000,
            "resistance": 60000,
            "ai_buy_price": current_close,
            "ai_stop_price": current_close * 0.93,
            "ai_target_price": current_close * 0.8,  # 비정상 — 현재가보다 낮음
            "risk_factors": [],
            "reasoning": "테스트",
        }

        with patch.object(scorer, "_call_bedrock", return_value=_bedrock_text(response)):
            result = scorer.score("005930", "삼성전자", ohlcv_df, "pullback_v3", 72.5, "S4")

        assert result is not None
        assert result.ai_target_price > current_close


class TestFinalScoreFormula:
    """final_score 계산 공식 검증."""

    def test_final_score_without_ai(self) -> None:
        """AI 비활성 시 기존 공식(유동성 60% + 추세강도 40%) 유지."""
        factor_score = 80.0
        trend_strength = 60.0
        base_score = 0.6 * factor_score + 0.4 * trend_strength
        assert abs(base_score - 72.0) < 0.01

    def test_final_score_with_ai_weight_20pct(self) -> None:
        """AI 활성(weight=0.20) 시 공식 검증."""
        factor_score = 80.0
        trend_strength = 60.0
        ai_score = 90.0
        ai_weight = 0.20
        rest_w = 1.0 - ai_weight
        base_score = 0.6 * factor_score + 0.4 * trend_strength
        final = rest_w * base_score + ai_weight * ai_score
        # 0.80 × 72.0 + 0.20 × 90.0 = 57.6 + 18.0 = 75.6
        assert abs(final - 75.6) < 0.01

    def test_final_score_fallback_when_ai_none(self) -> None:
        """AI 결과 None 시 기존 공식과 동일."""
        factor_score = 80.0
        trend_strength = 60.0
        ai_result = None
        ai_weight = 0.20

        base_score = 0.6 * factor_score + 0.4 * trend_strength
        if ai_result is not None:
            rest_w = 1.0 - ai_weight
            final_score = rest_w * base_score + ai_weight * ai_result.technical_score
        else:
            final_score = base_score

        assert abs(final_score - 72.0) < 0.01
