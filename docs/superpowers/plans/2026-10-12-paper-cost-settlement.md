# KIS paper cost settlement ExecPlan

## Purpose and binding design
Implement actual-cost automatic reconciliation with statement-backed manual exceptions. Never infer costs from a rate or cash residual. KIS paper only; live and mock contracts remain unchanged. Default is observe. No production DB, broker orders, activation or deployment in this work.

Collect current/previous-day expense fields with the execution balance response. Missing/invalid amounts are unknown. Persist immutable evidence and idempotent settlement receipts. Only current-session dated totals can support automation; previous-day values are diagnostic. Automatic interval costs require complete coverage, exact order gross, consistent quantities, no unknown orders and two stable observations. Missing days, corrections, overlapping legacy adjustments require manual evidence. Store latest reconciliation breakdown separately from historical adjustment rows.

Manual flow: statement source, SHA256, period and actual amounts -> read-only preview -> atomic commit with semantic fingerprint and idempotency key -> next broker reconciliation. Commit performs no broker calls and never opens trading. Account aggregate costs must not be allocated to orders. Exact order statements may use existing Fujimoto settlement with no nested commit. Unsupported broker capabilities are diagnostic; actual reconciled coverage is the gate. Existing cost/loss/ownership protections remain.

## Progress
- [x] Investigate production symptom (prior read-only task): a confirmed fee, an older pending difference, and a later additional cost; no quantity difference. Regression amounts are synthetic and never production defaults.
- [x] Create managed isolated worktree, feature branch.
- [x] Task 1: adapter cost observation, settings, evidence/receipt tables and migration; focused red/green tests.
- [ ] Task 2: interval accounting, stable auto reconciliation, semantic preview/atomic manual settlement; incident and failure tests.
- [ ] Task 3: risk API/UI and Fujimoto readiness/order-cost integration; API/UI regression tests.
- [ ] Task 4: documentation, full isolated tests, independent review, fixes and final diff.

## Interfaces and file ownership
Task 1 owns broker_adapter.py, kis_adapter.py, settings.py, models.py, migration 0042 and their tests/docs. PositionSnapshot.cost_observation is optional dict: source, ref_date (ISO KST receipt day), observed_at (ISO UTC), cash_field, today_total (decimal string/null), previous_total (decimal string/null), raw (allowlisted fields only), valid (bool). All nonempty valid amounts are finite nonnegative Decimal strings. Invalid fields never become zero.

PaperCostEvidence table: id; account_key indexed; environment; kind (balance/account_statement/order_statement); ref_date date; observed_at datetime; amount nullable Numeric(24,4); fingerprint String(64); payload JSON; supersedes_id nullable integer; unique(account_key,fingerprint). PaperCostSettlement table: id; account_key indexed; idempotency_key String(128); request_hash String(64); created_at datetime; actor String(128); payload JSON; result JSON; unique(account_key,idempotency_key). Evidence/receipts append-only, ORM guards reject update/delete.

Task 2 owns new maps/execution/paper_costs.py and reconciliation.py. Store balance observation fingerprint excluding receipt timestamp to deduplicate same day values; retain actual received_at in payload and account observations. Reconciliation snapshot stores coverage and actual residual, not guessed fees. Account statement interval is anchored to complete baseline and latest observation, with included legacy fee adjustment IDs explicitly tied to the statement; final compensation is only the uncovered portion. Settlements remain valid in observe mode.

Task 3 adds GET safety breakdown and POST /execution-safety/settlements/preview, /commit. Exact schema is defined alongside implementation/tests and shared with UI before editing it. Reject stale semantic changes but accept timestamp-only refresh; one transaction including Fujimoto costs and receipt. Existing /classify remains for non-cost adjustments; paper fees require statements. UI uses textContent and escaped DOM, no document upload/OCR. Default auth/admin gates remain; Fujimoto owner scope remains.

## Validation
Use D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py with relevant tests, then tests maps/tests -q --tb=short. No real broker network. Test real incident arithmetic, two-observation automation, missing/negative/empty/zero costs, same day and overnight coverage, restart, repeated sync, stale preview, duplicate keys, auth, rollback, old fee overlap, quantity/unknown-order guards, Fujimoto aggregate-vs-order costs, migrations and UI. Review code and docs, compile changed Python, inspect git diff --check. UI screenshot uses isolated fixture data only.

## Rollout / rollback
Additive 0042 migration, no data backfill. Observe records evidence only; auto enabled only after operator verifies real KIS paper expense behavior and date boundaries against statements. Historical gaps require statement settlement. Rollback to observe stops new automatic application; previously accepted costs and audit receipts remain. Do not downgrade populated audit tables or erase histories.

## Decisions / evidence
- User chose actual automatic + manual exceptions and mandatory broker statements (no new owner-only attestations).
- Existing confirmed fee adjustment is preserved and never automatically relabeled.
- Official KIS inquire_balance chk example defines thdt_tlex_amt/bfdy_tlex_amt; paper response population has not been measured.
- Worktree: C:/Users/jack/.codex/worktrees/84af/maps; branch codex/paper-cost-settlement.
- Test results and review findings will be appended here during implementation.

## Session checkpoint (2026-10-12)

User requested commit/push of all branches and handoff before final integration. Task 2 and Task 3 code are implemented with focused tests; full final verification, documentation and review closure remain. See [handoff](../../handoffs/2026-10-12-paper-cost-settlement.md). Do not treat this checkpoint as production-ready.

Final checkpoint focused run: 138 passed, 18 existing Alembic warnings in 77.33 seconds (core, API, persistence, UI, KIS adapter and migrations). Full suite was interrupted after failure markers; failure summary and clean final full-suite run remain outstanding.

Observe mode also retains incomplete cost coverage as a block and does not advance the complete baseline. Order-only historical receipts cannot establish current account-cost coverage. Historical order statements use their linked paid interval; orders already partially filled at that interval baseline require explicit reconstruction. Cross-day automatic coverage currently assumes two stable observations after 18:00 KST are closing evidence, with no missing trading day; this assumption needs actual-paper verification. No .env.example exists in this checkout; none was created.
