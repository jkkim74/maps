# KIS paper cost settlement ExecPlan

## Purpose and binding design
Implement actual-cost automatic reconciliation with statement-backed manual exceptions. Never infer costs from a rate or cash residual. KIS paper only; live and mock contracts remain unchanged. Default is observe. No production DB, broker orders, activation or deployment in this work.

Collect current/previous-day expense fields with the execution balance response. Missing/invalid amounts are unknown. Persist immutable evidence and idempotent settlement receipts. Only current-session dated totals can support automation; previous-day values are diagnostic. Automatic interval costs require complete coverage, exact order gross, consistent quantities, no unknown orders and two stable observations. Missing days, corrections, overlapping legacy adjustments require manual evidence. Store latest reconciliation breakdown separately from historical adjustment rows.

Manual flow: statement source, SHA256, period and actual amounts -> read-only preview -> atomic commit with semantic fingerprint and idempotency key -> next broker reconciliation. Commit performs no broker calls and never opens trading. Account aggregate costs must not be allocated to orders. Exact order statements may use existing Fujimoto settlement with no nested commit. Unsupported broker capabilities are diagnostic; actual reconciled coverage is the gate. Existing cost/loss/ownership protections remain.

## Progress
- [x] Investigate production symptom (prior read-only task): a confirmed fee, an older pending difference, and a later additional cost; no quantity difference. Regression amounts are synthetic and never production defaults.
- [x] Create managed isolated worktree, feature branch.
- [x] Task 1: adapter cost observation, settings, evidence/receipt tables and migration; focused red/green tests.
- [x] Task 2: interval accounting, stable auto reconciliation, semantic preview/atomic manual settlement; incident and failure tests.
- [x] Task 3: risk API/UI and Fujimoto readiness/order-cost integration; API/UI regression tests.
- [x] Task 4: documentation, full isolated tests, fixes and final self-review.
- [ ] Independent final reviewer sign-off and measured KIS paper evidence before operational auto rollout (not performed in this single-agent continuation).

## Resumed verification (2026-10-12)

The original implementation worktree is absent on this machine. Continue on
`D:/workspace/maps/maps/.worktrees/paper-cost-settlement`, tracking
`origin/codex/paper-cost-settlement` at checkpoint `b9d99c0`. Preserve the unrelated
ETF/backtest edits in the original checkout. The available interpreter is
`D:/workspace/maps/maps/.venv/Scripts/python.exe`.

- [x] Reproduce the checkpoint suite without concurrent source edits: one date-dependent failure reproduced; final pytest cleanup hit an unrelated Windows temporary-directory permission error.
- [x] Close reconciliation/receipt, shared allocation, restart and mode-transition
  regression gaps; fix confirmed root causes only.
- [x] Update module contracts and operator instructions; render the fixture UI.
- [x] Run final focused/full checks and review the complete change against master.

No production migration, configuration change, deployment or activation is part
of this continuation. Test output is retained outside the repository under the
system temporary directory; all account fixtures remain synthetic.

## Interfaces and file ownership
Task 1 owns broker_adapter.py, kis_adapter.py, settings.py, models.py, migration 0042 and their tests/docs. PositionSnapshot.cost_observation is optional dict: source, ref_date (ISO KST receipt day), observed_at (ISO UTC), cash_field, today_total (decimal string/null), previous_total (decimal string/null), raw (allowlisted fields only), valid (bool). All nonempty valid amounts are finite nonnegative Decimal strings. Invalid fields never become zero.

PaperCostEvidence table: id; account_key indexed; environment; kind (balance/account_statement/order_statement); ref_date date; observed_at datetime; amount nullable Numeric(24,4); fingerprint String(64); payload JSON; supersedes_id nullable integer; unique(account_key,fingerprint). PaperCostSettlement table: id; account_key indexed; idempotency_key String(128); request_hash String(64); created_at datetime; actor String(128); payload JSON; result JSON; unique(account_key,idempotency_key). Evidence/receipts append-only, ORM guards reject update/delete.

Task 2 owns new maps/execution/paper_costs.py and reconciliation.py. Store balance observation fingerprint excluding receipt timestamp to deduplicate same day values; retain actual received_at in payload and account observations. Reconciliation snapshot stores coverage and actual residual, not guessed fees. Account statement interval is anchored to complete baseline and latest observation, with included legacy fee adjustment IDs explicitly tied to the statement; final compensation is only the uncovered portion. Settlements remain valid in observe mode.

Task 3 adds GET safety breakdown and POST /execution-safety/settlements/preview, /commit. Exact schema is defined alongside implementation/tests and shared with UI before editing it. Reject stale semantic changes but accept timestamp-only refresh; one transaction including Fujimoto costs and receipt. Existing /classify remains for non-cost adjustments; paper fees require statements. UI uses textContent and escaped DOM, no document upload/OCR. Default auth/admin gates remain; Fujimoto owner scope remains.

## Validation
Use D:/workspace/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py with relevant tests, then tests maps/tests -q --tb=short with a dedicated --basetemp. No real broker network. Test real incident arithmetic, two-observation automation, missing/negative/empty/zero costs, same day and overnight coverage, restart, repeated sync, stale preview, duplicate keys, auth, rollback, old fee overlap, quantity/unknown-order guards, Fujimoto aggregate-vs-order costs, migrations and UI. Review code and docs, compile changed Python, inspect git diff --check. UI screenshot uses isolated fixture data only.

## Rollout / rollback
Additive 0042 migration, no data backfill. Observe records evidence only; auto enabled only after operator verifies real KIS paper expense behavior and date boundaries against statements. Historical gaps require statement settlement. Rollback to observe stops new automatic application; previously accepted costs and audit receipts remain. Do not downgrade populated audit tables or erase histories.

## Authorized production deployment (2026-10-12)

After development verification, the user explicitly requested production deployment.
This expands the earlier development-only scope to committing/pushing the tested
tree, fast-forward integration, a server-local database/config backup, additive
migration 0042 and service restart. Preserve observe and all existing strategy and
broker controls; do not submit orders, settle actual costs or activate auto.

- [x] Confirm production starts at `0f02fa8`, schema 0041, tracked files clean,
  KIS paper, no unresolved intents, and analyze idle outside the blocked window.
- [ ] Commit/push the tested tree and fast-forward master without altering the
  original local checkout's unrelated work.
- [ ] Verify PostgreSQL backup and preserve the server environment.
- [ ] Apply 0042, restart maps, check internal/external health, authenticated
  read-only cost API and scheduled broker observation without changing controls.
- [ ] Record deployed revision and verification in the handoff.

Rollback: if startup fails, inspect filtered logs and return application code to
the recorded previous revision, retaining additive schema/audit history. Do not
restore a database over newer trading records or downgrade populated audit tables.

## Decisions / evidence
- User chose actual automatic + manual exceptions and mandatory broker statements (no new owner-only attestations).
- Existing confirmed fee adjustment is preserved and never automatically relabeled.
- Official KIS inquire_balance chk example defines thdt_tlex_amt/bfdy_tlex_amt; paper response population has not been measured.
- Original checkpoint worktree: C:/Users/jack/.codex/worktrees/84af/maps (no longer available locally); continuation path is above.
- Reproduced allocation bypass: replacing an account statement reset prior order allocations. Validation now sums latest order evidence over the covered observation interval, including every order in the draft.
- Reproduced automatic receipt leak: historical quantity markers can reject adjustment resolution after a receipt is staged. A savepoint now rolls back the receipt, adjustment mutation and success event together while reconciliation retains diagnostics.
- Reproduced API failure: a decreasing Fujimoto cost raised DataQualityError as HTTP 500. Commit now returns 409 and rolls back the account and order writes. Amounts also respect the Numeric(24,4) storage bound at schema validation.
- Reproduced unrelated date-sensitive expiry fixture failure; pin its created_at before the fixed test expiry, leaving runtime behavior unchanged.
- Added actual reconcile-path guards, post-mutation rollback, mode switch/new-session idempotency and replacement-statement cumulative-limit regression tests. The final atomic subset passed: 22 tests, one existing Starlette warning.
- Wider focused run passed 252 tests with 19 existing warnings in 327.37 seconds (paper costs, Fujimoto execution/trial/UI, KIS, migrations and docs). The last atomic regressions were added after that run started; they are included in the 22-test subset and final full suite, so these counts are not additive.
- Browser evidence: [synthetic fixture screenshots](../../evidence/paper-cost-20261012/README.md). Server and tab closed after verification.
- This continuation uses one agent and a final self-review. Earlier independent review findings are covered by regressions; no new independent reviewer sign-off is claimed.
- Final fixed-tree `tests maps/tests` run: **2009 passed, 2596 warnings in 614.48 seconds**, exit 0. Command: `D:/workspace/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests maps/tests -q --tb=short --basetemp=$env:TEMP/maps-paper-cost-full-final-20261012 --junitxml=$env:TEMP/maps-paper-cost-full-final.xml`. Log/JUnit remain in TEMP. Warnings are existing NumPy/pandas, Starlette, Alembic, SQLAlchemy and test diagnostics.
- All changed Python files compile; shipped JavaScript passes `node --check`; full branch `git diff origin/master --check` passes. Reviewed changes and credential-pattern scan found no private keys or token patterns. No runtime/source edits were made during the final full run.
- After final result documentation, `test_docs_index.py` passed all 24 tests in 0.35 seconds.

## Original session checkpoint (2026-10-12; superseded by resumed verification above)

User requested commit/push of all branches and handoff before final integration. Task 2 and Task 3 code are implemented with focused tests; full final verification, documentation and review closure remain. See [handoff](../../handoffs/2026-10-12-paper-cost-settlement.md). Do not treat this checkpoint as production-ready.

Final checkpoint focused run: 138 passed, 18 existing Alembic warnings in 77.33 seconds (core, API, persistence, UI, KIS adapter and migrations). Full suite was interrupted after failure markers; failure summary and clean final full-suite run remain outstanding.

Observe mode also retains incomplete cost coverage as a block and does not advance the complete baseline. Order-only historical receipts cannot establish current account-cost coverage. Historical order statements use their linked paid interval; orders already partially filled at that interval baseline require explicit reconstruction. Cross-day automatic coverage currently assumes two stable observations after 18:00 KST are closing evidence, with no missing trading day; this assumption needs actual-paper verification. No .env.example exists in this checkout; none was created.
