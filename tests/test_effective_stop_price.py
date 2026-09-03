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
from maps.backtest.engine import _ATR_STOP_MULTIPLIER, backtest_stop_price
from maps.market.trading_rules import krx_tick_size, round_down_krx_price
from maps.ops.scheduler import OperationalPipeline
from maps.strategy.live_rules import (
    atr_stop_price,
    effective_stop_price,
    max_stop_price,
    stop_loss_price,
    stop_price_breakdown,
)


# ── 정본 규칙 ────────────────────────────────────────────────────────────────


def test_atr_wider_than_fixed_wins():
    """ATR 손절이 더 넓으면(가격이 낮으면) ATR 을 쓴다 — 잔진동 조기 손절 방지."""
    # donchian_v2: 고정 10%, ATR × 2.0, 손절폭 상한 20%
    # 원래 이 테스트는 atr=8,316(손절폭 20.9%)이었으나 이제 상한에 걸린다.
    # 상한 케이스는 test_atr_stop_is_capped_at_twice_the_fixed_width 가 덮으므로
    # 여기서는 "고정%보다 넓고 상한 이내" 구간을 고정한다.
    entry, atr = 79_500.0, 6_000.0
    fixed = stop_loss_price("donchian_v2", entry)      # 71,550
    atr_stop = atr_stop_price("donchian_v2", entry, atr)  # 67,500

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
        # pullback_v3 는 고정 5% → 상한 10%. ATR 손절 32,487(-11.2%)이
        # 상한 32,940 에 걸려 호가 내림 후 32,900 이 된다.
        ("pullback_v3", 36_600.0, 2_056.5, 32_900),
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
                # 비교 기준은 **상한을 적용한 뒤**의 값이다. 상한은 정렬이 아니라
                # 규칙이므로 "정렬이 조이지 않는다" 불변식과 별개다.
                cap = max_stop_price(strategy_id, entry)
                if cap is not None and cap > raw:
                    raw = cap
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

    ath_breakout_v1: 고정 10%, ATR × 2.5, 손절폭 상한 20%.
    price 10,000 / atr 1,000 → ATR 손절 7,500 이지만 상한 8,000 에 걸린다.
    손절폭 2,000 = 고정폭 1,000의 2배이므로 수량도 2배 작아진다: 500주 → 250주.
    상한이 없던 때는 200주였다 — 상한은 손절폭뿐 아니라 사이징도 함께 움직인다.
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
    assert with_atr == 250


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


def test_growing_atr_would_breach_the_risk_budget_if_recomputed(
    pipeline: OperationalPipeline,
) -> None:
    """보유 중 ATR 이 커지면 손절폭만 넓어져 계좌 위험이 예산을 넘는다.

    사이징은 진입 시 한 번뿐인데 손절가를 매일 재계산했기 때문이다
    (2026-07-31 운영: 089860 이 의도 0.50% → 실제 0.55%).
    그래서 진입 시점 ATR 을 order_log 에 기록해 청산·화면이 재사용한다.
    이 테스트는 **왜 고정해야 하는지**를 숫자로 고정한다.
    """
    # 손절폭 상한(20%)에 걸리면 ATR 이 커져도 손절가가 더는 안 움직인다.
    # 이 테스트가 고정하려는 것은 그 구간이 아니라 "상한 이내에서 재계산하면
    # 예산을 넘는다"이므로, 확대 후에도 상한 안에 남는 ATR 을 쓴다.
    equity, price, atr = 100_000_000.0, 10_000.0, 600.0
    qty = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=equity,
        remaining_cash=equity,
        price=price,
        remaining_slots=1,
        atr14=atr,
    )
    intended_risk = equity * pipeline._settings.account_risk_per_trade

    frozen = effective_stop_price("ath_breakout_v1", price, atr)        # 8,500
    grown = effective_stop_price("ath_breakout_v1", price, atr * 1.2)   # 8,200

    assert qty * (price - frozen) <= intended_risk * 1.05   # 고정하면 예산 안
    assert qty * (price - grown) > intended_risk            # 재계산하면 예산 초과


# ── 손절폭 상한 ──────────────────────────────────────────────────────────────


def test_atr_stop_is_capped_at_twice_the_fixed_width():
    """ATR 손절폭이 고정%의 2배를 넘으면 상한에서 멈춘다.

    2026-08-31 189330 씨이랩 실측: ATR14 가 주가의 10.1% 라 2.5배가 그대로
    손절폭 25.2% 가 됐고 −26.7% 에 청산됐다. 원화 위험은 사이징이 지켰지만
    (50만원), 손절폭이 넓을수록 복구에 필요한 상승률이 비선형으로 커진다.
    고정%는 "최소 이만큼 넓게"(하한), 상한은 "이보다 넓히지 마라"다.
    """
    entry, atr = 16_720.0, 1_681.8  # ath_breakout_v1: 고정 10%, ATR × 2.5

    uncapped = atr_stop_price("ath_breakout_v1", entry, atr)
    assert uncapped == pytest.approx(12_515.5)          # −25.2%, 상한 밖

    stop = effective_stop_price("ath_breakout_v1", entry, atr)
    assert stop == pytest.approx(13_370)                 # 상한 13,376 → 호가 내림
    assert stop > uncapped
    assert (entry - stop) / entry < 0.205                # 손절폭 20% + 1틱 이내


def test_atr_stop_within_the_cap_is_untouched():
    """상한 이내면 ATR 손절이 그대로 남는다 — 상한은 극단만 자른다.

    2026-08-27 419080 엔젯 실측: ATR14 가 주가의 7.0% 라 손절폭 −14.0% 였고
    상한(−20%) 안이므로 규칙이 바뀌지 않는다.
    """
    entry, atr = 6_405.0, 447.09  # donchian_v2: 고정 10%, ATR × 2.0

    assert effective_stop_price("donchian_v2", entry, atr) == pytest.approx(5_510)


def test_cap_never_tightens_past_the_fixed_stop():
    """상한이 고정% 손절보다 좁아지는 일은 없다 — 상한은 고정%의 2배다."""
    for strategy_id in ("pullback_v3", "donchian_v2", "ath_breakout_v1"):
        for entry in (3_140.0, 8_180.0, 36_600.0, 620_000.0):
            fixed = stop_loss_price(strategy_id, entry)
            for atr in (0.0, 55.5, 429.5, 2_056.5, 20_000.0):
                stop = effective_stop_price(strategy_id, entry, atr)
                if stop is None:
                    continue
                assert stop <= fixed


def test_unknown_strategy_has_no_cap_and_still_returns_none():
    """미등록 전략은 상한도 없다 — 기존 ``None`` 반환이 유지된다."""
    assert max_stop_price("nope_v1", 10_000.0) is None
    assert effective_stop_price("nope_v1", 10_000.0, 500.0) is None


def test_breakdown_reports_which_rule_won():
    """어느 규칙이 손절가를 정했는지 기록할 수 있어야 한다.

    2026-09-03 조사에서 손절 3건의 근거를 OHLCV 와 ATR 로 전부 역산해야 했다.
    """
    capped = stop_price_breakdown("ath_breakout_v1", 16_720.0, 1_681.8)
    assert capped["rule"] == "capped"
    assert capped["stop_price"] == pytest.approx(13_370)
    assert capped["atr14"] == pytest.approx(1_681.8)

    atr_won = stop_price_breakdown("donchian_v2", 6_405.0, 447.09)
    assert atr_won["rule"] == "atr"
    assert atr_won["stop_price"] == pytest.approx(5_510)

    fixed_won = stop_price_breakdown("donchian_v2", 79_500.0, 1_000.0)
    assert fixed_won["rule"] == "fixed"

    assert stop_price_breakdown("nope_v1", 10_000.0, 500.0)["rule"] is None


# ── 백테스트가 실거래와 같은 규칙을 쓰는지 ────────────────────────────────────


def test_backtest_uses_the_per_strategy_atr_multiplier():
    """백테스트도 전략별 ATR 배수를 써야 한다.

    `engine.py` 는 전략 무관 고정 2.0 배였다. 실거래 `ath_breakout_v1` 은 2.5 배라
    **2.0 으로 검증한 전략을 2.5 로 실매매**하고 있었다 — 승격 심사(WFA·플라토·MC)가
    실제보다 좁은 손절을 가정한 셈이다(2026-09-03 확인).
    """
    # ath_breakout_v1: 고정 10%, ATR × 2.5 → 8,750. 옛 고정 2.0 배였다면 9,000.
    assert backtest_stop_price("ath_breakout_v1", 10_000.0, atr14=500.0) == pytest.approx(8_750)


def test_backtest_falls_back_to_the_flat_multiplier_for_unknown_strategies():
    """미등록 전략은 기존 고정 배수로 폴백한다 — 백테스트는 임의 전략도 돌린다."""
    stop = backtest_stop_price("unknown_v9", 10_000.0, atr14=500.0)
    assert stop == pytest.approx(10_000.0 - _ATR_STOP_MULTIPLIER * 500.0)


def test_backtest_respects_the_same_stop_width_cap():
    """백테스트 손절도 상한을 넘지 않는다 — 리플레이만 빠지면 다시 갈라진다."""
    # 씨이랩 실수치. 상한이 없으면 12,515.5
    assert backtest_stop_price("ath_breakout_v1", 16_720.0, atr14=1_681.8) == pytest.approx(13_376)


def test_backtest_prefers_the_strategy_signal_stop_when_it_is_wider():
    """전략이 신호와 함께 낸 손절가는 백테스트에만 있는 입력이다 — 계속 우선한다."""
    stop = backtest_stop_price(
        "donchian_v2", 10_000.0, stop_from_signal=9_200.0, atr14=100.0
    )
    assert stop == pytest.approx(9_200)   # ATR 손절 9,800 보다 넓다


def test_portfolio_replay_stop_respects_the_cap():
    """포트폴리오 리플레이도 같은 상한을 쓴다.

    배수는 이미 전략별이었지만 상한이 없었다. 리플레이만 빠지면 20% 재측정
    결과가 실거래와 다시 갈라진다.
    """
    from maps.backtest.portfolio_replay import PortfolioReplayEngine

    prepared = SimpleNamespace(stop_src={}, atr_src={"d": 1_681.8})
    replay = object.__new__(PortfolioReplayEngine)

    stop = replay._resolve_stop("ath_breakout_v1", 16_720.0, prepared, "d")

    assert stop == pytest.approx(13_376)   # 상한이 없으면 12,515.5


def test_analysis_pick_stop_cap_is_a_flat_percentage():
    """분석 픽에는 전략 ID 가 없다 — 진입가 기준 고정 비율이 상한이다."""
    from maps.strategy.live_rules import (
        ANALYSIS_PICK_MAX_STOP_WIDTH_PCT,
        analysis_pick_max_stop_price,
    )

    assert ANALYSIS_PICK_MAX_STOP_WIDTH_PCT == 0.20
    assert analysis_pick_max_stop_price(10_000.0) == pytest.approx(8_000)


def test_analysis_pick_stop_cap_is_none_for_unusable_entry():
    """진입가가 없거나 0 이하면 상한도 없다 — 다른 검증이 먼저 막는다."""
    from maps.strategy.live_rules import analysis_pick_max_stop_price

    assert analysis_pick_max_stop_price(None) is None
    assert analysis_pick_max_stop_price(0.0) is None
    assert analysis_pick_max_stop_price(float("inf")) is None
