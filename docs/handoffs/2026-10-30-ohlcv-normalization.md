# OHLCV normalization implementation handoff — 2026-10-30

## Status: INCOMPLETE — user requested stop

Branch `fix/ohlcv-normalization-20261030`, worktree
`D:/workspace/maps/maps/maps/.worktrees/ohlcv-normalization`.
Implementation and review were stopped at the user's request. No deployment,
production DB/config/order changes, historical repair, or activation occurred.
Root worktree's pre-existing user edits are untouched. Do not deploy this branch yet.

## Implemented so far

- Bounded regular-session per-security KRX source, distinct adjusted close, strict
  parsing/date/identity checks, no_trade outcomes, public source evidence, guarded
  authenticated session transport with finite timeout and request/time budgets.
- Additive 0045 migration for collection run/item and daily-stage claim tables.
  Durable checkpoints, owner fencing, atomic price publication, immutable completed
  input snapshots, preserve-good-price behavior, request-budget resume.
- Daily collector integration, normalized list compatibility interface, explicit
  historical helper. Historical helper rejects current/future dates; legacy upsert
  refuses to overwrite normalized source rows. Manual halt overrides retained.
- Durable collection -> candidate -> validation dependencies with configured minimum
  times, manual/cron shared claims, heartbeat, restart detection, actual adjusted
  price requirement, pipeline status and incomplete-close-report annotation.
- Package docs/migration docs updated; approved plan maintained in
  `docs/superpowers/plans/2026-10-30-ohlcv-normalization.md`.

## Observed tests (not final acceptance)

- Source tests: 25 passed.
- Source + persistence after review fixes: 38 passed (before newest regression).
- Scheduler + related suite: 67 passed.
- Collector OHLCV validation including legacy protection: 48 passed.
- Integration run: 140 passed, 1 docs-index failure. The tree fix was initially
  inserted outside the fenced tree; corrected afterwards, not revalidated yet.
- Migration/docs run: 34 passed, 1 same docs-index failure before final tree fix.
- Latest new regression `test_discovery_evidence_stays_in_audit_not_job_quality`:
  FAILED as intended (RED); implementation fix deliberately NOT made after stop.
- Full isolated suite was running in tool session 34268, output `full-suite.log`
  (ignored local artifact). Stopped Python PIDs 11444/7964 at user request, around
  25% progress; NO full-suite pass. It started before the newest RED regression.
- Task-level py_compile/diff checks passed earlier; final branch verification pending.

## Must fix / verify next

1. **Known failing regression:** public result/job quality currently includes full
   `universe_evidence` raw membership/basic responses. Keep these in durable audit
   storage, strip from returned quality and reconstructed result; preserve small
   provenance/count fields. Regression is already present and failing.
2. **Whole-branch review incomplete:** suspected implicit SQLAlchemy transaction
   from reading committed `sector_run` ORM attributes before external fundamental
   and investor-flow calls. Add `db.in_transaction()` assertions at auxiliary
   network boundaries; close read-only transactions without losing pending writes.
3. Revalidate docs-index correction and migration test added for preservation of
   existing historical bars and refusing downgrade of populated audit tables.
4. Finish independent whole-branch review and address findings. Existing cross-review
   covered source identity/transport, immutable checkpoints, lease fences, legacy
   overwrite protection and manual halt preservation; it is NOT final approval.
5. Run full isolated suite after fixes, compile and diff checks. No real KRX/broker
   calls from tests. Update ExecPlan with exact final results and remaining limits.
6. Throughput and operating rollout remain unverified. Do not claim total-volume
   finality, normalized historical data, or production readiness from fixtures.

## Resume commands (PowerShell, worktree root)

```powershell
D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests/test_ohlcv_runs.py tests/test_ohlcv_source.py tests/test_daily_pipeline.py tests/test_collector_ohlcv_validation.py tests/test_migrations.py tests/test_docs_index.py -q --tb=short
D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests maps/tests -q --tb=short
git diff --check
```

No implementation agent is continuing writes. Production rollout and historical
355-row repair remain separate, unperformed operations. New audit tables must be
preserved on rollback if populated.

