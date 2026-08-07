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
