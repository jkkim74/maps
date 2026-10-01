import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from maps.common.models import CandidateSnapshot
from maps.common.settings import MapsSettings
from maps.data.krx_adapter import CollectionResult
from maps.data.security_repo import Security
from maps.ops.scheduler import OperationalPipeline, TickerContext
from tests.test_collection_readiness import quality


def test_collection_job_partial_and_generation_blocked_before_market_calls(db, monkeypatch):
    day = dt.date(2026, 9, 30)
    collection = CollectionResult(ref_date=day, metadata_quality=quality(94))
    pipeline = OperationalPipeline(settings=MapsSettings(), session_factory=lambda: db)
    monkeypatch.setattr('maps.ops.scheduler.DataCollector.collect_daily', lambda *args: collection)
    analyze = Mock(side_effect=AssertionError('must block before analysis'))
    monkeypatch.setattr(pipeline, '_analyze_regime', analyze)
    run = pipeline.collect_data(day)
    assert run.status == 'partial'
    assert run.details['metadata_quality']['markets']['KOSDAQ']['valid_count'] == 94
    run = pipeline.generate_candidates(day)
    assert run.status == 'failed'
    assert 'metadata_quality' in run.message
    assert not analyze.called


def test_research_candidate_persists_version_evidence_and_is_not_orderable(db):
    day = dt.date(2026, 9, 30)
    extra = {key: 50. for key in ('earnings_improvement_score','crowd_neglect_score',
                                  'accumulation_flow_score','technical_bottom_score')}
    extra.update(_sources={}, _evidence={'test': 'measured'})
    context = TickerContext(frame=pd.DataFrame(), trend_strength=30, ts_bucket='S2',
                            close=100, atr14=2, valuation=SimpleNamespace(valuation_score=80, reason='measured'),
                            contrarian_scores=extra)
    stock = Security(ticker='005930', name='Samsung', market='KOSPI', security_type='STOCK',
                     turnover_cache={day: 1e9})
    pipeline = OperationalPipeline(settings=MapsSettings(maps_contrarian_accumulation_enabled=False,
                                                       maps_strategy_aware_scoring_enabled=True,
                                                       maps_score_readiness_required=False))
    pipeline._save_candidate_snapshot(db, day, 'contrarian_quality_accumulation_v1', [stock],
                                      contexts={stock.ticker: context}, weekly_pass=True)
    row = db.query(CandidateSnapshot).one()
    assert row.score_ready
    assert row.score_version == 'contrarian_quality_20261001'
    assert row.score_scope == 'research'
    assert row.score_evidence == {'test': 'measured'}
    blocked = {}
    assert not pipeline._order_candidates(db, day + dt.timedelta(days=1), blocked=blocked)
    assert blocked == {'research_score_only': 1}


def test_newer_failed_collection_overrides_cached_success(db):
    from maps.common.models import CollectionLog
    day = dt.date(2026, 9, 30)
    pipeline = OperationalPipeline(settings=MapsSettings(), session_factory=lambda: db)
    pipeline._last_collection = CollectionResult(ref_date=day, metadata_quality=quality(100))
    db.add(CollectionLog(ref_date=day, source="krx", status="failed", items=0,
                         metadata_quality=quality(94)))
    db.commit()
    result = pipeline.generate_candidates(day)
    assert result.status == "failed"
    assert result.message == "metadata_quality_insufficient"


def test_known_listing_date_is_preserved_without_inventing_unknown_date(db):
    from maps.common.models import SecurityMetadata
    from maps.data.krx_adapter import SecurityMeta
    day = dt.date(2026, 9, 30)
    known = dt.date(2005, 3, 1)
    db.add(SecurityMetadata(ticker="005930", name="Samsung", market="KOSPI",
                            security_type="STOCK", listing_date=known))
    db.commit()
    meta = [SecurityMeta(ticker=ticker, name=ticker, market="KOSPI", security_type="STOCK")
            for ticker in ("005930", "000660")]
    stocks = OperationalPipeline(settings=MapsSettings())._to_securities(
        db, meta, CollectionResult(ref_date=day), day)
    assert [stock.listing_date for stock in stocks] == [known, None]
