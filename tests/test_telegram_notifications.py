"""텔레그램 알림 + 무장/무장해제 웹훅 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick


# ── 테스트 더블 ────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeHttp:
    """requests 대체 — post 호출을 기록한다."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResp(self.status_code)


class _RecordingNotifier:
    """TelegramNotifier 인터페이스 중 테스트에 필요한 부분만 기록한다."""

    def __init__(self) -> None:
        self.picks_calls: list[dict] = []
        self.answers: list[tuple[str, str]] = []
        self.edits: list[dict] = []

    def send_analysis_picks(self, picks, **kwargs):
        self.picks_calls.append({"picks": picks, **kwargs})
        return True

    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        self.answers.append((callback_query_id, text))
        return True

    def edit_message_text(self, **kwargs) -> bool:
        self.edits.append(kwargs)
        return True


def _settings_with(monkeypatch, **env):
    """TELEGRAM_* env를 설정하고 settings를 리로드해 반환한다."""
    from maps.common.settings import get_settings, reload_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reload_settings()
    return get_settings()


# ── TelegramNotifier ──────────────────────────────────────────────────────────
def test_notifier_disabled_when_token_empty(monkeypatch) -> None:
    from maps.ops.notifications import TelegramNotifier

    settings = _settings_with(monkeypatch, TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    http = _FakeHttp()
    tg = TelegramNotifier(settings=settings, http=http)
    assert tg.enabled is False
    assert tg.send_message("hi") is False
    assert tg.send_analysis_picks([{"id": 1, "ticker": "005930"}]) is False
    assert http.calls == []  # 어떤 HTTP도 발생하지 않음


def test_notifier_send_analysis_picks_payload(monkeypatch) -> None:
    from maps.ops.notifications import TelegramNotifier

    settings = _settings_with(
        monkeypatch, TELEGRAM_BOT_TOKEN="t0ken", TELEGRAM_CHAT_ID="999",
        MAPS_TELEGRAM_ALLOW_NONPROD="true",
    )
    http = _FakeHttp()
    tg = TelegramNotifier(settings=settings, http=http)
    assert tg.enabled is True

    picks = [{"id": 7, "ticker": "005930", "name": "삼성전자",
              "buy_price": 70000, "target_price": 80000, "stop_price": 66000}]
    assert tg.send_analysis_picks(picks, regime="mixed", ref_date="2026-06-29") is True

    # 헤더 1건 + 종목 1건 = 2회 sendMessage
    assert len(http.calls) == 2
    assert all("/sendMessage" in c["url"] and "t0ken" in c["url"] for c in http.calls)
    # 종목 메시지에 arm/disarm 버튼이 콜백 데이터로 직렬화됐는지
    buttons = http.calls[1]["json"]["reply_markup"]["inline_keyboard"][0]
    cb = {b["callback_data"] for b in buttons}
    assert cb == {"arm:7", "disarm:7"}


def test_notifier_http_failure_is_swallowed(monkeypatch) -> None:
    from maps.ops.notifications import TelegramNotifier

    settings = _settings_with(
        monkeypatch, TELEGRAM_BOT_TOKEN="t0ken", TELEGRAM_CHAT_ID="999",
        MAPS_TELEGRAM_ALLOW_NONPROD="true",
    )

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("network down")

    tg = TelegramNotifier(settings=settings, http=_Boom())
    assert tg.send_message("hi") is False  # 예외를 삼키고 False


def test_nonprod_blocks_real_send_even_with_token(monkeypatch) -> None:
    """비운영 환경에서는 운영 봇 토큰이 있어도 실발송을 막는다(더미 픽 유출 방지)."""
    from maps.ops.notifications import TelegramNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="development",
        TELEGRAM_BOT_TOKEN="t0ken", TELEGRAM_CHAT_ID="999",
    )
    http = _FakeHttp()
    tg = TelegramNotifier(settings=settings, http=http)
    assert tg.enabled is False
    # enabled를 우회하는 경로(명시적 chat_id)까지 _call에서 차단되는지
    assert tg.send_message("hi", chat_id="999") is False
    assert tg.send_analysis_picks(
        [{"id": 1, "ticker": "005930", "name": "삼성전자",
          "buy_price": 70000, "target_price": 80000, "stop_price": 66000}]
    ) is False
    assert tg.answer_callback("cb1", "x") is False
    assert http.calls == []  # 어떤 HTTP도 발생하지 않음


def test_production_allows_real_send(monkeypatch) -> None:
    """운영 환경에서는 별도 플래그 없이 정상 발송한다."""
    from maps.ops.notifications import TelegramNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="production",
        TELEGRAM_BOT_TOKEN="t0ken", TELEGRAM_CHAT_ID="999",
    )
    http = _FakeHttp()
    tg = TelegramNotifier(settings=settings, http=http)
    assert tg.enabled is True
    assert tg.send_message("hi") is True
    assert len(http.calls) == 1


# ── 웹훅 라우터 ────────────────────────────────────────────────────────────────
@pytest.fixture
def webhook_client(monkeypatch):
    """TELEGRAM secret/chat을 설정하고(토큰은 비워 HTTP no-op) TestClient를 만든다."""
    _settings_with(
        monkeypatch,
        TELEGRAM_BOT_TOKEN="",            # 토큰 없음 → answer/edit는 조용히 no-op
        TELEGRAM_CHAT_ID="555",
        TELEGRAM_WEBHOOK_SECRET="s3cret",
    )
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    client.session_factory = factory
    yield client

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_pick(factory, **overrides) -> int:
    # 기준일은 today 상대값이어야 한다 — 고정 날짜면 만료 가드가 들어온 뒤 시간이
    # 흐르면서 무장 테스트가 조용히 409 를 받게 된다.
    base = dict(
        ref_date=dt.date.today(), ticker="005930", name="삼성전자", market="KOSPI",
        source="analyze", buy_price=70000, target_price=80000, stop_price=66000, state="WATCH",
    )
    base.update(overrides)
    with factory() as s:
        pick = AnalysisPick(**base)
        s.add(pick)
        s.commit()
        s.refresh(pick)
        return pick.id


def _callback(data: str, *, chat_id="555", cb_id="cb1") -> dict:
    return {"callback_query": {
        "id": cb_id,
        "data": data,
        "message": {"message_id": 42, "chat": {"id": chat_id}},
    }}


def test_webhook_rejects_bad_secret(webhook_client) -> None:
    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback("arm:1"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 403


def test_webhook_ignores_unknown_chat(webhook_client) -> None:
    pid = _seed_pick(webhook_client.session_factory)
    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback(f"arm:{pid}", chat_id="000"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    with webhook_client.session_factory() as s:
        assert s.get(AnalysisPick, pid).state == "WATCH"  # 변경 없음


def test_webhook_arm_changes_state(webhook_client) -> None:
    pid = _seed_pick(webhook_client.session_factory)
    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback(f"arm:{pid}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    with webhook_client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        assert pick.state == "ARMED"
        assert pick.strategy_trade_enabled is True


def test_webhook_disarm_changes_state(webhook_client) -> None:
    pid = _seed_pick(
        webhook_client.session_factory, state="ARMED", strategy_trade_enabled=True
    )
    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback(f"disarm:{pid}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    with webhook_client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        assert pick.state == "WATCH"
        assert pick.strategy_trade_enabled is False


def test_webhook_disarm_bought_is_rejected_gracefully(webhook_client, monkeypatch) -> None:
    pid = _seed_pick(
        webhook_client.session_factory, state="BOUGHT", strategy_trade_enabled=True
    )
    # BOUGHT는 disarm 거부 — 콜백 응답 사유를 잡아낸다.
    rec = _RecordingNotifier()
    monkeypatch.setattr("maps.api.telegram.get_telegram_notifier", lambda: rec)

    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback(f"disarm:{pid}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    with webhook_client.session_factory() as s:
        assert s.get(AnalysisPick, pid).state == "BOUGHT"  # 그대로
    assert rec.answers and rec.answers[0][1].startswith("실패")


def test_webhook_unknown_action(webhook_client, monkeypatch) -> None:
    rec = _RecordingNotifier()
    monkeypatch.setattr("maps.api.telegram.get_telegram_notifier", lambda: rec)
    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback("bogus:1"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    assert rec.answers and "알 수 없는" in rec.answers[0][1]


# ── load_picks 트리거 ──────────────────────────────────────────────────────────
@pytest.fixture
def picks_session_factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _plan():
    return [{"ticker": "005930", "name": "삼성전자", "market": "KOSPI",
             "entry": 70000, "target": 80000, "stop_loss": 66000}]


def test_load_picks_triggers_telegram(picks_session_factory) -> None:
    from scripts.load_analysis_picks import load_picks

    rec = _RecordingNotifier()
    result = load_picks(
        _plan(), regime="mixed", context="ctx", source="analyze",
        ref_date=dt.date(2026, 6, 29), dry_run=False,
        session_factory=picks_session_factory, notifier=rec,
    )
    assert len(result) == 1
    assert len(rec.picks_calls) == 1
    sent = rec.picks_calls[0]["picks"]
    assert sent[0]["ticker"] == "005930" and "id" in sent[0]


def test_load_picks_no_telegram_flag(picks_session_factory) -> None:
    from scripts.load_analysis_picks import load_picks

    rec = _RecordingNotifier()
    load_picks(
        _plan(), regime="mixed", context="ctx", source="analyze",
        ref_date=dt.date(2026, 6, 29), dry_run=False,
        session_factory=picks_session_factory, notifier=rec, notify_telegram=False,
    )
    assert rec.picks_calls == []  # 전송 안 함


def test_load_picks_failed_status_skips_telegram(picks_session_factory) -> None:
    from scripts.load_analysis_picks import load_picks

    rec = _RecordingNotifier()
    load_picks(
        [], regime=None, context=None, source="analyze",
        ref_date=dt.date(2026, 6, 29), dry_run=False, status="failed",
        session_factory=picks_session_factory, notifier=rec,
    )
    assert rec.picks_calls == []  # 실패 기록은 알림 없음


def test_webhook_arm_rejects_stale_pick(webhook_client, monkeypatch) -> None:
    """옛 푸시 메시지의 무장 버튼은 영구히 살아 있다 — 서버 거부가 유일한 방어선이다.

    2026-07-30: 한 달 전 기준일 픽이 무장되자 17초 만에 진입 주문이 나갔다.
    """
    from maps.market.trading_rules import trading_days_ago

    pid = _seed_pick(
        webhook_client.session_factory, ref_date=trading_days_ago(dt.date.today(), 30)
    )
    rec = _RecordingNotifier()
    monkeypatch.setattr("maps.api.telegram.get_telegram_notifier", lambda: rec)

    r = webhook_client.post(
        "/api/telegram/webhook",
        json=_callback(f"arm:{pid}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    with webhook_client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        assert pick.state == "WATCH"                  # 무장되지 않았다
        assert pick.strategy_trade_enabled is False
    assert rec.answers and "만료" in rec.answers[0][1]
