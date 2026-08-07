"""Ops config API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

import main
from maps.api import ops_config
from maps.common.settings import get_settings


def test_ops_config_endpoint_returns_masked_sections() -> None:
    client = TestClient(main.app)

    response = client.get("/api/v1/ops/config")

    assert response.status_code == 200
    data = response.json()
    assert "sections" in data
    assert any(section["key"] == "kis" for section in data["sections"])
    assert "missing_required" in data
    assert data["ai_scoring_mode"] == "off"


def test_ai_scoring_mode_can_be_changed_and_persisted(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=value\nMAPS_AI_SCORING_MODE=off\n", encoding="utf-8")
    monkeypatch.setattr(ops_config, "_ENV_FILE", env_file)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/ops/config/ai-scoring-mode",
        json={"mode": "rerank"},
    )

    assert response.status_code == 200
    assert response.json() == {"mode": "rerank", "previous_mode": "off"}
    assert env_file.read_text(encoding="utf-8") == (
        "EXISTING=value\nMAPS_AI_SCORING_MODE=rerank\n"
    )
    assert get_settings().maps_ai_scoring_mode == "rerank"


def test_ai_scoring_mode_rejects_unknown_value(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(ops_config, "_ENV_FILE", env_file)

    response = TestClient(main.app).post(
        "/api/v1/ops/config/ai-scoring-mode",
        json={"mode": "unknown"},
    )

    assert response.status_code == 422
    assert not env_file.exists()
