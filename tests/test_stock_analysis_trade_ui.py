"""Static contracts for the approved analysis-to-strategy web flow."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "templates" / "_stock_analysis_panel.html"
STOCK_PAGE = ROOT / "templates" / "stock_analysis.html"
WATCHLIST = ROOT / "templates" / "analysis_picks.html"
SCRIPT = ROOT / "static" / "js" / "stock-analysis.js"
STYLE = ROOT / "static" / "css" / "stock-analysis.css"


def test_analysis_result_exposes_trade_setup_and_safe_api_flow() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'id="sa-trade-setup"' in panel
    assert 'id="sa-open-trade"' in panel
    assert 'id="sa-analysis-trade-plan"' in panel
    assert "openTradeSetup" in script
    assert "_lastAnalysisTradePlan = d.trade_plan" in script
    assert "_renderAnalysisTradePlan" in script
    assert "_applyAnalysisTradePlan" in script
    assert "/api/v1/stock-analysis/trade-plan" not in script
    assert "/api/v1/analysis-picks/trade-preview" in script
    assert "/api/v1/analysis-picks/arm-plan" in script
    assert "if (!preview.blocked)" in script
    assert "MANUAL_REQUIRED" in script


def test_trade_setup_requires_mode_and_displays_all_safe_limits() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'name="sa-trade-mode"' in panel
    assert 'value="single"' in panel
    assert 'value="split"' in panel
    assert "broker_cash" in script
    assert "single_exposure" in script
    assert "portfolio_capacity" in script
    assert "stop_risk" in script
    assert "safe_max_amount" in script
    assert "expected_remaining_cash" in script
    assert "planned_qty" in script


def test_watchlist_renders_split_progress_detail_and_stop_entries() -> None:
    html = WATCHLIST.read_text(encoding="utf-8")

    assert "filled_legs" in html
    assert "total_legs" in html
    assert "next_entry_price" in html
    assert "planned_qty" in html
    assert "remaining_qty" in html
    assert "stop-entries" in html
    assert "stopSplitEntries" in html


def test_trade_setup_has_responsive_styles() -> None:
    css = STYLE.read_text(encoding="utf-8")

    assert ".sa-trade-dialog" in css
    assert ".sa-trade-limits" in css
    assert "background:var(--bg-base)" in css
    assert "background:var(--bg);" not in css
    assert "@media" in css


def test_stock_analysis_page_exposes_persistent_history() -> None:
    """독립 종목분석 화면만 저장 이력 목록과 복원 동작을 제공한다."""
    page = STOCK_PAGE.read_text(encoding="utf-8")
    watchlist = WATCHLIST.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'id="sa-history-body"' in page
    assert 'id="sa-history-status"' in page
    assert 'id="sa-history-body"' not in watchlist
    assert "/api/v1/stock-analysis/history" in script
    assert "loadAnalysisHistory" in script
    assert "openAnalysisHistory" in script
    assert "refresh-price" in script
    assert "reanalyzeHistory" in script
    assert 'id="r-price-updated"' in PANEL.read_text(encoding="utf-8")


def test_split_entries_have_a_dedicated_stable_row() -> None:
    """숨김 분할 필드가 공통 입력의 그리드 배치를 밀지 않는다."""
    panel = PANEL.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    assert 'class="sa-trade-common-fields"' in panel
    assert 'class="sa-trade-entry-fields"' in panel
    assert ".sa-trade-entry-fields" in css
    assert "repeat(3,minmax(0,1fr))" in css


def test_mode_selection_calculates_and_applies_safe_budget() -> None:
    """매매 방식 선택 후 서버 한도를 먼저 구해 예산과 preview를 자동 설정한다."""
    panel = PANEL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert "/api/v1/analysis-picks/trade-limits" in script
    assert "_refreshTradeLimits" in script
    assert ".max =" in script
    assert "safe_max_amount" in script
    assert "minimum_orderable_amount" in script
    assert "안전한도 계산" not in panel
    assert "_lastAnalysis['시장']" in script
