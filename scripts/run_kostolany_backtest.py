"""코스톨라니 vs 레거시 전략단위 백테스트 검증 실행 스크립트.

DB(OHLCV 히스토리)를 사용해 9개 시나리오 × 4개 모드를 비교하고, 텍스트 리포트와
JSON 결과를 logs/ 아래에 저장한다. 실주문은 발생하지 않는다(순수 BacktestEngine).

사용:
    PYTHONPATH=<repo_root> python scripts/run_kostolany_backtest.py [sample_size]
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys

from maps.backtest.kostolany_comparison import KostolanyComparisonRunner
from maps.backtest.kostolany_driver import run_comparison
from maps.common.db import SessionLocal


def main() -> None:
    # Windows 콘솔(cp949)이 ✓/✗ 등 유니코드를 인코딩 못 하는 문제 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    started = dt.datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] 코스톨라니 백테스트 검증 시작 (sample_size={sample_size})", flush=True)

    with SessionLocal() as db:
        results = run_comparison(db, sample_size=sample_size)

    runner = KostolanyComparisonRunner()
    report = runner.generate_comparison_report(results)

    # JSON 저장 (ModeResult 평탄화) — 콘솔 출력보다 먼저 저장해 결과를 보존한다
    payload = {
        "generated_at": started.isoformat(),
        "sample_size": sample_size,
        "scenarios": [
            {
                "scenario": cr.scenario_name,
                "modes": [dataclasses.asdict(r) for r in cr.results],
            }
            for cr in results
        ],
    }
    out_dir = "logs"
    ts = started.strftime("%Y%m%d_%H%M%S")
    json_path = f"{out_dir}/kostolany_backtest_{ts}.json"
    txt_path = f"{out_dir}/kostolany_backtest_{ts}.txt"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(report)

    print(report, flush=True)
    elapsed = (dt.datetime.now() - started).total_seconds()
    print(f"\n저장: {json_path} / {txt_path}  (소요 {elapsed:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
