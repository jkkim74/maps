"""전략 설명 카탈로그 테스트.

전략관리 화면이 `strategy_id` 만 보여주던 원인은 설명이 어디에도 없었다는 것이다.
새 전략을 추가하면서 설명을 빠뜨리면 같은 상태로 되돌아가므로, 여기서 막는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.db import Base
from maps.strategy.catalog import (
    STRATEGY_CLASSES,
    STRATEGY_PROSE,
    describe_strategy,
    display_name,
)
from maps.strategy.live_rules import atr_multiplier, stop_loss_pct

_GUIDE_DIR = Path(__file__).resolve().parents[1] / "docs" / "strategy_guides"


# ── 커버리지 ─────────────────────────────────────────────────────────────────


def test_every_registered_strategy_has_prose():
    """`STRATEGY_GROUP_MAP` 의 모든 전략에 설명이 있어야 한다.

    실패하면 새 전략을 추가하고 `STRATEGY_PROSE` 등록을 잊은 것이다.
    화면에 식별자만 덩그러니 뜨게 되므로 여기서 막는다.
    """
    missing = sorted(set(STRATEGY_GROUP_MAP) - set(STRATEGY_PROSE))
    assert not missing, f"전략 설명 누락: {missing}"


def test_every_registered_strategy_has_class():
    """숫자를 읽어올 전략 클래스도 함께 등록돼야 한다."""
    missing = sorted(set(STRATEGY_GROUP_MAP) - set(STRATEGY_CLASSES))
    assert not missing, f"전략 클래스 매핑 누락: {missing}"


def test_prose_has_no_orphans():
    """반대로 존재하지 않는 전략 설명이 남아 있으면 안 된다."""
    orphans = sorted(set(STRATEGY_PROSE) - set(STRATEGY_GROUP_MAP))
    assert not orphans, f"매핑에 없는 전략 설명: {orphans}"


@pytest.mark.parametrize("strategy_id", sorted(STRATEGY_PROSE))
def test_guide_file_exists(strategy_id: str):
    """설명이 가리키는 원고 파일이 실제로 있어야 한다."""
    prose = STRATEGY_PROSE[strategy_id]
    assert (_GUIDE_DIR / prose.guide_file).is_file(), prose.guide_file


@pytest.mark.parametrize("strategy_id", sorted(STRATEGY_PROSE))
def test_prose_fields_are_filled(strategy_id: str):
    prose = STRATEGY_PROSE[strategy_id]
    assert prose.display_name.strip()
    assert prose.summary.strip()
    assert prose.idea.strip()
    assert len(prose.entry_rules) >= 1
    assert len(prose.exit_rules) >= 1


# ── 숫자는 코드에서 온다 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("strategy_id", sorted(STRATEGY_PROSE))
def test_numbers_come_from_code_not_catalog(strategy_id: str):
    """손절률·ATR 배수는 live_rules 값과 항상 같아야 한다.

    카탈로그에 숫자를 복사해 두면 전략을 손볼 때 설명만 조용히 어긋난다.
    """
    described = describe_strategy(strategy_id)
    assert described is not None
    assert described.stop_loss_pct == stop_loss_pct(strategy_id)
    assert described.atr_multiplier == atr_multiplier(strategy_id)


def test_preferred_regimes_match_strategy_class():
    described = describe_strategy("ath_breakout_v1")
    assert described.preferred_regimes == ("strong",)

    described = describe_strategy("pullback_v3")
    assert described.preferred_regimes == ("mixed", "strong")


def test_default_params_match_strategy_class():
    described = describe_strategy("donchian_v1")
    assert described.default_params == {"high_period": 40, "low_period": 15}


def test_mdd_limit_matches_group_allowance():
    # pullback_short 그룹 한도 18%
    assert describe_strategy("pullback_v3").mdd_limit == pytest.approx(0.18)
    # ath_outlier 그룹 한도 35%
    assert describe_strategy("ath_breakout_v1").mdd_limit == pytest.approx(0.35)


def test_unknown_strategy_is_none():
    assert describe_strategy("no_such_strategy") is None


def test_display_name_falls_back_to_id():
    assert display_name("no_such_strategy") == "no_such_strategy"
    assert display_name("donchian_v2") == "돈치안 채널 V2"


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    """전략 API 테스트용 클라이언트.

    `get_db` 를 인메모리 SQLite 로 오버라이드한다. 오버라이드하지 않으면 개발용
    `maps.db` 를 직접 쳐서, 그 파일이 없는 새 클론·새 worktree 의 첫 실행에서만
    `no such table: promotion_history` 로 실패한다(그 뒤로는 파일이 생겨 스스로 숨는다).
    """
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
    yield TestClient(app)

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_guide_endpoint_returns_prose_and_text(client: TestClient):
    res = client.get("/api/v1/strategies/guide/pullback_v3")
    assert res.status_code == 200

    body = res.json()
    assert body["display_name"] == "눌림목 매수 V3"
    assert body["stop_loss_pct"] == pytest.approx(0.05)
    assert body["preferred_regimes"] == ["mixed", "strong"]
    assert len(body["entry_rules"]) == 4
    # 원고 전문이 그대로 실려야 네이버 붙여넣기용 복사가 가능하다.
    assert body["guide_text"] and "눌림목" in body["guide_text"]


def test_guide_endpoint_404_for_unknown(client: TestClient):
    assert client.get("/api/v1/strategies/guide/no_such_strategy").status_code == 404


def test_strategy_list_exposes_display_name(client: TestClient):
    res = client.get("/api/v1/strategies")
    assert res.status_code == 200

    by_id = {s["strategy_id"]: s for s in res.json()["strategies"]}
    assert by_id["donchian_v2"]["display_name"] == "돈치안 채널 V2"
    assert by_id["donchian_v2"]["has_guide"] is True
    assert by_id["donchian_v2"]["summary"]
