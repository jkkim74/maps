"""Append-only paper actual-cost evidence and idempotency receipts."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from maps.common.exceptions import DataQualityError
from maps.common.models import PaperCostEvidence, PaperCostSettlement


def make_row(model):
    if model is PaperCostEvidence:
        return model(account_key="paper:test", environment="paper", kind="balance",
                     ref_date=dt.date(2026, 10, 12), observed_at=dt.datetime(2026, 10, 12),
                     amount=Decimal("160"), fingerprint="a" * 64, payload={})
    return model(account_key="paper:test", idempotency_key="key", request_hash="b" * 64,
                 actor="operator", payload={}, result={})


@pytest.mark.parametrize("model", [PaperCostEvidence, PaperCostSettlement])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_paper_cost_history_rejects_mutation(db, model, operation):
    row = make_row(model)
    db.add(row)
    db.commit()
    if operation == "update":
        row.payload = {"changed": True}
    else:
        db.delete(row)
    with pytest.raises(DataQualityError, match="immutable_paper_cost_history"):
        db.flush()
    db.rollback()
    assert db.get(model, row.id).payload == {}


@pytest.mark.parametrize("model", [PaperCostEvidence, PaperCostSettlement])
def test_paper_cost_duplicate_identity_rejected(db, model):
    db.add(make_row(model))
    db.commit()
    db.add(make_row(model))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
