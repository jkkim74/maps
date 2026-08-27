from __future__ import annotations

import pytest

from maps.common.settings import MapsSettings
from maps.ops.liquidity_cap import (
    BELOW_MIN_ORDER_AMOUNT,
    LIQUIDITY_CAPPED,
    TURNOVER_UNAVAILABLE,
    apply_liquidity_cap,
)


@pytest.fixture
def settings() -> MapsSettings:
    return MapsSettings(
        maps_order_max_turnover_pct=0.02,
        maps_order_min_amount_krw=500_000,
    )


def test_order_within_limit_passes_untouched(settings: MapsSettings) -> None:
    """한도 이내면 손대지 않는다 — 실측 19건 중 18건이 여기 해당한다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=1_000_000_000, settings=settings
    )

    assert result.qty == 100
    assert result.original_qty == 100
    assert result.reason is None


def test_order_over_limit_is_reduced(settings: MapsSettings) -> None:
    """2026-08-20 195990 실제 수치: 2,323주 @1,434원, 20일 평균 37,606,136원."""
    result = apply_liquidity_cap(
        qty=2323, price=1434, turnover_20d=37_606_136, settings=settings
    )

    assert result.reason == LIQUIDITY_CAPPED
    assert result.original_qty == 2323
    assert result.qty == 524
    assert result.qty * 1434 <= result.limit_amount


def test_reduced_below_minimum_is_blocked(settings: MapsSettings) -> None:
    """축소 결과가 최소 주문금액에 못 미치면 주문하지 않는다."""
    result = apply_liquidity_cap(
        qty=1000, price=1000, turnover_20d=10_000_000, settings=settings
    )

    assert result.qty == 0
    assert result.reason == BELOW_MIN_ORDER_AMOUNT


def test_missing_turnover_blocks_the_order(settings: MapsSettings) -> None:
    """거래대금을 모르면 사지 않는다(fail-closed). limit_amount 는 0 이다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=None, settings=settings
    )

    assert result.qty == 0
    assert result.reason == TURNOVER_UNAVAILABLE
    assert result.limit_amount == 0.0


def test_zero_turnover_blocks_the_order(settings: MapsSettings) -> None:
    """0 도 '모른다'와 같게 다룬다 — 20거래일 이력이 없으면 0 이 들어온다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=0.0, settings=settings
    )

    assert result.qty == 0
    assert result.reason == TURNOVER_UNAVAILABLE


def test_pct_zero_disables_the_gate() -> None:
    """설정으로 끌 수 있다 — 끄면 원래 수량 그대로."""
    off = MapsSettings(
        maps_order_max_turnover_pct=0.0, maps_order_min_amount_krw=500_000
    )

    result = apply_liquidity_cap(
        qty=2323, price=1434, turnover_20d=37_606_136, settings=off
    )

    assert result.qty == 2323
    assert result.reason is None


def test_non_positive_qty_is_returned_as_is(settings: MapsSettings) -> None:
    """상류에서 이미 0 이면 사유를 새로 붙이지 않는다."""
    result = apply_liquidity_cap(
        qty=0, price=10_000, turnover_20d=1_000_000_000, settings=settings
    )

    assert result.qty == 0
    assert result.reason is None


def test_preview_and_order_paths_share_one_implementation() -> None:
    """미리보기와 주문 경로가 같은 함수를 부르는지 소스로 확인한다.

    손절가가 사이징과 화면에서 갈려 포지션이 2배로 잡혔던 2026-07-29 사고와
    같은 구조다 — 경로마다 따로 구현하면 화면 수량과 실주문이 어긋난다.
    """
    from pathlib import Path

    preview = Path("maps/ops/order_preview.py").read_text(encoding="utf-8")
    scheduler = Path("maps/ops/scheduler.py").read_text(encoding="utf-8")

    for source, name in ((preview, "order_preview"), (scheduler, "scheduler")):
        assert "apply_liquidity_cap(" in source, f"{name} 이 공용 한도 함수를 쓰지 않는다"
        assert "avg_turnover_20d(" in source, f"{name} 이 공용 거래대금 계산을 쓰지 않는다"
        assert "candidates[0].ref_date" in source, (
            f"{name} 의 유동성 기준일이 후보 스냅샷 기준일이 아니다"
        )
