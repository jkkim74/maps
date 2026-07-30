"""블로그 조회 API 테스트 — 목록·본문·경로 순회 차단."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import maps.api.blog as blog_api
from main import app


@pytest.fixture
def blog_dir(tmp_path, monkeypatch):
    """maps_blog_dir 를 임시 디렉터리로 갈아끼운다."""
    monkeypatch.setattr(blog_api, "_blog_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_returns_newest_first(blog_dir, client) -> None:
    (blog_dir / "2026-07-24.txt").write_text("금요일", encoding="utf-8")
    (blog_dir / "2026-07-27.txt").write_text("월요일", encoding="utf-8")
    (blog_dir / "notes.txt").write_text("무관한 파일", encoding="utf-8")

    data = client.get("/api/v1/blog").json()

    assert [e["date"] for e in data["entries"]] == ["2026-07-27", "2026-07-24"]


def test_list_includes_legacy_markdown_posts(blog_dir, client) -> None:
    """포맷 전환 전 원고(.md)도 목록에 남아야 한다."""
    (blog_dir / "2026-07-24.md").write_text("# 구 원고", encoding="utf-8")
    (blog_dir / "2026-07-27.txt").write_text("신규 원고", encoding="utf-8")

    data = client.get("/api/v1/blog").json()

    assert [e["date"] for e in data["entries"]] == ["2026-07-27", "2026-07-24"]


def test_list_dedupes_when_both_suffixes_exist(blog_dir, client) -> None:
    """같은 날짜에 둘 다 있으면 한 번만 나오고 .txt 가 정본이다."""
    (blog_dir / "2026-07-27.md").write_text("구 원고", encoding="utf-8")
    (blog_dir / "2026-07-27.txt").write_text("신규 원고 — 더 길다", encoding="utf-8")

    data = client.get("/api/v1/blog").json()

    assert [e["date"] for e in data["entries"]] == ["2026-07-27"]
    assert client.get("/api/v1/blog/2026-07-27").json()["content"] == "신규 원고 — 더 길다"


def test_list_is_empty_when_directory_missing(tmp_path, monkeypatch, client) -> None:
    monkeypatch.setattr(blog_api, "_blog_dir", lambda: tmp_path / "nope")
    assert client.get("/api/v1/blog").json()["entries"] == []


def test_get_post_returns_body_verbatim(blog_dir, client) -> None:
    """원고는 붙여넣기용 평문이라 서버가 가공하면 안 된다."""
    body = "─────────────────────────────────\n📌 오늘의 결론\n\n손절 체결."
    (blog_dir / "2026-07-27.txt").write_text(body, encoding="utf-8")

    data = client.get("/api/v1/blog/2026-07-27").json()

    assert data["date"] == "2026-07-27"
    assert data["content"] == body


def test_get_post_falls_back_to_legacy_markdown(blog_dir, client) -> None:
    (blog_dir / "2026-07-27.md").write_text("# 월요일\n\n손절 체결.", encoding="utf-8")

    data = client.get("/api/v1/blog/2026-07-27").json()

    assert "손절 체결." in data["content"]


def test_missing_post_returns_404(blog_dir, client) -> None:
    assert client.get("/api/v1/blog/2026-07-27").status_code == 404


@pytest.mark.parametrize(
    "bad",
    ["..", "../../etc/passwd", "2026-07-27/../../secret", "not-a-date", "2026-13-99"],
)
def test_path_traversal_and_garbage_rejected(blog_dir, client, bad) -> None:
    """날짜로 파싱되지 않는 입력은 파일명 조립 전에 막힌다."""
    res = client.get(f"/api/v1/blog/{bad}")
    assert res.status_code in (400, 404), f"{bad} → {res.status_code}"
    assert "passwd" not in res.text
