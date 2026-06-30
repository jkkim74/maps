"""포트폴리오 리플레이 실측 러너.

검증(단일 종목 WFA)과 달리, 공유 현금·동시 보유 슬롯 제약 하에서 전 종목을 하나의
포트폴리오로 시뮬레이션해 전략별 실측 CAGR/Sharpe/MDD를 산출한다. NEW(t+1 시가)와
OLD(동일봉 종가) 체결 모드를 함께 돌려 look-ahead 편향의 포트폴리오 영향을 비교한다.

사용법:
    python scripts/portfolio_replay_run.py [TOP_N] [DB_PATH]

예: python scripts/portfolio_replay_run.py 150 maps.db

유동성 상위 TOP_N 종목(최근 1년 평균 거래대금)을 유니버스로 사용한다.
"""
from __future__ import annotations

import sqlite3
import sys
import time

import pandas as pd

from maps.backtest.portfolio_replay import PortfolioConfig, PortfolioReplayEngine
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy

START = "2019-01-01"
MAX_POS = 10

STRATEGIES = [
    PullbackV3Strategy(),
    ATHBreakoutV1Strategy(),
    ATHBreakoutV2Strategy(),
    DonchianV1Strategy(),
    DonchianV2Strategy(),
    MultiAssetTrendV1Strategy(),
]


def load_universe(conn: sqlite3.Connection, top_n: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT ticker, AVG(close*volume) adv, COUNT(*) n
        FROM historical_ohlcv
        WHERE date >= '2024-06-01'
        GROUP BY ticker HAVING n > 150
        ORDER BY adv DESC LIMIT ?
        """,
        (top_n,),
    ).fetchall()
    return [r[0] for r in rows]


def load_ohlcv(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM historical_ohlcv "
        "WHERE ticker=? AND date >= ? ORDER BY date",
        conn, params=(ticker, START), parse_dates=["date"],
    ).set_index("date")
    df.index.name = ticker
    return df


def main() -> None:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    db_path = sys.argv[2] if len(sys.argv) > 2 else "maps.db"

    conn = sqlite3.connect(db_path)
    universe = load_universe(conn, top_n)
    data = {tk: load_ohlcv(conn, tk) for tk in universe}
    data = {tk: df for tk, df in data.items() if len(df) > 260}
    conn.close()

    lo = min(df.index.min() for df in data.values()).date()
    hi = max(df.index.max() for df in data.values()).date()
    print(f"universe={len(data)} tickers, range {lo}..{hi}, slots={MAX_POS}\n")

    header = f"{'strategy':<22} {'mode':<14} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7} {'trades':>7} {'finalx1e8':>10}"
    print(header)
    print("-" * len(header))
    for strat in STRATEGIES:
        for mode in ("same_bar_close", "next_open"):
            t0 = time.time()
            cfg = PortfolioConfig(max_positions=MAX_POS, fill_mode=mode)
            res = PortfolioReplayEngine(cfg).run(strat, strat.default_params, data)
            print(f"{strat.strategy_id:<22} {mode:<14} {res.cagr*100:>7.1f}% "
                  f"{res.sharpe:>7.2f} {res.mdd*100:>6.1f}% {res.total_trades:>7} "
                  f"{res.final_value/1e8:>9.2f}  ({time.time()-t0:.0f}s)")
        print()


if __name__ == "__main__":
    main()
