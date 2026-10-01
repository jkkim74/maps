import datetime as dt

import pytest

from maps.common.models import CollectionLog


def quality(valid=95):
    return {"status": "partial", "candidate_ready": True, "markets": {
        market: {"expected_count": 100, "valid_count": valid,
                 "coverage_ratio": valid / 100, "error": None}
        for market in ("KOSPI", "KOSDAQ")
    }}


@pytest.mark.parametrize("valid,allowed", [(94, False), (95, True), (100, True)])
def test_each_market_threshold_is_checked_from_counts(valid, allowed):
    from maps.ops.score_readiness import metadata_quality_ready
    data = quality()
    data["markets"]["KOSDAQ"]["valid_count"] = valid
    assert metadata_quality_ready(data) == allowed


def test_missing_market_and_forged_ready_bit_cannot_allow_buys():
    from maps.ops.score_readiness import metadata_quality_ready
    data = quality()
    del data["markets"]["KOSDAQ"]
    assert not metadata_quality_ready(data)
    assert not metadata_quality_ready(None)
    assert not metadata_quality_ready({"candidate_ready": True, "scope": "mock"})


def test_exact_date_latest_collection_controls_readiness(db):
    from maps.ops.score_readiness import collection_metadata_ready
    day = dt.date(2026, 9, 30)
    assert collection_metadata_ready(db, day) == (False, "metadata_quality_legacy_unknown")
    old = CollectionLog(ref_date=day - dt.timedelta(days=1), source="krx",
                        status="success", items=100, metadata_quality=quality(100))
    db.add(old); db.commit()
    assert not collection_metadata_ready(db, day)[0]
    current = CollectionLog(ref_date=day, source="krx", status="partial",
                            items=100, metadata_quality=quality(94))
    db.add(current); db.commit()
    assert collection_metadata_ready(db, day) == (False, "metadata_quality_insufficient")
    db.add(CollectionLog(ref_date=day, source="krx", status="success", items=100,
                         metadata_quality=quality(100)))
    db.commit()
    assert collection_metadata_ready(db, day) == (True, None)


def test_missing_ticker_blocked_even_above_market_threshold(db):
    from maps.ops.score_readiness import collection_metadata_ready
    day = dt.date(2026, 9, 30)
    data = quality(99)
    data["markets"]["KOSPI"]["missing_tickers"] = ["005930"]
    db.add(CollectionLog(ref_date=day, source="krx", status="partial", items=198,
                         metadata_quality=data))
    db.commit()
    assert collection_metadata_ready(db, day, "005930") == (False, "ticker_metadata_incomplete")
    assert collection_metadata_ready(db, day, "000660") == (True, None)
