"""Report comparisons load only selected, causal latest payloads, one at a time."""
import base64
from datetime import date, datetime, timedelta, timezone
import json
import zlib

import pytest
from sqlalchemy import event

from maps.common.models import FujimotoEvidence
from maps.fujimoto.repository import FujimotoRepository
from maps.fujimoto.valuation import valuation_report


@pytest.mark.parametrize("tickers", [["A", "C"], None])
def test_report_loads_only_latest_selected_same_day_candidates_incrementally(db, monkeypatch, tickers):
    """Filtering after ORM loading, or buffering all latest rows, breaks this contract."""
    import maps.fujimoto.replay as replay
    # sources binds its shared builder at import; initialize before observing it.
    import maps.fujimoto.sources
    day = date(2026, 10, 21)
    now = datetime(2026, 10, 21, 13, tzinfo=timezone.utc)
    repo = FujimotoRepository(db)
    repo.record_evidence("universe", "*", now, now,
        {"ref_date": day, "members": [{"ticker": t} for t in ("A", "B", "C")]})

    def candidate(ticker, marker, *, ref_date=day, observed=None, available=None):
        """Store actual compressed inputs with a marker identifying the frozen revision."""
        raw = dict(marker=marker, annual_records=[], financial_records=[], valuations=[],
            sectors=None, eligible=False, prices=[dict(date=ref_date.isoformat(),
                open=100, high=101, low=99, close=100, volume=1000, turnover=100000)])
        payload = dict(ref_date=ref_date, raw_zlib=base64.b64encode(
            zlib.compress(json.dumps(raw).encode())).decode())
        record = repo.record_evidence("candidate", ticker, observed or now,
            available or now, payload)
        return record.id

    expected_ids = {}
    for ticker in ("A", "B", "C", "UNRELATED"):
        candidate(ticker, "old", observed=now - timedelta(hours=1))
        # Newest ID wins, preserving existing semantics even with earlier observation.
        expected_ids[ticker] = candidate(ticker, ticker, observed=now - timedelta(hours=2))
        candidate(ticker, "other-day", ref_date=day - timedelta(days=1))
        candidate(ticker, "pending", available=now + timedelta(seconds=1))
        candidate(ticker, "future", observed=now + timedelta(seconds=1),
            available=now + timedelta(seconds=1))
    db.commit()
    db.expunge_all()
    selected = ["A", "C"] if tickers is not None else ["A", "B", "C"]
    loaded, compared = [], []

    def on_load(record, context):
        if record.kind == "candidate":
            loaded.append(record.id)

    real_screen = replay.screening_evidence

    def observe_screen(raw, ticker, cutoff, **kwargs):
        # Use the real builder and observe the memory boundary before each comparison.
        index = selected.index(ticker)
        assert loaded == [expected_ids[t] for t in selected[:index + 1]]
        compared.append((ticker, cutoff, raw))
        return real_screen(raw, ticker, cutoff, **kwargs)

    event.listen(FujimotoEvidence, "load", on_load)
    monkeypatch.setattr(replay, "screening_evidence", observe_screen)
    try:
        result = valuation_report(db, day, now=now, tickers=tickers)
    finally:
        event.remove(FujimotoEvidence, "load", on_load)
    assert loaded == [expected_ids[t] for t in selected]
    assert result["comparison_candidates"] == len(selected)
    assert result["comparison"]["v2_selected"] == result["comparison"]["v3_selected"] == 0
    assert result["execution_permission"] is False
    for index, ticker in enumerate(selected):
        v2, v3 = compared[2 * index:2 * index + 2]
        assert v2[:2] == v3[:2] == (ticker, day)
        assert v2[2]["marker"] == v3[2]["marker"] == ticker
        assert v2[2]["screening_version"] == 2 and v3[2]["screening_version"] == 3
        assert {k:v for k,v in v2[2].items() if k != "screening_version"} == {
            k:v for k,v in v3[2].items() if k != "screening_version"}


def test_report_empty_tickers_does_not_query_candidate_payloads(db):
    """An explicitly empty selection must not read the candidate table for comparison."""
    statements = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        if "candidate" in str(parameters):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", on_execute)
    try:
        result = valuation_report(db, date(2026, 10, 21), tickers=[])
    finally:
        event.remove(db.bind, "before_cursor_execute", on_execute)
    assert statements == []
    assert result["rows"] == [] and result["comparison_candidates"] == 0
