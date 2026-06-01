from __future__ import annotations

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
