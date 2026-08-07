"""Compare configured Bedrock scoring models; dry-run unless --execute is given."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.ai.evaluation import EvaluationSample, ModelObservation, summarize_observations
from maps.ai.technical_scorer import AIStockFeatures, AITechnicalScorer
from maps.common.db import SessionLocal
from maps.common.models import CandidateSnapshot
from maps.common.settings import get_settings
from maps.data.ohlcv_repo import HistoricalOHLCVRepository


DEFAULT_MODELS = (
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)


def _parser() -> argparse.ArgumentParser:
    """Build the small guarded CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output")
    return parser


def _load_samples(
    ref_date: dt.date,
    sample_size: int,
) -> list[tuple[EvaluationSample, AIStockFeatures]]:
    """Load top unique signal tickers and build each compact feature set once."""
    db = SessionLocal()
    try:
        rows = (
            db.query(CandidateSnapshot)
            .filter(
                CandidateSnapshot.ref_date == ref_date,
                CandidateSnapshot.entry_signal.is_(True),
                CandidateSnapshot.weekly_pass.is_(True),
            )
            .order_by(CandidateSnapshot.final_score.desc())
            .all()
        )
        by_ticker: dict[str, list[CandidateSnapshot]] = {}
        for row in rows:
            if row.ticker not in by_ticker and len(by_ticker) >= sample_size:
                continue
            by_ticker.setdefault(row.ticker, []).append(row)

        repo = HistoricalOHLCVRepository(db)
        samples: list[tuple[EvaluationSample, AIStockFeatures]] = []
        for ticker, ticker_rows in by_ticker.items():
            first = ticker_rows[0]
            features = AIStockFeatures.from_frame(
                ticker=ticker,
                name=first.name,
                ref_date=ref_date.isoformat(),
                frame=repo.to_dataframe(ticker, end=ref_date),
                strategy_ids=tuple(row.strategy_id for row in ticker_rows),
                trend_strength=max(row.trend_strength for row in ticker_rows),
                ts_bucket=first.ts_bucket,
            )
            samples.append(
                (
                    EvaluationSample(
                        ticker=ticker,
                        name=first.name,
                        strategy_ids=features.strategy_ids,
                        features=features.to_payload(),
                    ),
                    features,
                )
            )
        return samples
    finally:
        db.close()


def run_live_evaluation(
    args: argparse.Namespace,
) -> tuple[list[EvaluationSample], list[ModelObservation]]:
    """Run explicitly approved model calls and return safe observations only."""
    settings = get_settings()
    samples = _load_samples(args.ref_date, args.sample_size)
    observations: list[ModelObservation] = []
    for model_id in args.models:
        scorer = AITechnicalScorer(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_region=settings.aws_region,
            model_id=model_id,
            timeout_seconds=settings.maps_ai_request_timeout_seconds,
        )
        for sample, features in samples:
            for _ in range(args.repeats):
                started = time.perf_counter()
                try:
                    result = scorer.score(features)
                    score = max(
                        result.score.score_for(strategy_id)
                        for strategy_id in sample.strategy_ids
                    )
                    observation = ModelObservation(
                        model_id,
                        sample.ticker,
                        score,
                        True,
                        result.input_tokens,
                        result.output_tokens,
                        time.perf_counter() - started,
                    )
                except Exception as exc:
                    observation = ModelObservation(
                        model_id,
                        sample.ticker,
                        None,
                        False,
                        0,
                        0,
                        time.perf_counter() - started,
                        type(exc).__name__,
                    )
                observations.append(observation)
    return [sample for sample, _features in samples], observations


def main(argv: list[str] | None = None) -> int:
    """Print planned cost, then optionally execute and write a safe JSON report."""
    args = _parser().parse_args(argv)
    planned = args.sample_size * args.repeats * len(args.models)
    print(f"{planned} planned calls")
    if not args.execute:
        return 0

    settings = get_settings()
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        print("AWS credentials are required with --execute", file=sys.stderr)
        return 2

    samples, observations = run_live_evaluation(args)
    output = Path(
        args.output
        or f"logs/ai-scoring-evaluation-{args.ref_date.isoformat()}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "samples": [asdict(sample) for sample in samples],
                "observations": [asdict(item) for item in observations],
                "summaries": [
                    asdict(item) for item in summarize_observations(observations)
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
