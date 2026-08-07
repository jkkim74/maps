"""Compact derived features and a no-retry Bedrock AI scoring adapter."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from maps.ai.scoring import AIStockScore
from maps.common.exceptions import (
    AIScoringError,
    AIScoringProviderError,
    AIScoringResponseError,
    AIScoringUnavailableError,
)


PROMPT_VERSION = "ai-score-v1"


def _rounded(value: float, digits: int = 4) -> float:
    """Return one finite rounded feature value or reject invalid input data."""
    if not math.isfinite(value):
        raise AIScoringResponseError("Derived feature is not finite")
    return round(float(value), digits)


@dataclass(frozen=True)
class AIStockFeatures:
    """Small normalized indicator set sent to Bedrock for one ticker."""

    ticker: str
    name: str
    ref_date: str
    close: float
    rsi14: float
    macd_pct: float
    macd_signal_pct: float
    macd_hist_pct: float
    volume_ratio: float
    atr_pct: float
    ma20_distance_pct: float
    ma60_distance_pct: float
    return_5d_pct: float
    return_20d_pct: float
    price_52w_position: float
    ma_alignment: str
    breakout: bool
    pullback: bool
    overextended: bool
    trend_strength: float
    ts_bucket: str
    strategy_ids: tuple[str, ...]

    @classmethod
    def from_frame(
        cls,
        *,
        ticker: str,
        name: str,
        ref_date: str,
        frame: pd.DataFrame,
        strategy_ids: tuple[str, ...],
        trend_strength: float,
        ts_bucket: str,
    ) -> "AIStockFeatures":
        """Derive normalized technical indicators without retaining raw bars."""
        required = {"open", "high", "low", "close", "volume"}
        if len(frame) < 60:
            raise AIScoringResponseError("At least 60 OHLCV bars are required")
        if not required.issubset(frame.columns):
            raise AIScoringResponseError("OHLCV frame is missing required columns")
        normalized_strategy_ids = tuple(sorted(set(strategy_ids)))
        if not normalized_strategy_ids:
            raise AIScoringResponseError("At least one strategy ID is required")

        values = frame.sort_index().copy()
        close = pd.to_numeric(values["close"], errors="coerce")
        high = pd.to_numeric(values["high"], errors="coerce")
        low = pd.to_numeric(values["low"], errors="coerce")
        volume = pd.to_numeric(values["volume"], errors="coerce")
        if close.tail(60).isna().any() or high.tail(60).isna().any() or low.tail(60).isna().any():
            raise AIScoringResponseError("OHLCV frame contains invalid price values")

        last_close = float(close.iloc[-1])
        if last_close <= 0:
            raise AIScoringResponseError("Latest close must be positive")
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1])

        delta = close.diff()
        average_gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        average_loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        if pd.isna(average_gain) or pd.isna(average_loss):
            raise AIScoringResponseError("Unable to derive RSI14")
        if float(average_loss) == 0.0:
            rsi14 = 100.0 if float(average_gain) > 0.0 else 50.0
        else:
            relative_strength = float(average_gain) / float(average_loss)
            rsi14 = 100.0 - (100.0 / (1.0 + relative_strength))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()

        true_range = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr14 = float(true_range.rolling(14).mean().iloc[-1])
        volume_average = float(volume.rolling(20).mean().iloc[-1])
        volume_ratio = (
            float(volume.iloc[-1]) / volume_average if volume_average > 0 else 1.0
        )

        recent_close = close.tail(252)
        period_low = float(recent_close.min())
        period_high = float(recent_close.max())
        position = (
            (last_close - period_low) / (period_high - period_low)
            if period_high > period_low
            else 0.5
        )
        ma20_distance = (last_close / ma20 - 1.0) * 100.0
        ma60_distance = (last_close / ma60 - 1.0) * 100.0
        ma_alignment = (
            "BULLISH"
            if last_close > ma20 > ma60
            else "BEARISH"
            if last_close < ma20 < ma60
            else "MIXED"
        )
        prior_20_high = float(close.iloc[-21:-1].max())

        return cls(
            ticker=ticker,
            name=name,
            ref_date=str(ref_date),
            close=_rounded(last_close, 2),
            rsi14=_rounded(rsi14, 2),
            macd_pct=_rounded(float(macd.iloc[-1]) / last_close * 100.0),
            macd_signal_pct=_rounded(
                float(macd_signal.iloc[-1]) / last_close * 100.0
            ),
            macd_hist_pct=_rounded(
                float(macd.iloc[-1] - macd_signal.iloc[-1])
                / last_close
                * 100.0
            ),
            volume_ratio=_rounded(volume_ratio, 3),
            atr_pct=_rounded(atr14 / last_close * 100.0, 3),
            ma20_distance_pct=_rounded(ma20_distance, 3),
            ma60_distance_pct=_rounded(ma60_distance, 3),
            return_5d_pct=_rounded(
                (last_close / float(close.iloc[-6]) - 1.0) * 100.0, 3
            ),
            return_20d_pct=_rounded(
                (last_close / float(close.iloc[-21]) - 1.0) * 100.0, 3
            ),
            price_52w_position=_rounded(position, 4),
            ma_alignment=ma_alignment,
            breakout=last_close >= prior_20_high,
            pullback=abs(ma20_distance) <= 3.0 and last_close >= ma60,
            overextended=ma20_distance >= 8.0 or rsi14 >= 75.0,
            trend_strength=_rounded(float(trend_strength), 2),
            ts_bucket=ts_bucket,
            strategy_ids=normalized_strategy_ids,
        )

    def to_payload(self) -> dict[str, object]:
        """Return the stable compact request payload."""
        return {
            "ticker": self.ticker,
            "name": self.name,
            "ref_date": self.ref_date,
            "close": self.close,
            "rsi14": self.rsi14,
            "macd_pct": self.macd_pct,
            "macd_signal_pct": self.macd_signal_pct,
            "macd_hist_pct": self.macd_hist_pct,
            "volume_ratio": self.volume_ratio,
            "atr_pct": self.atr_pct,
            "ma20_distance_pct": self.ma20_distance_pct,
            "ma60_distance_pct": self.ma60_distance_pct,
            "return_5d_pct": self.return_5d_pct,
            "return_20d_pct": self.return_20d_pct,
            "price_52w_position": self.price_52w_position,
            "ma_alignment": self.ma_alignment,
            "breakout": self.breakout,
            "pullback": self.pullback,
            "overextended": self.overextended,
            "trend_strength": self.trend_strength,
            "ts_bucket": self.ts_bucket,
            "strategy_ids": list(self.strategy_ids),
        }

    def canonical_json(self) -> str:
        """Serialize features deterministically for hashing and provider input."""
        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class BedrockScoreResponse:
    """Validated score and provider usage metadata for one network call."""

    score: AIStockScore
    input_tokens: int
    output_tokens: int
    raw_payload: dict[str, object]


class AITechnicalScorer:
    """Invoke Bedrock once with compact features and strict structured output."""

    def __init__(
        self,
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        aws_region: str = "us-east-1",
        model_id: str = "us.anthropic.claude-sonnet-4-6",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._key_id = aws_access_key_id
        self._key_secret = aws_secret_access_key
        self._region = aws_region
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "AITechnicalScorer":
        """Build the adapter from central Phase 2 scoring settings."""
        from maps.common.settings import get_settings

        settings = get_settings()
        return cls(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_region=settings.aws_region,
            model_id=settings.maps_ai_scoring_model_id,
            timeout_seconds=settings.maps_ai_request_timeout_seconds,
        )

    @property
    def is_configured(self) -> bool:
        """Return whether explicit access-key credentials are available."""
        return bool(self._key_id and self._key_secret)

    @property
    def model_id(self) -> str:
        """Return the configured model identifier for logs and persistence."""
        return self._model_id

    def _system_prompt(self) -> str:
        """Return static rubric instructions with no ticker-specific content."""
        return (
            "Evaluate Korean equity entry suitability using only the supplied derived "
            "indicators. Return rubric components, not a total: trend 0-25, momentum "
            "0-20, volume 0-15, risk 0-15 where safer is higher, timing 0-15, and one "
            "strategy_fit 0-10 for every requested strategy ID. Return confidence 0-1 "
            "and at most three allowed reason codes. Do not create buy, stop, or target "
            "prices. Use contrarian_opinion NONE and contrarian_score null unless the "
            "features justify the optional combined contrarian assessment."
        )

    def _response_schema(self) -> dict[str, object]:
        """Return one stable JSON Schema shared by every strategy combination."""
        return AIStockScore.model_json_schema()

    def _request_body(self, features: AIStockFeatures) -> dict[str, object]:
        """Build the structured Anthropic Messages request body."""
        output_config: dict[str, object] = {
            "format": {
                "type": "json_schema",
                "schema": self._response_schema(),
            }
        }
        body: dict[str, object] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": self._system_prompt(),
            "messages": [
                {"role": "user", "content": features.canonical_json()}
            ],
            "output_config": output_config,
        }
        if "sonnet-4-6" in self._model_id:
            body["thinking"] = {"type": "adaptive"}
            output_config["effort"] = "low"
        return body

    def _invoke(self, body: Mapping[str, object]) -> dict[str, object]:
        """Make one Bedrock Runtime request with SDK retry disabled."""
        import boto3
        from botocore.config import Config

        config = Config(
            connect_timeout=min(10.0, self._timeout_seconds),
            read_timeout=self._timeout_seconds,
            retries={"max_attempts": 0, "mode": "standard"},
        )
        client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=config,
            aws_access_key_id=self._key_id,
            aws_secret_access_key=self._key_secret,
        )
        response = client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body, ensure_ascii=False),
            contentType="application/json",
            accept="application/json",
        )
        raw_body = response["body"].read()
        decoded = json.loads(raw_body)
        if not isinstance(decoded, dict):
            raise AIScoringResponseError("Bedrock response must be an object")
        return decoded

    def score(self, features: AIStockFeatures) -> BedrockScoreResponse:
        """Invoke Bedrock once and validate its structured score payload."""
        if not self.is_configured:
            raise AIScoringUnavailableError("AWS credentials are not configured")
        try:
            response = self._invoke(self._request_body(features))
        except AIScoringError:
            raise
        except Exception as exc:
            raise AIScoringProviderError(type(exc).__name__) from exc

        content = response.get("content")
        if not isinstance(content, list):
            raise AIScoringResponseError("Bedrock response content is missing")
        text = next(
            (
                block.get("text")
                for block in content
                if isinstance(block, Mapping)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ),
            None,
        )
        if not text:
            raise AIScoringResponseError("Bedrock response has no text block")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIScoringResponseError("Bedrock returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AIScoringResponseError("AI score payload must be an object")
        score = AIStockScore.from_payload(payload, features.strategy_ids)
        usage = response.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return BedrockScoreResponse(
            score=score,
            input_tokens=int(input_tokens) if isinstance(input_tokens, int) else 0,
            output_tokens=int(output_tokens) if isinstance(output_tokens, int) else 0,
            raw_payload=score.to_payload(),
        )
