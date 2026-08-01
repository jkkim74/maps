#!/usr/bin/env python3
"""국면별 조건부 성과 통계 — 전략의 선호 장세 선언을 실측으로 검증한다.

백테스트 거래(TradeRecord)를 진입일의 국면 라벨로 태깅해 전략 × 국면 성과표를
만든다. 매일 17:10 검증은 전체 기간 견고성만 재므로 "어느 국면에서 통하는
전략인가"는 여기서만 보인다. 라벨 출처는 두 겹이다:

1. market_regime_log.applied_regime — 운영 실측 (2026-07-02부터).
2. KOSPI 주간 이동평균 프록시 — 로그 이전 과거분. 직전 완결 5·10주선 모두 위면
   strong, 모두 아래면 weak, 그 외 mixed. 플로어/Korea weak guard 와 같은
   휴리스틱이지만 8자산 투표 실측과는 다른 근사치다 (표의 출처 요약에 명시).

사용법:
    python scripts/regime_conditional_performance.py
    python scripts/regime_conditional_performance.py --tickers 30 --strategies pullback_v3,donchian_v2
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REGIMES = ("strong", "mixed", "weak")


def labels_from_daily_closes(close) -> dict[dt.date, str]:
    """일별 종가 시계열 → {날짜: 국면 프록시 라벨}.

    직전 완결 주의 MA5W/MA10W와 당일 종가를 비교한다 (shift(1)로 미래 참조 방지).
    MA가 아직 없는 초기 구간은 라벨을 만들지 않는다.
    """
    import pandas as pd  # noqa: PLC0415

    close = close.dropna()
    weekly = close.resample("W").last().dropna()
    ma5 = weekly.rolling(5).mean().shift(1)
    ma10 = weekly.rolling(10).mean().shift(1)

    daily = pd.DataFrame({"close": close})
    daily["ma5w"] = ma5.reindex(daily.index, method="ffill")
    daily["ma10w"] = ma10.reindex(daily.index, method="ffill")
    daily = daily.dropna()

    labels: dict[dt.date, str] = {}
    for ts, row in daily.iterrows():
        above5 = row["close"] > row["ma5w"]
        above10 = row["close"] > row["ma10w"]
        if above5 and above10:
            label = "strong"
        elif not above5 and not above10:
            label = "weak"
        else:
            label = "mixed"
        labels[ts.date()] = label
    return labels


def fetch_kospi_proxy_labels(start: dt.date, end: dt.date) -> dict[dt.date, str]:
    """yfinance ^KS11 일봉으로 프록시 라벨을 만든다 (KRX 로그인 가드 불필요)."""
    import yfinance as yf  # noqa: PLC0415

    df = yf.download(
        "^KS11",
        start=(start - dt.timedelta(days=120)).isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if df is None or df.empty:
        return {}
    close = df["Close"].squeeze()
    return labels_from_daily_closes(close)


def load_log_labels(db) -> dict[dt.date, str]:
    """market_regime_log 실측 라벨 (히스테리시스·가드 적용 후 값)."""
    from maps.common.models import MarketRegimeLog  # noqa: PLC0415

    rows = db.query(MarketRegimeLog.ref_date, MarketRegimeLog.applied_regime).all()
    return {ref_date: regime for ref_date, regime in rows}


def aggregate_trades(tagged: list[tuple[object, str]]) -> dict[str, dict[str, float]]:
    """[(TradeRecord, label)] → {label: 통계}.

    수익률은 포지션 대비(net_pnl / 진입금액). G2P는 이익 합 / |손실 합|.
    """
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "sum_ret": 0.0, "win_pnl": 0.0, "loss_pnl": 0.0, "net": 0.0}
    )
    for trade, label in tagged:
        b = buckets[label]
        b["trades"] += 1
        notional = trade.entry_price * trade.qty
        ret = trade.net_pnl / notional if notional else 0.0
        b["sum_ret"] += ret
        b["net"] += trade.net_pnl
        if trade.net_pnl > 0:
            b["wins"] += 1
            b["win_pnl"] += trade.net_pnl
        else:
            b["loss_pnl"] += trade.net_pnl

    out: dict[str, dict[str, float]] = {}
    for label, b in buckets.items():
        n = b["trades"]
        out[label] = {
            "trades": n,
            "win_rate": b["wins"] / n if n else 0.0,
            "avg_ret": b["sum_ret"] / n if n else 0.0,
            "g2p": b["win_pnl"] / abs(b["loss_pnl"]) if b["loss_pnl"] else float("inf"),
            "net": b["net"],
        }
    return out


def verdicts(stats: dict[str, dict[str, float]], preferred: set[str], min_trades: int) -> list[str]:
    """선언(preferred_regimes)과 실측의 불일치 목록."""
    notes: list[str] = []
    for regime in REGIMES:
        s = stats.get(regime)
        if not s or s["trades"] < min_trades:
            continue
        profitable = s["net"] > 0
        if regime in preferred and not profitable:
            notes.append(f"선호 장세 {regime}인데 합계 손실 ({s['net']:+,.0f}원)")
        if regime not in preferred and profitable:
            notes.append(f"선언 밖 {regime}에서 수익 ({s['net']:+,.0f}원)")
    return notes


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", type=int, default=20, help="전략당 시험 종목 수 (기본 20)")
    parser.add_argument("--strategies", default="", help="쉼표 구분 전략 ID (기본 전체)")
    parser.add_argument("--min-trades", type=int, default=5, help="판정에 필요한 최소 거래 수")
    args = parser.parse_args(argv[1:])

    from maps.api.backtest import RUNNABLE_STRATEGIES  # noqa: PLC0415
    from maps.backtest.engine import BacktestEngine  # noqa: PLC0415
    from maps.common.db import SessionLocal  # noqa: PLC0415
    from maps.data.ohlcv_repo import HistoricalOHLCVRepository  # noqa: PLC0415

    wanted = [s for s in args.strategies.split(",") if s] or list(RUNNABLE_STRATEGIES)
    unknown = [s for s in wanted if s not in RUNNABLE_STRATEGIES]
    if unknown:
        print(f"알 수 없는 전략: {unknown} (가능: {list(RUNNABLE_STRATEGIES)})")
        return 2

    db = SessionLocal()
    try:
        log_labels = load_log_labels(db)
        repo = HistoricalOHLCVRepository(db)
        engine = BacktestEngine()

        # 전략별 백테스트 → 거래 태깅
        all_rows: list[tuple[str, dict, set[str], int]] = []
        proxy_labels: dict[dt.date, str] = {}
        proxy_range: tuple[dt.date, dt.date] | None = None
        for strategy_id in wanted:
            strategy = RUNNABLE_STRATEGIES[strategy_id]()
            params = strategy.default_params
            min_bars = strategy.required_bars(params)
            tickers = repo.list_tickers_with_history(min_bars=min_bars)[: args.tickers]

            trades = []
            for ticker in tickers:
                df = repo.to_dataframe(ticker)
                if len(df) < min_bars:
                    continue
                try:
                    result = engine.run(strategy, params, df)
                except Exception:  # noqa: BLE001 — 개별 종목 실패는 통계에서 제외
                    continue
                trades.extend(result.trade_list)

            if trades and proxy_range is None:
                lo = min(t.entry_date for t in trades)
                hi = max(t.entry_date for t in trades)
                proxy_range = (lo, hi)
                proxy_labels = fetch_kospi_proxy_labels(lo, hi)

            tagged = [
                (t, log_labels.get(t.entry_date) or proxy_labels.get(t.entry_date) or "unknown")
                for t in trades
            ]
            stats = aggregate_trades(tagged)
            preferred = {getattr(r, "value", str(r)) for r in getattr(strategy, "preferred_regimes", set())}
            all_rows.append((strategy_id, stats, preferred, len(trades)))
    finally:
        db.close()

    # 출력
    log_days = len(log_labels)
    proxy_days = len(proxy_labels)
    print(f"라벨 출처: 운영 로그 {log_days}일 (우선) + KOSPI 주간MA 프록시 {proxy_days}일")
    print("프록시 = 직전 완결 5·10주선 모두 위 strong / 모두 아래 weak / 그 외 mixed")
    print("(8자산 투표 실측이 아니라 근사치다. 로그가 쌓일수록 실측 비중이 커진다)")
    for strategy_id, stats, preferred, total in all_rows:
        print()
        print(f"── {strategy_id}  (선언 선호 장세: {', '.join(sorted(preferred)) or '없음'} · 거래 {total}건)")
        print(f"{'국면':<8}{'거래수':>6}{'승률':>8}{'평균수익률':>10}{'G2P':>8}{'합계손익':>14}")
        for regime in (*REGIMES, "unknown"):
            s = stats.get(regime)
            if not s:
                continue
            g2p = f"{s['g2p']:.2f}" if s["g2p"] != float("inf") else "inf"
            print(
                f"{regime:<8}{s['trades']:>6}{s['win_rate']:>7.0%}{s['avg_ret']:>9.2%}"
                f"{g2p:>8}{s['net']:>14,.0f}"
            )
        for note in verdicts(stats, preferred, args.min_trades):
            print(f"  ! {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
