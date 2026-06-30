"""CostModel 단위 테스트 (Phase 2 Step 5)."""

from __future__ import annotations

import pytest

from maps.backtest.cost_model import (
    BROKER_FEE_PER_SIDE,
    LARGE_CAP_THRESHOLD,
    SLIPPAGE_LARGE_CAP,
    SLIPPAGE_SMALL_CAP,
    TRANSACTION_TAX_SELL,
    CostModel,
    Trade,
)


def _trade(
    entry_price: float = 10_000,
    exit_price: float = 11_000,
    qty: int = 100,
    is_etf: bool = False,
    market_cap: float = 1e12,
) -> Trade:
    return Trade(
        ticker="TEST",
        entry_price=entry_price,
        exit_price=exit_price,
        qty=qty,
        is_etf=is_etf,
        market_cap=market_cap,
    )


def test_etf_no_tax():
    """ETF는 거래세가 0이어야 한다."""
    model = CostModel()

    etf = _trade(is_etf=True, market_cap=1e12)
    stock = _trade(is_etf=False, market_cap=1e12)

    etf_net = model.apply(etf)
    stock_net = model.apply(stock)

    # ETF가 거래세만큼 더 유리해야 함
    tax_amount = stock.exit_price * stock.qty * TRANSACTION_TAX_SELL
    assert abs((etf_net - stock_net) - tax_amount) < 1e-6


def test_small_cap_slippage():
    """시총 < 5천억이면 SLIPPAGE_SMALL_CAP이 적용된다."""
    model = CostModel()

    small = _trade(market_cap=LARGE_CAP_THRESHOLD - 1)
    large = _trade(market_cap=LARGE_CAP_THRESHOLD)

    small_cost = model.total_cost(small)
    large_cost = model.total_cost(large)

    val = small.trade_value
    slip_diff = (SLIPPAGE_SMALL_CAP - SLIPPAGE_LARGE_CAP) * 2 * val
    assert abs((small_cost - large_cost) - slip_diff) < 1e-6


def test_commission_charged_on_both_legs():
    """수수료는 매수·매도 양측에 부과된다 (편도율 × (매수금액+매도금액))."""
    model = CostModel(slippage_large=0.0, slippage_small=0.0, tax_sell=0.0, vol_multiplier=1.0)
    trade = _trade(entry_price=10_000, exit_price=11_000, qty=100)
    expected_fee = (10_000 * 100 + 11_000 * 100) * BROKER_FEE_PER_SIDE
    assert abs(model.total_cost(trade) - expected_fee) < 1e-6


def test_sensitivity_higher_than_apply():
    """sensitivity(trade, 1.5) 의 비용이 apply(trade) 보다 커야 한다."""
    model = CostModel()
    trade = _trade()

    net_normal = model.apply(trade)
    net_scaled = model.sensitivity(trade, 1.5)

    assert net_scaled < net_normal
