"""Offline recovery contracts against real isolated SQLAlchemy storage."""
from __future__ import annotations

import base64
import copy
from datetime import date, datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from maps.common.exceptions import DataQualityError
from maps.common.models import CollectionLog, FujimotoEvidence, HistoricalOHLCV


def recovery():
    """Keep the initial missing-feature RED a clear assertion failure."""
    try:
        return importlib.import_module("maps.data.recovery")
    except ModuleNotFoundError:
        pytest.fail("operational recovery module is not implemented")


def envelope(ticker="005930", days=("2026/10/16",), **overrides):
    rows = [{"TRD_DD": day, "TDD_OPNPRC": "100", "TDD_HGPRC": "120",
             "TDD_LWPRC": "90", "TDD_CLSPRC": "110", "ACC_TRDVOL": "1,234"}
            for day in days]
    raw = json.dumps({"output": rows}).encode()
    result = {"version": 1, "source_url": "https://data.krx.co.kr/contents/MDC/STAT/standard/MDCSTAT017.jsp",
              "ticker": ticker, "observed_at": "2026-10-21T08:00:00+00:00",
              "request": {"bld": "dbms/MDC/STAT/standard/MDCSTAT01701", "isuCd": f"KR7{ticker}003",
                          "strtDd": "20261016", "endDd": "20261021", "adjStkPrc": "1"},
              "raw_base64": base64.b64encode(raw).decode(), "raw_sha256": hashlib.sha256(raw).hexdigest()}
    result.update(overrides)
    return result


def seed(db, ticker="005930", day=date(2026, 10, 16)):
    row = HistoricalOHLCV(ticker=ticker, date=day, open=100, high=120, low=90,
                          close=105, volume=1000, adj_close=104, source="legacy",
                          created_at=datetime(2026, 10, 16, 9), updated_at=datetime(2026, 10, 16, 10))
    db.add(row)
    db.commit()
    return row


def plan(db, envelopes=None, tickers=None, **kwargs):
    return recovery().build_manifest(db, envelopes or [envelope()], start=date(2026, 10, 16),
                                     end=date(2026, 10, 21), tickers=tickers or ["005930"], **kwargs)


def test_dry_run_inventory_and_manifest_preserve_all_storage(db):
    row = seed(db)
    db.add(CollectionLog(ref_date=row.date, source="krx", status="partial", items=0,
                         note='{"ohlcv_rejected_rows":1}'))
    db.commit()
    r = recovery()
    report = r.inventory(db, start=date(2026, 10, 16), end=date(2026, 10, 18), tickers=["005930"])
    manifest = plan(db)
    assert report["counts"]["present"] == 1
    assert report["counts"]["nontrading"] == 2
    assert report["collection_failures"][0]["status"] == "partial"
    assert manifest["changes"][0]["before"]["adj_close"] == 104
    assert manifest["changes"][0]["after"]["adj_close"] is None
    assert manifest["changes"][0]["after"]["volume"] == 1234
    assert row.close == 105
    assert db.query(FujimotoEvidence).count() == 0


def test_apply_and_rollback_are_atomic_idempotent_and_preserve_decisions(db):
    seed(db)
    frozen = FujimotoEvidence(kind="decision", ticker="005930", observed_at=datetime(2026, 10, 16),
                             available_at=datetime(2026, 10, 16), payload={"close": 105}, fingerprint="f" * 64)
    db.add(frozen)
    db.commit()
    r = recovery()
    manifest = plan(db, [envelope(days=("2026/10/16", "2026/10/19"))])
    assert r.apply_manifest(db, manifest)["changed"] == 2
    assert r.apply_manifest(db, manifest)["changed"] == 0
    rows = db.query(HistoricalOHLCV).order_by(HistoricalOHLCV.date).all()
    assert [(v.close, v.adj_close, v.source) for v in rows] == [(110, None, "krx_per_security")] * 2
    receipt = db.query(FujimotoEvidence).filter_by(kind="operational_recovery").one()
    assert receipt.available_at >= receipt.observed_at > datetime(2026, 10, 16)
    assert receipt.payload["manifest"]["sources"][0]["raw_sha256"] == manifest["sources"][0]["raw_sha256"]
    assert r.rollback_manifest(db, manifest)["changed"] == 2
    assert r.rollback_manifest(db, manifest)["changed"] == 0
    restored = db.query(HistoricalOHLCV).one()
    assert (restored.close, restored.adj_close, restored.volume, restored.source) == (105, 104, 1000, "legacy")
    assert restored.updated_at == datetime(2026, 10, 16, 10)
    assert frozen.payload == {"close": 105}


def test_later_conflict_leaves_earlier_rows_and_audit_unchanged(db):
    first = seed(db)
    second = seed(db, "000660")
    manifest = plan(db, [envelope(), envelope("000660")], ["005930", "000660"])
    second.close = 108
    db.commit()
    with pytest.raises(DataQualityError, match="conflict"):
        recovery().apply_manifest(db, manifest)
    db.refresh(first)
    assert first.close == 105
    assert db.query(FujimotoEvidence).count() == 0


@pytest.mark.parametrize("mutation", ["hash", "ticker", "adjusted", "source", "range", "nan", "duplicate", "future", "fraction_volume"])
def test_untrusted_or_malformed_source_is_rejected_before_any_change(db, mutation):
    raw = envelope()
    if mutation == "hash":
        raw["raw_sha256"] = "0" * 64
    elif mutation == "ticker":
        raw["request"]["isuCd"] = "KR7000660001"
    elif mutation == "adjusted":
        raw["request"]["adjStkPrc"] = "2"
    elif mutation == "source":
        raw["source_url"] = "https://evil.example/MDCSTAT017.jsp"
    elif mutation == "future":
        raw["observed_at"] = "2026-10-16T01:00:00+00:00"
    else:
        body = json.loads(base64.b64decode(raw["raw_base64"]))
        row = body["output"][0]
        if mutation == "duplicate":
            body["output"].append(copy.deepcopy(row))
        elif mutation == "range":
            row["TDD_CLSPRC"] = "130"
        elif mutation == "fraction_volume":
            row["ACC_TRDVOL"] = "1.5"
        else:
            row["TDD_OPNPRC"] = "NaN"
        encoded = json.dumps(body).encode()
        raw.update(raw_base64=base64.b64encode(encoded).decode(), raw_sha256=hashlib.sha256(encoded).hexdigest())
    with pytest.raises(DataQualityError):
        plan(db, [raw])
    assert db.query(HistoricalOHLCV).count() == db.query(FujimotoEvidence).count() == 0


def test_manifest_tampering_is_rejected_even_if_digest_is_recomputed(db):
    manifest = plan(db)
    manifest["changes"][0]["after"]["close"] = 115
    manifest["manifest_id"] = recovery().manifest_digest(manifest)
    with pytest.raises(DataQualityError, match="source"):
        recovery().apply_manifest(db, manifest)
    assert db.query(HistoricalOHLCV).count() == 0


def test_rollback_conflict_and_unapplied_manifest_do_not_overwrite_current_rows(db):
    seed(db)
    manifest = plan(db)
    r = recovery()
    with pytest.raises(DataQualityError, match="not_applied"):
        r.rollback_manifest(db, manifest)
    r.apply_manifest(db, manifest)
    row = db.query(HistoricalOHLCV).one()
    row.volume = 999
    db.commit()
    with pytest.raises(DataQualityError, match="conflict"):
        r.rollback_manifest(db, manifest)
    assert row.volume == 999
    assert db.query(FujimotoEvidence).count() == 1


def test_explicit_scope_and_zero_trade_rows_do_not_invent_bars(db):
    raw = envelope()
    body = json.loads(base64.b64decode(raw["raw_base64"]))
    for key in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL"):
        body["output"][0][key] = "0"
    encoded = json.dumps(body).encode()
    raw.update(raw_base64=base64.b64encode(encoded).decode(), raw_sha256=hashlib.sha256(encoded).hexdigest())
    manifest = plan(db, [raw])
    assert manifest["changes"] == []
    assert manifest["unrepairable"][0]["reason"] == "source_no_trade"
    with pytest.raises(DataQualityError, match="scope"):
        plan(db, [envelope("000660")])


def test_inventory_reports_invalid_rows_and_unknown_missing_separately(db):
    row = seed(db)
    row.close = 130
    db.commit()
    report = recovery().inventory(db, start=date(2026, 10, 16), end=date(2026, 10, 19), tickers=["005930"])
    assert report["counts"] == {"present": 0, "invalid": 1, "missing_unclassified": 1, "nontrading": 2}
    assert report["gaps"][0]["date"] == "2026-10-19"


def test_cli_defaults_to_inventory_and_requires_manifest_for_write(db, tmp_path):
    from scripts import recover_operational_data as cli
    out = tmp_path / "inventory.json"
    assert cli.main(["--start", "2026-10-16", "--end", "2026-10-19", "--tickers", "005930", "--output", str(out)], session=db) == 0
    assert json.loads(out.read_text())["counts"]["missing_unclassified"] == 2
    with pytest.raises(SystemExit):
        cli.main(["--apply"], session=db)
    assert db.query(HistoricalOHLCV).count() == 0


def test_no_trade_source_with_carried_close_is_classified_not_filled(db):
    raw = envelope()
    body = json.loads(base64.b64decode(raw["raw_base64"]))
    for key in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL"):
        body["output"][0][key] = "0"
    encoded = json.dumps(body).encode()
    raw.update(raw_base64=base64.b64encode(encoded).decode(), raw_sha256=hashlib.sha256(encoded).hexdigest())
    manifest = plan(db, [raw])
    assert manifest["changes"] == []
    assert manifest["unrepairable"][0]["reason"] == "source_no_trade"


def test_manifest_cannot_backdate_stored_timestamps(db):
    manifest = plan(db)
    manifest["changes"][0]["after"]["updated_at"] = "2026-10-16T00:00:00+00:00"
    manifest["manifest_id"] = recovery().manifest_digest(manifest)
    with pytest.raises(DataQualityError, match="timestamp"):
        recovery().apply_manifest(db, manifest)
    assert db.query(HistoricalOHLCV).count() == 0


def test_sql_compare_and_swap_rolls_back_all_earlier_writes(db, monkeypatch):
    from sqlalchemy import update
    seed(db)
    seed(db, "000660")
    manifest = plan(db, [envelope(), envelope("000660")], ["005930", "000660"])
    execute = db.execute
    raced = False

    def race_once(statement, *args, **kwargs):
        nonlocal raced
        if getattr(statement, "is_update", False) and not raced:
            raced = True
            # A real SQL change after the preflight; the second CAS must reject.
            db.connection().execute(update(HistoricalOHLCV).where(HistoricalOHLCV.ticker == "000660").values(volume=999))
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", race_once)
    with pytest.raises(DataQualityError, match="conflict"):
        recovery().apply_manifest(db, manifest)
    assert [(r.close, r.volume) for r in db.query(HistoricalOHLCV).all()] == [(105, 1000), (105, 1000)]
    assert db.query(FujimotoEvidence).count() == 0


def test_atomic_report_failure_preserves_existing_manifest(db, tmp_path, monkeypatch):
    from scripts import recover_operational_data as cli
    target = tmp_path / "manifest.json"
    target.write_text("existing reviewed manifest", encoding="utf-8")

    def reject_replace(*args):
        raise OSError("simulated output failure")

    monkeypatch.setattr(cli.os, "replace", reject_replace)
    assert cli.main(["--start", "2026-10-16", "--end", "2026-10-19", "--tickers", "005930", "--output", str(target)], session=db) == 2
    assert target.read_text(encoding="utf-8") == "existing reviewed manifest"
    assert list(tmp_path.glob("*.tmp")) == []
    assert db.query(FujimotoEvidence).count() == 0


@pytest.mark.parametrize("operation", ["inventory", "plan"])
def test_dry_run_rejects_pending_unrelated_changes_without_flushing(db, operation):
    row = seed(db)
    row.close = 106
    if operation == "plan":
        call = lambda: plan(db)
    else:
        call = lambda: recovery().inventory(db, start=date(2026, 10, 16), end=date(2026, 10, 19), tickers=["005930"])
    with pytest.raises(DataQualityError, match="clean_session"):
        call()
    db.rollback()
    assert db.query(HistoricalOHLCV).one().close == 105


@pytest.mark.parametrize("observed,accepted", [
    ("2026-10-16T06:30:01+00:00", False),  # regular close does not finalize total volume
    ("2026-10-16T14:59:59+00:00", False),  # still the same KST day
    ("2026-10-16T15:00:00+00:00", True),   # next KST calendar date
])
def test_total_volume_requires_observation_on_a_later_kst_date(observed, accepted):
    source = envelope(observed_at=observed)
    if accepted:
        parsed = recovery().parse_envelope(source)
        assert parsed[0]["values"]["volume"] == 1234
        assert parsed[0]["observed_at"] == observed
    else:
        with pytest.raises(DataQualityError, match="incomplete_session"):
            recovery().parse_envelope(source)


@pytest.mark.parametrize("value,expected", [
    ("1234.5", None), ("1234.00000000000001", None),
    ("1234.0000000000000000000000000000000000000000000001", None),
    ("1234.00000000000000", 1234), ("2,147,483,647", 2147483647),
    ("2147483647.00000000000001", None), ("2147483648", None),
])
def test_volume_integrality_uses_exact_source_decimal(value, expected):
    source = envelope()
    body = json.loads(base64.b64decode(source["raw_base64"]))
    body["output"][0]["ACC_TRDVOL"] = value
    raw = json.dumps(body).encode()
    source.update(raw_base64=base64.b64encode(raw).decode(), raw_sha256=hashlib.sha256(raw).hexdigest())
    if expected is None:
        with pytest.raises(DataQualityError):
            recovery().parse_envelope(source)
    else:
        assert recovery().parse_envelope(source)[0]["values"]["volume"] == expected


def test_standalone_cli_receipts_reject_orm_update_and_delete(db, tmp_path):
    """A fresh interpreter catches missing listener imports masked by other tests."""
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(plan(db)), encoding="utf-8")
    child = tmp_path / "test_standalone_recovery_receipts.py"
    child.write_text(textwrap.dedent(f'''
        import sys
        import pytest

        def test_standalone_receipt_protection():
            assert "maps.fujimoto.repository" not in sys.modules
            from scripts.recover_operational_data import main
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session
            from maps.common.db import Base
            from maps.common.models import FujimotoEvidence, HistoricalOHLCV
            from maps.common.exceptions import DataQualityError
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            with Session(engine, autoflush=False) as db:
                args = ["--manifest", {str(manifest_file)!r}, "--output", {str(tmp_path / "result.json")!r}]
                assert main(["--apply", *args], session=db) == 0
                receipt = db.query(FujimotoEvidence).one()
                receipt.payload = {{"tampered": True}}
                with pytest.raises(DataQualityError, match="immutable"):
                    db.commit()
                db.rollback()
                receipt = db.query(FujimotoEvidence).one()
                assert receipt.payload["operation"] == "apply"
                db.delete(receipt)
                with pytest.raises(DataQualityError, match="immutable"):
                    db.commit()
                db.rollback()
                assert main(["--apply", *args], session=db) == 0
                assert main(["--rollback", *args], session=db) == 0
                assert main(["--rollback", *args], session=db) == 0
                assert db.query(FujimotoEvidence).count() == 2
                assert db.query(HistoricalOHLCV).count() == 0
            engine.dispose()
    '''), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/run_isolated_tests.py"), str(child), "-q", "--tb=short"],
                            cwd=root, text=True, capture_output=True, timeout=60, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stdout + result.stderr
