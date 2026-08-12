"""Measured KRX liquidity and psychology feeds with one daily news overlay."""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import replace
from email.utils import parsedate_to_datetime
from typing import Mapping

from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.common.db import SessionLocal
from maps.common.models import (
    HistoricalOHLCV,
    InvestorFlowSnapshot,
    MarketNewsSentiment,
)
from maps.common.settings import MapsSettings, get_settings

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")


def _clamp(value: float) -> float:
    """Clamp one score to the public 0-100 range."""
    return round(max(0.0, min(float(value), 100.0)), 2)


def _latest_ref_date(db: Session) -> dt.date | None:
    """Return the latest collected OHLCV date."""
    return db.query(func.max(HistoricalOHLCV.date)).scalar()


def _market_observations(db: Session, ref_date: dt.date) -> dict[str, float] | None:
    """Build deterministic market internals for one exact KRX session."""
    dates = [
        row[0]
        for row in (
            db.query(HistoricalOHLCV.date)
            .filter(HistoricalOHLCV.date <= ref_date)
            .distinct()
            .order_by(HistoricalOHLCV.date.desc())
            .limit(253)
            .all()
        )
    ]
    if len(dates) < 20 or dates[0] != ref_date:
        return None
    rows = (
        db.query(
            HistoricalOHLCV.date,
            HistoricalOHLCV.ticker,
            HistoricalOHLCV.close,
            HistoricalOHLCV.volume,
        )
        .filter(HistoricalOHLCV.date.in_(dates))
        .all()
    )
    by_ticker: dict[str, list[tuple[dt.date, float, int]]] = {}
    turnover_by_date: dict[dt.date, float] = {}
    latest_turnovers: list[float] = []
    for date, ticker, close, volume in rows:
        close_f = float(close or 0.0)
        volume_i = int(volume or 0)
        by_ticker.setdefault(ticker, []).append((date, close_f, volume_i))
        turnover_by_date[date] = turnover_by_date.get(date, 0.0) + close_f * volume_i
        if date == ref_date:
            latest_turnovers.append(close_f * volume_i)
    latest_turnover = turnover_by_date.get(ref_date, 0.0)
    previous = [turnover_by_date[d] for d in dates[1:20] if turnover_by_date.get(d, 0) > 0]
    if latest_turnover <= 0 or not previous:
        return None
    turnover_score = _clamp(50.0 + (latest_turnover / (sum(previous) / len(previous)) - 1.0) * 100.0)

    new_high = sharp_drop = advancing = comparable = 0
    for values in by_ticker.values():
        values.sort(key=lambda item: item[0], reverse=True)
        if len(values) < 2 or values[0][0] != ref_date or values[1][1] <= 0:
            continue
        comparable += 1
        close = values[0][1]
        prior = values[1][1]
        advancing += close > prior
        sharp_drop += (close / prior - 1.0) <= -0.05
        history_high = max(item[1] for item in values[1:] if item[1] > 0)
        new_high += history_high > 0 and close >= history_high
    if comparable == 0:
        return None
    top = sum(sorted(latest_turnovers, reverse=True)[:20])
    return {
        "turnover_score": turnover_score,
        "breadth_score": _clamp(advancing / comparable * 100.0),
        "new_high_score": _clamp(new_high / comparable * 100.0),
        "sharp_drop_score": _clamp(100.0 - sharp_drop / comparable * 100.0),
        "concentration_score": _clamp(100.0 - top / latest_turnover * 100.0),
        "total_turnover": latest_turnover,
    }


def _flow_observations(db: Session, ref_date: dt.date, total_turnover: float) -> dict[str, float] | None:
    """Return normalized exact-date investor flow observations."""
    rows = db.query(InvestorFlowSnapshot).filter(InvestorFlowSnapshot.date == ref_date).all()
    if not rows or total_turnover <= 0:
        return None
    fields = ("foreign_net_value", "institutional_net_value", "individual_net_value")
    if any(any(getattr(row, field) is None for field in fields) for row in rows):
        return None
    totals = {field: sum(float(getattr(row, field) or 0.0) for row in rows) for field in fields}
    return {
        "foreign_score": _clamp(50.0 + totals["foreign_net_value"] / total_turnover * 500.0),
        "institution_score": _clamp(50.0 + totals["institutional_net_value"] / total_turnover * 500.0),
        # Heavy retail net buying is treated as crowding/overheat.
        "retail_score": _clamp(50.0 - totals["individual_net_value"] / total_turnover * 500.0),
    }


class DatabaseKostolanyDataProvider:
    """Enrich the composite scorer from exact-date persisted operational feeds."""

    def __init__(self, ref_date: dt.date | None = None) -> None:
        self._ref_date = ref_date

    def enrich(self, base):  # noqa: ANN001, ANN201 - protocol-compatible dataclass
        """Return a copied input; unavailable inputs remain None and fail closed."""
        db = SessionLocal()
        try:
            ref_date = self._ref_date or _latest_ref_date(db)
            if ref_date is None:
                return base
            market = _market_observations(db, ref_date)
            if market is None:
                return base
            flows = _flow_observations(db, ref_date, market["total_turnover"])
            news = (
                db.query(MarketNewsSentiment)
                .filter(MarketNewsSentiment.ref_date == ref_date)
                .first()
            )
            liquidity = None
            psychology = None
            sources = dict(base.factor_sources)
            if flows is not None:
                liquidity = (
                    market["turnover_score"] * 0.50
                    + flows["foreign_score"] * 0.25
                    + flows["institution_score"] * 0.25
                )
                sources["liquidity"] = "krx.ohlcv+pykrx.investor_flow"
            if news is not None and news.status == "success" and news.score is not None and flows is not None:
                psychology = sum(
                    (
                        market["breadth_score"],
                        market["new_high_score"],
                        market["sharp_drop_score"],
                        market["concentration_score"],
                        flows["retail_score"],
                        float(news.score),
                    )
                ) / 6.0
                sources["psychology"] = "krx.internals+naver.news+bedrock"
            return replace(
                base,
                measured_liquidity_score=_clamp(liquidity) if liquidity is not None else None,
                measured_psychology_score=_clamp(psychology) if psychology is not None else None,
                factor_sources=sources,
            )
        finally:
            db.close()


def _clean_text(value: str) -> str:
    """Remove search-result markup without storing publisher HTML."""
    return html.unescape(_TAG_RE.sub("", value or "")).strip()


def _reconcile_news_counts(result: dict[str, object], article_count: int) -> None:
    """Scale valid model counts to the exact article total without another AI call."""
    keys = ("positive_count", "neutral_count", "negative_count")
    raw = {key: int(result[key]) for key in keys}
    total = sum(raw.values())
    if article_count <= 0 or total <= 0 or any(value < 0 for value in raw.values()):
        raise RuntimeError("bedrock_news_counts_invalid")
    if total == article_count:
        return
    scaled = {key: raw[key] * article_count / total for key in keys}
    normalized = {key: int(scaled[key]) for key in keys}
    remaining = article_count - sum(normalized.values())
    fractions = sorted(
        keys,
        key=lambda name: scaled[name] - normalized[name],
        reverse=True,
    )
    for key in fractions[:remaining]:
        normalized[key] += 1
    result.update(normalized)
    logger.warning("Reconciled Bedrock news counts %s to %s", raw, normalized)


def _fetch_naver_headlines(settings: MapsSettings, ref_date: dt.date) -> list[dict[str, str]]:
    """Fetch and de-duplicate Naver news snippets published on the KST date."""
    if not settings.naver_client_id or not settings.naver_client_secret:
        raise RuntimeError("naver_credentials_missing")
    items: dict[str, dict[str, str]] = {}
    for query in ("코스피", "코스닥", "국내 증시"):
        params = urllib.parse.urlencode(
            {"query": query, "display": settings.maps_market_news_query_limit, "sort": "date"}
        )
        request = urllib.request.Request(
            f"https://naverapihub.apigw.ntruss.com/search/v1/news?{params}",
            headers={
                "X-NCP-APIGW-API-KEY-ID": settings.naver_client_id,
                "X-NCP-APIGW-API-KEY": settings.naver_client_secret,
                "User-Agent": "MAPS/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed HTTPS host
            payload = json.loads(response.read())
        for raw in payload.get("items", []):
            try:
                published = parsedate_to_datetime(raw.get("pubDate", ""))
            except (TypeError, ValueError):
                continue
            if published.astimezone(dt.timezone(dt.timedelta(hours=9))).date() != ref_date:
                continue
            url = str(raw.get("originallink") or raw.get("link") or "")
            if url:
                items[url] = {
                    "title": _clean_text(str(raw.get("title") or "")),
                    "description": _clean_text(str(raw.get("description") or "")),
                    "url": url,
                    "published_at": published.isoformat(),
                }
    return list(items.values())[:100]


def _score_news(settings: MapsSettings, headlines: list[dict[str, str]]) -> dict[str, object]:
    """Call Bedrock once and require a small structured sentiment result."""
    if not headlines:
        raise RuntimeError("no_same_day_news")
    from maps.ai.technical_scorer import AITechnicalScorer

    scorer = AITechnicalScorer(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_region=settings.aws_region,
        model_id=settings.maps_ai_scoring_model_id,
        timeout_seconds=settings.maps_ai_request_timeout_seconds,
    )
    if not scorer.is_configured:
        raise RuntimeError("bedrock_credentials_missing")
    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
            "label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            "confidence": {"type": "number"},
            "positive_count": {"type": "integer"},
            "neutral_count": {"type": "integer"},
            "negative_count": {"type": "integer"},
        },
        "required": ["score", "label", "confidence", "positive_count", "neutral_count", "negative_count"],
        "additionalProperties": False,
    }
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": "Score Korean stock-market sentiment from supplied same-day headlines only. 0 is very negative, 50 neutral, 100 very positive. Counts must sum to article count.",
        "messages": [{"role": "user", "content": json.dumps(headlines, ensure_ascii=False)}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    response = scorer._invoke(body)  # noqa: SLF001 - reuse the one audited no-retry adapter
    content = response.get("content")
    text = next(
        (block.get("text") for block in content or [] if isinstance(block, Mapping) and block.get("type") == "text"),
        None,
    )
    if not isinstance(text, str):
        raise RuntimeError("bedrock_news_text_missing")
    result = json.loads(text)
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    result["input_tokens"] = int(usage.get("input_tokens", 0))
    result["output_tokens"] = int(usage.get("output_tokens", 0))
    result["model_id"] = scorer.model_id
    _reconcile_news_counts(result, len(headlines))
    if sum(int(result[key]) for key in ("positive_count", "neutral_count", "negative_count")) != len(headlines):
        raise RuntimeError("bedrock_news_counts_invalid")
    result["score"] = _clamp(float(result["score"]))
    result["confidence"] = max(0.0, min(float(result["confidence"]), 1.0))
    return result


def collect_market_news_sentiment(
    db: Session, ref_date: dt.date, settings: MapsSettings | None = None
) -> MarketNewsSentiment:
    """Upsert one daily sentiment snapshot; failures are explicit observations."""
    settings = settings or get_settings()
    row = db.query(MarketNewsSentiment).filter_by(ref_date=ref_date).first()
    if row is None:
        row = MarketNewsSentiment(ref_date=ref_date)
        db.add(row)
    try:
        headlines = _fetch_naver_headlines(settings, ref_date)
        result = _score_news(settings, headlines)
        row.score = float(result["score"])
        row.label = str(result["label"])
        row.confidence = float(result["confidence"])
        row.article_count = len(headlines)
        row.positive_count = int(result["positive_count"])
        row.neutral_count = int(result["neutral_count"])
        row.negative_count = int(result["negative_count"])
        row.headlines = headlines
        row.model_id = str(result["model_id"])
        row.input_tokens = int(result["input_tokens"])
        row.output_tokens = int(result["output_tokens"])
        row.status = "success"
        row.error_message = None
    except Exception as exc:  # noqa: BLE001 - persisted fail-closed boundary
        row.score = None
        row.status = "failed"
        row.error_message = type(exc).__name__ + ": " + str(exc)[:450]
        logger.warning("Market news sentiment unavailable [%s]: %s", ref_date, exc)
    db.commit()
    return row
