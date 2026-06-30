"""다종목 포트폴리오 리플레이 엔진.

단일 종목 `BacktestEngine` 은 종목별 독립 실행이라 공유 현금·동시 보유 슬롯 제약을
반영하지 못한다(보고서 C-2). 이 엔진은 하나의 포트폴리오(현금 + 동시 보유)로 전 종목을
이벤트 기반 시뮬레이션해 검증과 실거래의 자산곡선 괴리를 재현/측정한다.

핵심 정합:
- 체결: 신호 t일 종가 → t+1일 시가 (`fill_mode="next_open"`, 기본). look-ahead 비교용으로
  동일봉 종가 체결(`fill_mode="same_bar_close"`)도 지원한다.
- 사이징: 공용 `maps.common.sizing.risk_based_qty` (실거래·단일 백테스트와 동일).
- 손절: 전략별 고정%(`live_rules`) 와 ATR 손절 중 넓은 쪽, 갭하락 시 시가 체결.
- 비용: `CostModel` 왕복.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from maps.backtest.cost_model import VOL_MULTIPLIER_MAP, CostModel, Trade
from maps.backtest.engine import _ATR_STOP_MULTIPLIER, _compute_atr14
from maps.common.exceptions import BacktestError
from maps.common.sizing import risk_based_qty
from maps.strategy.base import BaseStrategy
from maps.strategy.live_rules import atr_stop_price, stop_loss_price

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """포트폴리오 리플레이 설정."""

    initial_capital: float = 100_000_000
    max_positions: int = 10          # 동시 보유 슬롯 상한
    account_risk: float = 0.005      # 1회 거래당 계좌 위험
    max_exposure: float = 0.10       # 단일 종목 노출 상한
    risk_free_rate: float = 0.03     # Sharpe 무위험수익률
    fill_mode: str = "next_open"     # "next_open" | "same_bar_close"
    vol_regime: str = "normal"       # 비용 모델 변동성 배수


@dataclass
class PortfolioTrade:
    """체결 거래 기록."""

    ticker: str
    entry_date: datetime.date
    exit_date: datetime.date
    entry_price: float
    exit_price: float
    qty: int
    net_pnl: float
    exit_reason: str


@dataclass
class PortfolioResult:
    """포트폴리오 성과 지표."""

    strategy_id: str
    fill_mode: str
    initial_capital: float
    final_value: float
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    dates: list[datetime.date] = field(default_factory=list)


@dataclass
class _Prepared:
    """종목별 사전계산 신호/시리즈."""

    sig: pd.DataFrame
    entry_fill: pd.Series
    exit_fill: pd.Series
    stop_src: pd.Series
    atr_src: pd.Series
    momentum: pd.Series
    last_date: object
    market_cap: float
    is_etf: bool


class PortfolioReplayEngine:
    """공유 현금·슬롯 제약 하의 다종목 포트폴리오 백테스트."""

    def __init__(self, config: PortfolioConfig | None = None) -> None:
        self._cfg = config or PortfolioConfig()
        self._cost = CostModel(vol_multiplier=VOL_MULTIPLIER_MAP[self._cfg.vol_regime])

    def run(
        self,
        strategy: BaseStrategy,
        params: dict,
        data: dict[str, pd.DataFrame],
        market_caps: dict[str, float] | None = None,
        etfs: set[str] | None = None,
    ) -> PortfolioResult:
        """포트폴리오 리플레이를 실행한다.

        Args:
            strategy: 전략 객체.
            params: 전략 파라미터.
            data: {ticker: date 인덱스 OHLCV DataFrame}.
            market_caps: {ticker: 시가총액} (비용 모델 슬리피지 구분용).
            etfs: ETF ticker 집합 (거래세 면제).

        Returns:
            PortfolioResult.
        """
        if not data:
            raise BacktestError("데이터가 비어 있습니다.")

        market_caps = market_caps or {}
        etfs = etfs or set()
        next_open = self._cfg.fill_mode == "next_open"

        prepared: dict[str, _Prepared] = {}
        for ticker, df in data.items():
            if df.empty or "entry_signal" not in (sig := strategy.generate_signals(df.copy(), params)).columns:
                continue
            atr = _compute_atr14(sig)
            if next_open:
                entry_fill = sig["entry_signal"].shift(1, fill_value=False)
                exit_fill = sig["exit_signal"].shift(1, fill_value=False)
                stop_src = sig["stop_price"].shift(1) if "stop_price" in sig.columns else pd.Series(np.nan, index=sig.index)
                atr_src = atr.shift(1)
            else:
                entry_fill = sig["entry_signal"].astype(bool)
                exit_fill = sig["exit_signal"].astype(bool) if "exit_signal" in sig.columns else pd.Series(False, index=sig.index)
                stop_src = sig["stop_price"] if "stop_price" in sig.columns else pd.Series(np.nan, index=sig.index)
                atr_src = atr
            momentum = sig["close"] / sig["close"].shift(20) - 1.0
            prepared[ticker] = _Prepared(
                sig=sig,
                entry_fill=entry_fill,
                exit_fill=exit_fill,
                stop_src=stop_src,
                atr_src=atr_src,
                momentum=momentum,
                last_date=sig.index[-1],
                market_cap=float(market_caps.get(ticker, 0.0)),
                is_etf=ticker in etfs,
            )

        if not prepared:
            raise BacktestError("유효한 신호를 가진 종목이 없습니다.")

        calendar = sorted({d for p in prepared.values() for d in p.sig.index})

        cash = self._cfg.initial_capital
        positions: dict[str, dict] = {}
        last_close: dict[str, float] = {}
        trades: list[PortfolioTrade] = []
        equity_curve: list[float] = []
        curve_dates: list[datetime.date] = []

        for dt in calendar:
            ref_date = dt.date() if hasattr(dt, "date") else dt

            # ── 보유 종목: 청산/손절 ──
            for ticker in list(positions):
                p = prepared[ticker]
                if dt not in p.sig.index:
                    continue
                row = p.sig.loc[dt]
                bar_open = float(row.get("open", row["close"]))
                bar_low = float(row.get("low", row["close"]))
                bar_close = float(row["close"])
                last_close[ticker] = bar_close
                pos = positions[ticker]

                exit_reason = None
                exit_price = bar_close
                is_last = dt == p.last_date

                if next_open:
                    if bool(p.exit_fill.get(dt, False)):
                        exit_reason, exit_price = "signal", bar_open
                    elif bar_low <= pos["stop"]:
                        exit_reason, exit_price = "stop_loss", min(pos["stop"], bar_open)
                    elif is_last:
                        exit_reason, exit_price = "end_of_period", bar_close
                else:
                    if bar_close <= pos["stop"]:
                        exit_reason, exit_price = "stop_loss", pos["stop"]
                    elif bool(p.exit_fill.get(dt, False)):
                        exit_reason, exit_price = "signal", bar_close
                    elif is_last:
                        exit_reason, exit_price = "end_of_period", bar_close

                if exit_reason:
                    cash += self._close_position(ticker, pos, exit_price, ref_date, p, trades)
                    del positions[ticker]

            # ── 신규 진입 (슬롯·현금 제약) ──
            free_slots = self._cfg.max_positions - len(positions)
            if free_slots > 0:
                portfolio_value = cash + sum(
                    pos["qty"] * last_close.get(tk, pos["entry_price"])
                    for tk, pos in positions.items()
                )
                candidates = [
                    tk for tk, p in prepared.items()
                    if tk not in positions and dt in p.sig.index and bool(p.entry_fill.get(dt, False))
                ]
                # 신호가 슬롯보다 많으면 모멘텀(20일 수익률) 상위부터 선택
                candidates.sort(key=lambda tk: _safe_float(prepared[tk].momentum.get(dt, np.nan)), reverse=True)

                for ticker in candidates:
                    if free_slots <= 0:
                        break
                    p = prepared[ticker]
                    row = p.sig.loc[dt]
                    entry_price = float(row.get("open", row["close"])) if next_open else float(row["close"])
                    if entry_price <= 0:
                        continue
                    stop_price = self._resolve_stop(strategy.strategy_id, entry_price, p, dt)
                    qty = risk_based_qty(
                        equity=portfolio_value,
                        entry_price=entry_price,
                        stop_price=stop_price,
                        account_risk=self._cfg.account_risk,
                        max_exposure=self._cfg.max_exposure,
                        available_cash=cash,
                    )
                    if qty > 0 and entry_price * qty <= cash:
                        cash -= entry_price * qty
                        positions[ticker] = {
                            "qty": qty,
                            "entry_price": entry_price,
                            "entry_date": ref_date,
                            "stop": stop_price,
                        }
                        last_close[ticker] = float(row["close"])
                        free_slots -= 1

            # ── 일일 평가 ──
            mark = cash + sum(
                pos["qty"] * last_close.get(tk, pos["entry_price"])
                for tk, pos in positions.items()
            )
            equity_curve.append(mark)
            curve_dates.append(ref_date)

        # 잔여 포지션 강제 청산 (마지막 종가)
        for ticker in list(positions):
            pos = positions[ticker]
            p = prepared[ticker]
            exit_price = last_close.get(ticker, pos["entry_price"])
            cash += self._close_position(ticker, pos, exit_price, curve_dates[-1], p, trades)
            del positions[ticker]

        return self._metrics(strategy.strategy_id, equity_curve, curve_dates, trades)

    # ------------------------------------------------------------------

    def _resolve_stop(self, strategy_id: str, entry_price: float, p: _Prepared, dt) -> float:
        """전략 고정% 손절과 ATR 손절 중 넓은(낮은) 쪽을 택한다."""
        default_stop = entry_price * 0.95
        stop_from_signal = _safe_float(p.stop_src.get(dt, default_stop))
        if not np.isfinite(stop_from_signal) or stop_from_signal <= 0:
            stop_from_signal = stop_loss_price(strategy_id, entry_price) or default_stop
        atr_val = _safe_float(p.atr_src.get(dt, 0.0))
        atr_stop = (
            atr_stop_price(strategy_id, entry_price, atr_val)
            if atr_val and atr_val > 0
            else (entry_price - _ATR_STOP_MULTIPLIER * atr_val if atr_val > 0 else None)
        )
        if atr_stop and atr_stop > 0:
            return min(stop_from_signal, atr_stop)
        return stop_from_signal

    def _close_position(
        self,
        ticker: str,
        pos: dict,
        exit_price: float,
        exit_date: datetime.date,
        p: _Prepared,
        trades: list[PortfolioTrade],
    ) -> float:
        """포지션을 청산하고 현금 증가분(매도대금 - 거래비용)을 반환한다."""
        trade = Trade(
            ticker=ticker,
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            qty=pos["qty"],
            is_etf=p.is_etf,
            market_cap=p.market_cap,
        )
        net = self._cost.apply(trade)
        cash_delta = pos["qty"] * exit_price - (trade.gross_pnl - net)
        trades.append(
            PortfolioTrade(
                ticker=ticker,
                entry_date=pos["entry_date"],
                exit_date=exit_date,
                entry_price=pos["entry_price"],
                exit_price=exit_price,
                qty=pos["qty"],
                net_pnl=net,
                exit_reason="",
            )
        )
        return cash_delta

    def _metrics(
        self,
        strategy_id: str,
        equity_curve: list[float],
        dates: list[datetime.date],
        trades: list[PortfolioTrade],
    ) -> PortfolioResult:
        arr = np.array(equity_curve, dtype=float)
        if len(arr) < 2:
            return PortfolioResult(
                strategy_id=strategy_id,
                fill_mode=self._cfg.fill_mode,
                initial_capital=self._cfg.initial_capital,
                final_value=float(arr[-1]) if len(arr) else self._cfg.initial_capital,
                equity_curve=equity_curve,
                dates=dates,
            )

        daily_rets = np.diff(arr) / arr[:-1]
        span_days = (dates[-1] - dates[0]).days if dates else 0
        years = span_days / 365.25 if span_days > 0 else len(arr) / 252
        final = float(arr[-1])
        cagr = (final / self._cfg.initial_capital) ** (1 / max(years, 1e-6)) - 1

        peak = np.maximum.accumulate(arr)
        dd = (arr - peak) / peak
        mdd = float(dd.min())

        rf_daily = self._cfg.risk_free_rate / 252.0
        sharpe = (
            float((daily_rets.mean() - rf_daily) / daily_rets.std() * np.sqrt(252))
            if daily_rets.std() > 0
            else 0.0
        )
        wins = sum(1 for t in trades if t.net_pnl > 0)
        win_rate = wins / len(trades) if trades else 0.0

        return PortfolioResult(
            strategy_id=strategy_id,
            fill_mode=self._cfg.fill_mode,
            initial_capital=self._cfg.initial_capital,
            final_value=final,
            cagr=cagr,
            mdd=mdd,
            sharpe=sharpe,
            total_trades=len(trades),
            win_rate=win_rate,
            equity_curve=equity_curve,
            dates=dates,
        )


def _safe_float(value) -> float:
    """NaN/None 을 안전하게 float 로 변환 (랭킹·비교용 -inf 폴백)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    return f if np.isfinite(f) else float("-inf")
