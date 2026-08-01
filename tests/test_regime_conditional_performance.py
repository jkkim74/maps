"""국면별 조건부 성과 스크립트의 순수 함수 테스트 (네트워크·DB 불필요)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from maps.backtest.engine import TradeRecord
from scripts.regime_conditional_performance import (
    aggregate_trades,
    labels_from_daily_closes,
    verdicts,
)


def _trade(entry: dt.date, net_pnl: float, entry_price: float = 10_000, qty: int = 10) -> TradeRecord:
    return TradeRecord(
        ticker="T",
        entry_date=entry,
        exit_date=entry + dt.timedelta(days=3),
        entry_price=entry_price,
        exit_price=entry_price + net_pnl / qty,
        qty=qty,
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        exit_reason="signal",
    )


def test_aggregate_splits_by_label_and_computes_stats() -> None:
    d = dt.date(2024, 1, 5)
    tagged = [
        (_trade(d, +50_000), "strong"),
        (_trade(d, +10_000), "strong"),
        (_trade(d, -20_000), "strong"),
        (_trade(d, -5_000), "weak"),
    ]
    stats = aggregate_trades(tagged)

    strong = stats["strong"]
    assert strong["trades"] == 3
    assert strong["win_rate"] == pytest.approx(2 / 3)
    assert strong["net"] == pytest.approx(40_000)
    assert strong["g2p"] == pytest.approx(60_000 / 20_000)
    # 진입금액 10만 원(10,000×10주) 대비 평균 수익률
    assert strong["avg_ret"] == pytest.approx((0.5 + 0.1 - 0.2) / 3)
    assert stats["weak"]["net"] == pytest.approx(-5_000)


def test_aggregate_g2p_zero_when_only_losses() -> None:
    tagged = [(_trade(dt.date(2024, 1, 5), -1_000), "weak")]
    assert aggregate_trades(tagged)["weak"]["g2p"] == pytest.approx(0.0)


def test_labels_uptrend_is_strong_downtrend_is_weak() -> None:
    idx = pd.date_range("2023-01-02", periods=240, freq="B")
    up = pd.Series(range(1000, 1000 + 240), index=idx, dtype=float)
    down = pd.Series(range(2000, 2000 - 240, -1), index=idx, dtype=float)

    up_labels = labels_from_daily_closes(up)
    down_labels = labels_from_daily_closes(down)

    # MA 워밍업 이후 후반부 라벨로 판정
    assert up_labels[max(up_labels)] == "strong"
    assert down_labels[max(down_labels)] == "weak"
    # 미래 참조 방지: 초기 10주 구간은 라벨이 없어야 한다
    assert min(up_labels) > idx[0].date()


def test_verdicts_flags_declared_loss_and_undeclared_profit() -> None:
    stats = {
        "strong": {"trades": 10, "win_rate": 0.4, "avg_ret": -0.01, "g2p": 0.5, "net": -100.0},
        "weak": {"trades": 10, "win_rate": 0.6, "avg_ret": 0.02, "g2p": 1.5, "net": 500.0},
        "mixed": {"trades": 2, "win_rate": 1.0, "avg_ret": 0.05, "g2p": 9.9, "net": 900.0},
    }
    notes = verdicts(stats, preferred={"strong"}, min_trades=5)

    assert any("선호 장세 strong" in n for n in notes)
    assert any("선언 밖 weak" in n for n in notes)
    # mixed는 min_trades 미달이라 판정 제외
    assert not any("mixed" in n for n in notes)
