"""손절가 정본(`effective_stop_price`) 및 경로 간 일치 테스트.

배경: 손절가를 구하는 방식이 경로마다 달랐다.

* 백테스트(`portfolio_replay._resolve_stop`) — 고정%와 ATR 중 넓은 쪽
* 실거래 청산(`scheduler._submit_exit_orders`) — ``atr or fixed`` (ATR 우선)
* 화면 표시(`api/risk.py`) — ``atr or fixed``
* 실거래 사이징(`scheduler._order_qty`) — 고정%만

ATR 손절이 고정%보다 **좁을** 때 실거래가 백테스트보다 일찍 털렸고,
ATR 손절이 더 **넓을** 때는 사이징이 손절폭을 과소평가해 포지션이 과대 산정됐다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.market.trading_rules import krx_tick_size, round_down_krx_price
from maps.ops.scheduler import OperationalPipeline
from maps.strategy.live_rules import (
    atr_stop_price,
    effective_stop_price,
    stop_loss_price,
)


# ── 정본 규칙 ────────────────────────────────────────────────────────────────


def test_atr_wider_than_fixed_wins():
    """ATR 손절이 더 넓으면(가격이 낮으면) ATR 을 쓴다 — 잔진동 조기 손절 방지."""
    # donchian_v2: 고정 10%, ATR × 2.0
    entry, atr = 79_500.0, 8_316.0
    fixed = stop_loss_price("donchian_v2", entry)      # 71,550
    atr_stop = atr_stop_price("donchian_v2", entry, atr)  # 62,868

    assert atr_stop < fixed
    # 결과는 호가 단위로 내림된다 (62,868 → 62,800)
    assert effective_stop_price("donchian_v2", entry, atr) == pytest.approx(
        round_down_krx_price(atr_stop)
    )


def test_atr_narrower_than_fixed_keeps_fixed():
    """ATR 손절이 더 좁으면 고정%를 쓴다 — 이 경로가 실거래에서 깨져 있었다.

    저변동성 종목에서 ``atr or fixed`` 는 항상 ATR 을 골라 백테스트보다
    일찍 손절시킨다.
    """
    entry, atr = 79_500.0, 1_000.0
    fixed = stop_loss_price("donchian_v2", entry)         # 71,550
    atr_stop = atr_stop_price("donchian_v2", entry, atr)  # 77,500

    assert atr_stop > fixed
    assert effective_stop_price("donchian_v2", entry, atr) == pytest.approx(
        round_down_krx_price(fixed)
    )


def test_missing_atr_falls_back_to_fixed():
    entry = 10_000.0
    assert effective_stop_price("pullback_v3", entry, None) == pytest.approx(9_500.0)
    assert effective_stop_price("pullback_v3", entry, 0.0) == pytest.approx(9_500.0)


def test_unknown_strategy_returns_none():
    """미등록 전략은 손절가를 만들어내지 않는다 (호출부가 폴백을 고르게 한다)."""
    assert effective_stop_price("no_such_strategy", 10_000.0, 500.0) is None


def test_invalid_entry_price_returns_none():
    assert effective_stop_price("pullback_v3", 0.0, 100.0) is None
    assert effective_stop_price("pullback_v3", None, 100.0) is None
    assert effective_stop_price(None, 10_000.0, 100.0) is None


def test_result_is_never_above_fixed_stop():
    """정본은 어떤 ATR 값에도 고정% 손절보다 위로 올라가지 않는다."""
    entry = 50_000.0
    fixed = stop_loss_price("ath_breakout_v1", entry)
    for atr in (1.0, 100.0, 1_000.0, 5_000.0, 20_000.0):
        assert effective_stop_price("ath_breakout_v1", entry, atr) <= fixed


# ── 호가 정렬 ────────────────────────────────────────────────────────────────

def test_stop_lands_on_a_valid_krx_tick():
    """손절가가 시장에 존재하는 가격이어야 한다.

    2026-07-31 운영 보유 3종목이 21,322 / 7,321 / 32,487 로 표시되고 있었다.
    셋 다 호가 단위에 맞지 않아 그 가격에는 주문을 걸 수도, 체결될 수도 없다.
    화면뿐 아니라 청산 판정(`현재가 <= 손절가`)과 사이징(`진입가 - 손절가`)이
    모두 실제와 어긋난다.
    """
    live_cases = [
        ("multi_asset_trend_v1", 23_344.026, 1_011.0, 21_300),
        ("donchian_v1", 8_180.0, 429.5, 7_320),
        ("pullback_v3", 36_600.0, 2_056.5, 32_450),
    ]
    for strategy_id, entry, atr, expected in live_cases:
        stop = effective_stop_price(strategy_id, entry, atr)
        assert stop == pytest.approx(expected)
        assert stop % krx_tick_size(stop) == 0


def test_alignment_never_tightens_the_stop():
    """호가 정렬은 손절을 조이지 않는다 — 반올림이면 조여진다.

    32,487 을 반올림하면 32,500 이 되어 손절폭이 13원 좁아진다. 백테스트·사이징이
    가정한 폭보다 좁아지면 실거래에서만 더 일찍 털린다.
    """
    for strategy_id in ("pullback_v3", "donchian_v2", "ath_breakout_v1"):
        for entry in (3_140.0, 8_180.0, 23_344.0, 36_600.0, 145_500.0, 620_000.0):
            for atr in (0.0, 55.5, 429.5, 2_056.5):
                stop = effective_stop_price(strategy_id, entry, atr)
                if stop is None:
                    continue
                raw = min(
                    p for p in (
                        stop_loss_price(strategy_id, entry),
                        atr_stop_price(strategy_id, entry, atr),
                    ) if p is not None and p > 0
                )
                assert stop <= raw            # 조여지지 않는다
                assert raw - stop < krx_tick_size(raw)   # 한 틱 이상 벌어지지도 않는다
                assert stop % krx_tick_size(stop) == 0


def test_etf_uses_five_won_tick():
    """ETF·ETN·ELW 는 가격대와 무관하게 5원 고정이다."""
    stop = effective_stop_price("donchian_v1", 36_600.0, 2_056.5, security_type="ETF")
    assert stop % 5 == 0
    assert stop == pytest.approx(32_485)   # 주식이면 32,450


# ── 사이징이 정본을 쓰는지 ────────────────────────────────────────────────────


@pytest.fixture
def pipeline() -> OperationalPipeline:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return OperationalPipeline(session_factory=factory)


def _cand(strategy_id: str) -> SimpleNamespace:
    return SimpleNamespace(strategy_id=strategy_id, estimated_qty=None)


def test_order_qty_uses_atr_widened_stop(pipeline: OperationalPipeline) -> None:
    """ATR 손절이 넓으면 수량이 그만큼 줄어야 한다.

    ath_breakout_v1: 고정 10%, ATR × 2.5.
    price 10,000 / atr 1,000 → ATR 손절 7,500 (손절폭 2,500 = 고정폭 1,000의 2.5배).
    계좌위험 0.5% 기준 수량도 2.5배 작아진다: 500주 → 200주.
    """
    without_atr = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    with_atr = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
        atr14=1_000.0,
    )

    assert without_atr == 500
    assert with_atr == 200


def test_order_qty_ignores_narrow_atr(pipeline: OperationalPipeline) -> None:
    """ATR 손절이 고정%보다 좁으면 수량이 커지지 않는다 (위험 상향 금지)."""
    baseline = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    narrow_atr = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
        atr14=100.0,   # ATR 손절 9,750 → 고정 9,000 보다 좁다
    )

    assert narrow_atr == baseline


def test_sizing_risk_matches_actual_stop_distance(pipeline: OperationalPipeline) -> None:
    """사이징이 가정한 위험액이 실제 손절 시 손실과 일치한다.

    2026-07-27 실거래에서 어긋났던 부분이다. 고정%(10%)로 사이징하고 ATR(20.9%)로
    손절해 실현손실이 의도한 계좌위험의 2배를 넘었다.
    """
    equity, price, atr = 100_000_000.0, 10_000.0, 1_000.0
    qty = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=equity,
        remaining_cash=equity,
        price=price,
        remaining_slots=1,
        atr14=atr,
    )
    stop = effective_stop_price("ath_breakout_v1", price, atr)

    loss_at_stop = qty * (price - stop)
    intended_risk = equity * pipeline._settings.account_risk_per_trade

    assert loss_at_stop <= intended_risk * 1.05
