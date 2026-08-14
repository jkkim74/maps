"""Static candidate-screen assertions for transparent AI score provenance."""

from __future__ import annotations

from pathlib import Path


def test_candidate_ui_contains_score_source_badges() -> None:
    """The UI distinguishes score sources and makes no AI price claims."""
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "RULE_FALLBACK" in script
    assert "규칙점수" in script
    assert "추천점수" in script
    assert "AI점수" in script
    assert "AI 적정 매수가" not in script


def test_candidate_template_describes_rule_based_price_plans() -> None:
    """The legend states that price plans remain rule based."""
    template = Path("templates/candidates.html").read_text(encoding="utf-8")

    assert "계획 매수가" in template
    assert "규칙 기반" in template
    assert "AI가 제안하는 적정 지정가" not in template


def test_candidates_screen_renders_filter_badge() -> None:
    """개인 필터가 걸리면 화면이 그 사실과 해제 경로를 보여 준다."""
    source = Path("static/js/app.js").read_text(encoding="utf-8")
    assert "candidates-filter-badge" in source
    assert "/settings" in source


def test_filter_badge_fetch_does_not_redirect_to_login() -> None:
    """배지용 개인설정 조회는 401 에 로그인으로 튀면 안 된다.

    `apiFetch` 는 401 에서 `_redirectToLogin()` 을 throw 보다 **먼저** 호출하므로
    `try/catch` 로 막히지 않는다. 인증이 꺼진 배포에서는 `/users/me` 가 401 이라
    후보 화면을 열 때마다 페이지 전체가 `/login` 으로 이동한다.
    """
    source = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "apiFetchQuiet" in source, "부가 조회용 비-리다이렉트 헬퍼가 있어야 한다"
    badge_block = source.split("candidates-filter-badge")[0][-800:]
    assert "apiFetch('/users/me')" not in badge_block


def test_filter_badge_shown_when_filter_empties_the_list() -> None:
    """내 필터로 전량 걸러진 경우에도 원인과 해제 경로를 보여 준다."""
    source = Path("static/js/app.js").read_text(encoding="utf-8")

    empty_index = source.index("'후보 스냅샷 없음'")
    badge_index = source.index("candidates-filter-badge")
    assert badge_index < empty_index, "배지는 빈 목록 early return 보다 먼저 만들어져야 한다"
