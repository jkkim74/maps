"""Durable source observations publish validated bars once and survive reconstruction."""
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from maps.common.exceptions import DataCollectionError
from maps.data.ohlcv_runs import NormalizedDailyCollector, save_collection, load_collection
from maps.common.models import OhlcvCollectionRun, OhlcvCollectionItem, HistoricalOHLCV
from maps.data.ohlcv_source import SourceBar, SourceItem, SourceBatch
from maps.data.krx_adapter import CollectionResult, SecurityMeta

DAY = dt.datetime.now(ZoneInfo('Asia/Seoul')).date()


class Source:
    def __init__(self, items, complete=True):
        self.items, self.complete, self.calls = items, complete, 0
    def collect(self, day, **kw):
        self.calls += 1
        expected = {item.ticker: 'ISIN' for item in self.items}
        kw['on_universe'](expected)
        kw['on_request'](2)
        for item in self.items:
            if item.ticker not in kw['skip_tickers']:
                kw['on_item'](item)
        return SourceBatch(day,self.items,expected,2,self.complete)


def valid(ticker='005930', adjusted=225000):
    return SourceItem(ticker,DAY,'valid' if adjusted else 'adjustment_missing',
        SourceBar(DAY,ticker,223500,229500,216500,225000,100,adjusted))


def test_publish_separates_no_trade_and_reuses_frozen_prices(db):
    source=Source([valid(),SourceItem('000300',DAY,'no_trade')])
    rows, quality=NormalizedDailyCollector(db,source).collect(DAY)
    assert quality['saved_count']==1 and quality['no_trade_count']==1
    assert quality['status']=='complete'
    assert db.query(HistoricalOHLCV).one().close==225000
    assert db.query(HistoricalOHLCV).one().source=='krx_per_security'
    assert NormalizedDailyCollector(db,source).collect(DAY)[0][0].close==225000
    assert source.calls==1


def test_failed_adjustment_preserves_valid_raw_but_is_partial(db):
    rows,q=NormalizedDailyCollector(db,Source([valid(adjusted=None)])).collect(DAY)
    assert rows[0].adj_close is None
    assert q['status']=='partial' and q['adjustment_missing_count']==1


def test_failed_source_does_not_overwrite_good_prices(db):
    db.add(HistoricalOHLCV(ticker='005930',date=DAY,open=10,high=12,low=9,close=11,volume=50,source='old'))
    db.commit()
    with pytest.raises(DataCollectionError):
        NormalizedDailyCollector(db,Source([SourceItem('005930',DAY,'failed',reason='timeout')])).collect(DAY)
    assert db.query(HistoricalOHLCV).one().close==11
    assert db.query(OhlcvCollectionRun).one().published_at is None


def test_active_owner_blocks_second_collector(db):
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.add(OhlcvCollectionRun(ref_date=DAY,status='running',owner='other',lease_until=now+dt.timedelta(seconds=60),started_at=now,expected={},quality={},requests=0))
    db.commit()
    with pytest.raises(DataCollectionError,match='in_progress'):
        NormalizedDailyCollector(db,Source([valid()])).collect(DAY)


def test_collection_snapshot_not_visible_until_auxiliary_completion(db):
    bars,q=NormalizedDailyCollector(db,Source([valid()])).collect(DAY)
    assert load_collection(db,DAY) is None
    result=CollectionResult(DAY,ohlcv=bars,ohlcv_quality=q)
    save_collection(db,result)
    restored=load_collection(db,DAY)
    assert restored.ref_date==DAY and restored.ohlcv[0].close==225000
    assert restored.ohlcv_quality['run_id']==q['run_id']


def test_malformed_checkpoint_cannot_publish(db):
    item=valid();item.bar.high=200000
    with pytest.raises(DataCollectionError):
        NormalizedDailyCollector(db,Source([item])).collect(DAY)
    assert db.query(HistoricalOHLCV).count()==0


def test_checkpoint_bar_must_match_item_identity_and_day(db):
    item=valid();item.bar.ticker='000660'
    with pytest.raises(DataCollectionError,match='identity'):
        NormalizedDailyCollector(db,Source([item])).collect(DAY)
    assert db.query(HistoricalOHLCV).count()==0


def test_snapshot_is_immutable_after_first_save(db):
    bars,q=NormalizedDailyCollector(db,Source([valid()])).collect(DAY)
    result=CollectionResult(DAY,ohlcv=bars,ohlcv_quality=q)
    save_collection(db,result)
    result.ohlcv[0].close=220000
    with pytest.raises(DataCollectionError,match='immutable'):
        save_collection(db,result)
    assert load_collection(db,DAY).ohlcv[0].close==225000


def test_missing_expected_symbol_does_not_publish_as_complete(db):
    class Incomplete(Source):
        def collect(self,day,**kw):
            kw['on_universe']({'005930':'ISIN','000660':'ISIN2'})
            kw['on_item'](valid())
            return SourceBatch(day,[],{'005930':'ISIN','000660':'ISIN2'},0,True)
    with pytest.raises(DataCollectionError,match='incomplete'):
        NormalizedDailyCollector(db,Incomplete([])).collect(DAY)
    assert db.query(HistoricalOHLCV).count()==0


def test_adjustment_failure_does_not_erase_existing_complete_row(db):
    db.add(HistoricalOHLCV(ticker='005930',date=DAY,open=223500,high=229500,low=216500,
                          close=225000,adj_close=225000,volume=100,source='krx_per_security'))
    db.commit()
    _,quality=NormalizedDailyCollector(db,Source([valid(adjusted=None)])).collect(DAY)
    assert db.query(HistoricalOHLCV).one().adj_close==225000
    assert quality['preserved_count']==1 and quality['status']=='partial'


def test_expired_lease_resumes_with_checkpoint_and_original_budget(db):
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    run=OhlcvCollectionRun(ref_date=DAY,status='running',owner='expired',
        lease_until=now-dt.timedelta(seconds=1),started_at=now-dt.timedelta(seconds=120),
        expected={'005930':'ISIN','000660':'ISIN2'},quality={},requests=9)
    db.add(run);db.commit()
    db.add(OhlcvCollectionItem(run_id=run.id,ticker='005930',status='valid',
                              payload=valid().as_dict(),observed_at=now))
    db.commit()
    class Resume:
        def collect(self,day,**kw):
            assert kw['expected']=={'005930':'ISIN','000660':'ISIN2'}
            assert kw['skip_tickers']==['005930'] and kw['initial_requests']==9
            assert kw['remaining_seconds']<10700
            kw['on_request'](10)
            kw['on_item'](valid('000660'))
            return SourceBatch(day,[],kw['expected'],10,True)
    bars,quality=NormalizedDailyCollector(db,Resume()).collect(DAY)
    assert len(bars)==2 and quality['requests']==10


def test_lost_lease_cannot_write_item_or_fail_new_owner(db):
    class LoseLease:
        def collect(self,day,**kw):
            kw['on_universe']({'005930':'ISIN'})
            run=db.query(OhlcvCollectionRun).one()
            run.owner='replacement';db.commit()
            kw['on_item'](valid())
    with pytest.raises(DataCollectionError,match='lease_lost'):
        NormalizedDailyCollector(db,LoseLease()).collect(DAY)
    assert db.query(OhlcvCollectionItem).count()==0
    assert db.query(OhlcvCollectionRun).one().owner=='replacement'


def test_publication_error_rolls_back_all_price_writes(db,monkeypatch):
    from sqlalchemy import event
    def fail(mapper, connection, target):
        if target.ticker=='005930':
            raise RuntimeError('injected database failure')
    event.listen(HistoricalOHLCV,'before_insert',fail)
    try:
        with pytest.raises(RuntimeError,match='injected'):
            NormalizedDailyCollector(db,Source([valid('000660'),valid()])).collect(DAY)
    finally:
        event.remove(HistoricalOHLCV,'before_insert',fail)
    assert db.query(HistoricalOHLCV).count()==0
    assert db.query(OhlcvCollectionRun).one().published_at is None


def test_discovery_evidence_stays_in_audit_not_job_quality(db):
    class EvidenceSource(Source):
        def collect(self, day, **kw):
            kw['on_universe_evidence']([{'body': {'OutBlock_1': [{'ISU_SRT_CD':'005930'}]}}])
            return super().collect(day,**kw)
    _,quality=NormalizedDailyCollector(db,EvidenceSource([valid()])).collect(DAY)
    assert 'universe_evidence' not in quality
    assert db.query(OhlcvCollectionRun).one().quality['universe_evidence']
