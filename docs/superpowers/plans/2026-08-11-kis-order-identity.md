# KIS Order Identity Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent reused KIS ODNO values from corrupting historical order rows and restore the missing 041830 entry audit record and stop-loss monitoring.

**Architecture:** Keep the existing unique `order_log.order_id`, but store KIS orders under a deterministic broker/account/date namespace. Preserve raw ODNO interoperability at the KIS adapter boundary and require identity plus ticker/side agreement during broker synchronization.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI scheduler, pytest, PostgreSQL, systemd, KIS OpenAPI.

## Global Constraints

- Do not add a database migration or remove the existing unique constraint.
- Never persist the raw KIS account number in `order_log.order_id`; use an eight-character SHA-256 fingerprint.
- Treat KIS `submitted_at` without timezone information as KST.
- Preserve raw order IDs for non-KIS brokers and legacy KIS cancellation calls.
- Refuse a sync update when ticker or side differs; never repair mismatches by overwriting identity fields.
- Back up production PostgreSQL before editing order rows.
- Preserve the user's existing uncommitted `HANDOFF.md` changes.

---

### Task 1: Deterministic KIS audit identity

**Files:**
- Modify: `maps/execution/broker_adapter.py`
- Modify: `maps/execution/order_manager.py`
- Test: `tests/test_order_manager.py`

**Interfaces:**
- Produces: `order_log_id(raw_order_id: str, *, broker: str, account_no: str, submitted_at: datetime.datetime) -> str`
- Produces: `raw_broker_order_id(order_id: str) -> str`
- Consumes: `MapsSettings.maps_broker_mode`, `MapsSettings.kis_account_no`, `OrderResult.submitted_at`

- [ ] **Step 1: Write failing identity tests**

```python
def test_kis_order_log_id_includes_account_and_kst_day():
    first = order_log_id("0000000755", broker="kis", account_no="11111111-01", submitted_at=dt.datetime(2026, 8, 6, 8, 55))
    later = order_log_id("0000000755", broker="kis", account_no="11111111-01", submitted_at=dt.datetime(2026, 8, 10, 8, 55))
    assert first != later
    assert first.endswith(":20260806:0000000755")
    assert "11111111" not in first

def test_non_kis_order_log_id_is_unchanged():
    assert order_log_id("mock-1", broker="mock", account_no="", submitted_at=dt.datetime(2026, 8, 10)) == "mock-1"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_order_manager.py -k "order_log_id" -v`

Expected: collection/import failure because the helpers do not exist.

- [ ] **Step 3: Implement the minimal helpers and transform KIS submit results**

```python
def order_log_id(raw_order_id: str, *, broker: str, account_no: str, submitted_at: datetime.datetime) -> str:
    if broker != "kis" or raw_order_id.startswith("kis:"):
        return raw_order_id
    submitted = submitted_at if submitted_at.tzinfo else submitted_at.replace(tzinfo=_KST)
    day = submitted.astimezone(_KST).date()
    account_key = hashlib.sha256(account_no.encode("utf-8")).hexdigest()[:8]
    return f"kis:{account_key}:{day:%Y%m%d}:{raw_order_id}"

def raw_broker_order_id(order_id: str) -> str:
    return order_id.rsplit(":", 1)[-1] if order_id.startswith("kis:") else order_id
```

Use `dataclasses.replace()` in `OrderManager._submit()` so KIS results are logged and returned with the internal ID while other brokers remain unchanged.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_order_manager.py -k "order_log_id or transient_broker_error" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add maps/execution/broker_adapter.py maps/execution/order_manager.py tests/test_order_manager.py
git commit -m "fix: namespace KIS order audit IDs"
```

### Task 2: Sync reused ODNO without corrupting history

**Files:**
- Modify: `maps/execution/order_manager.py`
- Test: `tests/test_order_manager_sync.py`

**Interfaces:**
- Consumes: `order_log_id(...)`, `kst_day_bounds_utc(...)`, `OrderResult.ticker`, `OrderResult.side`
- Produces: strict current-day order lookup and mismatch refusal in `OrderManager.sync_broker_state()`

- [ ] **Step 1: Write the incident regression test**

```python
def test_sync_reused_kis_order_id_does_not_update_prior_day_different_ticker(db, monkeypatch):
    old = OrderLog(order_id="0000000755", strategy_id="ath_breakout_v1", ticker="051160", side="buy", qty=427, order_price=57200, fill_qty=0, status="expired", broker="kis", mode="mock", created_at=dt.datetime(2026, 8, 5, 23, 55))
    db.add(old)
    db.commit()
    broker.get_daily_order_results.return_value = [OrderResult(order_id="0000000755", strategy_id="", ticker="041830", side=OrderSide.BUY, status=OrderStatus.FILLED, filled_quantity=35, avg_price=69200, submitted_at=dt.datetime(2026, 8, 10, 8, 55))]
    manager.sync_broker_state()
    db.refresh(old)
    assert (old.ticker, old.status, old.fill_qty, old.fill_price) == ("051160", "expired", 0, None)
    assert db.query(OrderLog).filter(OrderLog.ticker == "041830").one().order_id.endswith(":20260810:0000000755")
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_order_manager_sync.py::test_sync_reused_kis_order_id_does_not_update_prior_day_different_ticker -v`

Expected: FAIL because the old `051160` row is updated or the new row cannot be inserted.

- [ ] **Step 3: Implement strict lookup**

For each broker result, compute its internal ID. Query that ID first. For legacy raw IDs query only the result's KST day plus ticker and side. Before any field update, compare `row.ticker` and `row.side`; on mismatch log an error, increment `sync_errors`, and skip the row. Insert unmatched external orders under the internal ID. Track internal IDs in `broker_result_ids` for position fallback exclusion.

- [ ] **Step 4: Add and run mismatch-defense test**

```python
def test_sync_refuses_identity_match_with_ticker_mismatch(db):
    # Seed the computed current-day ID with ticker 051160, return 041830.
    # Assert no mutation, updated_orders == 0, sync_errors == 1.
```

Run: `pytest tests/test_order_manager_sync.py -k "reused_kis or identity_match" -v`

Expected: PASS.

- [ ] **Step 5: Run the order sync suite and commit**

Run: `pytest tests/test_order_manager_sync.py tests/test_sync_fill_reconciliation.py -v`

```powershell
git add maps/execution/order_manager.py tests/test_order_manager_sync.py
git commit -m "fix: validate KIS fills before syncing"
```

### Task 3: Preserve KIS cancellation behavior

**Files:**
- Modify: `maps/execution/kis_adapter.py`
- Test: `tests/test_kis_adapter.py`

**Interfaces:**
- Consumes: `raw_broker_order_id(order_id: str) -> str`
- Produces: `KISAdapter.cancel_order()` accepts both internal IDs and raw ODNO values

- [ ] **Step 1: Write the failing cancellation test**

```python
def test_cancel_order_extracts_raw_odno_from_internal_id(broker, http):
    broker.cancel_order("kis:deadbeef:20260810:0000000755")
    assert http.last_json["ORGN_ODNO"] == "0000000755"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_kis_adapter.py -k "extracts_raw" -v`

Expected: FAIL because the composite value is sent unchanged.

- [ ] **Step 3: Strip the namespace at the adapter boundary**

Call `raw_broker_order_id(order_id)` before constructing the KIS cancel request body. Do not alter `PendingOrder.order_id`, which is already raw broker data.

- [ ] **Step 4: Verify GREEN and regression tests**

Run: `pytest tests/test_kis_adapter.py -k "cancel_order" -v`

Expected: PASS for raw and internal IDs.

- [ ] **Step 5: Commit**

```powershell
git add maps/execution/kis_adapter.py tests/test_kis_adapter.py
git commit -m "fix: unwrap KIS IDs for cancellation"
```

### Task 4: Verify, recover production data, and deploy

**Files:**
- Modify: `HANDOFF.md`
- Production backup: `/opt/maps/backups/maps-pre-order-identity-20260811-<time>.dump`

**Interfaces:**
- Consumes: the three implementation commits and production `MAPS_DB_URL`
- Produces: restored `051160` audit row, correct `041830` filled entry, active stop monitoring, deployed service

- [ ] **Step 1: Run focused and full verification**

Run: `pytest tests/test_order_manager.py tests/test_order_manager_sync.py tests/test_sync_fill_reconciliation.py tests/test_kis_adapter.py -v`

Run: `pytest --tb=short`

Expected: all tests pass with no new warnings or errors.

- [ ] **Step 2: Back up PostgreSQL without printing credentials**

Use the application settings loader to pass the DB URL directly to `pg_dump -Fc`; create `/opt/maps/backups` with mode `700`, the dump with mode `600`, and verify a non-zero size.

- [ ] **Step 3: Repair both rows in one transaction**

Compute the internal ID with the production KIS account and execute parameterized SQL:

```sql
UPDATE order_log
SET status='expired', fill_qty=0, fill_price=NULL
WHERE id=53 AND order_id='0000000755' AND ticker='051160';

INSERT INTO order_log
(order_id,strategy_id,ticker,side,qty,order_price,fill_price,fill_qty,status,broker,mode,exit_reason,atr14,created_at)
VALUES
(:internal_id,'ath_breakout_v1','041830','buy',35,71600,69200,35,'filled','kis','mock',NULL,5638.281459,TIMESTAMP '2026-08-09 23:55:24');
```

Require exactly one updated and one inserted row before commit.

- [ ] **Step 4: Commit the handoff, push, and deploy**

Update the incident section with the backup path, repair result, tests, commits, and deployment verification. Stage only tracked intended files, push without force, then deploy with `git pull`, `sudo systemctl restart maps`, and `systemctl is-active maps`.

- [ ] **Step 5: Verify live state**

Check `/health` internally and externally, `alembic current`, the two repaired DB rows, actual KIS `041830` position, broker sync `sync_errors`, and scheduler output showing `skipped_sell_orders=0`. Confirm the effective stop price is 55,100 won without submitting a sell unless the market price actually crosses the rule.

- [ ] **Step 6: Remove temporary order-cycle mitigation**

If the 08:55 cycle was deliberately skipped, restore normal service scheduling after the window and verify the next order run remains scheduled for the following KRX trading day.
