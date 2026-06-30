"""공용 포지션 사이징.

백테스트(`maps.backtest.engine.PositionSizingEngine`)와 실거래 주문 경로가 동일한
계좌-위험 기반 사이징을 쓰도록 단일 진입점을 제공한다. 두 경로가 서로 다른 공식을
쓰면 검증한 자산곡선과 실거래 자산곡선이 어긋난다(보고서 C-2).
"""

from __future__ import annotations


def risk_based_qty(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    account_risk: float,
    max_exposure: float,
    available_cash: float | None = None,
) -> int:
    """계좌 위험 기반 주문 수량을 계산한다.

    수량 = (equity × account_risk) / (entry − stop)
    단일 종목 노출 상한(max_exposure × equity / entry)을 적용하고,
    ``available_cash`` 가 주어지면 가용 현금 상한(cash // entry)도 함께 적용한다.

    Args:
        equity: 사이징 기준 자산(백테스트=현재 equity, 실거래=계좌 total_value).
        entry_price: 진입가.
        stop_price: 손절가. entry 보다 작아야 유효.
        account_risk: 1회 거래당 허용 계좌 위험 비율(예 0.005 = 0.5%).
        max_exposure: 단일 종목 최대 노출 비율(예 0.10 = 10%).
        available_cash: 가용 현금. None 이면 현금 상한 미적용(백테스트 호환).

    Returns:
        주문 수량(>= 0). 진입가/손절폭/자산이 비정상이면 0.
    """
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0 or entry_price <= 0 or equity <= 0:
        return 0

    qty_by_risk = int((equity * account_risk) / per_share_risk)
    max_qty = int((equity * max_exposure) / entry_price)
    qty = min(qty_by_risk, max_qty)

    if available_cash is not None and available_cash >= 0:
        qty = min(qty, int(available_cash // entry_price))

    return max(0, qty)
