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

from maps.backtest.cost_model import VOL_MULTIPLIER_MAP, CostModel, Trade
from maps.common.exceptions import BacktestError
from maps.common.sizing import risk_based_qty
from maps.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

# 포지션 사이징 상수
ACCOUNT_RISK_PER_TRADE = 0.005   # 계좌 위험 0.5%
MAX_SINGLE_EXPOSURE = 0.10       # 단일 종목 노출 10% 상한

# ATR 기반 손절 배율 (변동성 적응형)
_ATR_STOP_MULTIPLIER = 2.0       # 손절 = 진입가 - ATR(14) × 2.0


def _compute_atr14(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(14)을 계산한다 (Wilder EMA 방식).

    변동성 장세에서 고정% 손절보다 넓은 손절폭을 제공해 노이즈에 의한 조기 청산을 줄인다.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


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
        단일 종목 노출 상한(10%) 적용. 실거래 주문 경로와 동일한 공용 사이징
        (`maps.common.sizing.risk_based_qty`)을 사용해 백테스트=실거래 정합 유지(C-2).
        """
        return risk_based_qty(
            equity=equity,
            entry_price=entry_price,
            stop_price=stop_price,
            account_risk=self._risk,
            max_exposure=self._max_exp,
        )


class BacktestEngine:
    """이벤트 기반 백테스트 엔진.

    단일 종목 OHLCV DataFrame을 받아 전략 신호로 매매를 시뮬레이션한다.
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_capital: float = 100_000_000,
        risk_free_rate: float = 0.03,
    ) -> None:
        self._cost = cost_model or CostModel(vol_multiplier=VOL_MULTIPLIER_MAP["normal"])
        self._capital = initial_capital
        self._sizer = PositionSizingEngine()
        # 연 무위험수익률. Sharpe 계산 시 초과수익(excess return) 기준으로 보정한다(H-4).
        self._rf = risk_free_rate

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
        atr_series = _compute_atr14(df)

        # 체결 타이밍 (look-ahead 제거):
        #   신호(entry/exit)는 t일 종가로 생성되지만, 종가가 확정되는 순간 장은 이미
        #   끝났으므로 그 종가로 체결할 수 없다. 따라서 신호를 한 칸 시프트해 신호 발생
        #   봉의 "다음 봉 시가(open)"에서 체결한다. 손절은 장중 도달(low ≤ stop) 시
        #   갭하락이면 시가, 아니면 손절가로 체결해 보수화한다. 실거래(익일 지정가 주문)와
        #   정합한 체결 모델.
        false_series = pd.Series(False, index=df.index)
        entry_fill = (
            df["entry_signal"].shift(1, fill_value=False)
            if "entry_signal" in df.columns
            else false_series
        )
        exit_fill = (
            df["exit_signal"].shift(1, fill_value=False)
            if "exit_signal" in df.columns
            else false_series
        )
        # 진입 손절가/ATR도 신호 봉(t) 기준값을 사용한다 (체결은 t+1 시가).
        stop_src = (
            df["stop_price"].shift(1)
            if "stop_price" in df.columns
            else pd.Series(np.nan, index=df.index)
        )
        atr_src = atr_series.shift(1)

        equity = self._capital
        position: dict = {}       # {"qty": int, "entry_price": float, "entry_date": date, "stop": float}
        equity_curve: list[float] = [equity]
        exposure_curve: list[float] = [0.0]   # 일별 투자 비중 (포지션 평가액 / 총자산)
        trade_list: list[TradeRecord] = []

        dates = df.index.tolist()

        for i, dt in enumerate(dates):
            row = df.loc[dt]
            ref_date = dt.date() if hasattr(dt, "date") else dt
            bar_open = float(row.get("open", row["close"]))
            bar_low = float(row.get("low", row["close"]))
            bar_close = float(row["close"])
            exited_this_bar = False

            # ── 보유 중: 청산/손절 체크 (체결은 당일 시가 또는 손절가) ──
            if position:
                exit_reason = None
                exit_price = bar_close

                if bool(exit_fill.get(dt, False)):
                    # 전일 종가 기준 청산 신호 → 당일 시가 체결
                    exit_reason = "signal"
                    exit_price = bar_open
                elif bar_low <= position["stop"]:
                    # 장중 손절 도달 → 갭하락 시 시가, 아니면 손절가 체결 (보수적)
                    exit_reason = "stop_loss"
                    exit_price = min(position["stop"], bar_open)
                elif i == len(dates) - 1:
                    exit_reason = "end_of_period"
                    exit_price = bar_close

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
                    exited_this_bar = True

            # ── 미보유: 진입 체크 (전일 종가 신호 → 당일 시가 체결) ──
            # 같은 봉에서 손절 청산 후 즉시 재진입하지 않는다 (exited_this_bar 가드).
            if not position and not exited_this_bar and bool(entry_fill.get(dt, False)):
                entry_price = bar_open
                default_stop = entry_price * 0.95
                stop_from_signal = float(stop_src.get(dt, default_stop))
                if not np.isfinite(stop_from_signal):
                    stop_from_signal = default_stop
                # ATR 기반 손절: 변동성 장세에서 고정% 손절보다 넓은 쪽을 선택
                # 넓은 손절 → 포지션 크기 자동 감소 (계좌 위험 0.5% 고정 원칙 유지)
                atr_val = float(atr_src.get(dt, 0.0) or 0.0)
                if atr_val > 0:
                    atr_stop = entry_price - _ATR_STOP_MULTIPLIER * atr_val
                    stop_price = min(stop_from_signal, atr_stop)  # 더 낮은(넓은) 손절 선택
                else:
                    stop_price = stop_from_signal
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
            position_value = position["qty"] * bar_close if position else 0.0
            mark = equity + position_value
            equity_curve.append(mark)
            exposure_curve.append(position_value / mark if mark > 0 else 0.0)

        return self._compute_metrics(
            strategy.strategy_id, data, equity_curve, trade_list, exposure_curve
        )

    def _compute_metrics(
        self,
        strategy_id: str,
        data: pd.DataFrame,
        equity_curve: list[float],
        trade_list: list[TradeRecord],
        exposure_curve: list[float] | None = None,
    ) -> BacktestResult:
        arr = np.array(equity_curve, dtype=float)
        daily_rets = np.diff(arr) / arr[:-1]

        start = data.index[0].date() if hasattr(data.index[0], "date") else data.index[0]
        end = data.index[-1].date() if hasattr(data.index[-1], "date") else data.index[-1]

        # 연수는 실제 달력일 기반으로 산정한다. 거래일 수/252 는 거래일이 적은 fold에서
        # CAGR을 왜곡한다(H-4). 달력 정보를 못 구하면 거래일 수/252 로 폴백.
        try:
            span_days = (end - start).days
        except TypeError:
            span_days = 0
        years = span_days / 365.25 if span_days > 0 else len(arr) / 252

        final = arr[-1]
        cagr = (final / self._capital) ** (1 / max(years, 1e-6)) - 1

        peak = np.maximum.accumulate(arr)
        dd = (arr - peak) / peak
        mdd = float(dd.min())

        # 노출 가중 rf 초과수익 기준 Sharpe (2026-08-02 재설계, 이월 20번).
        # 종전에는 총자산 수익률에서 rf 3% 전체를 차감했는데, 단일 종목 백테스트는
        # 리스크 사이징 탓에 노출이 ~10%뿐이라 유휴 현금 90%가 rf에 그대로 얻어맞아
        # 샤프가 -3 ~ -12로 왜곡됐다 (검증 게이트 sharpe_mean>0 구조적 통과 불가).
        # rf를 그날 투자 비중만큼만 차감하면 수학적으로 노출 규모와 무관해진다
        # (완전 투자 시 종전 공식과 동일, 완전 현금 시 초과수익 0).
        rf_daily = self._rf / 252.0
        if exposure_curve is not None and len(exposure_curve) == len(arr):
            exposure = np.array(exposure_curve[:-1], dtype=float)
        else:
            exposure = np.ones_like(daily_rets)  # 노출 정보 없으면 종전(완전 투자) 가정
        excess = daily_rets - rf_daily * exposure
        sharpe = (
            float(excess.mean() / excess.std() * np.sqrt(252))
            if excess.std() > 0
            else 0.0
        )

        gains = daily_rets[daily_rets > 0].sum()
        losses = abs(daily_rets[daily_rets < 0].sum())
        g2p = float(gains / losses) if losses > 0 else float("inf")

        wins = sum(1 for t in trade_list if t.net_pnl > 0)
        win_rate = wins / len(trade_list) if trade_list else 0.0

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
