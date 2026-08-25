from __future__ import annotations

from types import SimpleNamespace

import pytest

from maps.common.models import StockReportRun
from maps.stock_report import runner


def test_run_all_reports_if_idle_skips_when_report_is_running(db, monkeypatch) -> None:
    db.add(StockReportRun(report_type="premium", status="running"))
    db.commit()

    monkeypatch.setattr(
        runner,
        "run_all_reports",
        lambda _db: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert runner.run_all_reports_if_idle(db) == []


def test_run_all_reports_if_idle_runs_all_reports(db, monkeypatch) -> None:
    monkeypatch.setattr(runner, "run_all_reports", lambda _db: [1, 2, 3, 4])

    assert runner.run_all_reports_if_idle(db) == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("kospi_ticker", "row_count", "latest_age_days", "max_gap_days"),
    [
        ("^KS200", 200, 0, 3),
        ("^KS11", 59, 0, 3),
        ("^KS11", 200, 8, 3),
        ("^KS11", 200, 0, 39),
    ],
)
def test_summary_report_fails_closed_on_invalid_index_metadata(
    db, monkeypatch, kospi_ticker, row_count, latest_age_days, max_gap_days
) -> None:
    """KOSPI 오표기·결측을 가진 Market Summary가 completed로 저장되면 안 된다."""
    report = SimpleNamespace(
        html_content="<p>invalid composite</p>",
        trade_date="20260825",
        metadata={
            "report_type": "market_summary",
            "data_valid": True,
            "index_inputs": {
                "kospi": {
                    "ticker": kospi_ticker,
                    "row_count": row_count,
                    "latest_age_days": latest_age_days,
                    "max_gap_days": max_gap_days,
                },
                "kosdaq": {
                    "ticker": "^KQ11",
                    "row_count": 200,
                    "latest_age_days": 0,
                    "max_gap_days": 3,
                },
            },
        },
    )
    monkeypatch.setattr(
        runner,
        "_import_generators",
        lambda: {"summary": lambda: report},
    )

    run_id = runner.run_report(db, "summary")

    row = db.query(StockReportRun).filter(StockReportRun.id == run_id).one()
    assert row.status == "failed"
    assert "index metadata invalid" in (row.error_message or "")
    assert row.html_content is None


def test_summary_report_completes_with_valid_index_metadata(db, monkeypatch) -> None:
    report = SimpleNamespace(
        html_content="<p>valid composite</p>",
        trade_date="20260825",
        metadata={
            "data_valid": True,
            "index_inputs": {
                "kospi": {
                    "ticker": "^KS11", "row_count": 200,
                    "latest_age_days": 1, "max_gap_days": 3,
                },
                "kosdaq": {
                    "ticker": "^KQ11", "row_count": 200,
                    "latest_age_days": 1, "max_gap_days": 3,
                },
            },
        },
    )
    monkeypatch.setattr(runner, "_import_generators", lambda: {"summary": lambda: report})

    run_id = runner.run_report(db, "summary")

    row = db.query(StockReportRun).filter(StockReportRun.id == run_id).one()
    assert row.status == "completed"
    assert row.html_content == "<p>valid composite</p>"
