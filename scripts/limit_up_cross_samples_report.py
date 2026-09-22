"""상한가 V1 교차 표본(`limit_up_session.cross_samples`) 분포 리포트.

운영 서버에서 실행한다 (PYTHONPATH=/opt/maps). 임계값 조합별 통과 세션 수를 출력해
`docs/HANDOFF_limit_up_cross_samples.md` 의 표를 갱신하는 데 쓴다.

    PYTHONPATH=/opt/maps python scripts/limit_up_cross_samples_report.py 2026-09-21
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter
from itertools import groupby

from sqlalchemy import text

from maps.common.db import SessionLocal

TURNOVER_GRID = (1e10, 2e10, 3e10, 5e10)
STRENGTH_GRID = (110, 120, 130, 150)


def main(since: str) -> None:
    """`since`(YYYY-MM-DD) 이후 세션의 교차 표본을 요약해 stdout 에 출력한다."""
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "select ticker, ref_date, state, end_reason, max_turnover_krw, max_strength, "
                "observed_tick_count, cross_samples from limit_up_session "
                "where ref_date >= :since order by ref_date, ticker"
            ),
            {"since": since},
        ).fetchall()
    samples: list[dict] = []
    print(f"sessions: {len(rows)}")
    for r in rows:
        s = r[7] or []
        for x in s:
            x["ticker"], x["date"] = r[0], str(r[1])
        samples += s
        print(f"{r[1]} {r[0]:7} state={r[2]:10} end={r[3]} ticks={r[6]} max_to={r[4]} max_st={r[5]} samples={len(s)}")
    print("total samples:", len(samples))
    if not samples:
        return
    to = [x["turnover"] for x in samples]
    st = [x["strength"] for x in samples]
    print("turnover min/med/max:", min(to), statistics.median(to), max(to))
    print("strength min/med/max:", min(st), statistics.median(st), max(st))
    print("failed:", Counter(x["failed"] for x in samples))
    print("st>=150 max turnover:", max((x["turnover"] for x in samples if x["strength"] >= 150), default=None))
    print("to>=500억 max strength:", max((x["strength"] for x in samples if x["turnover"] >= 5e10), default=None))
    for thr_to in TURNOVER_GRID:
        for thr_st in STRENGTH_GRID:
            hit = [x for x in samples if x["turnover"] >= thr_to and x["strength"] >= thr_st]
            sess = len({(x["ticker"], x["date"]) for x in hit})
            print(f"  to>={thr_to / 1e8:.0f}억 st>={thr_st}: samples={len(hit)} sessions={sess}")
    print("--- per session first/last sample ---")
    for key, g in groupby(samples, key=lambda x: (x["date"], x["ticker"])):
        g = list(g)
        print(key, len(g), g[0]["kst"], "~", g[-1]["kst"], "to", g[0]["turnover"], "->", g[-1]["turnover"], "st", g[0]["strength"], "->", g[-1]["strength"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-09-21")
