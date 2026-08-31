"""상한가 운영 알림 계약 — 원장 훅이 조용해야 할 때 조용한지 검사한다."""

from __future__ import annotations

import datetime as dt

from maps.limit_up import notify
from maps.limit_up.domain import LimitUpState
from maps.limit_up.repository import LimitUpRepository


def _capture(monkeypatch) -> list[str]:
    """`push` 를 가로채 발송될 문자열만 모은다(스레드·HTTP 없음)."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "push", sent.append)
    return sent


def _session(db):
    """감시 상태의 세션 하나."""
    return LimitUpRepository(db).create_or_get_session(
        ref_date=dt.date(2026, 8, 31),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        execution_mode="recommend_only",
    )


def test_only_whitelisted_ledger_actions_alert(db, monkeypatch) -> None:
    """원장은 알림보다 훨씬 촘촘하다 — 등록하지 않은 action 은 조용해야 한다."""
    sent = _capture(monkeypatch)
    repo = LimitUpRepository(db)
    session = _session(db)

    repo.append_event(
        session, action="submit_buy", state_version=1, leg="S",
        payload={"price": 100_000, "quantity": 10},
    )

    assert sent == []


def test_trigger_alert_carries_the_recommended_grid(db, monkeypatch) -> None:
    """추천 모드의 알림은 종목만으로 쓸모가 없다 — 가격·수량이 있어야 한다."""
    sent = _capture(monkeypatch)
    repo = LimitUpRepository(db)
    session = _session(db)

    repo.transition(
        session,
        state=LimitUpState.NET_OPEN,
        action="fire_net",
        payload={
            "turnover": 50_000_000_000,
            "grid": [{"name": "S", "price": 100_000, "quantity": 12}],
        },
    )

    assert len(sent) == 1
    assert "005930" in sent[0]
    assert "S 12주 @ 100,000원" in sent[0]


def test_repeated_intent_does_not_alert_twice(db, monkeypatch) -> None:
    """멱등 원장이 알림 중복도 막는다 — 재시도가 텔레그램을 도배하면 안 된다."""
    sent = _capture(monkeypatch)
    repo = LimitUpRepository(db)
    session = _session(db)

    for _ in range(3):
        repo.append_event(
            session, action="market_sell", state_version=2,
            payload={"quantity": 12, "reason": "hard_stop"},
        )

    assert len(sent) == 1
    assert "hard_stop" in sent[0]


def test_alert_failure_never_reaches_the_engine(monkeypatch) -> None:
    """텔레그램이 죽었다고 매매가 멈추면 안 된다 — 발송 실패는 삼킨다."""

    class Boom:
        def send_message(self, text: str) -> bool:
            raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "get_telegram_notifier", Boom)

    notify._send("아무 메시지")  # 예외가 새면 여기서 실패한다
