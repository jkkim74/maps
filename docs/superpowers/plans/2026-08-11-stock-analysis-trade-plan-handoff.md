# Stock Analysis Trade Plan Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use one validated stock-analysis trade plan for both the displayed analysis prices and the strategy-trade popup, while rendering the popup on an opaque surface.

**Architecture:** Generate and normalize the structured plan once inside the stock-analysis run, include it in the narrative prompt and final SSE event, then keep it in browser state for both the visible price summary and popup defaults. Retain the existing `/trade-plan` endpoint for compatibility, but remove the popup's second call. Keep all existing preview, risk-limit, arm, live-trading, and user-confirmation gates.

**Tech Stack:** FastAPI, Pydantic v2, AWS Bedrock native API, Server-Sent Events, vanilla JavaScript, Jinja2, CSS, pytest.

## Global Constraints

- The values shown in the analysis price summary and the popup's initial price fields must come from the same `trade_plan` object.
- Do not parse Markdown to obtain executable prices.
- `BUY`, `WATCH`, and `SELL` may all carry a valid staged-buy plan; recommendation is advisory, not the auto-fill gate.
- Validate finite positive numbers, KRX tick normalization, and `target > entry1 > entry2 > entry3 > stop` on the server.
- Auto-fill never sends an order. Existing mode selection, budget input, preview, account/risk validation, final confirmation, and trading switches remain mandatory.
- Keep `/api/v1/stock-analysis/trade-plan` for compatibility, but the browser popup must not call it.
- Use `--bg-base` for opaque dialog surfaces; translucency is allowed only on the backdrop.
- Add no database table, migration, package, or mobile APK work.

---

### Task 1: Restore Bedrock Structured Trade Plans

**Files:**
- Modify: `maps/ai/trade_planner.py`
- Modify: `maps/api/stock_analysis.py`
- Test: `tests/test_ai_trade_planner.py`
- Test: `tests/test_stock_analysis_api.py`

**Interfaces:**
- Consumes: `StockTradeFacts` and the configured Bedrock model.
- Produces: `AITradePlan` with three entries, target, stop, recommendation, and rationale for every valid recommendation; `generate_trade_plan(req: StockTradePlanRequest) -> StockTradePlanResponse` for both the route and stream.

- [ ] **Step 1: Write failing provider-schema and non-BUY plan tests**

Add tests that require Bedrock-compatible array schema and a valid `WATCH` plan with prices:

```python
def test_response_schema_replaces_prefix_items_with_numeric_items() -> None:
    schema = AITradePlanner()._response_schema()
    encoded = json.dumps(schema)
    assert "prefixItems" not in encoded
    entries = schema["properties"]["entries"]["anyOf"][0]
    assert entries["items"]["type"] == "number"


def test_watch_plan_keeps_valid_staged_buy_prices() -> None:
    plan = AITradePlan.from_payload({
        "recommendation": "WATCH",
        "entries": [70_000, 68_000, 66_000],
        "target": 78_000,
        "stop": 64_000,
        "rationale": "가격 대기",
    })
    assert plan.entries == (70_000, 68_000, 66_000)
```

Extend the API test with a fake planner returning `WATCH` plus prices and assert the normalized response keeps `recommendation="WATCH"`, `source="AI"`, and all prices.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py -q
```

Expected: failures show `prefixItems` remains, non-BUY prices are rejected, and the API changes `WATCH` to `MANUAL_REQUIRED`.

- [ ] **Step 3: Implement the minimum shared planner contract**

Change `AITradePlan` so every valid result requires exactly three prices, a target, and a stop. Preserve tuple storage and the price-order validator. Update the system prompt to require the plan for every recommendation.

In `_response_schema()`, recursively replace a homogeneous `prefixItems` array with its first item schema:

```python
prefix_items = value.pop("prefixItems", None)
if prefix_items:
    value["items"] = prefix_items[0]
```

In `maps/api/stock_analysis.py`, extract the current route logic into synchronous `generate_trade_plan(req)`. It must return `MANUAL_REQUIRED` only for missing configuration, provider/validation failure, or invalid prices. Normalize all valid recommendations to KRX ticks. The async route calls this function through `run_in_executor`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add maps/ai/trade_planner.py maps/api/stock_analysis.py tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py
git commit -m "fix: restore structured analysis trade plans"
```

---

### Task 2: Carry One Plan Through Analysis and Narrative

**Files:**
- Modify: `maps/api/stock_analysis.py`
- Modify: `maps/stock_analysis/analyzer.py`
- Test: `tests/test_stock_analysis_api.py`
- Create: `tests/test_stock_analysis_narrative.py`

**Interfaces:**
- Consumes: `generate_trade_plan()` from Task 1 and the raw `analyze()` result.
- Produces: final SSE payload `{..., "data": result, "trade_plan": response_dict}` and `stream_llm_analysis(data, ..., trade_plan=...)` whose prompt treats the plan as authoritative.

- [ ] **Step 1: Write failing SSE single-source tests**

Add a stream test that replaces only the external data/Bedrock boundaries, consumes every SSE event, and asserts:

```python
assert final_event["trade_plan"] == {
    "recommendation": "WATCH",
    "entries": [70_000.0, 68_000.0, 66_000.0],
    "target": 78_000.0,
    "stop": 64_000.0,
    "rationale": "가격 대기",
    "source": "AI",
    "message": None,
}
assert captured_narrative_plan == final_event["trade_plan"]
```

Add a failure case asserting the stream still completes with `source="MANUAL_REQUIRED"` and no prices when structured planning fails.

- [ ] **Step 2: Write a failing narrative prompt test**

Use a small fake Bedrock streaming client to capture the request body passed by `stream_llm_analysis`. Assert the prompt contains the literal authoritative values `70000`, `68000`, `66000`, `78000`, and `64000`, plus an instruction not to generate different prices.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_stock_analysis_api.py tests/test_stock_analysis_narrative.py -q
```

Expected: the stream has no `trade_plan` field and `stream_llm_analysis` does not accept or include the plan.

- [ ] **Step 4: Implement server-side analysis handoff**

Add a private converter in `maps/api/stock_analysis.py` that maps the collected Korean-keyed result into `StockTradePlanRequest`. Inside the existing worker thread:

```python
trade_plan = generate_trade_plan(_trade_plan_request(result))
for chunk in stream_llm_analysis(..., trade_plan=trade_plan.model_dump(mode="json")):
    ...
queue.put({..., "data": result, "trade_plan": trade_plan.model_dump(mode="json")})
```

Update `stream_llm_analysis` to accept `trade_plan: Mapping[str, object] | None`. Include an authoritative JSON block in the prompt. If `source` is `AI`, STEP 7 must repeat those exact values. If not, STEP 7 must state that validated prices are unavailable and must not invent numbers.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_stock_analysis_api.py tests/test_stock_analysis_narrative.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add maps/api/stock_analysis.py maps/stock_analysis/analyzer.py tests/test_stock_analysis_api.py tests/test_stock_analysis_narrative.py
git commit -m "feat: carry analysis prices through the stream"
```

---

### Task 3: Reuse Analysis Prices in the Opaque Popup

**Files:**
- Modify: `templates/_stock_analysis_panel.html`
- Modify: `static/js/stock-analysis.js`
- Modify: `static/css/stock-analysis.css`
- Test: `tests/test_stock_analysis_trade_ui.py`

**Interfaces:**
- Consumes: final SSE `trade_plan` from Task 2.
- Produces: visible analysis-price summary and popup defaults from the same browser object; `source="manual"` after a user changes an initial price.

- [ ] **Step 1: Write failing UI contract tests**

Require a visible price summary target in the panel, require the script to save `d.trade_plan`, and require the popup code not to call the compatibility endpoint:

```python
assert 'id="sa-analysis-trade-plan"' in panel
assert "_lastAnalysisTradePlan = d.trade_plan" in script
assert "_renderAnalysisTradePlan" in script
assert "await _tradeApi('/api/v1/stock-analysis/trade-plan'" not in script
assert "_applyAnalysisTradePlan" in script
```

Require opaque surfaces:

```python
assert ".sa-trade-dialog" in css
assert "background:var(--bg-base)" in css
assert "background:var(--bg);" not in css
```

- [ ] **Step 2: Run the UI test and verify RED**

Run:

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py -q
```

Expected: failures show no stored SSE plan or summary target, the popup still calls `/trade-plan`, and the dialog still uses undefined `--bg`.

- [ ] **Step 3: Implement the minimal browser handoff**

Add `_lastAnalysisTradePlan`, reset it at analysis start, and assign `d.trade_plan || null` before `renderResult(d.data)`. Render the recommendation and exact three entries/target/stop into `#sa-analysis-trade-plan`.

Replace the async price-generation block in `openTradeSetup()` with `_applyAnalysisTradePlan()`, which copies the stored values to `sa-entry-1..3`, `sa-plan-target`, and `sa-plan-stop`. If the plan is missing or `MANUAL_REQUIRED`, clear the inputs and show the manual-entry warning.

In `_buildTradePayload()`, compare the submitted prices with the stored defaults. Use `source="ai_trade_plan"` only while the relevant mode's prices still match; otherwise use `source="manual"`. Do not alter budget or safety-limit behavior.

Replace `var(--bg)` with `var(--bg-base)` on `.sa-modal-dialog`, its sticky header, and `.sa-trade-dialog`. Keep the current translucent `::backdrop` unchanged.

- [ ] **Step 4: Run focused UI and API safety tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_stock_analysis_trade_ui.py tests/test_analysis_picks_api.py -q
node --check static/js/stock-analysis.js
```

Expected: all tests pass and Node syntax check exits 0.

- [ ] **Step 5: Commit Task 3**

```powershell
git add templates/_stock_analysis_panel.html static/js/stock-analysis.js static/css/stock-analysis.css tests/test_stock_analysis_trade_ui.py
git commit -m "fix: reuse analysis prices in trade setup"
```

---

### Task 4: Full Verification and Handoff

**Files:**
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: all prior task commits.
- Produces: verified branch state and current-session handoff; no push or production deployment without a separate request.

- [ ] **Step 1: Run focused regression tests**

```powershell
python -m pytest tests/test_ai_trade_planner.py tests/test_stock_analysis_api.py tests/test_stock_analysis_narrative.py tests/test_stock_analysis_trade_ui.py tests/test_analysis_picks_api.py -q
node --check static/js/stock-analysis.js
```

Expected: all pass.

- [ ] **Step 2: Run the full suite**

```powershell
python -m pytest --tb=short -q
```

Expected: 0 failures.

- [ ] **Step 3: Update and verify handoff**

Record the two root causes, implementation commits, exact test results, branch name, and the fact that production remains unchanged. Then run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only intended tracked files.

- [ ] **Step 4: Commit the handoff**

```powershell
git add HANDOFF.md
git commit -m "docs: hand off analysis price popup fix"
```
