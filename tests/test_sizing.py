"""공용 포지션 사이징(risk_based_qty) 단위 테스트 (C-2)."""

from __future__ import annotations

from maps.common.sizing import risk_based_qty


def test_risk_qty_capped_by_exposure() -> None:
    # risk 1% of 1억 / 4000 = 250주이나 단일종목 10% 노출 상한 → 142주
    qty = risk_based_qty(
        equity=100_000_000,
        entry_price=70_000,
        stop_price=66_000,
        account_risk=0.01,
        max_exposure=0.10,
    )
    assert qty == 142


def test_risk_qty_capped_by_cash() -> None:
    # 현금이 작으면 현금 상한이 바인딩 (500_000 // 70_000 = 7)
    qty = risk_based_qty(
        equity=100_000_000,
        entry_price=70_000,
        stop_price=66_000,
        account_risk=0.01,
        max_exposure=0.10,
        available_cash=500_000,
    )
    assert qty == 7


def test_risk_qty_zero_when_stop_invalid() -> None:
    assert risk_based_qty(
        equity=100_000_000,
        entry_price=70_000,
        stop_price=70_000,  # per_share_risk = 0
        account_risk=0.01,
        max_exposure=0.10,
    ) == 0


def test_backtest_default_risk_sizing_matches() -> None:
    # 백테스트 기본값: account_risk=0.005, max_exposure=0.10, 현금 상한 없음
    qty = risk_based_qty(
        equity=100_000_000,
        entry_price=10_000,
        stop_price=9_500,
        account_risk=0.005,
        max_exposure=0.10,
    )
    # risk: 500_000 / 500 = 1000주, 노출상한: 0.10*1억/10000 = 1000주 → 1000
    assert qty == 1000
