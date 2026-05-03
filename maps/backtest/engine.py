"""백테스트 엔진.

설계서 v2.6.3 §2 기준.
as-of-date 원칙: run() 호출 시 universe 는 이미 ref_date 기준으로 필터된 목록이어야 한다.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from maps.backtest.cost_model import CostModel, Trade
from maps.common.exceptions import BacktestError
from maps.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

# 포지션 사이징 상수
ACCOUNT_RISK_PER_TRADE = 0.005   # 계좌 위험 0.5%
MAX_SINGLE_EXPOSURE = 0.10       # 단일 종목 노출 10% 상한


@dataclass
class TradeRecord:
    """개별 거래 기록."""

    ticker: str
    entry_date: datetime.date
    exit_date: datetime.date
    entry_price: float
    exit_price: float
    qty: int
    gross_pnl: float
    net_pnl: float
    exit_reason: str   # "signal" | "stop_loss" | "end_of_period"


@dataclass
class BacktestResult:
    """백테스트 성과 지표."""

    strategy_id: str
    start_date: datetime.date
    end_date: datetime.date
    initial_capital: float
    final_value: float

    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    gain_to_pain: float = 0.0

    total_trades: int = 0
    win_rate: float = 0.0

    equity_curve: list[float] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)
    trade_list: list[TradeRecord] = field(default_factory=list)


class PositionSizingEngine:
    """계좌 위험 기반 포지션 사이징."""

    def __init__(
        self,
        account_risk: float = ACCOUNT_RISK_PER_TRADE,
        max_exposure: float = MAX_SINGLE_EXPOSURE,
    ) -> None:
        self._risk = account_risk
        self._max_exp = max_exposure

    def calc_qty(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
    ) -> int:
        """주문 수량을 계산한다.

        수량 = (계좌 × risk) / (진입가 - 손절가)
        단일 종목 노출 상한(10%) 적용.
        """
        risk_amount = equity * self._risk
        per_share_risk = entry_price - stop_price
        if per_share_risk <= 0:
            return 0

        qty_by_risk = int(risk_amount / per_share_risk)
        max_qty = int(equity * self._max_exp / entry_price)
        return max(0, min(qty_by_risk, max_qty))


class BacktestEngine:
    """이벤트 기반 백테스트 엔진.

    단일 종목 OHLCV DataFrame을 받아 전략 신호로 매매를 시뮬레이션한다.
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_capital: float = 100_000_000,
    ) -> None:
        self._cost = cost_model or CostModel()
        self._capital = initial_capital
        self._sizer = PositionSizingEngine()

    def run(
        self,
        strategy: BaseStrategy,
        params: dict,
        data: pd.DataFrame,
        universe: list[str] | None = None,
        market_cap: float = 0.0,
        is_etf: bool = False,
    ) -> BacktestResult:
        """백테스트를 실행한다.

        Args:
            strategy: 전략 객체.
            params: 전략 파라미터.
            data: date 인덱스 OHLCV DataFrame (단일 종목 또는 MultiIndex).
            universe: 허용 ticker 목록 (None 이면 data 전체 사용).
            market_cap: 시가총액 (비용 모델용).
            is_etf: ETF 여부 (거래세 면제).

        Returns:
            BacktestResult.

        Raises:
            BacktestError: 데이터 부족 등 실행 불가 시.
        """
        if data.empty:
            raise BacktestError("데이터가 비어 있습니다.")

        df = strategy.generate_signals(data.copy(), params)

        equity = self._capital
        position: dict = {}       # {"qty": int, "entry_price": float, "entry_date": date, "stop": float}
        equity_curve: list[float] = [equity]
        trade_list: list[TradeRecord] = []

        dates = df.index.tolist()

        for i, dt in enumerate(dates):
            row = df.loc[dt]
            ref_date = dt.date() if hasattr(dt, "date") else dt

            # ── 보유 중: 청산/손절 체크 ──
            if position:
                exit_reason = None
                exit_price = float(row["close"])

                if float(row["close"]) <= position["stop"]:
                    exit_reason = "stop_loss"
                    exit_price = position["stop"]
                elif bool(row.get("exit_signal", False)):
                    exit_reason = "signal"
                elif i == len(dates) - 1:
                    exit_reason = "end_of_period"

                if exit_reason:
                    trade = Trade(
                        ticker=str(data.index.name or "unknown"),
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        qty=position["qty"],
                        is_etf=is_etf,
                        market_cap=market_cap,
                    )
                    net = self._cost.apply(trade)
                    # 매도 시 현금 = 매도대금 - 거래비용 (원금 복귀 + 순손익)
                    equity += position["qty"] * exit_price - (trade.gross_pnl - net)
                    trade_list.append(
                        TradeRecord(
                            ticker=str(data.index.name or "unknown"),
                            entry_date=position["entry_date"],
                            exit_date=ref_date,
                            entry_price=position["entry_price"],
                            exit_price=exit_price,
                            qty=position["qty"],
                            gross_pnl=trade.gross_pnl,
                            net_pnl=net,
                            exit_reason=exit_reason,
                        )
                    )
                    position = {}

            # ── 미보유: 진입 체크 ──
            if not position and bool(row.get("entry_signal", False)):
                entry_price = float(row["close"])
                stop_price = float(row.get("stop_price", entry_price * 0.95))
                qty = self._sizer.calc_qty(equity, entry_price, stop_price)
                if qty > 0 and entry_price * qty <= equity:
                    position = {
                        "qty": qty,
                        "entry_price": entry_price,
                        "entry_date": ref_date,
                        "stop": stop_price,
                    }
                    equity -= entry_price * qty

            # 포지션 평가액 포함한 총 자산
            mark = equity + (position["qty"] * float(row["close"]) if position else 0)
            equity_curve.append(mark)

        return self._compute_metrics(strategy.strategy_id, data, equity_curve, trade_list)

    def _compute_metrics(
        self,
        strategy_id: str,
        data: pd.DataFrame,
        equity_curve: list[float],
        trade_list: list[TradeRecord],
    ) -> BacktestResult:
        arr = np.array(equity_curve, dtype=float)
        daily_rets = np.diff(arr) / arr[:-1]

        years = len(arr) / 252
        final = arr[-1]
        cagr = (final / self._capital) ** (1 / max(years, 1e-6)) - 1

        peak = np.maximum.accumulate(arr)
        dd = (arr - peak) / peak
        mdd = float(dd.min())

        sharpe = (
            float(daily_rets.mean() / daily_rets.std() * np.sqrt(252))
            if daily_rets.std() > 0
            else 0.0
        )

        gains = daily_rets[daily_rets > 0].sum()
        losses = abs(daily_rets[daily_rets < 0].sum())
        g2p = float(gains / losses) if losses > 0 else float("inf")

        wins = sum(1 for t in trade_list if t.net_pnl > 0)
        win_rate = wins / len(trade_list) if trade_list else 0.0

        start = data.index[0].date() if hasattr(data.index[0], "date") else data.index[0]
        end = data.index[-1].date() if hasattr(data.index[-1], "date") else data.index[-1]

        return BacktestResult(
            strategy_id=strategy_id,
            start_date=start,
            end_date=end,
            initial_capital=self._capital,
            final_value=final,
            cagr=cagr,
            mdd=mdd,
            sharpe=sharpe,
            gain_to_pain=g2p,
            total_trades=len(trade_list),
            win_rate=win_rate,
            equity_curve=equity_curve,
            daily_returns=daily_rets.tolist(),
            trade_list=trade_list,
        )
