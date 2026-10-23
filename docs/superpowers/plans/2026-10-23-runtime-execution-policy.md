# Runtime execution policy and pick reconciliation

## Purpose and binding decisions
Implement the approved plan: durable account-scoped execution settings editable
without restart, supporting disabled / Fujimoto-only paper execution. Environment
settings are the fallback only until a policy is saved. Dry-run and real-account
guards are never overridden. No operational orders are enabled by deployment.

Use one policy table with optimistic versioning and existing safety audit events.
Serialize policy writes and dispatch through the existing account writer lock.
Read effective policy at scheduling and actual broker dispatch; storage failures
block execution. Trusted dispatch source is process-local, never client input.
The admin API requires same-origin mutation and exposes no real-account switch.

Reconcile completed picks from linked, same-account terminal order evidence,
independent of execution flags. Never infer closure from absent holdings alone.
Historical repair is an explicit-ID dry-run/apply script with audit evidence.

## Progress
- [x] Inspect production evidence and existing boundaries; four fully sold single picks remain BOUGHT.
- [x] Implement policy model/migration, API/UI and common dispatch checks.
- [x] Implement durable pick completion and evidence-only repair tool.
- [x] Run regression, migration, offline UI and full isolated checks; review final diff.

## Acceptance and rollout
Test hot changes, persistence, stale versions, authentication/origin, fail-closed
DB failures, scoped buy/sell/cancel, pending-to-filled reconciliation, partial
fills, account mismatches and idempotency. Update package and operations docs.
Deployment requires migration before starting new code. Keep trading disabled.
Before rollback disable execution; do not drop populated policy/audit history.
The subsequent user request authorizes production deployment, migration and
explicit correction of picks 13, 14, 16 and 18. It does not enable trading.

## Production rollout progress
- [x] Recheck production after the 16:00-16:45 KST deployment exclusion; analysis lock is free.
- [x] Confirm clean tracked production files, baseline commit 56021f6 and migration 0043.
- [x] Back up the four picks and their linked orders privately on the server;
  record environment/control and complete order/fill fingerprints.
- [ ] Deploy the reviewed commit and apply migration 0044 before restarting.
- [ ] Preview and apply the four evidence-backed historical closures.
- [ ] Verify health, effective disabled policy, audit events and unchanged orders/settings.

## Validation evidence
- Focused watchlist/policy suite: 75 passed.
- UI logic, policy and documentation smoke suite: 93 passed.
- Initial full suite: 2484 passed, 2 failed (changed UI wording assertion and the
  formerly database-free config API fixture). Both fixtures were corrected.
- Final `python scripts/run_isolated_tests.py tests maps/tests -q --tb=short`:
  **2495 passed**, 2604 dependency/existing warnings, 565.26 seconds.
- The final Fujimoto restart-free settings guidance also passed its focused
  shipped-JavaScript regression after the wording update.
- Isolated localhost HTTP integration passed: enable/disable changed versions
  1/2 in the same process; after process restart version 2 remained disabled.
- Python bytecode compilation and `node --check static/js/app.js` passed.
- Repair CLI integration on synthetic SQLite data passed preview (no writes),
  explicit apply (CLOSED + one audit event), and repeated apply (no new changes).
- Final implementation `git diff --check` passed. The implementation turn did
  not change production; the authorized rollout is tracked separately above.
- Real-browser QA attempted on isolated localhost; Chrome extension UI blocked
  automation. Visual browser verification remains uncompleted; offline shipped-JavaScript
  tests cover rendering, PUT success/failure/conflict and duplicate-click guards.
