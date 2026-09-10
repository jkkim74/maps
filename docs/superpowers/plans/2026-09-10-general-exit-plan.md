# General strategy exit plans: approved implementation

## Global constraints

- Existing five general-strategy holdings retain baseline stop/strategy exits. No retroactive enrollment, including shadow-to-live promotion. New submitted general candidate BUY orders alone carry an immutable versioned policy marker. Exclude analysis picks, external trades, and limit-up in every cycle.
- Risk stop uses actual final average fill price and the BUY's stored ATR via effective_stop_price. Freeze stop, ATR, risk multiple (current 2), target rounded UP to tick, trailing activation (current 0.05), trailing distance (current 0.08). Do not use mutable CandidateSnapshot technical_stop/final_sell_price/ai_target_price for execution.
- First fill observation time is UTC and honestly named, never submitted_at/created_at or the broker adapter's misleading filled_at. First HWM is observed fill price, subsequent valid fresh quotes only. Persist HWM monotonic with observation time. Never use entry-day/pre-entry OHLCV highs. Recovery permits only completed subsequent KST sessions <= evaluation date/time.
- Partial buys are baseline-protected. Finalize plan only after full fill or confirmed terminal cancellation/expiry with remaining quantity settled; freeze actual final average. Uncertain fills remain baseline. Do not retroactively infer a first-fill time for legacy orders.
- Per-entry durable state keyed to order_log.id; serialize lifecycle/decision updates and reuse OrderManager duplicate handling for exits. Existing/live open orders must not be forgotten on rollback.
- Evaluation order: baseline price stop, trailing stop, target, strategy exit. Shadow cannot add orders; baseline protections always continue. Live master switch OFF suppresses additional exits but never baseline protections. Submitted shadow cohort never becomes live on switch changes.
- Display actual executable prices and mode separately from shadow/reference plans. Add optional backward-compatible API fields. Record reason, observed current quote/time, HWM/time, trigger level, signal as-of date.
- No new dependency; standard SQLAlchemy/Pydantic/pytest stack. Keep unrelated user docs untouched. No broad refactors.
- Operational rollout: shadow first, minimum five trading days AND at least one new entry with fill finalization/HWM update; no new exit orders from shadow, no legacy behavior change, no state/price/time inconsistencies. No live activation in this implementation turn: prerequisites cannot yet be satisfied. Code readiness is not profitability proof.

## Task 1: Core lifecycle, persistence and trading integration

Implement core in maps/common/models.py, a new migration after 0033, maps/common/settings.py, focused maps/ops/general_exit.py module, maps/execution/order_manager.py, maps/ops/scheduler.py and focused tests. Do not edit API schemas/risk/digest/front-end (Task 2 owns those).

Keep existing maps_plan_based_exits_enabled as live authorization switch; add maps_plan_based_exits_shadow_enabled (default false). Policy stamping at new candidate BUY submission: live if live switch true, otherwise shadow if shadow switch true, otherwise none. Captured mode/settings immutable. Unmarked orders always legacy even if live switch true. Observe existing marked live positions while switch is off but suppress extra orders.

Use a GeneralExitState model/table with unique entry_order_id FK. Agree/send the state read/presentation contract early to controller. Prefer a JSON-safe public serializer/helper for Task 2, with immutable cohort mode, effective execution_enabled, finalized status, stop_price, target_price, trailing_stop_price, high_water_mark, observed timestamps and latest decision fields. Actual baseline stop remains displayed for shadow. No reads may mutate state.

Identify credible fill observations at OrderManager sync and immediate fills at submission. Handle partial->terminal, duplicate/replayed broker results, stale/untrusted quote fallback, no future/entry-day bars, persistence after restart, and holdings owner isolation. Price enrichment must distinguish freshly fetched quotes from cached balance or previous close. At morning order cycle do not treat balance last-price as fresh without a fresh successful fetch. Keep old baseline logic available.

Test-first focused tests for lifecycle/policy cohort, first-fill timestamps and KST boundary, full/partial/terminal, mutable candidate/settings independence, non-finite inputs, price failures, recovery, duplicate cycles, both scheduler call paths, rollback and unchanged legacy/analysis/limit-up. Adjust old enabled-plan tests to intentional new cohort behavior. Test migration. Do not perform deployment or external writes.

Use shared interpreter D:/workspace2/maps/maps/maps/.venv/Scripts/python.exe and working directory of this worktree, no .env copy or live broker access. Commit only owned files when tests pass; report commands/results, interfaces, unresolved risks.

## Task 2: Read-only API, screen and diary presentation

Use the Task 1 serializer/state. Add optional general_exit_plan field on HoldingItem and DigestHolding; expose for correctly owned positions only. Render explicit shadow/live/paused/awaiting-fill labels, actual baseline stop separate from simulated plan values, confirmed target/trailing/HWM with timestamps. Never label an AI-named candidate field as actual executable target. Legacy, analysis, limit-up UI behavior unchanged. Tests must prove field compatibility, correct entry linkage, frozen prices, and shadow distinction. No read endpoint mutates state.

## Task 3: Verification and rollout record

Run focused tests then suite, JS syntax, migration on disposable SQLite and PostgreSQL SQL validation/available isolated PG. Review whole branch. Document deployment/migration order, rollback to baseline, observation start/cohort isolation and five-trading-day plus new-entry validation gate in an operations runbook. Commit and integrate after clean review. Deploy only shadow after verified backup/migration, preserve existing configuration and holdings, verify flags/service/migration and legacy coverage. Record that live activation and observation acceptance remain pending real future observations.
