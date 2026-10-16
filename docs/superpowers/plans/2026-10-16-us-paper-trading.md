# US paper trading ExecPlan

## Purpose and binding design

Implement docs/us-trading-program-list.md and docs/ui-design/us-trading-screen-design.md. US dedicated account; local mock and KIS paper only; disabled by default. No real endpoint orders, strategy activation, external holding adoption or production migration. Credentials never copied from domestic settings. Order safety and recovery are required, not optional UI behavior.

## Implementation units

- [x] Execution core: explicit US configuration, common Order/ExecutionContext metadata, Decimal durable ledger and migration, source policy, locking/reservations, idempotency, reconciliation, ownership, limits and persistent local mock. Focused regression tests before implementation.
- [x] Broker and market: paper-only KIS adapter using existing transport, official endpoint evidence, complete pagination and conservative unknown handling; US calendar/price rules and mock HTTP tests.
- [x] UI: server template and static JS for all US-S01 through US-S07, keyboard-accessible preview/confirmation, safe status and uncertain orders; no raw HTML from API values.
- [x] Integration: administrator schemas/router, main/nav/lifecycle, periodic synchronization, docs/example settings and operational smoke checks.
- [x] Offline review and verification: focused suite, domestic regressions, temporary DB migration, syntax checks and full regression run; results and limitations below.
- [ ] Operational acceptance: actual KIS paper-account smoke and visual screenshots remain unverified.

## Architecture decisions

Existing domestic execution has KST/float/account-global assumptions. US service will reuse common order contracts, exception types, locking and audit patterns but own Decimal ledger if needed to preserve domestic compatibility. Any deviation from shared persistence is recorded here and in user-facing docs. UI and future strategy producers call the same US execution service; the broker remains source-agnostic.

Use worktree .worktrees/us-paper on feat/us-paper-trading. Python executable is original repository .venv/Scripts/python.exe. No .env copied. Mock fixtures and isolated databases only. Existing unrelated diary files stay untouched.

## Acceptance and observed checks

US-T01 through US-T11 from program list apply. Real KIS paper smoke requires dedicated credentials and must be reported unverified if unavailable. Missing costs/quote timestamps must block buys, never be synthesized. US strategy operational sources remain disabled; test-only producers demonstrate reuse.

- Baseline: tests/test_docs_index.py: 24 passed.

## Progress / review ledger

- Core, adapter, API/runtime, simulator and seven administrator screen sections implemented. Shared contracts and account locks are reused; US Decimal persistence is separate from domestic accounting.
- Final focused US + documentation tests: 92 passed, including stale marks for other holdings and KIS transport cleanup regressions. KIS/runtime 24 passed separately.
- API/UI/runtime/simulator/migration integration: 28 passed, 13 Alembic configuration deprecation warnings (before the additional disabled-sync regression).
- Baseline domestic execution safety: 71 passed, one existing SQLAlchemy warning.
- Additional package tests (`pytest maps/tests`): 81 passed.
- Final migration suite: 7 passed, 15 existing Alembic configuration deprecation warnings. New USD fixed-precision tables and nonempty-ledger downgrade refusal verified.
- Python 3.11 syntax inspection: 23 changed/new modules passed. JavaScript syntax and staged diff whitespace checks passed.
- HTTP smoke with disabled US configuration and an isolated local database: screen, status, account, positions, orders and events all returned HTTP 200.
- Review corrections: cancellation replay suppression; fresh pre-submit cash/quantity comparison; trading-date-qualified KIS order IDs; cancelled zero residual handling; sticky UI UNKNOWN responses; unresolved synchronization after disable; transport cleanup forwarding.
- Full regression run: 1,892 passed / 8 failed / 17 warnings (743 seconds). Five failures were stale modules loaded before concurrent implementation corrections (KIS dated IDs, two migration-head expectations, pending cost handling, disabled runtime fixture); latest focused reruns passed. Two failures exposed the missing new screen in the existing authentication design matrix; that document now includes the administrator-only US screen and updated count.
- Remaining baseline failure: `tests/test_fujimoto_execution.py::test_observation_expiry_does_not_expire_unknown_orders` also fails in original master at `0f02fa8`. It creates a cycle using the current clock but requests expiry at hard-coded 2026-10-09; on 2026-10-16 no expiry evidence is created, producing IndexError. Reproduced separately in both worktrees. The unrelated domestic test/source was not changed. Full suite is therefore NOT claimed green.
- Final regression rerun after the auth-document fix: 113 passed / 15 Alembic configuration deprecation warnings (53 seconds). Includes all US tests, auth screen documentation, migrations and documentation-index contracts. Implementation remains on `feat/us-paper-trading` in the preserved worktree; original master and diary files are unchanged.
- Visual screenshot verification could not run: Chrome automation reported another extension UI blocking access, and the in-app browser is unavailable. Template rendering, JavaScript syntax and event-flow tests passed; visual layout remains unverified.
- US-T11 actual KIS paper-account validation is NOT performed: dedicated credentials are unavailable. WebSocket availability, account entitlement and actual paper order/fill/cancel behavior require account validation. No production credentials or orders were used.
- KIS paper cost evidence remains unavailable; confirmed quantities can support explicitly authorized exits, while missing fees/tax prevent further buys. Prior-day cancellation is refused because the documented cancellation request cannot disambiguate reused raw IDs by original trading date.
- Single-host writer only. Local simulator CLI requires stopping the app if its account writer lock is held. No automatic liquidation, real trading, strategy activation or production database migration.

## Rollback

Disable US execution and retain sync for unresolved orders. Revert app wiring to stop US subsystem without altering domestic behavior. Never drop ledger tables with unresolved orders; migration downgrade is only for empty/test databases. No automatic liquidation or cancellation on disable.


## Production deployment follow-up (2026-10-16)

User explicitly requested production deployment. Follow the documented Lightsail target (`/opt/maps`, systemd `maps`, maps.magable.kr). Do not enable US trading, alter domestic trading settings or submit orders.

- [x] Read deployment runbooks, verify SSH and current production revision.
- [x] Production is on `be7901a`, newer than the original feature base. Integrated its domestic paper-cost settlement changes without replacing them. Its existing date-fixture correction resolves the baseline expiry test; the redundant local fixture change was discarded.
- [x] Renumber US migration to `0043_us_paper_trading` after deployed `0042_paper_cost_settlement`; retain both domestic and US models/contracts.
- [x] Full integrated regression: 1,997 passed / 24 existing warnings in 671 seconds; separate package tests: 81 passed. Python 3.11 syntax, JavaScript syntax, single Alembic head 0043 and diff whitespace checks passed. Release commit prepared from this verified tree.
- [x] Release `8e1601ae816e0f0140f0d4878d1b896742167396` pushed to origin/master by fast-forward; unrelated untracked local/server files preserved.
- [x] After 16:45 KST under the analyze lock: database/config backup, dependency installation, transactional PostgreSQL migration 0042 to 0043, service restart and health verification completed.
- [x] Verified deployed code revision, schema 0043, systemd active/running with NRestarts=0, external health HTTP 200/ok, administrator screen/status/account/positions/orders/events HTTP 200, anonymous API HTTP 401. US status: environment mock, buy_allowed=false, sync_running=false, us_disabled. Config file hash matches backup.

Observed preflight: 16:33 KST server running, analyze lock available, tracked source clean, PostgreSQL schema 0042, active queried order intents 0. Domestic KIS remains paper (`kis_real=false`), global execution true and paper-cost settlement observe. These settings are preserved. Exchange calendars is a new server dependency; websockets 16.0 is already installed. No server mutation occurred during the prohibited deployment window.

Rollback: preserve predeployment commit and database/config backups. If startup or migration fails, restore previous application revision and restart; additive empty US tables may remain. Do not automatically downgrade nonempty trading ledgers or restore the database over newer trading evidence.


### Deployment evidence

- Code release: `8e1601a`, 2026-10-16 after 16:45 KST.
- Successful backup: `/opt/maps/backups/pre_us_20261016_164700/`; `database.dump` is 394,192,959 bytes, PostgreSQL archive listing verified. Environment backup mode 600; directory mode 700. Restore was not rehearsed in this deployment.
- First backup attempt at `pre_us_20261016_164628` failed because maps_app cannot read historical `order_log_backup_20260724`; it is NOT a valid database backup. Service/source were unchanged at failure. Retried using local PostgreSQL administrator read access, with no table permission changes.
- Added dependency exchange-calendars 4.13.2; existing websockets 16.0 retained; pip check passes.
- Authenticated smoke script initially expected the runtime error code `us_trading_disabled`; status contract uses `us_disabled`. Corrected the smoke assertion against source and reran all six requests successfully; application code did not change.
- Production `.env` unchanged byte-for-byte. Domestic KIS remains paper and actual-cost reconciliation remains observe. No order, cancellation, liquidation, strategy activation or US account setup was performed.
- KIS US paper-account execution and visual screenshot acceptance remain outstanding. The deployed UI is available to administrators at https://maps.magable.kr/us-trading.
