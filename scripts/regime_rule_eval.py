#!/usr/bin/env python3
"""장세 규칙 오프라인 평가 — 현행 판정을 과거에 그대로 재생하고 대안 규칙과 비교한다.

운영 로그(2026-07~09)로 확인된 문제는 라벨이 아니라 게이트 구조였다: 후행 주간추세의
0/1 하드블록, 상시 HIGH 인 절대 변동성 임계, 8표 중 한국 2표. 규칙을 감으로 바꾸면
다음 국면에서 또 틀리므로, 같은 코드(`MarketRegimeAnalyzer._compute`)를 과거 매일에
돌려 라벨·한도를 재생하고, 후보 규칙을 파라미터로 바꿔 같은 지표로 비교한다.

재생은 운영과 같은 주봉 규칙(`resample("W").last()`, 당주 미완결 포함)을 쓰고, 해외
자산은 16:20 KST 실행 시점에 전일 종가까지만 있으므로 하루 늦춘다.

사용법:
    python scripts/regime_rule_eval.py --start 2024-01-01                   # 규칙 비교표
    python scripts/regime_rule_eval.py --replay-log market_regime_log.csv   # 운영 로그 재현 검증
    python scripts/regime_rule_eval.py --cache closes.csv                    # 다운로드 캐시
운영 데이터를 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps.market.regime import (  # noqa: E402
    MarketRegimeAnalyzer,
    RegimeLabel,
    RegimeResult,
    VolRegimeLabel,
    WeeklyTrendLabel,
    korea_weak_guard_triggered,
)

_TICKERS = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "USD/KRW": "KRW=X",
    "금": "GC=F",
    "WTI": "CL=F",
    "구리": "HG=F",
    "SOX": "^SOX",
}
_DOMESTIC = {"KOSPI", "KOSDAQ"}
# 16:20 KST 실행 시점에 당일 봉이 아직 없는 자산 — 미국 주식지수뿐이다. 환율·선물은
# 거의 24시간 거래라 yfinance 가 당일 미완결 행을 준다.
_US_EQUITY = {"S&P 500", "NASDAQ", "SOX"}
_BASE_ASSETS = list(MarketRegimeAnalyzer._ASSETS)
_BASE_RATIO = {"strong": 1.0, "mixed": 0.5, "weak": 0.25}
_STEP_DOWN = {1.0: 0.5, 0.5: 0.25, 0.25: 0.0, 0.0: 0.0}
_WEAK_ENTER_RATIO = 0.35
_MIXED_ENTER_RATIO = 0.45
_KOREA_WEAK_TS = 35.0


@dataclass(frozen=True)
class Rules:
    """평가할 규칙 조합. 기본값 = 현행 운영 규칙."""

    name: str = "baseline"
    assets: tuple[str, ...] = tuple(_BASE_ASSETS)
    weekly_fail: str = "zero"          # zero(현행) | step(한 단계 하향)
    vol_mode: str = "absolute"         # absolute(현행 12%/20%) | percentile
    vol_percentile: float = 0.80       # percentile 모드에서 HIGH 경계 (과거 2년 분포)
    strong_band: tuple[float, float] | None = None   # mixed↔strong 밴드 (up_ratio)
    guard: bool = True                 # Korea weak guard (breadth 없이 ts 로만)


RULE_SETS: dict[str, Rules] = {
    "baseline": Rules(),
    "a_step": Rules(name="a_step", weekly_fail="step"),
    "b_volpct": Rules(name="b_volpct", vol_mode="percentile"),
    "c_sox": Rules(name="c_sox", assets=tuple(a if a != "구리" else "SOX" for a in _BASE_ASSETS)),
    "d_band": Rules(name="d_band", strong_band=(0.62, 0.70)),
    "e_kospi2": Rules(name="e_kospi2", assets=("KOSPI", *_BASE_ASSETS)),
    "abcd": Rules(
        name="abcd", weekly_fail="step", vol_mode="percentile",
        assets=tuple(a if a != "구리" else "SOX" for a in _BASE_ASSETS), strong_band=(0.62, 0.70),
    ),
}


# ── 데이터 ────────────────────────────────────────────────────────────────────
def load_closes(start: str, cache: Path | None = None) -> pd.DataFrame:
    """자산별 일봉 종가 (열=자산). 캐시가 있으면 읽고, 없으면 yfinance 로 받아 저장한다."""
    if cache is not None and cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    import yfinance as yf  # noqa: PLC0415

    frames = {}
    for name, ticker in _TICKERS.items():
        df = yf.download(ticker, start=start, interval="1d", auto_adjust=True, progress=False)
        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        frames[name] = close.astype(float)
    closes = pd.DataFrame(frames).sort_index()
    if cache is not None:
        closes.to_csv(cache)
    return closes


class HistoricalWeeklyProvider:
    """``as_of`` 시점에 운영 제공자가 봤을 주봉을 그대로 만든다."""

    def __init__(self, closes: pd.DataFrame, as_of: pd.Timestamp, *, overseas_lag_days: int = 1) -> None:
        self._closes = closes
        self._as_of = as_of
        self._lag = overseas_lag_days

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name not in self._closes.columns:
            return []
        cutoff = self._as_of - pd.Timedelta(days=self._lag) if asset_name in _US_EQUITY else self._as_of
        series = self._closes[asset_name].loc[:cutoff].dropna()
        if series.empty:
            return []
        weekly = series.resample("W").last().dropna()
        return [float(v) for v in weekly.tail(n_weeks).tolist()]


# ── 규칙별 분석기 ─────────────────────────────────────────────────────────────
class _RuleAnalyzer(MarketRegimeAnalyzer):
    """자산 목록·변동성 판정만 바꿀 수 있는 분석기. 나머지는 운영 코드 그대로."""

    def __init__(self, provider: HistoricalWeeklyProvider, rules: Rules, vol_history: pd.Series | None) -> None:
        super().__init__(provider)
        self._ASSETS = list(rules.assets)  # type: ignore[misc]
        self._rules = rules
        self._vol_history = vol_history

    def _compute_vol_regime(self) -> VolRegimeLabel:
        if self._rules.vol_mode == "absolute":
            return super()._compute_vol_regime()
        closes = self._provider.get_weekly_closes("KOSPI", self._VOL_LOOKBACK + 1)
        if len(closes) < self._VOL_LOOKBACK or self._vol_history is None or self._vol_history.empty:
            return VolRegimeLabel.NORMAL
        arr = np.array(closes, dtype=float)
        vol = float((np.diff(arr) / arr[:-1]).std() * np.sqrt(52))
        self._volatility_measured = True
        rank = float((self._vol_history < vol).mean())
        if rank >= self._rules.vol_percentile:
            return VolRegimeLabel.HIGH
        if rank <= 1.0 - self._rules.vol_percentile:
            return VolRegimeLabel.LOW
        return VolRegimeLabel.NORMAL


def realized_vol_history(kospi: pd.Series, as_of: pd.Timestamp, years: int = 2) -> pd.Series:
    """as_of 이전 ``years`` 년의 20주 실현변동성 분포 (백분위 모드 기준선)."""
    weekly = kospi.loc[: as_of - pd.Timedelta(days=1)].dropna().resample("W").last().dropna()
    weekly = weekly.loc[as_of - pd.DateOffset(years=years):]
    returns = weekly.pct_change().dropna()
    return (returns.rolling(MarketRegimeAnalyzer._VOL_LOOKBACK).std() * np.sqrt(52)).dropna()


def apply_offline_hysteresis(
    result: RegimeResult, rules: Rules, prev: dict | None
) -> tuple[str, bool, bool]:
    """regime_history.apply_hysteresis 를 DB 없이 재현한다 (breadth 는 UNKNOWN).

    Returns:
        (applied_regime, floor_applied, guard_applied)
    """
    regime = result.regime
    floor_applied = result.floor_applied
    guard_applied = False
    up_ratio = (result.up_count / result.total_assets) if result.total_assets else 0.0
    prev_applied = prev["applied"] if prev else None
    if (
        regime == RegimeLabel.WEAK
        and not floor_applied
        and result.kospi_above_ma5w
        and result.weekly_trend == WeeklyTrendLabel.PASS
        and prev is not None
        and prev["above5"]
    ):
        regime, floor_applied = RegimeLabel.MIXED, True
    elif (
        _WEAK_ENTER_RATIO < up_ratio < _MIXED_ENTER_RATIO
        and prev_applied is not None
        and prev_applied != regime.value
    ):
        regime = RegimeLabel(prev_applied)
    elif (
        rules.strong_band is not None
        and rules.strong_band[0] < up_ratio < rules.strong_band[1]
        and prev_applied is not None
        and prev_applied != regime.value
        and {prev_applied, regime.value} == {"mixed", "strong"}
    ):
        regime = RegimeLabel(prev_applied)
    if rules.guard:
        probe = replace(result, regime=regime)
        if korea_weak_guard_triggered(probe, ts_threshold=_KOREA_WEAK_TS):
            regime, guard_applied = RegimeLabel.WEAK, True
    return regime.value, floor_applied, guard_applied


def entry_limit(applied: str, weekly_trend: str, vol_regime: str, rules: Rules) -> float:
    """RegimeResult.entry_limit_ratio 와 같은 매트릭스에 weekly_fail 모드만 더한 것."""
    base = _BASE_RATIO[applied]
    if vol_regime == "high":
        base = _STEP_DOWN[base]
    if weekly_trend == "fail":
        return 0.0 if rules.weekly_fail == "zero" else _STEP_DOWN[base]
    return base


# ── 재생 ──────────────────────────────────────────────────────────────────────
def replay(closes: pd.DataFrame, rules: Rules, start: pd.Timestamp, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """KRX 거래일(KOSPI 종가가 있는 날)마다 규칙을 적용해 일별 판정표를 만든다."""
    kospi = closes["KOSPI"].dropna()
    days = kospi.loc[start:end].index
    rows: list[dict] = []
    prev: dict | None = None
    for day in days:
        provider = HistoricalWeeklyProvider(closes, day)
        vol_hist = realized_vol_history(kospi, day) if rules.vol_mode == "percentile" else None
        result = _RuleAnalyzer(provider, rules, vol_hist)._compute()
        applied, floor_applied, guard_applied = apply_offline_hysteresis(result, rules, prev)
        row = {
            "date": day,
            "raw": result.regime.value,
            "applied": applied,
            "weekly_trend": result.weekly_trend.value,
            "vol_regime": result.vol_regime.value,
            "up_count": result.up_count,
            "total_assets": result.total_assets,
            "kospi_ts": result.kospi_ts,
            "floor": floor_applied,
            "guard": guard_applied,
            "above5": bool(result.kospi_above_ma5w),
        }
        row["elr"] = entry_limit(applied, row["weekly_trend"], row["vol_regime"], rules)
        rows.append(row)
        prev = row
    frame = pd.DataFrame(rows).set_index("date")
    frame["kospi"] = kospi.reindex(frame.index)
    frame["r1"] = kospi.pct_change().shift(-1).reindex(frame.index)
    return frame


# ── 지표 ──────────────────────────────────────────────────────────────────────
def summarize(frame: pd.DataFrame, peak: pd.Timestamp | None = None, trough: pd.Timestamp | None = None) -> dict:
    """한도 가중 노출의 성과와 게이트 반응 속도."""
    pnl = (frame["elr"] * frame["r1"]).fillna(0.0)
    equity = (1 + pnl).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()
    out = {
        "days": len(frame),
        "avg_elr": round(float(frame["elr"].mean()), 3),
        "zero_days": int((frame["elr"] == 0).sum()),
        "switches": int((frame["applied"] != frame["applied"].shift()).sum() - 1),
        "exposure_return_pct": round(float(equity.iloc[-1] - 1) * 100, 2),
        "max_drawdown_pct": round(float(drawdown) * 100, 2),
        "kospi_return_pct": round(float(frame["kospi"].iloc[-1] / frame["kospi"].iloc[0] - 1) * 100, 2),
    }
    if peak is not None:
        after = frame.loc[peak:]
        closed = after.index[after["elr"] == 0]
        out["days_to_close_after_peak"] = int((closed[0] - peak).days) if len(closed) else None
    if trough is not None:
        after = frame.loc[trough:]
        opened = after.index[after["elr"] > 0]
        out["days_to_open_after_trough"] = int((opened[0] - trough).days) if len(opened) else None
    return out


def compare_with_log(frame: pd.DataFrame, log_csv: Path) -> pd.DataFrame:
    """운영 market_regime_log 와 재생 결과를 날짜별로 대조한다."""
    log = pd.read_csv(log_csv, parse_dates=["ref_date"]).set_index("ref_date")
    joined = log[["raw_regime", "applied_regime", "weekly_trend", "vol_regime", "up_count"]].join(
        frame[["raw", "applied", "weekly_trend", "vol_regime", "up_count"]],
        rsuffix="_replay", how="inner",
    )
    joined["raw_ok"] = joined["raw_regime"] == joined["raw"]
    joined["applied_ok"] = joined["applied_regime"] == joined["applied"]
    joined["weekly_ok"] = joined["weekly_trend"] == joined["weekly_trend_replay"]
    joined["vol_ok"] = joined["vol_regime"] == joined["vol_regime_replay"]
    # 종합점수(kostolany)가 결정 시점에 하향한 날은 운영 raw 가 투표 결과가 아니다 —
    # DB 피드 없이는 재현할 수 없으니 "composite 로 설명됨" 으로 따로 센다.
    if "composite_regime" in log.columns:
        composite = log["composite_regime"].reindex(joined.index)
        joined["composite_explains"] = (~joined["raw_ok"]) & (joined["raw_regime"] == composite)
    return joined


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2024-01-01", help="평가 시작일 (데이터는 그보다 2년 앞부터 받는다)")
    parser.add_argument("--end", default=None, help="평가 종료일 (기본 마지막 거래일)")
    parser.add_argument("--rules", default=",".join(RULE_SETS), help="쉼표 구분 규칙 이름")
    parser.add_argument("--cache", default=None, help="일봉 캐시 CSV 경로")
    parser.add_argument("--replay-log", default=None, help="운영 market_regime_log CSV — 현행 재현 검증")
    parser.add_argument("--peak", default="2026-06-22", help="폭락 구간 고점일 (닫히기까지 일수)")
    parser.add_argument("--trough", default="2026-07-30", help="폭락 구간 저점일 (다시 열리기까지 일수)")
    parser.add_argument("--window", default="2026-06-01", help="폭락 구간 별도 집계 시작일")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end) if args.end else None
    data_start = (start - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    closes = load_closes(data_start, Path(args.cache) if args.cache else None)

    if args.replay_log:
        frame = replay(closes, RULE_SETS["baseline"], start, end)
        joined = compare_with_log(frame, Path(args.replay_log))
        print(joined.to_string())
        for col in ("raw_ok", "applied_ok", "weekly_ok", "vol_ok"):
            print(f"{col}: {joined[col].mean() * 100:.0f}% ({int(joined[col].sum())}/{len(joined)})")
        if "composite_explains" in joined:
            explained = joined["raw_ok"] | joined["composite_explains"]
            print(f"raw_ok or composite_explains: {explained.mean() * 100:.0f}% ({int(explained.sum())}/{len(joined)})")
        return 0

    peak, trough, window = pd.Timestamp(args.peak), pd.Timestamp(args.trough), pd.Timestamp(args.window)
    full, crash = [], []
    for name in args.rules.split(","):
        rules = RULE_SETS[name]
        frame = replay(closes, rules, start, end)
        full.append({"rules": name, **summarize(frame)})
        crash.append({"rules": name, **summarize(frame.loc[window:], peak=peak, trough=trough)})
    print(f"\n== 전 구간 {start.date()} ~ {end.date() if end else '최근'}")
    print(pd.DataFrame(full).set_index("rules").to_string())
    print(f"\n== 폭락 구간 {window.date()} ~ (고점 {peak.date()}, 저점 {trough.date()})")
    print(pd.DataFrame(crash).set_index("rules").to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
