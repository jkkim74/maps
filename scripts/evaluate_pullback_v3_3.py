"""Pullback V3.3의 6개 청산 조합을 사전 등록 기준으로 평가한다.

실행 결과는 기존 콘솔과 동일하게 ``backtest_run_log``에 저장하며, stdout에는
기계 판독 가능한 JSON 한 줄을 출력한다. 통과 후보가 없으면 종료 코드 2를 반환한다.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.api.backtest import run_backtest
from maps.api.schemas import BacktestRunRequest
from maps.common.db import SessionLocal
from maps.common.models import HistoricalOHLCV
from maps.strategy.pullback_v3_3 import EXIT_RESEARCH_GRID


_WINDOWS = (
    ("2017_semiconductor", dt.date(2017, 1, 2), dt.date(2017, 11, 30), 0.24450461449493716),
    ("2020_rebound", dt.date(2020, 4, 1), dt.date(2021, 6, 30), 0.3881013653192235),
    ("2023_rally", dt.date(2023, 1, 2), dt.date(2023, 7, 31), -0.004845789665473533),
)
_ENTRY_PARAMS = {"rsi_threshold": 10, "ma_long": 20}


def _run(db, *, params: dict, start: dt.date, end: dt.date, mode: str) -> dict:
    item = run_backtest(
        BacktestRunRequest(
            strategy_id="pullback_v3_3",
            params=params,
            start=start,
            end=end,
            mode=mode,
            universe="market",
            universe_arg="KOSPI",
        ),
        db,
    )
    stats = item.stats or {}
    return {
        "run_id": item.run_id,
        "mode": mode,
        "sharpe": item.sharpe,
        "cagr": item.net_cagr,
        "mdd": item.mdd,
        "trades": item.trade_count,
        "payoff_ratio": stats.get("payoff_ratio"),
        "win_rate": stats.get("win_rate"),
        "avg_r_multiple": stats.get("avg_r_multiple"),
        "median_holding_days": stats.get("median_holding_days"),
        "exit_reason_counts": stats.get("exit_reason_counts"),
    }


def _window_passed(result: dict, baseline_sharpe: float) -> bool:
    payoff = result.get("payoff_ratio")
    return bool(
        result.get("trades", 0) >= 30
        and payoff is not None
        and payoff >= 1.3
        and result.get("sharpe") is not None
        and result["sharpe"] >= max(0.0, baseline_sharpe)
        and abs(result.get("mdd") or 0.0) <= 0.18
    )


def main() -> int:
    db = SessionLocal()
    try:
        candidates: list[dict] = []
        for idx, exit_params in enumerate(EXIT_RESEARCH_GRID, start=1):
            params = {**_ENTRY_PARAMS, **exit_params}
            windows: list[dict] = []
            for label, start, end, baseline in _WINDOWS:
                try:
                    result = _run(db, params=params, start=start, end=end, mode="per_ticker")
                    result.update({"window": label, "baseline_sharpe": baseline})
                    result["passed"] = _window_passed(result, baseline)
                except HTTPException as exc:
                    result = {"window": label, "passed": False, "error": str(exc.detail)}
                windows.append(result)
            candidates.append({
                "candidate": idx,
                "params": params,
                "windows": windows,
                "strong_windows_passed": all(row["passed"] for row in windows),
            })

        eligible = [row for row in candidates if row["strong_windows_passed"]]
        if eligible:
            eligible.sort(
                key=lambda row: (
                    min(float(w["sharpe"]) for w in row["windows"]),
                    min(float(w["payoff_ratio"]) for w in row["windows"]),
                    -max(abs(float(w["mdd"])) for w in row["windows"]),
                ),
                reverse=True,
            )
            selected = eligible[0]
            data_end = db.query(func.max(HistoricalOHLCV.date)).scalar()
            full_results: list[dict] = []
            if data_end is not None:
                for mode in ("per_ticker", "portfolio"):
                    full_results.append(_run(
                        db,
                        params=selected["params"],
                        start=dt.date(2016, 1, 4),
                        end=data_end,
                        mode=mode,
                    ))
            selected["full_period"] = full_results
            per_ticker = next((r for r in full_results if r["mode"] == "per_ticker"), None)
            portfolio = next((r for r in full_results if r["mode"] == "portfolio"), None)
            selected["full_period_passed"] = bool(
                per_ticker
                and portfolio
                and (per_ticker.get("trades") or 0) >= 300
                and (per_ticker.get("cagr") or 0.0) > 0
                and (per_ticker.get("sharpe") or 0.0) > 0
                and (portfolio.get("cagr") or 0.0) > 0
                and (portfolio.get("sharpe") or 0.0) > 0
                and abs(per_ticker.get("mdd") or 0.0) <= 0.18
                and abs(portfolio.get("mdd") or 0.0) <= 0.18
            )
        else:
            selected = None

        payload = {
            "strategy_id": "pullback_v3_3",
            "universe": "market:KOSPI",
            "candidates": candidates,
            "selected": selected,
            "accepted": bool(selected and selected.get("full_period_passed")),
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0 if payload["accepted"] else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
