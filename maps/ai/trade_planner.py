"""Fail-closed structured Bedrock adapter for stock trade plans."""

from __future__ import annotations

import json
import math
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from maps.common.exceptions import (
    AIScoringError,
    AIScoringProviderError,
    AIScoringResponseError,
    AIScoringUnavailableError,
)


PROMPT_VERSION = "trade-plan-v1"


class StockTradeFacts(BaseModel):
    """Bounded stock facts supplied to the trade-planning model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str
    name: str
    market: str = "KOSPI"
    ref_date: str
    current_price: float
    high_52w: float | None = None
    low_52w: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    per: float | None = None
    pbr: float | None = None
    bps: float | None = None

    @model_validator(mode="after")
    def validate_facts(self) -> "StockTradeFacts":
        values = (
            self.current_price,
            self.high_52w,
            self.low_52w,
            self.ma20,
            self.ma60,
            self.ma120,
            self.rsi14,
            self.macd,
            self.macd_signal,
            self.per,
            self.pbr,
            self.bps,
        )
        if not self.ticker.strip() or not self.name.strip() or not self.ref_date.strip():
            raise ValueError("ticker, name, and ref_date are required")
        if self.current_price <= 0:
            raise ValueError("current_price must be positive")
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("stock facts must contain only finite values")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class AITradePlan(BaseModel):
    """Strict provider output; every recommendation carries executable prices.

    WATCH/SELL 도 entries/target/stop 을 반드시 갖는다 — 검증자가 세 값을 필수로
    강제하기 때문이다. 즉 **가격의 존재는 매수 권고가 아니다.** 하류(화면·무장·주문)
    는 가격만 보고 매수로 해석하면 안 되고 `recommendation` 을 함께 읽어야 한다.
    (예전 docstring 은 "only BUY may contain executable prices" 라고 적어 코드에 없는
    안전장치를 있다고 주장했고, 그 오독으로 WATCH 계획이 그대로 체결된 적이 있다.)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation: Literal["BUY", "WATCH", "SELL"]
    entries: tuple[float, float, float]
    target: float
    stop: float
    rationale: str

    @model_validator(mode="after")
    def validate_price_contract(self) -> "AITradePlan":
        prices = (*self.entries, self.target, self.stop)
        if any(price is None or not math.isfinite(price) or price <= 0 for price in prices):
            raise ValueError("trade plan prices must be finite and positive")
        entry1, entry2, entry3 = self.entries
        if not self.target > entry1 > entry2 > entry3 > self.stop:
            raise ValueError("prices must satisfy target > entry1 > entry2 > entry3 > stop")
        return self

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "AITradePlan":
        try:
            return cls.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as exc:
            raise AIScoringResponseError("Invalid structured AI trade plan") from exc

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class AITradePlanner:
    """Invoke Bedrock once and validate a structured trade plan."""

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
    def from_settings(cls) -> "AITradePlanner":
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
        return bool(self._key_id and self._key_secret)

    def _system_prompt(self) -> str:
        return (
            "Use only the supplied Korean-equity facts. Return BUY, WATCH, or SELL. "
            "For every recommendation, provide exactly three descending staged-buy "
            "limit prices plus one higher target and one lower stop. Recommendation is "
            "advisory; prices describe the analyzed entry plan. Do not choose quantities "
            "or a budget and do not invent missing facts."
        )

    def _response_schema(self) -> dict[str, object]:
        schema = AITradePlan.model_json_schema()
        unsupported = {
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "maxItems",
            "minItems",
        }

        def strip_constraints(value: object) -> None:
            if isinstance(value, dict):
                for key in unsupported & value.keys():
                    del value[key]
                prefix_items = value.pop("prefixItems", None)
                if prefix_items:
                    value["items"] = prefix_items[0]
                for child in value.values():
                    strip_constraints(child)
            elif isinstance(value, list):
                for child in value:
                    strip_constraints(child)

        strip_constraints(schema)
        return schema

    def _request_body(self, facts: StockTradeFacts) -> dict[str, object]:
        output_config: dict[str, object] = {
            "format": {"type": "json_schema", "schema": self._response_schema()}
        }
        body: dict[str, object] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 768,
            "system": self._system_prompt(),
            "messages": [{"role": "user", "content": facts.canonical_json()}],
            "output_config": output_config,
        }
        if "sonnet-4-6" in self._model_id:
            body["thinking"] = {"type": "adaptive"}
            output_config["effort"] = "low"
        return body

    def _invoke(self, body: Mapping[str, object]) -> dict[str, object]:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=Config(
                connect_timeout=min(10.0, self._timeout_seconds),
                read_timeout=self._timeout_seconds,
                retries={"max_attempts": 0, "mode": "standard"},
            ),
            aws_access_key_id=self._key_id,
            aws_secret_access_key=self._key_secret,
        )
        response = client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body, ensure_ascii=False),
            contentType="application/json",
            accept="application/json",
        )
        decoded = json.loads(response["body"].read())
        if not isinstance(decoded, dict):
            raise AIScoringResponseError("Bedrock response must be an object")
        return decoded

    def plan(self, facts: StockTradeFacts) -> AITradePlan:
        if not self.is_configured:
            raise AIScoringUnavailableError("AWS credentials are not configured")
        try:
            response = self._invoke(self._request_body(facts))
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
            raise AIScoringResponseError("AI trade-plan payload must be an object")
        return AITradePlan.from_payload(payload)
