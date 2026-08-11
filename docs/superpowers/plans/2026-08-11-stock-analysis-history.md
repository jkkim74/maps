# Stock Analysis History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every completed stock analysis as an immutable history row, refresh only its current-price overlay when opened, restore the saved detail for strategy-trade testing, and remove the split-layout and blind-budget UX defects.

**Architecture:** Add a dedicated `StockAnalysisHistory` model instead of mixing analysis snapshots with executable `AnalysisPick` state. A focused history service owns immutable snapshot persistence and mutable quote overlays; the stock-analysis API exposes list/detail/refresh endpoints and records successful analyses. Refactor the existing trade-plan validator so a budget-free limits endpoint and preview/arm share one server calculation, then make the web UI restore history and calculate limits automatically.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, PostgreSQL/SQLite, Jinja2, browser JavaScript, CSS Grid, pytest.

## Global Constraints

- Every explicit reanalysis appends one row; it never updates or deduplicates an older analysis.
- `snapshot`, `narrative`, `trade_plan`, `recommendation`, and `analyzed_price` are immutable after insert.
- Price refresh may update only `latest_price`, `latest_reference_close`, `latest_price_source`, and `price_refreshed_at`.
- History detail must render even when current-price refresh fails.
- Price refresh must never regenerate RSI, MACD, moving averages, financial data, AI narrative, entries, target, or stop.
- The stored structured `trade_plan`, not parsed narrative text, supplies strategy-trade prices.
- `AnalysisPick` remains the only persisted execution/watch state; opening history must not create one.
- Final `/arm-plan` must re-read broker/account/gates and may reject a previously valid preview.
- Do not add history deletion, retention, filtering, tagging, mobile authoring, or forced live reanalysis.
- No new third-party dependency.
- Follow TDD for every task: add the failing test, run RED, implement the minimum, run GREEN, then commit.
- Do not push or deploy without a separate user request. Deployment requires a PostgreSQL backup and `alembic upgrade head`.

---

### Task 1: Add the immutable stock-analysis history schema

**Files:**
- Modify: `maps/common/models.py:702`
- Create: `alembic/versions/0022_stock_analysis_history.py`
- Modify: `tests/test_migrations.py:13`
- Create: `tests/test_stock_analysis_history_model.py`

**Interfaces:**
- Produces: `maps.common.models.StockAnalysisHistory`
- Produces: Alembic head `0022_stock_analysis_history`
- The model fields must match section 4 of the approved design exactly.

- [ ] **Step 1: Write the failing model test**

```python
def test_same_ticker_analysis_rows_append_without_overwrite(db) -> None:
    import maps.common.models as models

    assert hasattr(models, "StockAnalysisHistory")
    first = models.StockAnalysisHistory(
        ticker="005930", name="삼성전자", market="KOSPI",
        ref_date=datetime.date(2026, 8, 11),
        snapshot={"기술적분석": {"현재가": 70_000}},
        narrative="첫 분석", trade_plan={"recommendation": "WATCH"},
        recommendation="WATCH", analyzed_price=70_000,
    )
    second = models.StockAnalysisHistory(
        ticker="005930", name="삼성전자", market="KOSPI",
        ref_date=datetime.date(2026, 8, 11),
        snapshot={"기술적분석": {"현재가": 71_000}},
        narrative="두 번째 분석", trade_plan={"recommendation": "BUY"},
        recommendation="BUY", analyzed_price=71_000,
    )
    db.add_all([first, second])
    db.commit()

    rows = db.query(models.StockAnalysisHistory).order_by(models.StockAnalysisHistory.id).all()
    assert [row.analyzed_price for row in rows] == [70_000, 71_000]
```

- [ ] **Step 2: Extend the fresh-migration test and verify RED**

Change `test_fresh_database_reaches_split_plan_schema` to expect revision
`0022_stock_analysis_history` and assert the table/columns:

```python
assert revision == "0022_stock_analysis_history"
assert {
    "snapshot", "narrative", "trade_plan", "recommendation",
    "analyzed_price", "latest_price", "latest_reference_close",
    "latest_price_source", "price_refreshed_at",
} <= {c["name"] for c in inspector.get_columns("stock_analysis_history")}
```

Run:

```powershell
python -m pytest tests/test_stock_analysis_history_model.py tests/test_migrations.py -q
```

Expected: FAIL because `StockAnalysisHistory` and revision `0022_stock_analysis_history` do not exist.

- [ ] **Step 3: Implement the model**

Add after `AnalysisRun` or immediately before it in `models.py`:

```python
class StockAnalysisHistory(Base):
    __tablename__ = "stock_analysis_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trade_plan: Mapped[dict] = mapped_column(JSON, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    analyzed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_reference_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price_refreshed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: Implement the migration**

Create revision `0022_stock_analysis_history`, `down_revision="0021_analysis_pick_split_plan"`.
Create the table with the fields above and indexes named:

```python
op.create_index("ix_stock_analysis_history_created_at", "stock_analysis_history", ["created_at"])
op.create_index("ix_stock_analysis_history_ticker", "stock_analysis_history", ["ticker"])
op.create_index("ix_stock_analysis_history_ref_date", "stock_analysis_history", ["ref_date"])
```

The downgrade drops those indexes and then the table. The revision ID is under the repository's 32-character limit.

- [ ] **Step 5: Run GREEN and commit**

```powershell
python -m pytest tests/test_stock_analysis_history_model.py tests/test_migrations.py -q
git diff --check
git add maps/common/models.py alembic/versions/0022_stock_analysis_history.py tests/test_stock_analysis_history_model.py tests/test_migrations.py
git commit -m "feat: add stock analysis history schema"
```

---

### Task 2: Add history persistence, list/detail, and price-overlay APIs

**Files:**
- Create: `maps/stock_analysis/history.py`
- Modify: `maps/api/schemas.py:849`
- Modify: `maps/api/stock_analysis.py:19`
- Create: `tests/test_stock_analysis_history_api.py`

**Interfaces:**
- Produces: `save_analysis_history(db, result, narrative, trade_plan) -> StockAnalysisHistory`
- Produces: `refresh_analysis_price(db, history) -> StockAnalysisPriceOverlay`
- Produces: `GET /api/v1/stock-analysis/history`
- Produces: `GET /api/v1/stock-analysis/history/{history_id}`
- Produces: `POST /api/v1/stock-analysis/history/{history_id}/refresh-price`
- Consumes: `HistoricalOHLCV`, configured broker `get_current_prices()`, and `StockAnalysisHistory`.

- [ ] **Step 1: Create the API fixture and failing append/list/detail tests**

Create a StaticPool SQLite client fixture with `app.dependency_overrides[get_db]`, matching
`tests/test_analysis_picks_api.py`. Add:

```python
def test_save_appends_same_ticker_and_list_is_latest_first(client) -> None:
    first = _seed_history(client, analyzed_price=70_000, narrative="first")
    second = _seed_history(client, analyzed_price=71_000, narrative="second")

    body = client.get("/api/v1/stock-analysis/history").json()
    assert [item["id"] for item in body["items"]] == [second, first]
    assert "snapshot" not in body["items"][0]
    assert "narrative" not in body["items"][0]

    detail = client.get(f"/api/v1/stock-analysis/history/{first}").json()
    assert detail["snapshot"]["기술적분석"]["현재가"] == 70_000
    assert detail["narrative"] == "first"
    assert detail["trade_plan"]["entries"] == [69_000, 67_000, 65_000]
```

Use `_seed_history()` only to insert rows with `client.session_factory`; do not call production save code from test setup.

Add a separate mapping test that calls the real service with a session from `client.session_factory`:

```python
def test_save_service_maps_analysis_and_plan_without_deduplication(client) -> None:
    from maps.stock_analysis.history import save_analysis_history
    with client.session_factory() as db:
        first = save_analysis_history(
            db, result=_analysis_result(70_000), narrative="first",
            trade_plan=_trade_plan("WATCH"),
        )
        second = save_analysis_history(
            db, result=_analysis_result(71_000), narrative="second",
            trade_plan=_trade_plan("BUY"),
        )
        assert first.id != second.id
        assert first.snapshot["기술적분석"]["현재가"] == 70_000
        assert second.recommendation == "BUY"
```

- [ ] **Step 2: Write failing immutable-refresh tests**

```python
def test_refresh_updates_only_quote_overlay(client, monkeypatch) -> None:
    history_id = _seed_history(client)
    before = client.get(f"/api/v1/stock-analysis/history/{history_id}").json()
    import maps.stock_analysis.history as service
    monkeypatch.setattr(service, "get_broker", lambda: _QuoteBroker(72_000))
    _seed_ohlcv(client, closes=[68_000, 70_000])

    refreshed = client.post(
        f"/api/v1/stock-analysis/history/{history_id}/refresh-price"
    )

    assert refreshed.status_code == 200
    assert refreshed.json()["current_price"] == 72_000
    assert refreshed.json()["reference_close"] == 70_000
    after = client.get(f"/api/v1/stock-analysis/history/{history_id}").json()
    assert after["snapshot"] == before["snapshot"]
    assert after["narrative"] == before["narrative"]
    assert after["trade_plan"] == before["trade_plan"]
```

Add separate tests for broker failure → latest OHLCV fallback, and no broker/no OHLCV → 503 with unchanged overlay.

- [ ] **Step 3: Run RED**

```powershell
python -m pytest tests/test_stock_analysis_history_api.py -q
```

Expected: FAIL with 404 routes and missing `maps.stock_analysis.history`.

- [ ] **Step 4: Add response schemas**

Add typed schemas in `maps/api/schemas.py`:

```python
class StockAnalysisHistoryListItem(BaseModel):
    id: int
    created_at: datetime.datetime
    ticker: str
    name: str
    market: str | None = None
    ref_date: datetime.date
    recommendation: str | None = None
    analyzed_price: float | None = None
    latest_price: float | None = None
    latest_price_source: str | None = None
    price_refreshed_at: datetime.datetime | None = None

class StockAnalysisHistoryListResponse(BaseModel):
    total: int
    items: list[StockAnalysisHistoryListItem]

class StockAnalysisHistoryDetail(StockAnalysisHistoryListItem):
    snapshot: dict[str, Any]
    narrative: str
    trade_plan: dict[str, Any]
    latest_reference_close: float | None = None

class StockAnalysisPriceOverlay(BaseModel):
    history_id: int
    current_price: float
    reference_close: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    source: str
    refreshed_at: datetime.datetime
    plan_distances: dict[str, dict[str, float]] = {}
```

Use `Field(default_factory=dict)` for `plan_distances`, not a shared mutable literal.

- [ ] **Step 5: Implement the focused history service**

In `maps/stock_analysis/history.py` implement:

```python
class CurrentPriceUnavailable(RuntimeError):
    pass

def save_analysis_history(
    db: Session, *, result: Mapping[str, Any], narrative: str,
    trade_plan: Mapping[str, Any],
) -> StockAnalysisHistory:
    technical = result.get("기술적분석") or {}
    row = StockAnalysisHistory(
        ticker=str(result.get("종목코드") or "").strip(),
        name=str(result.get("종목명") or result.get("종목코드") or "").strip(),
        market=result.get("시장"),
        ref_date=datetime.date.fromisoformat(str(technical["기준일"])),
        snapshot=dict(result), narrative=narrative,
        trade_plan=dict(trade_plan),
        recommendation=trade_plan.get("recommendation"),
        analyzed_price=technical.get("현재가"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
```

Implement quote resolution as one broker call followed by a two-row OHLCV query ordered by date descending. Determine `today` in `Asia/Seoul`. If the broker returns a positive price and the newest OHLCV row is dated today, use the second row as reference close; if the newest row predates today, use the newest row as reference. If the broker has no price, use the newest OHLCV close as current and the second newest as reference.

Implement plan distances with this exact sign convention:

```python
distance_amount = level_price - current_price
distance_pct = round(distance_amount / current_price * 100.0, 2)
```

Positive means the market must rise to the level; negative means the level is below current price. Build keys `entry_1`, `entry_2`, `entry_3`, `target`, and `stop` only when the stored AI plan contains those prices.

- [ ] **Step 6: Add list/detail/refresh routes**

In `maps/api/stock_analysis.py`, inject `DbDep` into the three routes. Use `limit: int = Query(50, ge=1, le=200)` and `offset: int = Query(0, ge=0)`. Query total separately and list with `created_at.desc(), id.desc()`.

For refresh:

```python
try:
    return refresh_analysis_price(db, row)
except CurrentPriceUnavailable as exc:
    raise HTTPException(status_code=503, detail=str(exc)) from exc
```

Do not catch persistence/database exceptions as quote failures.

- [ ] **Step 7: Run GREEN and commit**

```powershell
python -m pytest tests/test_stock_analysis_history_api.py tests/test_analysis_picks_api.py -q
git diff --check
git add maps/stock_analysis/history.py maps/api/schemas.py maps/api/stock_analysis.py tests/test_stock_analysis_history_api.py
git commit -m "feat: add stock analysis history APIs"
```

---

### Task 3: Persist each completed analysis exactly once

**Files:**
- Modify: `maps/stock_analysis/history.py`
- Modify: `maps/api/stock_analysis.py:108-207`
- Modify: `tests/test_stock_analysis_api.py:203`

**Interfaces:**
- Produces: `save_analysis_history_with_new_session(result, narrative, trade_plan) -> int`
- SSE final event adds `history_id: int | None` and `history_error: str | None`.
- `POST /analyze` returns existing analysis keys plus `history_id` without changing the SSE data snapshot format.

- [ ] **Step 1: Write the failing SSE persistence test**

Extend the existing stream test with a fake saver:

```python
saved = []
def fake_save(result, narrative, trade_plan):
    saved.append((result, narrative, trade_plan))
    return 41

monkeypatch.setattr(api, "save_analysis_history_with_new_session", fake_save)
response = TestClient(app).get("/api/v1/stock-analysis/stream?ticker=005930")
final = next(event for event in _sse_events(response) if event.get("done"))

assert len(saved) == 1
assert saved[0][1] == "분석"
assert saved[0][2] == plan.model_dump(mode="json")
assert final["history_id"] == 41
assert final["history_error"] is None
```

Add a failure test where the saver raises `RuntimeError("db down")`; assert the completed analysis still returns and `history_error == "db down"`.

Add a non-streaming endpoint test by monkeypatching `analyze`, `generate_trade_plan`, and `save_analysis_history`; assert `POST /api/v1/stock-analysis/analyze` returns `history_id` and passes a snapshot without `history_id` to the saver.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_stock_analysis_api.py -q
```

Expected: FAIL because the saver hook and final-event fields do not exist.

- [ ] **Step 3: Add the short-lived session wrapper**

In the history service:

```python
def save_analysis_history_with_new_session(
    result: Mapping[str, Any], narrative: str, trade_plan: Mapping[str, Any]
) -> int:
    with SessionLocal() as db:
        return save_analysis_history(
            db, result=result, narrative=narrative, trade_plan=trade_plan
        ).id
```

The worker thread must not reuse a FastAPI dependency session across threads.

- [ ] **Step 4: Accumulate narrative and save before the final SSE event**

Initialize `narrative_parts: list[str] = []`. Append every chunk before queueing it. If AI is not configured, the joined narrative is `""`; if narrative generation throws, build `error_marker = f"\n\n[AI 분석 오류: {llm_err}]"`, append it, and queue that exact marker.

Call the saver once after narrative generation. Catch the persistence exception only around this call, log it with ticker context, and put its text in `history_error`. The final event must contain:

```python
"history_id": history_id,
"history_error": history_error,
```

- [ ] **Step 5: Persist the non-streaming endpoint**

Add `db: Session = DbDep` to `analyze_stock`, generate the same trade plan, call `save_analysis_history`, and return:

```python
return {**result, "history_id": history.id}
```

Do not nest `history_id` inside the stored snapshot.

- [ ] **Step 6: Run GREEN and commit**

```powershell
python -m pytest tests/test_stock_analysis_api.py tests/test_stock_analysis_history_api.py -q
git diff --check
git add maps/stock_analysis/history.py maps/api/stock_analysis.py tests/test_stock_analysis_api.py
git commit -m "feat: persist completed stock analyses"
```

---

### Task 4: Render persistent history and restore saved detail

**Files:**
- Modify: `templates/stock_analysis.html:7-18`
- Modify: `templates/_stock_analysis_panel.html:30-48`
- Modify: `static/js/stock-analysis.js:33-230`
- Modify: `static/css/stock-analysis.css:7-75`
- Modify: `tests/test_stock_analysis_trade_ui.py`

**Interfaces:**
- Consumes: history list/detail/refresh APIs from Task 2.
- Produces: `loadAnalysisHistory()`, `openAnalysisHistory(id)`, `reanalyzeHistory(ticker)`, `_applyHistoryPrice(overlay)`.
- `renderResult()` remains the single structured-detail renderer.

- [ ] **Step 1: Write failing static UI contracts**

Add assertions:

```python
STOCK_PAGE = ROOT / "templates" / "stock_analysis.html"

def test_stock_analysis_page_exposes_persistent_history() -> None:
    page = STOCK_PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'id="sa-history-body"' in page
    assert 'id="sa-history-status"' in page
    assert "/api/v1/stock-analysis/history" in script
    assert "loadAnalysisHistory" in script
    assert "openAnalysisHistory" in script
    assert "refresh-price" in script
    assert "reanalyzeHistory" in script
```

Assert `analysis_picks.html` does not contain `sa-history-body`.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py -q
```

Expected: FAIL because the history DOM and functions are absent.

- [ ] **Step 3: Add the standalone-page history table**

In `stock_analysis.html`, between search and panel, add a card containing:

```html
<div id="sa-history-section" class="card sa-history">
  <div class="card-body">
    <div class="section-title">분석 이력</div>
    <div id="sa-history-status" class="sa-history-status"></div>
    <div class="sa-history-scroll">
      <table><thead><tr>
        <th>분석 시각</th><th>종목</th><th>의견</th><th>분석 당시</th>
        <th>현재가</th><th>가격 확인</th><th>동작</th>
      </tr></thead><tbody id="sa-history-body"></tbody></table>
    </div>
  </div>
</div>
```

Add `id="r-price-updated"` beside the result date in `_stock_analysis_panel.html` for overlay status.

- [ ] **Step 4: Implement list loading and reanalysis**

`loadAnalysisHistory()` fetches the first 50 rows, handles an empty list, and creates escaped cells using DOM nodes or a small `_escapeHtml()` helper. `reanalyzeHistory(ticker)` sets `sa-input`, scrolls to the search bar, and calls `runStockAnalysis(ticker)`. It does not update an existing history ID.

On SSE success, call `loadAnalysisHistory()` only when `sa-history-body` exists. If `d.history_error` exists, set `sa-error` to ``분석은 완료됐지만 이력 저장에 실패했습니다: ${d.history_error}`` without hiding the rendered result.

- [ ] **Step 5: Implement saved-detail restore and asynchronous quote overlay**

`openAnalysisHistory(id)` must:

```javascript
const detail = await _tradeApi(`/api/v1/stock-analysis/history/${id}`, undefined, 'GET');
_lastAnalysisTradePlan = detail.trade_plan;
_analysisText = detail.narrative || '';
renderResult(detail.snapshot);
finalizeAnalysis();
if (_analysisText) _byId('sa-ai-card').style.display = 'block';
```

Extend `_tradeApi` to support GET without a JSON body, or add a minimal `_getJson(url)` helper; do not send a request body on GET.

After stored detail renders, POST `refresh-price`. `_applyHistoryPrice()` updates only `r-price`, `r-change`, and `r-price-updated`, plus a compact distance strip. It must not mutate `_lastAnalysis`, `_lastAnalysisTradePlan`, the chart, indicators, or narrative. On refresh failure, keep the detail visible and set `r-price-updated` to `현재가 갱신 실패 · 마지막 확인값`.

- [ ] **Step 6: Add responsive list and overlay styles**

Add `.sa-history-scroll{overflow-x:auto}`, fixed numeric alignment, compact action buttons, and a mobile breakpoint that keeps the table scrollable. Do not hide analysis time or current-price provenance on mobile.

- [ ] **Step 7: Run GREEN and commit**

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py tests/test_stock_analysis_api.py -q
node --check static/js/stock-analysis.js
git diff --check
git add templates/stock_analysis.html templates/_stock_analysis_panel.html static/js/stock-analysis.js static/css/stock-analysis.css tests/test_stock_analysis_trade_ui.py
git commit -m "feat: restore saved stock analysis history"
```

---

### Task 5: Split budget-free safe limits from full plan validation

**Files:**
- Modify: `maps/ops/strategy_trade_plan.py`
- Modify: `maps/api/schemas.py:845`
- Modify: `maps/api/analysis_picks.py:48-84`
- Modify: `tests/test_strategy_trade_plan.py`
- Modify: `tests/test_analysis_picks_api.py:638-681`

**Interfaces:**
- Produces: `StrategyTradeLimitInput`
- Produces: `CalculatedTradeLimits`
- Produces: `calculate_trade_limits(request, account, settings, existing_position_value, has_active_pick=False)`
- Produces: `POST /api/v1/analysis-picks/trade-limits`
- `validate_trade_plan()` consumes `calculate_trade_limits()` and adds only budget/quantity validation.

- [ ] **Step 1: Write the failing pure-calculation test**

```python
def test_limits_do_not_require_total_budget() -> None:
    from maps.ops.strategy_trade_plan import StrategyTradeLimitInput, calculate_trade_limits

    request = StrategyTradeLimitInput(**{
        k: v for k, v in _split_request().model_dump().items()
        if k != "total_budget"
    })
    result = calculate_trade_limits(
        request,
        account=AccountBalance(cash=12_500_000, positions_value=50_000_000, total_assets=100_000_000),
        settings=_settings(), existing_position_value=0,
    )
    assert result.safe_max_amount == 10_000_000
    assert result.minimum_orderable_amount == 233_334
    assert result.blocked is False
```

- [ ] **Step 2: Write the failing endpoint test**

```python
payload = _trade_plan_payload()
payload.pop("total_budget")
response = client.post("/api/v1/analysis-picks/trade-limits", json=payload)
assert response.status_code == 200
assert response.json()["safe_max_amount"] == 10_000_000
assert response.json()["minimum_orderable_amount"] == 233_334
assert client.get("/api/v1/analysis-picks").json()["total"] == 0
```

- [ ] **Step 3: Run RED**

```powershell
python -m pytest tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py -q
```

Expected: FAIL because the limit input/function/route do not exist.

- [ ] **Step 4: Refactor the pure models and calculation**

Make `StrategyTradeLimitInput` contain the existing fields except `total_budget`; make `StrategyTradePlanInput(StrategyTradeLimitInput)` add `total_budget: float`.

`CalculatedTradeLimits` contains:

```python
blocked: bool
blockers: tuple[TradePlanBlocker, ...]
limits: dict[str, float]
safe_max_amount: float
minimum_orderable_amount: float
```

Move gate, duplicate, leg shape, weights, finite positive account/prices, tick, price ordering, risk fraction, and four limit calculations into `calculate_trade_limits()`. Calculate minimum orderable budget as:

```python
minimum_orderable_amount = max(
    math.ceil(leg.entry_price * 100.0 / leg.weight_pct) for leg in legs
)
```

`validate_trade_plan()` starts with the returned blockers/limits, then calculates quantities, adds `ZERO_QUANTITY` and `BUDGET_EXCEEDS_SAFE_MAX`, and returns the unchanged `ValidatedTradePlan` contract. Existing tests must stay green without changing expected blockers.

- [ ] **Step 5: Add the shared account-context helper and route**

Refactor `_validate_requested_plan` account loading into:

```python
def _trade_account_context(ticker: str, db: Session):
    settings = get_settings()
    broker = get_broker(settings.maps_broker_mode)
    account = broker.get_account_balance()
    position = broker.get_position(ticker)
    existing_value = float(position.market_value) if position else 0.0
    has_active = (
        db.query(AnalysisPick.id)
        .filter(
            AnalysisPick.ticker == ticker.strip(),
            AnalysisPick.state.in_(["ARMED", "BOUGHT"]),
        )
        .first()
        is not None
    )
    return settings, account, existing_value, has_active
```

Both `/trade-limits` and `_validate_requested_plan` call it. The new route returns `StrategyTradeLimitResponse(**limits.model_dump())` and performs no write.

- [ ] **Step 6: Run GREEN and commit**

```powershell
python -m pytest tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py -q
git diff --check
git add maps/ops/strategy_trade_plan.py maps/api/schemas.py maps/api/analysis_picks.py tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py
git commit -m "feat: calculate strategy trade limits before budget"
```

---

### Task 6: Auto-fill safe budget and stabilize single/split field layout

**Files:**
- Modify: `templates/_stock_analysis_panel.html:94-134`
- Modify: `static/css/stock-analysis.css:112-141`
- Modify: `static/js/stock-analysis.js:39-620`
- Modify: `tests/test_stock_analysis_trade_ui.py`

**Interfaces:**
- Consumes: `/analysis-picks/trade-limits`, `/trade-preview`, `/arm-plan`.
- Produces: `_buildTradeLimitPayload()`, `_refreshTradeLimits()`, `_scheduleTradeLimits()`, `_scheduleTradePreview()`.
- Maintains: final arm enabled only for a preview matching the current input payload.

- [ ] **Step 1: Write failing layout and automatic-limit contracts**

```python
def test_split_entries_have_a_dedicated_stable_row() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")
    assert 'class="sa-trade-common-fields"' in panel
    assert 'class="sa-trade-entry-fields"' in panel
    assert ".sa-trade-entry-fields" in css
    assert "repeat(3,minmax(0,1fr))" in css

def test_mode_selection_calculates_and_applies_safe_budget() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "/api/v1/analysis-picks/trade-limits" in script
    assert "_refreshTradeLimits" in script
    assert ".max =" in script
    assert "safe_max_amount" in script
    assert "minimum_orderable_amount" in script
    assert "안전한도 계산" not in panel
```

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py -q
```

Expected: FAIL on missing field groups and automatic limits flow.

- [ ] **Step 3: Restructure the form without changing IDs**

Replace `.sa-trade-fields` with:

```html
<div class="sa-trade-common-fields">
  <label>시장
    <select id="sa-plan-market"><option>KOSPI</option><option>KOSDAQ</option></select>
  </label>
  <label>총 매수금액
    <input id="sa-plan-budget" type="number" min="1" step="1">
  </label>
  <label>목표가
    <input id="sa-plan-target" type="number" min="1" step="1">
  </label>
  <label>손절가
    <input id="sa-plan-stop" type="number" min="1" step="1">
  </label>
</div>
<div class="sa-trade-entry-fields">
  <label>1차 진입가 <small id="sa-weight-1"></small>
    <input id="sa-entry-1" type="number" min="1" step="1">
  </label>
  <label class="sa-split-only">2차 진입가 <small>30%</small>
    <input id="sa-entry-2" type="number" min="1" step="1">
  </label>
  <label class="sa-split-only">3차 진입가 <small>40%</small>
    <input id="sa-entry-3" type="number" min="1" step="1">
  </label>
</div>
```

Remove the `안전한도 계산` button. Keep only the final arm button and the preview/validation containers.

Use four equal columns for common fields and three equal columns for entry fields. At `max-width:760px`, use two columns for common fields and one column for entry fields so all three split entries remain vertically aligned. Do not use implicit placement across the two groups.

- [ ] **Step 4: Add automatic limit/preview state**

Add:

```javascript
let _lastTradeLimits = null;
let _tradeLimitTimer = null;
let _tradePreviewTimer = null;
let _tradeRequestVersion = 0;
```

`_invalidateTradePreview()` clears `_lastTradePreview`, disables arm, and increments `_tradeRequestVersion`. `_buildTradeLimitPayload()` returns the same payload as `_buildTradePayload()` without `total_budget`; it still requires mode and valid prices.

`_refreshTradeLimits()` captures the current request version, POSTs `/trade-limits`, and ignores a response if the version changed. On success:

```javascript
const maximum = Math.floor(limits.safe_max_amount);
budget.max = String(maximum);
if (maximum >= Math.ceil(limits.minimum_orderable_amount)) {
  budget.value = String(maximum);
  await previewTradeSetup();
} else {
  budget.value = '';
  // render the server blockers plus the minimum-vs-maximum explanation
}
```

Never silently clamp a user-entered amount during final arm; input changes invalidate the preview and the server remains authoritative.

- [ ] **Step 5: Wire events with debounce**

- `onTradeModeChange()` toggles split fields, invalidates prior state, and calls `_scheduleTradeLimits()`.
- Entry/target/stop change schedules limits and then preview.
- Budget change checks `input.max`, shows a clear over-limit message, and schedules preview only.
- Use one 300ms debounce constant; clear pending timers when the dialog closes or a new analysis starts.
- Store a stable `JSON.stringify(_buildTradePayload())` signature when preview succeeds. Before arm, rebuild and compare; if different, reject locally and require the automatic preview to finish.

- [ ] **Step 6: Run GREEN and commit**

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py -q
node --check static/js/stock-analysis.js
git diff --check
git add templates/_stock_analysis_panel.html static/css/stock-analysis.css static/js/stock-analysis.js tests/test_stock_analysis_trade_ui.py
git commit -m "fix: automate safe budget and align split entries"
```

---

### Task 7: Verify the integrated flow and hand it off

**Files:**
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes all tasks and records exact evidence.
- Produces no production behavior.

- [ ] **Step 1: Run the focused suite**

```powershell
python -m pytest tests/test_stock_analysis_history_model.py tests/test_stock_analysis_history_api.py tests/test_stock_analysis_api.py tests/test_stock_analysis_narrative.py tests/test_stock_analysis_trade_ui.py tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py tests/test_migrations.py -q
node --check static/js/stock-analysis.js
```

Expected: all focused tests pass; only known warnings are allowed.

- [ ] **Step 2: Run the complete regression suite**

```powershell
python -m pytest --tb=short -q
git diff --check
git status --short
```

Expected: all tests pass and only the intended HANDOFF edit remains before its commit.

- [ ] **Step 3: Perform a local UI smoke check**

Use a temporary SQLite database with auth, scheduler, and live trading disabled. Verify:

1. A full analysis creates exactly one list row.
2. Navigating away and returning restores the list.
3. Opening detail first shows the saved snapshot, then updates the price timestamp.
4. Reanalysis creates a second row for the same ticker.
5. Opening saved detail restores its exact entries/target/stop in the strategy dialog.
6. Single and split layouts remain aligned at desktop and mobile widths.
7. Choosing a mode auto-fills the current safe maximum and renders the preview.

- [ ] **Step 4: Update HANDOFF with exact results**

Record:

- branch and commit IDs
- schema head `0022_stock_analysis_history`
- immutable snapshot vs mutable quote-overlay contract
- append-on-reanalysis behavior
- safe-limit auto-fill and final revalidation behavior
- exact focused/full test counts and JS result
- local smoke result
- not pushed/not deployed status

- [ ] **Step 5: Commit the handoff**

```powershell
git add HANDOFF.md
git commit -m "docs: hand off stock analysis history"
git status -sb
```

- [ ] **Step 6: Review before integration**

Review `git diff master...HEAD` for Critical/Important issues. Re-run any affected focused test after fixes. Then use `superpowers:finishing-a-development-branch` to offer local merge, PR, or branch preservation. Do not push or deploy unless the user selects or separately requests it.
