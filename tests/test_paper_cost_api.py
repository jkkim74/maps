"""HTTP boundary for evidence-backed paper settlement, without broker access."""
import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.api import risk
from maps.api.deps import get_db
from maps.common.db import Base
from maps.common.models import AccountObservation, ExecutionAccountState, PaperCostSettlement, PaperCostEvidence
from maps.common.settings import MapsSettings
from maps.execution.safety import account_key, utcnow


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = MapsSettings(maps_broker_mode='kis', kis_real_trading=False, kis_account_no='12345678-01')
    now, key = utcnow(), account_key(settings)
    with factory() as db:
        base = AccountObservation(account_key=key, observed_at=now-dt.timedelta(days=1), ref_date=(now-dt.timedelta(days=1)).date(), nav=10000, cash=10000, evidence={'positions': {}, 'orders': {}, 'costs': '0'}, complete=True)
        current = AccountObservation(account_key=key, observed_at=now, ref_date=now.date(), nav=9900, cash=9900, evidence={'positions': {}, 'orders': {}, 'costs': '0'}, complete=False)
        db.add_all([base, current, ExecutionAccountState(account_key=key, environment='kis_paper', status='BLOCKED', block_reasons=['paper_costs_missing'], checked_at=now, version=3, killed=False)])
        db.commit()
    identity = SimpleNamespace(username='operator', id=7, is_admin=True)
    app = FastAPI()
    @app.middleware('http')
    async def identify(request: Request, call_next):
        request.state.user = identity
        return await call_next(request)
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.include_router(risk.router)
    monkeypatch.setattr(risk, 'get_settings', lambda: settings)
    def forbidden(*args, **kwargs):
        raise AssertionError('Settlement API must never access a broker')
    monkeypatch.setattr(risk, 'get_broker', forbidden)
    body = {'reason': 'Broker statement verified', 'items': [{'scope': 'account', 'period_start': str((now-dt.timedelta(days=1)).date()), 'period_end': str(now.date()), 'amount': '100', 'source_url': 'https://broker.example/statement', 'document_hash': 'a'*64}]}
    yield SimpleNamespace(client=TestClient(app), body=body, identity=identity, factory=factory, settings=settings)
    engine.dispose()


def test_preview_commit_and_replay_do_not_execute_orders(api):
    overview = api.client.get('/api/v1/risk/execution-safety')
    assert overview.status_code == 200
    assert overview.json()['paper_costs']['supported'] is True
    preview = api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body)
    assert preview.status_code == 200
    with api.factory() as db:
        assert db.query(PaperCostSettlement).count() == 0
        assert db.query(PaperCostEvidence).count() == 0
    payload = {**api.body, 'fingerprint': preview.json()['fingerprint'], 'version': preview.json()['version'], 'idempotency_key': 'receipt-1'}
    receipt = api.client.post('/api/v1/risk/execution-safety/settlements/commit', json=payload)
    assert receipt.status_code == 200
    assert api.client.post('/api/v1/risk/execution-safety/settlements/commit', json=payload).json() == receipt.json()
    with api.factory() as db:
        row = db.query(PaperCostSettlement).one()
        assert row.actor == 'operator'
        assert row.payload['request']['items'][0]['period_start'] == api.body['items'][0]['period_start']
        assert db.get(ExecutionAccountState, account_key(api.settings)).status == 'BLOCKED'


@pytest.mark.parametrize('change', [{'amount': '-1'}, {'amount': 'NaN'}, {'amount': '0.00001'}, {'amount': '100000000000000000000'}, {'source_url': 'http://broker.example'}, {'source_url': 'https://user:pass@broker.example'}, {'document_hash': 'bad'}, {'included_adjustment_ids': [1, 1]}, {'included_settlement_ids': [0]}, {'account_key': 'other'}])
def test_statement_schema_rejects_invalid_inputs(api, change):
    api.body['items'][0].update(change)
    assert api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body).status_code == 422


@pytest.mark.parametrize('path', ['preview', 'commit'])
def test_admin_required(api, path):
    api.identity.is_admin = False
    payload = {**api.body, **({'fingerprint': 'a'*64, 'version': 3, 'idempotency_key': 'x'} if path == 'commit' else {})}
    assert api.client.post('/api/v1/risk/execution-safety/settlements/'+path, json=payload).status_code == 403


def test_nonpaper_and_semantic_conflicts_are_safe(api):
    api.settings.kis_real_trading = True
    response = api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body)
    assert response.status_code == 409
    assert response.json()['detail'] == 'Paper cost settlement rejected'


def test_changed_account_rejects_preview_commit_without_partial_writes(api):
    preview = api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body).json()
    payload = {**api.body, 'fingerprint': preview['fingerprint'], 'version': preview['version'], 'idempotency_key': 'stale'}
    with api.factory() as db:
        db.query(AccountObservation).filter_by(complete=False).one().cash -= 10
        db.commit()
    response = api.client.post('/api/v1/risk/execution-safety/settlements/commit', json=payload)
    assert response.status_code == 409
    with api.factory() as db:
        assert db.query(PaperCostEvidence).count() == 0
        assert db.query(PaperCostSettlement).count() == 0


@pytest.mark.parametrize('change', [{'items': []}, {'reason': 'short'}, {'account_key': 'other'}])
def test_request_bounds_and_current_account_scope(api, change):
    api.body.update(change)
    assert api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body).status_code == 422


@pytest.mark.parametrize('change', [{'fingerprint': 'short'}, {'version': 0}, {'idempotency_key': ''}])
def test_commit_metadata_bounds(api, change):
    payload = {**api.body, 'fingerprint': 'a'*64, 'version': 3, 'idempotency_key': 'x', **change}
    assert api.client.post('/api/v1/risk/execution-safety/settlements/commit', json=payload).status_code == 422


def test_rejected_fujimoto_cost_correction_returns_conflict_and_rolls_back(api) -> None:
    """An exact-order ledger rejection is a conflict, never a partial account settlement."""
    from tests.test_fujimoto_execution import seeded, linked_intent
    from maps.fujimoto.repository import FillEvent
    from maps.common.models import FujimotoOrder

    with api.factory() as db:
        repo, cycle, order = seeded(db, api.settings)
        intent, log = linked_intent(db, cycle, order)
        repo.bind_intent(order.id, intent.id, 'broker')
        current = db.query(AccountObservation).filter_by(complete=False).one()
        repo.apply_fill(FillEvent(order.id, cycle.account_key, intent.id, 2, 2000, 20, 0,
                                 'FILLED', current.ref_date))
        intent.status = 'FILLED'; log.status = 'filled'; log.fill_qty = 2
        current.cash -= 2000
        current.evidence = {**current.evidence, 'positions': {'AAA': {'qty': 2}},
            'orders': {'broker': {'ticker': 'AAA', 'side': 'buy', 'qty': 2, 'notional': '2000'}}}
        order_id = order.id
        db.commit()
    api.body['items'].append({**api.body['items'][0], 'scope': 'order', 'broker_order_id': 'broker',
        'amount': '10', 'gross': '2000', 'fees': '10', 'tax': '0'})
    preview = api.client.post('/api/v1/risk/execution-safety/settlements/preview', json=api.body)
    assert preview.status_code == 200
    payload = {**api.body, 'fingerprint': preview.json()['fingerprint'],
               'version': preview.json()['version'], 'idempotency_key': 'decreasing-order-cost'}
    response = api.client.post('/api/v1/risk/execution-safety/settlements/commit', json=payload)
    assert response.status_code == 409
    with api.factory() as db:
        assert db.query(PaperCostSettlement).count() == db.query(PaperCostEvidence).count() == 0
        assert db.get(FujimotoOrder, order_id).fees == 20
