"""거래 비용 모델.

설계서 v2.6.3 §2 CostModel 기준.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 고정 상수
# ---------------------------------------------------------------------------
TRANSACTION_TAX_SELL: float = 0.0018    # 코스피·코스닥 매도 거래세
TRANSACTION_TAX_ETF: float = 0.0        # ETF 거래세 면제
BROKER_FEE_ROUNDTRIP: float = 0.00015   # 편도 수수료 (매수+매도 합산)
SLIPPAGE_LARGE_CAP: float = 0.0005      # 시총 >= 5천억 슬리피지
SLIPPAGE_SMALL_CAP: float = 0.0015      # 시총 < 5천억 슬리피지

LARGE_CAP_THRESHOLD: float = 5e11       # 5천억 원


@dataclass
class Trade:
    """단일 거래 정보."""

    ticker: str
    entry_price: float
    exit_price: float
    qty: int
    is_etf: bool = False
    market_cap: float = 0.0             # 진입 시점 시가총액 (원)

    @property
    def gross_pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def trade_value(self) -> float:
        return self.entry_price * self.qty


class CostModel:
    """거래 비용 계산 모델.

    비용 항목:
        - 브로커 수수료 (편도, 매수/매도 각 1회)
        - 슬리피지 (대형주 / 소형주)
        - 거래세 (매도 시, ETF 면제)
    """

    def __init__(
        self,
        broker_fee: float = BROKER_FEE_ROUNDTRIP,
        slippage_large: float = SLIPPAGE_LARGE_CAP,
        slippage_small: float = SLIPPAGE_SMALL_CAP,
        tax_sell: float = TRANSACTION_TAX_SELL,
    ) -> None:
        self._fee = broker_fee
        self._slip_large = slippage_large
        self._slip_small = slippage_small
        self._tax = tax_sell

    def apply(self, trade: Trade) -> float:
        """거래 비용을 적용한 순손익을 반환한다.

        Args:
            trade: 거래 정보.

        Returns:
            net_pnl = gross_pnl - total_cost
        """
        cost = self._calc_cost(trade)
        return trade.gross_pnl - cost

    def sensitivity(self, trade: Trade, multiplier: float) -> float:
        """비용을 multiplier 배 적용한 순손익을 반환한다.

        multiplier=1.5 → 비용 +50% 시나리오.

        Args:
            trade: 거래 정보.
            multiplier: 비용 배율 (e.g. 0.5, 1.5).

        Returns:
            net_pnl under scaled cost.
        """
        cost = self._calc_cost(trade) * multiplier
        return trade.gross_pnl - cost

    def total_cost(self, trade: Trade) -> float:
        """총 거래 비용(원)을 반환한다."""
        return self._calc_cost(trade)

    # ------------------------------------------------------------------

    def _calc_cost(self, trade: Trade) -> float:
        val = trade.trade_value
        slippage_rate = (
            self._slip_large
            if trade.market_cap >= LARGE_CAP_THRESHOLD
            else self._slip_small
        )
        tax = 0.0 if trade.is_etf else self._tax

        fee_cost = val * self._fee            # 편도 수수료 × 2 (매수+매도)
        slip_cost = val * slippage_rate * 2   # 슬리피지 왕복
        tax_cost = trade.exit_price * trade.qty * tax

        return fee_cost + slip_cost + tax_cost
