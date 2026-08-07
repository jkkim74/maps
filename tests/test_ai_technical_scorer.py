"""Compact feature and Bedrock adapter tests for Phase 2 AI scoring."""

from __future__ import annotations

import json
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from maps.ai.technical_scorer import AIStockFeatures, AITechnicalScorer
from maps.common.exceptions import (
    AIScoringProviderError,
    AIScoringResponseError,
    AIScoringUnavailableError,
)


VALID_PAYLOAD = {
    "trend": 21,
    "momentum": 15,
    "volume": 11,
    "risk": 12,
    "timing": 10,
    "strategy_fit": [{"strategy_id": "pullback_v3", "score": 8}],
    "confidence": 0.82,
    "reason_codes": ["UPTREND", "HEALTHY_PULLBACK", "VOLUME_WEAK"],
    "contrarian_opinion": "NONE",
    "contrarian_score": None,
}


def _make_ohlcv(n: int = 80, base_price: float = 50_000.0) -> pd.DataFrame:
    """Build deterministic OHLCV bars for feature tests."""
    rng = np.random.default_rng(42)
    closes = base_price + np.cumsum(rng.normal(40, 450, n))
    dates = pd.bdate_range(end="2026-06-18", periods=n)
    return pd.DataFrame(
        {
            "open": closes * 0.99,
            "high": closes * 1.01,
            "low": closes * 0.98,
            "close": closes,
            "volume": rng.integers(100_000, 1_000_000, n),
        },
        index=dates,
    )


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """Return a valid feature frame with enough history for MA60."""
    return _make_ohlcv()


@pytest.fixture
def features(ohlcv_df: pd.DataFrame) -> AIStockFeatures:
    """Return compact features for one strategy."""
    return AIStockFeatures.from_frame(
        ticker="005930",
        name="삼성전자",
        ref_date="2026-06-18",
        frame=ohlcv_df,
        strategy_ids=("pullback_v3",),
        trend_strength=72.5,
        ts_bucket="S4",
    )


@pytest.fixture
def scorer() -> AITechnicalScorer:
    """Return a configured scorer whose network boundary can be patched."""
    return AITechnicalScorer(
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        aws_region="us-east-1",
        model_id="us.anthropic.claude-sonnet-4-6",
    )


def test_features_use_derived_values_without_raw_ohlcv(
    ohlcv_df: pd.DataFrame,
) -> None:
    """Features expose normalized indicators but no raw bar table."""
    built = AIStockFeatures.from_frame(
        ticker="005930",
        name="삼성전자",
        ref_date="2026-06-18",
        frame=ohlcv_df,
        strategy_ids=("pullback_v3",),
        trend_strength=72.5,
        ts_bucket="S4",
    )

    payload = built.to_payload()
    assert payload["ticker"] == "005930"
    assert payload["strategy_ids"] == ["pullback_v3"]
    assert "rsi14" in payload and "atr_pct" in payload
    assert "recent_ohlcv" not in payload
    assert "open" not in payload and "volume" not in payload
    assert len(built.canonical_json()) < 1800


def test_features_require_sixty_bars_for_ma60() -> None:
    """Frames too short for MA60 are rejected before any request."""
    with pytest.raises(AIScoringResponseError):
        AIStockFeatures.from_frame(
            ticker="005930",
            name="삼성전자",
            ref_date="2026-06-18",
            frame=_make_ohlcv(n=59),
            strategy_ids=("pullback_v3",),
            trend_strength=72.5,
            ts_bucket="S4",
        )


def test_bedrock_request_uses_structured_output_and_low_effort(
    scorer: AITechnicalScorer,
    features: AIStockFeatures,
) -> None:
    """Sonnet requests use structured output and approved inference controls."""
    body = scorer._request_body(features)

    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    schema = json.dumps(body["output_config"]["format"]["schema"])
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "maxItems"):
        assert f'"{unsupported}"' not in schema
    assert body["max_tokens"] == 1024
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_static_system_prompt_does_not_contain_ticker_or_name(
    scorer: AITechnicalScorer,
    features: AIStockFeatures,
) -> None:
    """Static instructions remain reusable and contain no ticker-specific data."""
    system = scorer._system_prompt()

    assert features.ticker not in system
    assert features.name not in system
    assert len(system + features.canonical_json()) < 5000


def test_score_parses_usage_and_validated_payload(
    scorer: AITechnicalScorer,
    features: AIStockFeatures,
    monkeypatch,
) -> None:
    """The adapter skips thinking blocks and records provider token usage."""
    response = {
        "content": [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": json.dumps(VALID_PAYLOAD)},
        ],
        "usage": {"input_tokens": 410, "output_tokens": 86},
    }
    monkeypatch.setattr(scorer, "_invoke", lambda _body: response)

    result = scorer.score(features)

    assert result.score.score_for("pullback_v3") == 77
    assert result.input_tokens == 410
    assert result.output_tokens == 86


def test_missing_credentials_raise_unavailable(features: AIStockFeatures) -> None:
    """Missing explicit credentials are distinguished from provider failures."""
    scorer = AITechnicalScorer(aws_access_key_id="", aws_secret_access_key="")

    with pytest.raises(AIScoringUnavailableError):
        scorer.score(features)


def test_provider_error_is_not_retried(
    scorer: AITechnicalScorer,
    features: AIStockFeatures,
    monkeypatch,
) -> None:
    """One provider failure consumes one attempt and is not retried."""
    invoke = Mock(side_effect=TimeoutError("timeout"))
    monkeypatch.setattr(scorer, "_invoke", invoke)

    with pytest.raises(AIScoringProviderError):
        scorer.score(features)
    assert invoke.call_count == 1
