"""Read-only entry-gate comparison; never a fill or profit backtest.

python -m scripts.limit_up_cross_samples_report 2026-09-21 --logs logs/maps.log*
Use --verified-day only after auditing the entire 09:10-14:30 engine/feed window.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from maps.common.db import SessionLocal

TURNOVER_GRID = (10_000_000_000, 20_000_000_000, 30_000_000_000, 50_000_000_000)
STRENGTH_GRID = (110, 120, 130, 150)
KST = ZoneInfo("Asia/Seoul")


def read_logs(paths: list[str], since: str) -> dict:
    """Extract persistent KOSDAQ latch times and regular-session request health."""
    latches: dict[str, str] = {}
    counts: dict[str, Counter] = defaultdict(Counter)
    syncs: dict[str, list[dt.datetime]] = defaultdict(list)
    seen: set[str] = set()
    files = sorted({p for pattern in paths for p in glob.glob(pattern)})
    for filename in files:
        with Path(filename).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if line in seen or not re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line):
                    continue
                day, clock = line[:10], line[11:19]
                if day < since:
                    continue
                seen.add(line)
                if "kosdaq_drawdown 래치" in line:
                    latches[day] = min(latches.get(day, clock), clock)
                if not "09:00:00" <= clock <= "15:30:00":
                    continue
                if "KIS req summary" in line:
                    counts[day].update({
                        key: int(value) for key, value in re.findall(
                            r"(n|ok|rate_limited|http_error|read_timeout|connect_timeout|exception)=(\d+)", line
                        )
                    })
                if "Scheduler job broker_sync: success" in line:
                    syncs[day].append(dt.datetime.fromisoformat(line[:19]))
    health = {}
    for day in sorted(counts.keys() | syncs.keys()):
        times = sorted(set(syncs[day]))
        gaps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]))
        n = counts[day]["n"]
        health[day] = {
            "attempts": dict(counts[day]),
            "rate_limited_ratio": counts[day]["rate_limited"] / n if n else None,
            "read_timeout_ratio": counts[day]["read_timeout"] / n if n else None,
            "broker_sync_gap_p95_seconds": gaps[math.ceil(len(gaps) * .95) - 1] if gaps else None,
            "broker_sync_successes": len(times),
        }
    return {"files": files, "kosdaq_latched_at": latches, "health": health}


def guard_phase(day: str, clock: str | None, guards: dict, latches: dict) -> str:
    """Missing historical latch times are unknown, never implicitly clear."""
    if not clock:
        return "unknown_guard_timing"
    cutoff = latches.get(day)
    if cutoff and clock and clock >= cutoff:
        return "after_kosdaq_latch"
    reasons = guards.get(day)
    if reasons is None or set(reasons) - {"kosdaq_drawdown"}:
        return "unknown_guard_timing"
    if "kosdaq_drawdown" in reasons and (not cutoff or not clock):
        return "unknown_guard_timing"
    return "before_kosdaq_latch" if cutoff else "no_persistent_latch_recorded"


def build_report(rows: list[dict], guards: dict, logs: dict, *,
                 now: dt.datetime, verified_days: set[str]) -> dict:
    """Compare same-tick numeric gates, separating modes and incomplete evidence."""
    samples = []
    days: dict[str, dict] = {}
    sessions = []
    for row in rows:
        day = str(row["ref_date"])
        observations = row["cross_samples"] or []
        missing = max(0, row["trigger_cross_count"] - len(observations))
        capped = len(observations) >= 200
        summary = days.setdefault(day, {
            "sessions": 0, "crosses": 0, "samples": 0, "missing_samples": 0,
            "sample_cap_sessions": 0, "modes": Counter(),
        })
        summary["sessions"] += 1
        summary["crosses"] += row["trigger_cross_count"]
        summary["samples"] += len(observations)
        summary["missing_samples"] += missing
        summary["sample_cap_sessions"] += int(capped)
        summary["modes"][row["execution_mode"] or "unknown"] += 1
        sessions.append({
            "id": row["id"], "date": day, "ticker": row["ticker"],
            "mode": row["execution_mode"], "crosses": row["trigger_cross_count"],
            "samples": len(observations), "missing_samples": missing, "sample_cap": capped,
        })
        for sample in observations:
            samples.append({
                **sample, "date": day, "ticker": row["ticker"],
                "session_id": row["id"], "mode": row["execution_mode"] or "unknown",
                "guard_phase": guard_phase(day, sample.get("kst"), guards, logs["kosdaq_latched_at"]),
            })
    today = now.astimezone(KST).date().isoformat()
    for day, summary in days.items():
        partial = day > today or (day == today and now.astimezone(KST).time() < dt.time(14, 30))
        complete = not partial and not summary["missing_samples"] and not summary["sample_cap_sessions"]
        summary["partial_day"] = partial
        summary["coverage_verified"] = day in verified_days and complete
        summary["requires_coverage_review"] = not summary["coverage_verified"]
    unknown_days = verified_days - days.keys()
    if unknown_days:
        raise ValueError(f"verified days have no session evidence: {sorted(unknown_days)}")

    comparisons = []
    for turnover in TURNOVER_GRID:
        for strength in STRENGTH_GRID:
            groups: dict[tuple, list[dict]] = defaultdict(list)
            for sample in samples:
                if (sample.get("buy") is True and sample["turnover"] >= turnover
                        and sample["strength"] >= strength):
                    groups[(sample["date"], sample["mode"], sample["guard_phase"])].append(sample)
            comparisons.append({
                "min_turnover_krw": turnover, "min_execution_strength": strength,
                "groups": [{
                    "date": key[0], "mode": key[1], "guard_phase": key[2],
                    "samples": len(hits), "sessions": len({s["session_id"] for s in hits}),
                    "tickers": sorted({s["ticker"] for s in hits}),
                } for key, hits in sorted(groups.items())],
            })
    verified = sorted(day for day, value in days.items() if value["coverage_verified"])
    return {
        "as_of_kst": now.astimezone(KST).isoformat(),
        "scope": "gate comparison only; not order eligibility, fills or profit",
        "limitations": [
            "No complete post-cross tape; no fill, slot, cash or profit simulation.",
            "Guard phases cover persisted latches only; transient feed/manual locks are not reconstructed.",
            "Verified days require a manual full-window engine/feed audit; samples alone cannot prove uptime.",
            "Request summaries cover all KIS callers and may straddle session boundaries.",
        ],
        "days": days, "sessions": sessions, "samples": len(samples),
        "failed": dict(Counter(s["failed"] for s in samples)),
        "comparisons": comparisons, "log_evidence": logs,
        "verified_days": verified, "verified_day_count": len(verified),
        "ten_day_evidence_available": len(verified) >= 10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("since", nargs="?", type=dt.date.fromisoformat, default=dt.date(2026, 9, 21))
    parser.add_argument("--logs", nargs="*", default=[], help="Application log paths/globs")
    parser.add_argument("--verified-day", action="append", type=dt.date.fromisoformat, default=[])
    args = parser.parse_args()
    with SessionLocal() as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        rows = [dict(r) for r in session.execute(text(
            "SELECT id, ticker, ref_date, execution_mode, trigger_cross_count, cross_samples "
            "FROM limit_up_session WHERE ref_date >= :since ORDER BY ref_date, ticker"
        ), {"since": args.since}).mappings()]
        guards = {str(r.ref_date): r.halted_reasons or [] for r in session.execute(text(
            "SELECT ref_date, halted_reasons FROM limit_up_daily_guard WHERE ref_date >= :since"
        ), {"since": args.since})}
    report = build_report(rows, guards, read_logs(args.logs, str(args.since)),
                          now=dt.datetime.now(KST), verified_days={str(d) for d in args.verified_day})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
