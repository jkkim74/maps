# Stock Analysis Strategy Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the approved stock-analysis screen design into a production MAPS flow that creates and arms safe single-entry or three-leg 30/30/40 strategies, executes split entries without duplication, and exposes consistent progress on web and mobile.

**Architecture:** Preserve the existing single-entry `AnalysisPick` path and add child `AnalysisPickLeg` rows only for split plans. A shared server-side validator computes quantities and safe budget limits for both preview and final arm requests; the scheduler remains the only component allowed to submit orders. Structured AI trade-plan generation is isolated from the narrative analysis and fails closed to manual input.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy/Alembic, pytest, Jinja2/vanilla JavaScript, React 19/TypeScript/Vitest, AWS Bedrock structured output.

## Global Constraints

- The approved source of truth is `docs/ui-design/maps-analysis-trade-prototype.html` and `docs/ui-design/MAPS_종목분석_전략매매_화면설계서.pptx`.
- Arming saves a plan as `ARMED`; it never submits an order in the request thread.
- Split weights default to exactly `30/30/40`, and all supplied weights must sum to exactly `100`.
- Entry prices must satisfy `target > entry_1 > entry_2 > entry_3 > stop`; single mode uses `target > entry_1 > stop`.
- Every price must be normalized to a valid KRX quotation unit before persistence.
- The safe maximum is the minimum of broker cash, remaining single-name exposure, remaining portfolio cash capacity, and stop-loss risk capacity.
- Preview validation and final arm validation must call the same production function.
- Only one split leg may submit an order in one scheduler cycle.
- A later split leg cannot submit until the preceding leg is fully filled.
- Partial fills accumulate; a replacement order may request only the unfilled remainder.
- Expiry and “stop remaining buys” block new entries but never disable target/stop monitoring for held shares.
- No test may contact KIS, Bedrock, pykrx, or another live external service.

---

### Task 1: Persist split plans and expose compatible response fields

**Files:**
- Create: `alembic/versions/0021_analysis_pick_split_plan.py`
- Modify: `maps/common/models.py`
- Modify: `maps/api/schemas.py`
- Modify: `maps/api/analysis_picks.py`
- Test: `tests/test_analysis_picks_api.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `AnalysisPickLeg`, `AnalysisPick.trade_mode`, `total_budget`, `entries_cancelled`, `exit_pending_reason`.
- Produces: `AnalysisPickLegItem` and split progress fields on `AnalysisPickItem`.
- Preserves: legacy rows without legs serialize as one-leg `single` plans.

- [ ] **Step 1: Write failing model/API tests**

```python
def test_split_pick_response_exposes_ordered_leg_progress(client) -> None:
    pid = _new_pick(client)
    with client.session_factory() as db:
        pick = db.get(AnalysisPick, pid)
        pick.trade_mode = "split"
        pick.total_budget = 9_900_000
        db.add_all([
            AnalysisPickLeg(pick_id=pid, sequence=1, entry_price=70_000, weight_pct=30, planned_qty=42, filled_qty=42, status="FILLED"),
            AnalysisPickLeg(pick_id=pid, sequence=2, entry_price=67_000, weight_pct=30, planned_qty=44, filled_qty=18, status="PARTIAL"),
            AnalysisPickLeg(pick_id=pid, sequence=3, entry_price=64_000, weight_pct=40, planned_qty=61, status="PENDING"),
        ])
        db.commit()

    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["trade_mode"] == "split"
    assert [leg["sequence"] for leg in item["legs"]] == [1, 2, 3]
    assert item["filled_legs"] == 1
    assert item["next_entry_price"] == 67_000


def test_legacy_pick_without_legs_is_single(client) -> None:
    _new_pick(client, qty=10)
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["trade_mode"] == "single"
    assert item["total_legs"] == 1
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_analysis_picks_api.py -k "split_pick_response or legacy_pick" -q`

Expected: FAIL because `AnalysisPickLeg` and the response fields do not exist.

- [ ] **Step 3: Add the migration and minimum model fields**

```python
class AnalysisPickLeg(Base):
    __tablename__ = "analysis_pick_leg"
    __table_args__ = (UniqueConstraint("pick_id", "sequence", name="uq_analysis_pick_leg_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pick_id: Mapped[int] = mapped_column(ForeignKey("analysis_pick.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    weight_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_order_fill_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
```

Add nullable/backward-compatible columns to `analysis_pick`: `trade_mode` with server default `single`, `total_budget`, `entries_cancelled` with server default false, and `exit_pending_reason`.

- [ ] **Step 4: Serialize legs and derived progress**

Order legs by `sequence`. For split plans, calculate `filled_legs`, `total_legs`, `next_entry_price`, and weighted `fill_price` from the child rows. For legacy single plans, keep the existing `entry_order_id` lookup and synthesize only the summary counts; do not create child rows during reads.

- [ ] **Step 5: Verify GREEN and migration round-trip**

Run: `python -m pytest tests/test_analysis_picks_api.py tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0021_analysis_pick_split_plan.py maps/common/models.py maps/api/schemas.py maps/api/analysis_picks.py tests/test_analysis_picks_api.py tests/test_migrations.py
git commit -m "feat: persist split analysis trade plans"
```

### Task 2: Generate a structured AI trade plan and fail closed

**Files:**
- Create: `maps/ai/trade_planner.py`
- Modify: `maps/api/schemas.py`
- Modify: `maps/api/stock_analysis.py`
- Test: `tests/test_ai_trade_planner.py`
- Test: `tests/test_stock_analysis_api.py`

**Interfaces:**
- Consumes: the already returned stock-analysis facts, not the narrative Markdown.
- Produces: `POST /api/v1/stock-analysis/trade-plan`.
- Produces: `recommendation`, three normalized entries, target, stop, `source=AI|MANUAL_REQUIRED`, and a user-safe message.

- [ ] **Step 1: Write failing structured-output tests**

```python
def test_trade_plan_accepts_only_ordered_buy_prices() -> None:
    plan = AITradePlan.from_payload({
        "recommendation": "BUY",
        "entries": [70_000, 67_000, 64_000],
        "target": 80_000,
        "stop": 60_000,
        "rationale": "trend and support",
    })
    assert plan.entries == (70_000.0, 67_000.0, 64_000.0)


@pytest.mark.parametrize("recommendation", ["WATCH", "SELL"])
def test_non_buy_recommendation_cannot_supply_order_prices(recommendation) -> None:
    with pytest.raises(AITradePlanResponseError):
        AITradePlan.from_payload({
            "recommendation": recommendation,
            "entries": [70_000, 67_000, 64_000],
            "target": 80_000,
            "stop": 60_000,
            "rationale": "not a buy",
        })
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py -q`

Expected: FAIL because the planner and endpoint do not exist.

- [ ] **Step 3: Implement the strict Pydantic contract and Bedrock adapter**

```python
class AITradePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation: Literal["BUY", "WATCH", "SELL"]
    entries: tuple[float, float, float] | None
    target: float | None
    stop: float | None
    rationale: str

    @model_validator(mode="after")
    def validate_orderability(self) -> "AITradePlan":
        if self.recommendation != "BUY":
            if self.entries is not None or self.target is not None or self.stop is not None:
                raise ValueError("non-BUY recommendations cannot contain order prices")
            return self
        if self.entries is None or self.target is None or self.stop is None:
            raise ValueError("BUY requires entries, target, and stop")
        e1, e2, e3 = self.entries
        if not self.target > e1 > e2 > e3 > self.stop > 0:
            raise ValueError("invalid price order")
        return self
```

Build the Bedrock request using the same schema-constraint stripping and `output_config.format.type=json_schema` pattern as `maps/ai/technical_scorer.py`. Send compact technical/fundamental facts only. Never parse the streamed Markdown.

- [ ] **Step 4: Add the fail-closed endpoint**

When Bedrock is unconfigured, returns WATCH/SELL, times out, or fails schema validation, return HTTP 200 with `source="MANUAL_REQUIRED"` and no price values. For BUY, normalize all prices with `round_up_krx_price` for entries and `round_to_krx_tick` for target/stop, then revalidate the ordering.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py -q`

Expected: PASS with no external calls.

- [ ] **Step 6: Commit**

```bash
git add maps/ai/trade_planner.py maps/api/schemas.py maps/api/stock_analysis.py tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py
git commit -m "feat: add structured stock trade planning"
```

### Task 3: Share safe-budget and plan validation between preview and arm

**Files:**
- Create: `maps/ops/strategy_trade_plan.py`
- Modify: `maps/api/schemas.py`
- Modify: `maps/api/analysis_picks.py`
- Test: `tests/test_strategy_trade_plan.py`
- Test: `tests/test_analysis_picks_api.py`

**Interfaces:**
- Produces: `validate_trade_plan(request, account, settings, existing_position_value) -> ValidatedTradePlan`.
- Produces: `POST /api/v1/analysis-picks/trade-preview` and `POST /api/v1/analysis-picks/arm-plan`.
- The preview and arm endpoints must return the same blocker codes for the same inputs.

- [ ] **Step 1: Write failing safe-limit and parity tests**

```python
def test_safe_budget_is_minimum_of_all_limits(settings) -> None:
    plan = validate_trade_plan(
        _split_request(total_budget=9_900_000),
        account=AccountBalance(cash=12_500_000, positions_value=87_500_000),
        settings=settings,
        existing_position_value=0,
    )
    assert plan.safe_max_amount == min(plan.limits.values())
    assert [leg.weight_pct for leg in plan.legs] == [30, 30, 40]
    assert all(leg.planned_qty > 0 for leg in plan.legs)


def test_arm_revalidates_gate_instead_of_trusting_preview(client, monkeypatch) -> None:
    preview = client.post("/api/v1/analysis-picks/trade-preview", json=_payload()).json()
    assert preview["blocked"] is False
    monkeypatch.setattr("maps.api.analysis_picks.get_settings", lambda: _settings(maps_strategy_trade_enabled=False))
    armed = client.post("/api/v1/analysis-picks/arm-plan", json=_payload())
    assert armed.status_code == 409
    assert "GATE_OFF" in armed.json()["detail"]
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py -k "safe_budget or arm_revalidates or arm_plan" -q`

Expected: FAIL because the shared validator and endpoints do not exist.

- [ ] **Step 3: Implement the pure validator**

Calculate quantities as `floor(total_budget * weight_pct / 100 / entry_price)`. Return explicit limits named `broker_cash`, `single_exposure`, `portfolio_capacity`, and `stop_risk`. Validate positive values, KRX tick alignment, price ordering, weight total, nonzero quantities, duplicate active ticker, `maps_strategy_trade_enabled`, and total budget not exceeding the safe maximum.

- [ ] **Step 4: Add preview and atomic arm endpoints**

Preview may call `broker.get_account_balance()` but must never write or order. Arm must repeat the broker lookup and shared validation, then create one `AnalysisPick` plus three `AnalysisPickLeg` rows for split mode or the existing `qty/buy_price` fields for single mode. Commit once after all rows are present; response state is `ARMED`.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add maps/ops/strategy_trade_plan.py maps/api/schemas.py maps/api/analysis_picks.py tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py
git commit -m "feat: validate and arm analysis trade plans"
```

### Task 4: Execute split entries safely in the scheduler

**Files:**
- Modify: `maps/ops/scheduler.py`
- Modify: `maps/api/analysis_picks.py`
- Test: `tests/test_strategy_trade.py`
- Test: `tests/test_analysis_picks_api.py`

**Interfaces:**
- Consumes: ordered `AnalysisPickLeg` rows and the existing `OrderManager`.
- Produces: one order at most per pick per cycle, cumulative partial-fill state, and `POST /api/v1/analysis-picks/{pick_id}/stop-entries`.
- Preserves: the legacy single path and BOUGHT target/stop exits.

- [ ] **Step 1: Write failing scheduler tests**

```python
def test_split_submits_only_first_eligible_leg_per_cycle(split_env) -> None:
    pipeline, broker, manager, db, pick = split_env
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    assert (submitted, closed) == (1, 0)
    assert pick.legs[0].order_id
    assert pick.legs[1].order_id is None


def test_split_waits_for_full_fill_before_next_leg(split_env) -> None:
    pipeline, broker, manager, db, pick = split_env
    _seed_leg_order(db, pick.legs[0], status="partially_filled", fill_qty=18)
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    assert pick.legs[0].filled_qty == 18
    assert pick.legs[1].order_id is None


def test_dead_partial_order_retries_only_remaining_quantity(split_env) -> None:
    pipeline, broker, manager, db, pick = split_env
    _seed_leg_order(db, pick.legs[0], status="expired", fill_qty=18)
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    newest = db.query(OrderLog).order_by(OrderLog.id.desc()).first()
    assert newest.qty == pick.legs[0].planned_qty - 18


def test_stop_entries_preserves_bought_exit_monitoring(client) -> None:
    pid = _split_bought_pick(client)
    response = client.post(f"/api/v1/analysis-picks/{pid}/stop-entries")
    assert response.status_code == 200
    item = response.json()
    assert item["state"] == "BOUGHT"
    assert item["entries_cancelled"] is True
    assert item["strategy_trade_enabled"] is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_strategy_trade.py tests/test_analysis_picks_api.py -k "split or stop_entries" -q`

Expected: FAIL because split scheduling and stop-entries do not exist.

- [ ] **Step 3: Synchronize current leg order state**

For the current order, add only `OrderLog.fill_qty - leg.current_order_fill_qty` to cumulative `leg.filled_qty`. Keep a pending/partial order attached. When a cancelled/expired/rejected order is dead, clear `order_id` and reset `current_order_fill_qty` so only the remaining quantity can be retried. Mark the leg `FILLED` only when cumulative fills reach `planned_qty`.

- [ ] **Step 4: Submit at most one eligible leg**

Process split entries for both `ARMED` and `BOUGHT` picks while `entries_cancelled` is false. Select the first non-filled leg only after all predecessors are `FILLED`; require `current <= entry_price`; submit the remaining quantity; save its namespaced order ID; and return to the outer loop immediately after one submission.

- [ ] **Step 5: Make exits dominate new entries**

Evaluate target/stop before submitting a new split leg. On a trigger, set `entries_cancelled=True` and `exit_pending_reason`; cancel the live entry order. Submit the existing market exit only after the entry cancellation is confirmed or no live entry exists. Continue this state on later cycles until the held position is closed, then set `CLOSED` and clear `exit_pending_reason`.

- [ ] **Step 6: Implement stop-entries**

Cancel only the live split-entry order and mark all future unsubmitted legs `CANCELLED`. If any quantity is held, keep `state=BOUGHT` and `strategy_trade_enabled=True`; otherwise return to `WATCH` with strategy trading disabled.

- [ ] **Step 7: Verify GREEN and legacy regression**

Run: `python -m pytest tests/test_strategy_trade.py tests/test_analysis_picks_api.py tests/test_order_manager.py tests/test_order_manager_sync.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add maps/ops/scheduler.py maps/api/analysis_picks.py tests/test_strategy_trade.py tests/test_analysis_picks_api.py
git commit -m "feat: execute split strategy trade legs"
```

### Task 5: Connect the approved web flow to live APIs

**Files:**
- Modify: `templates/_stock_analysis_panel.html`
- Modify: `templates/analysis_picks.html`
- Modify: `static/js/stock-analysis.js`
- Modify: `static/css/stock-analysis.css`
- Test: `tests/test_stock_analysis_trade_ui.py`

**Interfaces:**
- Consumes: `/stock-analysis/trade-plan`, `/analysis-picks/trade-preview`, `/analysis-picks/arm-plan`, and `/analysis-picks/{id}/stop-entries`.
- Produces: the approved analysis-result, AI-fallback, mode, setup, validation, confirmation, armed, watchlist, and leg-detail behavior.

- [ ] **Step 1: Write failing HTML/JS contract tests**

```python
def test_analysis_result_exposes_trade_setup_and_safe_api_flow() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'id="sa-trade-setup"' in panel
    assert "openTradeSetup" in script
    assert "/api/v1/stock-analysis/trade-plan" in script
    assert "/api/v1/analysis-picks/trade-preview" in script
    assert "/api/v1/analysis-picks/arm-plan" in script
    assert "if (!preview.blocked)" in script


def test_watchlist_renders_split_progress_and_stop_entries() -> None:
    html = WATCHLIST.read_text(encoding="utf-8")
    assert "filled_legs" in html
    assert "next_entry_price" in html
    assert "stop-entries" in html
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_stock_analysis_trade_ui.py -q`

Expected: FAIL because the live UI controls are absent.

- [ ] **Step 3: Add the trade setup dialog and state**

Add one reusable dialog to `_stock_analysis_panel.html`. Store the last completed analysis object in module state. `매매 설정` first requests the structured plan; BUY pre-fills values, while WATCH/failure leaves price inputs empty and displays the manual-required warning. Require an explicit single/split choice.

- [ ] **Step 4: Add preview, validation, and final arm behavior**

Render broker cash, each named safe limit, safe maximum, expected remaining cash, integer quantities, and blockers. Disable final confirmation for any client validation error or server blocker. The arm button must issue a fresh `/arm-plan` request and handle a new 409 even if preview had passed.

- [ ] **Step 5: Upgrade the watchlist and detail view**

Show mode, `filled_legs/total_legs`, next entry, plan amount, and each leg’s planned/filled/remaining quantity. For split plans, expose `남은 매수 중단`; for held positions, keep target/stop status visible after entries are stopped.

- [ ] **Step 6: Verify GREEN and browser-safe syntax**

Run: `python -m pytest tests/test_stock_analysis_trade_ui.py tests/test_analysis_picks_api.py -q`

Run: `node --check static/js/stock-analysis.js`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add templates/_stock_analysis_panel.html templates/analysis_picks.html static/js/stock-analysis.js static/css/stock-analysis.css tests/test_stock_analysis_trade_ui.py
git commit -m "feat: connect stock analysis trade setup UI"
```

### Task 6: Show split progress and stop controls in the mobile app

**Files:**
- Modify: `apps/mobile/src/api.ts`
- Modify: `apps/mobile/src/hooks/usePicks.ts`
- Modify: `apps/mobile/src/screens/WatchlistScreen.tsx`
- Modify: `apps/mobile/src/HoldingDetail.tsx`
- Modify: `apps/mobile/src/Detail.test.tsx`
- Modify: `apps/mobile/src/App.test.tsx`

**Interfaces:**
- Consumes: the expanded `AnalysisPickItem` and stop-entries endpoint.
- Produces: read-only split progress/detail plus the approved stop-remaining-buys action; mobile does not create plans.

- [ ] **Step 1: Write failing mobile tests**

```tsx
it('3분할 진행과 다음 진입가를 표시한다', () => {
  render(<WatchlistScreen {...props({ trade_mode: 'split', filled_legs: 1, total_legs: 3, next_entry_price: 67000 })} />)
  expect(screen.getByText('3분할 · 1/3 체결')).toBeInTheDocument()
  expect(screen.getByText(/다음 진입.*67,000원/)).toBeInTheDocument()
})

it('남은 매수 중단은 확인 후 stop-entries를 호출한다', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  fireEvent.click(screen.getByText('남은 매수 중단'))
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/stop-entries'), expect.objectContaining({ method: 'POST' }))
})
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `npm test -- --run src/Detail.test.tsx src/App.test.tsx`

Working directory: `apps/mobile`

Expected: FAIL because split response fields and stop action are absent.

- [ ] **Step 3: Extend types, API, hook, list, and detail**

Add `trade_mode`, `total_budget`, `entries_cancelled`, progress fields, and typed legs to `AnalysisPick`. Add `stopPickEntries(id)`, expose `onStopEntries` from the hook, show mode/progress/next condition on cards, and show every leg plus the stop action in detail. Keep plan creation absent from mobile.

- [ ] **Step 4: Verify GREEN and production build**

Run: `npm test -- --run`

Run: `npm run build`

Working directory: `apps/mobile`

Expected: all Vitest tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/mobile/src/api.ts apps/mobile/src/hooks/usePicks.ts apps/mobile/src/screens/WatchlistScreen.tsx apps/mobile/src/HoldingDetail.tsx apps/mobile/src/Detail.test.tsx apps/mobile/src/App.test.tsx
git commit -m "feat: show split trade progress on mobile"
```

### Task 7: Synchronize documentation and run final verification

**Files:**
- Modify: `docs/ui-design/maps-analysis-trade-prototype.html` only if implementation behavior requires wording corrections
- Modify: `docs/ui-design/MAPS_종목분석_전략매매_화면설계서.pptx` only through the generator
- Modify: `HANDOFF.md`
- Modify: `tests/test_ui_design_deliverables.py`

**Interfaces:**
- Produces: synchronized design artifacts, accurate handoff, and complete verification evidence.

- [ ] **Step 1: Strengthen deliverable contracts for implemented behavior**

Assert that the prototype and handoff state: structured AI values fail closed, final arm revalidates the server gates, one split order occurs per cycle, partial fills retry only the remainder, and stopped entries do not stop held-position exits.

- [ ] **Step 2: Run focused RED if any wording is stale**

Run: `python -m pytest tests/test_ui_design_deliverables.py -q`

Expected: FAIL only for a stale artifact statement that the implementation now supersedes.

- [ ] **Step 3: Update the prototype wording and rebuild the deck**

Run: `python scripts/build_stock_analysis_ui_ppt.py`

Do not hand-edit the PPT binary.

- [ ] **Step 4: Run focused backend and UI verification**

```bash
python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py tests/test_strategy_trade_plan.py tests/test_analysis_picks_api.py tests/test_strategy_trade.py tests/test_stock_analysis_trade_ui.py tests/test_ui_design_deliverables.py -q
node --check static/js/stock-analysis.js
```

- [ ] **Step 5: Run mobile verification**

Run: `npm test -- --run && npm run build`

Working directory: `apps/mobile`

- [ ] **Step 6: Run the complete Python regression suite**

Run: `python -m pytest -q`

Baseline before implementation: `723 passed, 10 warnings`.

- [ ] **Step 7: Inspect the live-rendered web flow**

Start the local FastAPI application with authentication disabled and mock broker mode, then inspect `/stock-analysis` and `/analysis-picks` at desktop width. Verify mode selection, manual fallback, safe-budget blocker, final 409 handling, split progress, leg detail, and stop-entries behavior without any KIS or Bedrock call.

- [ ] **Step 8: Update handoff and commit**

Record the final commit IDs, migration head, focused/full test results, mobile build result, remaining limitations, and confirm that production deployment was not performed unless separately requested.

```bash
git add HANDOFF.md docs/ui-design tests/test_ui_design_deliverables.py
git commit -m "docs: complete strategy trading implementation handoff"
```

