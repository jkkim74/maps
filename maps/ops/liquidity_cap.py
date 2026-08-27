"""주문 금액을 종목 유동성에 맞춰 제한하는 순수 함수.

주문 경로와 주문 미리보기가 **같은 함수**를 쓴다. 경로마다 따로 구현하면 화면이
보여 준 수량과 실제 주문이 갈린다 — 손절가가 사이징과 화면에서 갈려 포지션이
2배로 잡혔던 2026-07-29 사고와 같은 구조다(CLAUDE.md 제약 7번).
"""

from __future__ import annotations

from dataclasses import dataclass

from maps.common.settings import MapsSettings

LIQUIDITY_CAPPED = "LIQUIDITY_CAPPED"
BELOW_MIN_ORDER_AMOUNT = "BELOW_MIN_ORDER_AMOUNT"
TURNOVER_UNAVAILABLE = "TURNOVER_UNAVAILABLE"

#: 주문을 아예 내지 않게 만드는 사유들. 화면·다이제스트가 차단으로 표시한다.
BLOCKING_REASONS = frozenset({BELOW_MIN_ORDER_AMOUNT, TURNOVER_UNAVAILABLE})


@dataclass(frozen=True)
class LiquidityCapResult:
    """유동성 한도 적용 결과."""

    qty: int
    original_qty: int
    reason: str | None
    turnover_20d: float | None
    limit_amount: float


def apply_liquidity_cap(
    *,
    qty: int,
    price: float,
    turnover_20d: float | None,
    settings: MapsSettings,
) -> LiquidityCapResult:
    """주문 수량을 20거래일 평균 거래대금 대비 상한 이하로 줄인다.

    한도를 넘으면 주문을 버리지 않고 수량을 줄인다. 줄인 결과가 최소 주문금액에
    못 미치면 주문하지 않는다. 거래대금을 알 수 없으면 사지 않는다(fail-closed) —
    유니버스 필터가 이미 데이터 없는 종목을 걸러 내므로 주문 시점의 결측은
    이상 상황이다.

    매도에는 쓰지 않는다. 유동성 때문에 청산을 막으면 얇은 종목에 갇힌다.
    """
    pct = settings.maps_order_max_turnover_pct
    if pct <= 0 or qty <= 0:
        return LiquidityCapResult(
            qty=qty,
            original_qty=qty,
            reason=None,
            turnover_20d=turnover_20d,
            limit_amount=0.0,
        )

    if not turnover_20d or turnover_20d <= 0:
        return LiquidityCapResult(
            qty=0,
            original_qty=qty,
            reason=TURNOVER_UNAVAILABLE,
            turnover_20d=turnover_20d,
            limit_amount=0.0,
        )

    limit_amount = turnover_20d * pct
    if price <= 0 or qty * price <= limit_amount:
        return LiquidityCapResult(
            qty=qty,
            original_qty=qty,
            reason=None,
            turnover_20d=turnover_20d,
            limit_amount=limit_amount,
        )

    capped_qty = int(limit_amount // price)
    if capped_qty * price < settings.maps_order_min_amount_krw:
        return LiquidityCapResult(
            qty=0,
            original_qty=qty,
            reason=BELOW_MIN_ORDER_AMOUNT,
            turnover_20d=turnover_20d,
            limit_amount=limit_amount,
        )
    return LiquidityCapResult(
        qty=capped_qty,
        original_qty=qty,
        reason=LIQUIDITY_CAPPED,
        turnover_20d=turnover_20d,
        limit_amount=limit_amount,
    )
