#!/usr/bin/env python3
"""Recompute the composite market score on existing `market_regime_log` rows.

2026-08-12~14 은 수급 NULL 가드 결함(`market/feeds.py`) 때문에 `score_coverage_ratio`
가 0.65 에 고정돼 신규 매수가 전량 막혔다. 가드를 고쳐도 **이미 저장된 행은 그대로**라,
그 기준일을 참조하는 후보 주문은 계속 차단된다. 이 스크립트가 그 행들을 복구한다.

경계 세 가지:

1. **히스테리시스를 다시 돌리지 않는다.** `MarketRegimeAnalyzer.analyze()` 는 `ref_date`
   를 받지 않아 과거 날짜를 오늘의 주봉으로 채점하게 되고, `apply_hysteresis` 는 전일 행에
   의존해 연쇄적이다. 무엇보다 `applied_regime`·`entry_limit_ratio` 는 실제 주문을 가른
   **결정 기록**이지 파생값이 아니다. composite 점수 컬럼만 갱신한다.
2. **행이 없으면 만들지 않는다.** 없던 결정을 지어내지 않는다.
3. **커버리지를 낮추지 않는다.** 재계산이 저장값보다 낮으면 건너뛴다 — 멱등이고, 장중에
   잘못 돌려도 08:55 행을 훼손하지 않는다.

> ⚠️ 이 스크립트를 돌린 날짜의 다이제스트·블로그는 **재생성하지 않는다.** 재생성하면
> 결정 시점이 아니라 복구 후 값을 설명하게 된다. 그래서 `score_reason` 에 결정 시점
> 커버리지를 함께 남긴다.

실행 (16:00~16:45 및 08:50~09:05, 16:40~17:00 을 피한다):

    python scripts/backfill_market_score.py --start 2026-08-12 --end 2026-08-14
    python scripts/backfill_market_score.py --start 2026-08-12 --end 2026-08-14 --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps.common.db import SessionLocal
from maps.common.models import CollectionLog, MarketRegimeLog
from maps.market.feeds import DatabaseKostolanyDataProvider, _market_observations
from maps.market.regime import MarketRegimeCompositeScorer, MarketRegimeInput

_DIRECT_FACTORS = ("price_trend", "volatility", "foreign_fx")


def _decision_time_input(row: MarketRegimeLog) -> MarketRegimeInput:
    """저장된 행에서 결정 시점 입력을 복원한다.

    `factor_scores` 를 그대로 읽으면 안 된다 — `regime.py` 가 미측정 팩터를 `50.0` 으로
    써 놓아서 "측정된 50" 과 구분되지 않는다. `measured_factors` 를 정본으로 삼는다.
    """
    scores = row.factor_scores or {}
    measured = set(row.measured_factors or [])
    sources = row.factor_sources or {}
    return MarketRegimeInput(
        legacy_regime=row.raw_regime,
        vol_regime=row.vol_regime,
        weekly_trend=row.weekly_trend,
        price_trend_score=scores.get("price_trend") if "price_trend" in measured else None,
        volatility_score=scores.get("volatility") if "volatility" in measured else None,
        foreign_fx_score=scores.get("foreign_fx") if "foreign_fx" in measured else None,
        factor_sources={k: v for k, v in sources.items() if k in _DIRECT_FACTORS},
    )


def recompute(db, start: dt.date, end: dt.date, *, apply: bool = False) -> list[dict]:
    """기간 내 **기존** `market_regime_log` 행의 composite 점수만 다시 계산한다."""
    rows = (
        db.query(MarketRegimeLog)
        .filter(MarketRegimeLog.ref_date >= start, MarketRegimeLog.ref_date <= end)
        .order_by(MarketRegimeLog.ref_date)
        .all()
    )
    by_date = {row.ref_date: row for row in rows}
    report: list[dict] = []
    day = start
    while day <= end:
        row = by_date.get(day)
        day_iso = day.isoformat()
        day += dt.timedelta(days=1)
        if row is None:
            report.append({"ref_date": day_iso, "action": "no_decision_row"})
            continue
        if _market_observations(db, row.ref_date) is None:
            report.append({"ref_date": day_iso, "action": "market_observations_unavailable"})
            continue
        before_coverage = float(row.score_coverage_ratio or 0.0)
        before_status = row.score_status
        result = MarketRegimeCompositeScorer().score(
            DatabaseKostolanyDataProvider(row.ref_date).enrich(_decision_time_input(row))
        )
        if result.coverage_ratio < before_coverage:
            report.append({
                "ref_date": day_iso,
                "action": "would_lower_coverage",
                "before": before_coverage,
                "after": result.coverage_ratio,
            })
            continue
        entry = {
            "ref_date": day_iso,
            "action": "updated" if apply else "would_update",
            "before": {"coverage": before_coverage, "status": before_status, "ready": row.score_ready},
            "after": {
                "coverage": result.coverage_ratio,
                "status": result.score_status,
                "ready": result.score_ready,
                "final_market_score": result.final_market_score,
            },
        }
        report.append(entry)
        if not apply:
            continue
        reason = (
            f"{result.reason}; recomputed {dt.date.today().isoformat()} by backfill_market_score "
            f"(decision-time coverage={before_coverage:.2f}, status={before_status})"
        )
        row.score_reason = reason[:1000]
        row.final_market_score = result.final_market_score
        row.composite_regime = result.composite_regime
        row.score_coverage_ratio = result.coverage_ratio
        row.score_status = result.score_status
        row.score_ready = result.score_ready
        row.factor_scores = {
            "price_trend": result.price_trend_score,
            "volatility": result.volatility_score,
            "liquidity": result.liquidity_score,
            "foreign_fx": result.foreign_fx_score,
            "psychology": result.psychology_score,
        }
        row.factor_sources = result.factor_sources
        row.measured_factors = list(result.measured_factors)
        row.missing_factors = list(result.missing_factors)
        db.add(CollectionLog(
            ref_date=row.ref_date,
            source="scripts.market_score",
            status="success",
            items=1,
            note=json.dumps(entry, ensure_ascii=False),
        ))
    if apply:
        db.commit()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true", help="생략하면 dry-run")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        report = recompute(db, args.start, args.end, apply=args.apply)
    finally:
        db.close()
    print(json.dumps(
        {"apply": args.apply, "entries": report},
        ensure_ascii=False, indent=2,
    ))
    changed = [e for e in report if e["action"] in ("updated", "would_update")]
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
