"""Ops config API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

import main


def test_ops_config_endpoint_returns_masked_sections() -> None:
    client = TestClient(main.app)

    response = client.get("/api/v1/ops/config")

    assert response.status_code == 200
    data = response.json()
    assert "sections" in data
    assert any(section["key"] == "kis" for section in data["sections"])
    assert "missing_required" in data
