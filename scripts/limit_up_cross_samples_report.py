"""Read-only entry-gate comparison; shadow outcomes are estimates, not a backtest.

python -m scripts.limit_up_cross_samples_report 2026-09-21 --logs logs/maps.log*
Use --verified-day only after auditing the entire 09:10-14:30 engine/feed window.
The "shadow" section replays each probed cross under the real fill/lock/stop rules.
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
from maps.limit_up.domain import realized_pnl

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


SHADOW_TURNOVER_GRID = (0, 10_000_000_000, 50_000_000_000)
SHADOW_STRENGTH_GRID = (0, 120, 130, 150)
SHADOW_MAX_CROSS_GRID = (None, 5)
SHADOW_BEFORE_GRID = (None, "09:30:00")


def probe_return(probe: dict, upper: int, bars: dict[str, dict], day: str) -> tuple[float, str] | None:
    """Net return (%) of one shadow probe after fees and tax, with its exit basis.

    Intraday exits are exact. A locked probe is only an estimate: the tape after
    the lock is not kept, so a close at the limit is taken as carried to the next
    open and anything else as sold at the close, floored at the 5% hard stop.
    """
    entry = probe.get("entry")
    if not entry:
        return None
    if probe["outcome"] in {"hard_stop", "time_stop"}:
        exit_price, basis = probe["exit_price"], "intraday"
    else:
        bar = bars.get(day)
        if bar is None:
            return None
        if bar["close"] == upper:
            later = sorted(d for d in bars if d > day)
            if not later:
                return None
            exit_price, basis = bars[later[0]]["open"], "next_open_estimate"
        else:
            exit_price, basis = max(bar["close"], entry * 0.95), "unlocked_estimate"
    net = realized_pnl(buy_amount=entry, buy_quantity=1, sell_amount=exit_price, sell_quantity=1)
    return net / entry * 100, basis


def shadow_report(rows: list[dict], ohlcv: dict[str, dict[str, dict]]) -> dict:
    """Judge candidate gates by what the first qualifying cross would have become."""
    probes = [p for row in rows for p in (row.get("shadow_probes") or [])]
    rules = []
    for turnover in SHADOW_TURNOVER_GRID:
        for strength in SHADOW_STRENGTH_GRID:
            for max_cross in SHADOW_MAX_CROSS_GRID:
                for before in SHADOW_BEFORE_GRID:
                    outcomes: Counter = Counter()
                    returns, pending, sessions = [], 0, 0
                    for row in rows:
                        hit = next((p for p in row.get("shadow_probes") or [] if (
                            p.get("buy") is True and p["turnover"] >= turnover
                            and p["strength"] >= strength
                            and (max_cross is None or p["cross_no"] <= max_cross)
                            and (before is None or (p.get("kst") or "99") < before)
                        )), None)
                        if hit is None:
                            continue
                        sessions += 1
                        outcomes[hit["outcome"]] += 1
                        if not hit.get("entry"):
                            continue
                        result = probe_return(hit, row["upper_limit_price"],
                                              ohlcv.get(row["ticker"], {}), str(row["ref_date"]))
                        if result is None:
                            pending += 1
                        else:
                            returns.append(result[0])
                    rules.append({
                        "min_turnover_krw": turnover, "min_execution_strength": strength,
                        "max_cross_no": max_cross, "before_kst": before,
                        "sessions": sessions, "outcomes": dict(outcomes),
                        "trades": len(returns), "pending": pending,
                        "mean_net_pct": sum(returns) / len(returns) if returns else None,
                        "wins": sum(r > 0 for r in returns),
                    })
    return {
        "scope": "first qualifying probe per session; virtual touch fills, not broker fills",
        "limitations": [
            "One probe runs at a time, so crosses inside a running probe have no outcome.",
            "Locked probes are estimates: no tape after the lock, exit taken from daily bars.",
            "Guards, slots, cash and the two-session limit are not applied.",
        ],
        "probes": len(probes), "outcomes": dict(Counter(p["outcome"] for p in probes)),
        "rules": rules,
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
            "SELECT id, ticker, ref_date, execution_mode, trigger_cross_count, cross_samples, "
            "shadow_probes, upper_limit_price FROM limit_up_session WHERE ref_date >= :since ORDER BY ref_date, ticker"
        ), {"since": args.since}).mappings()]
        guards = {str(r.ref_date): r.halted_reasons or [] for r in session.execute(text(
            "SELECT ref_date, halted_reasons FROM limit_up_daily_guard WHERE ref_date >= :since"
        ), {"since": args.since})}
        ohlcv: dict[str, dict[str, dict]] = defaultdict(dict)
        tickers = sorted({r["ticker"] for r in rows if r["shadow_probes"]})
        for ticker in tickers:
            for bar in session.execute(text(
                "SELECT date, open, close FROM historical_ohlcv WHERE ticker = :t AND date >= :since"
            ), {"t": ticker, "since": args.since}):
                ohlcv[ticker][str(bar.date)] = {"open": bar.open, "close": bar.close}
    report = build_report(rows, guards, read_logs(args.logs, str(args.since)),
                          now=dt.datetime.now(KST), verified_days={str(d) for d in args.verified_day})
    report["shadow"] = shadow_report(rows, ohlcv)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
