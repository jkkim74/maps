from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from maps.common.db import Base
from maps.common.settings import MapsSettings
from maps.common.exceptions import ExecutionBlockedError
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.safety import ExecutionContext
from maps.execution.us_service import USExecutionService
from maps.execution.us_mock_broker import USMockBroker

NOW = datetime(2026,10,16,15,tzinfo=timezone.utc)

@pytest.fixture
def env(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'us.db'))
    Base.metadata.create_all(engine)
    db=Session(engine)
    settings=MapsSettings(_env_file=None,maps_us_enabled=True,maps_us_account_no='test-'+uuid4().hex,maps_us_allowed_symbols='AAPL:NASD',maps_us_max_order_usd=1000,maps_us_max_exposure_usd=3000,maps_us_max_daily_loss_usd=100,maps_us_mock_cash_usd=10000,maps_us_fee_buffer_rate=0)
    broker=USMockBroker(db,settings=settings,now=lambda:NOW)
    broker.set_quote('AAPL','NASD',Decimal('100.00'))
    service=USExecutionService(db,broker,settings=settings,now=lambda:NOW)
    service.sync()
    try:
        yield db,settings,broker,service
    finally:
        broker.db.close()
        db.close()
        engine.dispose()

def order(side=OrderSide.BUY,quantity=2):
    return Order('manual','AAPL',side,OrderType.LIMIT,quantity,Decimal('100.00'),market='US',currency='USD',exchange='NASD')

def context(key='one',**kw):
    return ExecutionContext(key,source='manual',actor='admin',is_admin=True,instance_id='manual',**kw)

def test_default_settings_fail_closed():
    settings=MapsSettings(_env_file=None)
    assert not settings.maps_us_enabled
    assert settings.maps_us_broker_mode=='mock'
    assert settings.maps_us_max_daily_loss_usd is None

def test_submit_is_idempotent_and_survives_restart(env):
    db,settings,broker,service=env
    first=service.submit(order(),context())
    assert first['status']=='pending'
    assert service.submit(order(),context())['id']==first['id']
    restarted=USExecutionService(db,USMockBroker(db,settings=settings,now=lambda:NOW),settings=settings,now=lambda:NOW)
    restarted.sync()
    assert len(restarted.orders())==1
    assert restarted.account()['reserved_usd']=='200.000000'

def test_fill_costs_ownership_and_no_external_adoption(env):
    db,settings,broker,service=env
    row=service.submit(order(),context())
    broker.fill(row['broker_order_id'],1,Decimal('99.50'),Decimal('0.10'))
    service.sync()
    assert service.positions()[0]['owned_quantity']==1
    assert service.order_detail(row['id'])['filled_quantity']==1
    with pytest.raises(ExecutionBlockedError,match='sell_consent'):
        service.submit(order(OrderSide.SELL,1),context('sell'))
    sell=service.submit(order(OrderSide.SELL,1),context('sell',sell_consent=True))
    assert sell['status']=='pending'

def test_cancel_request_holds_reservation_until_confirmed(env):
    db,settings,broker,service=env
    row=service.submit(order(),context())
    cancelled=service.cancel(row['id'],'admin')
    assert cancelled['status']=='cancel_requested'
    assert service.account()['reserved_usd']=='200.000000'
    service.sync()
    assert service.order_detail(row['id'])['status']=='cancelled'
    assert Decimal(service.account()['reserved_usd'])==0

def test_source_and_limits_and_stop(env):
    db,settings,broker,service=env
    with pytest.raises(ExecutionBlockedError,match='source'):
        service.submit(order(),ExecutionContext('bad'))
    service.set_entry_stop(True,'admin')
    assert not service.preview(order(),context())['allowed']
    with pytest.raises(ExecutionBlockedError,match='entry_stop'):
        service.submit(order(),context())

def test_unknown_submission_never_retries_or_releases_reservation(env,monkeypatch):
    db,settings,broker,service=env
    calls=[]
    def timeout(order):
        calls.append(order)
        raise TimeoutError('secret must not appear')
    monkeypatch.setattr(broker,'place_order',timeout)
    row=service.submit(order(),context())
    assert row['status']=='unknown'
    assert service.submit(order(),context())['id']==row['id']
    service.sync()
    assert len(calls)==1
    assert Decimal(service.account()['reserved_usd'])>=200
    assert 'secret' not in str(service.events())
    assert not service.status()['buy_allowed']

def test_request_conflict_is_rejected(env):
    *_,service=env
    service.submit(order(),context())
    with pytest.raises(ExecutionBlockedError,match='request_id_conflict'):
        service.submit(order(quantity=3),context())

def test_stale_quote_and_missing_limits_are_closed(env):
    from datetime import timedelta
    db,settings,broker,service=env
    broker.set_quote('AAPL','NASD',Decimal('100'),as_of=NOW-timedelta(seconds=31))
    assert service.preview(order(),context())['block_reasons']==['observation_stale']
    broker.set_quote('AAPL','NASD',Decimal('100'))
    settings.maps_us_max_daily_loss_usd=None
    assert service.preview(order(),context())['block_reasons']==['usd_limits_missing']

def test_global_dry_run_blocks_us_but_domestic_enabled_not_required(env):
    db,settings,broker,service=env
    settings.maps_live_trading_enabled=False
    assert service.preview(order(),context())['allowed']
    settings.maps_dry_run=True
    assert service.preview(order(),context())['block_reasons']==['dry_run']

def test_unexplained_cash_and_shares_block_new_entry(env):
    from maps.common.models import USMockTruth
    db,settings,broker,service=env
    truth=broker.db.get(USMockTruth,broker.key)
    truth.cash+=Decimal('1')
    broker.db.commit()
    assert service.sync()['block_reasons']==['unexplained_cash_movement']
    assert not service.preview(order(),context())['allowed']
    # Re-running sync must not silently adopt the changed baseline.
    assert 'unexplained_cash_movement' in service.sync()['block_reasons']

def test_account_and_positions_are_persisted_reads(env,monkeypatch):
    db,settings,broker,service=env
    def fail():raise AssertionError('screen read queried broker')
    monkeypatch.setattr(broker,'get_execution_snapshot',fail)
    assert service.account()['cash_usd']=='10000.000000'
    assert service.positions()==[]

def test_registered_strategy_shared_path_and_instance_ownership(env):
    db,settings,broker,service=env
    from datetime import timedelta
    def policy(order,ctx,db):return ctx.strategy_version=='test-v1' and ctx.source_id==42
    service.source_policies['test-producer']=policy
    ctx=ExecutionContext('strategy1',source='test-producer',source_id=42,instance_id='test1',strategy_version='test-v1',decision_at=NOW,data_as_of=NOW,valid_until=NOW+timedelta(seconds=30))
    buy=order();buy.strategy_id='test-strategy'
    row=service.submit(buy,ctx)
    broker.fill(row['broker_order_id'],2,Decimal('100'))
    service.sync()
    with pytest.raises(ExecutionBlockedError,match='owned_shares_insufficient'):
        service.submit(order(OrderSide.SELL,1),context('steal',sell_consent=True))
    assert service.positions()[0]['source']=='test1'

def test_terminal_regression_does_not_release_or_reapply_inventory(env,monkeypatch):
    from dataclasses import replace
    db,settings,broker,service=env
    row=service.submit(order(),context())
    broker.fill(row['broker_order_id'],2,Decimal('100'))
    service.sync()
    original=broker.get_order_history
    def regressed(start,end):
        from maps.execution.broker_adapter import OrderStatus
        return [replace(r,status=OrderStatus.PENDING) for r in original(start,end)]
    monkeypatch.setattr(broker,'get_order_history',regressed)
    assert 'terminal_state_regression' in service.sync()['block_reasons']
    assert service.order_detail(row['id'])['status']=='filled'
    assert service.positions()[0]['owned_quantity']==2

def test_fees_reserved_separately_from_confirmed_expense(env):
    db,settings,broker,service=env
    settings.maps_us_fee_buffer_rate=Decimal('.01')
    service.submit(order(),context())
    assert Decimal(service.account()['reserved_usd'])==Decimal('202')


def test_duplicate_cancel_does_not_repeat_broker_request(env,monkeypatch):
    db,settings,broker,service=env
    row=service.submit(order(),context())
    original=broker.cancel_order;calls=[]
    def counted(oid):calls.append(oid);return original(oid)
    monkeypatch.setattr(broker,'cancel_order',counted)
    service.cancel(row['id'],'admin')
    service.cancel(row['id'],'admin')
    assert len(calls)==1
    assert not service.order_detail(row['id'])['cancellable']

def test_quantity_truth_with_pending_costs_allows_authorized_exit(env,monkeypatch):
    from dataclasses import replace
    db,settings,broker,service=env
    row=service.submit(order(),context())
    broker.fill(row['broker_order_id'],2,Decimal('100'))
    original=broker.get_order_history
    def unconfirmed(start,end):
        return [replace(r,commission=None,tax=None,costs_complete=False) for r in original(start,end)]
    monkeypatch.setattr(broker,'get_order_history',unconfirmed)
    service.sync()
    assert 'costs_unconfirmed' in service.status()['block_reasons']
    assert service.preview(order(OrderSide.SELL,1),context('exit',sell_consent=True))['allowed']

def test_single_account_reservations_cover_independent_instances(env):
    db,settings,broker,service=env
    settings.maps_us_max_exposure_usd=Decimal('300')
    service.submit(order(),context())
    assert service.preview(order(),context('two'))['block_reasons']==['exposure_limit']


def test_buy_requires_fresh_marks_for_other_held_symbols(env,monkeypatch):
    from dataclasses import replace
    from datetime import timedelta
    db,settings,broker,service=env
    first=service.submit(order(),context())
    broker.fill(first['broker_order_id'],2,Decimal('100'))
    service.sync()
    settings.maps_us_allowed_symbols='AAPL:NASD,MSFT:NASD'
    broker.set_quote('MSFT','NASD',Decimal('100'))
    original=broker.get_quote
    def quote(ticker,exchange):
        result=original(ticker,exchange)
        return replace(result,as_of=NOW-timedelta(minutes=5)) if ticker=='AAPL' else result
    monkeypatch.setattr(broker,'get_quote',quote)
    other=replace(order(),ticker='MSFT')
    assert not service.preview(other,context('other'))['allowed']
    with pytest.raises(ExecutionBlockedError):
        service.submit(other,context('other'))
    assert len(service.orders())==1

def test_loss_latch_survives_restart_and_entry_resume(env):
    db,settings,broker,service=env
    row=service.submit(order(),context())
    broker.fill(row['broker_order_id'],2,Decimal('100'))
    service.sync()
    broker.set_quote('AAPL','NASD',Decimal('40'))
    assert 'daily_loss_limit' in service.sync()['block_reasons']
    broker.set_quote('AAPL','NASD',Decimal('100'))
    restarted=USExecutionService(db,broker,settings=settings,now=lambda:NOW)
    restarted.sync()
    restarted.set_entry_stop(False,'admin')
    assert 'daily_loss_limit' in restarted.status()['block_reasons']


def test_preflight_reconciles_fresh_cash_before_submit(env):
    from maps.common.models import USMockTruth
    db,settings,broker,service=env
    truth=broker.db.get(USMockTruth,broker.key)
    truth.cash+=Decimal('1')
    broker.db.commit()
    assert service.preview(order(),context())['block_reasons']==['unexplained_cash_movement']
    with pytest.raises(ExecutionBlockedError,match='unexplained_cash_movement'):
        service.submit(order(),context())
    assert service.orders()==[]


def test_cancelled_zero_open_residual_releases_only_unfilled_reservation(env,monkeypatch):
    from dataclasses import replace
    from maps.execution.broker_adapter import OrderStatus
    db,settings,broker,service=env
    row=service.submit(order(),context())
    broker.fill(row['broker_order_id'],1,Decimal('100'))
    service.sync()
    service.cancel(row['id'],'admin')
    original=broker.get_order_history
    monkeypatch.setattr(broker,'get_order_history',lambda start,end:[replace(r,remaining_quantity=0) if r.status==OrderStatus.CANCELLED else r for r in original(start,end)])
    assert service.sync()['block_reasons']==[]
    assert service.order_detail(row['id'])['status']=='cancelled'
    assert service.positions()[0]['owned_quantity']==1
    assert Decimal(service.account()['reserved_usd'])==0

def test_concurrent_connections_cannot_reuse_cash(env):
    from concurrent.futures import ThreadPoolExecutor
    db,settings,broker,service=env
    settings.maps_us_max_exposure_usd=Decimal('300')
    engine=db.get_bind()
    def submit_one(key):
        with Session(engine) as local:
            local_broker=USMockBroker(local,settings=settings,now=lambda:NOW)
            local_service=USExecutionService(local,local_broker,settings=settings,now=lambda:NOW)
            try:
                return local_service.submit(order(),context(key))['status']
            except ExecutionBlockedError as exc:
                return str(exc)
            finally:
                local_broker.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit_one,['concurrent-one','concurrent-two']))
    assert sorted(results)==['exposure_limit','pending']
