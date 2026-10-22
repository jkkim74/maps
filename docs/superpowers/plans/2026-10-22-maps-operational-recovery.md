# MAPS operational recovery ExecPlan — 2026-10-22

## Purpose and binding decisions

Implement the approved conversation design: correct order readiness/account scope,
job status/serialization, stale upper-limit decisions and recovery, KIS token/send
races, Naver theme contract, and evidence-based recent/current-input data recovery.
Production orders remain OFF, upper-limit recommend_only, Fujimoto observe.
Never liquidate existing holdings, weaken thresholds (KRW 50bn/strength 150),
change strategy eligibility, fabricate source evidence, backdate availability, or
overwrite immutable decisions. No DB schema migration. Preserve user changes.

Upper-limit receipt freshness is strictly <3 seconds, monotonic. Signal lifetime
is fixed at original crossing +15 seconds for both legs; fresh current entry
conditions must be rechecked immediately before each HTTP send. A feed gap/reset
invalidates old signals permanently. Resume automatically only on a new crossing.
Keep raw Fujimoto recording independent; bound decision feed work to 256.

Recovery scope: failures since 2026-10-16 through latest completed KRX session,
plus missing/invalid historical inputs actually required by current strategies.
Default dry-run; exact manifests, before/after, source/hash, observed time,
optimistic conflict check, idempotency and selective rollback are mandatory.
Current theme memberships must never be assigned to missing historical dates.

## Execution and validation

Worktree C:/Users/jack/.codex/worktrees/3087/maps, base 5c5c459.
Use D:/workspace/maps/maps/maps/.venv/Scripts/python.exe with this worktree's
scripts/run_isolated_tests.py (mock DB/broker, blocked external network). Baseline
audit: 247 relevant tests passed; new baseline/results recorded below.
Read index.md -> package CLAUDE.md -> code. TDD for substantive bugs. No new
dependencies or broad refactors. Update package docs. Scoped tasks may run
independently with explicit file ownership; integration/review remains sequential.
Do not publish/send notifications or activate trading as part of tests/recovery.

## Task 1: Order readiness, account-scoped API and truthful UI

Owner files: maps/api/orders.py, order portions of maps/api/schemas.py,
maps/execution/readiness.py (new helper if useful), static/js/app.js order screen,
templates/orders.html, apps/mobile relevant types/screens/tests, tests for orders.
Read API/execution/mobile docs. No edits to safety.py or order_manager.py.
Expose execution_state ready/blocked/unknown, block_reasons, checked_at,
strategy-specific blocks; preserve boolean auto_order_active derived from ready.
Use configuration global enable/dry-run/live confirmation, current account READY,
kill status, reconciliation freshness (existing max age) with no broker polling.
Strategy-specific kills must not falsely mark the whole account active/blocked.
Filter order lists to exact current account+environment; unknown legacy accounts
are not adopted. Pending query independent of 100 recent rows, include partial
remaining shares. Today means KST order-created day, not invented fill timestamps;
label today orders' cumulative fills including canceled partial fills. Actual
slippage uses this same scope, aggregate absolute deviation and sample count,
null when absent; unknown cap classes null, assumptions separately retained.
Web/mobile show actual readiness/reasons and truthful collection basis.
Tests: OFF/dryrun/unconfirmed/unknown/stale/ready/account and strategy kills,
cross-account rows, pending older than history window, canceled partial fills,
KST boundary, no samples/null, UI visible reasons; mobile test/build.
Return task-1-report.md with RED/GREEN commands, results, changes, limitations.

## Task 2: Explicit scheduler outcomes and normalized result recording

Owner files: maps/ops/scheduler.py, maps/api/batch_monitor.py if needed,
scheduler tests/docs. Explicit JobResult statuses success/skipped/partial/failed
for OFF/skip branches and incomplete required source collection. Successful
evaluation with zero strategy passes remains successful evaluation.
Normalize date/datetime and supported result values once before persistence,
memory/log/API; no generic default=str concealment. Unexpected values fail with
explicit diagnostics, never replay business work. Durable last runs after restart.
Tests real job outputs, dates, disabled skips, DART partial, restored status.
Return task-2-report.md with RED/GREEN evidence.

## Task 3: KIS single-flight authentication and final send safety

Owner files: maps/execution/kis_adapter.py, broker_adapter.py, order_manager.py,
tests/test_kis_adapter.py and order-manager tests, execution docs. Coordinate
readiness helper via separate file. Add transient Order.pre_send_check optional
Callable[[], None], repr/compare excluded, never serialized. Manager composes
deadline/current-quote/execution/account-snapshot checks into broker order copy;
KIS propagates guard through _request/_send_with_retry and invokes after pacing
immediately before EVERY actual send, including token refresh/retry. No new guard
on cancellation. Known non-sent expiry releases reservation using terminal status;
ambiguous transport remains UNKNOWN/no resend.
Same credential cache key token issue uses keyed lock + double check; rejected
old token CAS invalidation; shared 60s token-issue cooldown on EGW00133; atomic
cache file replacement. Keep credentials out of diagnostics. All HTTP paths
honor existing pace interval; release request serialization before auth/retry.
Expose rank-only and per-ticker details methods for runtime early eligibility
filter (retain get_limit_up_candidates compatibility). No DB/API/limit-up edits.
Tests concurrent refresh, stale rejection, cooldown, real send spacing, delayed
hash/auth/pace and safe retry guard, UNKNOWN safety, compatibility mocks.
Return task-3-report.md with interface signatures and RED/GREEN evidence.

## Task 4: Upper-limit freshness, reconnect and bounded decision work

Owner root: maps/limit_up/*, limit-up portions api/schemas + api/limit_up.py,
tests/test_limit_up*. Add receipt UTC and connection generation. Maintain latest
market observations independently of blocked service pump. Enforce <3s before
machine/virtual fills/lock timers, queue cap256, observable gaps, invalidation.
Do not corrupt raw recording. Feed halt separate from market/loss halt; connection
alone does not recover. Per ticker first fresh trade seeds then new crossing.
Pass original 15s deadline and lightweight quote predicate through fire_grid to
ExecutionContext; current snapshot verifies fresh price/buy/turnover/strength.
Gap invalidation survives later fresh messages. If leg two blocks, cancel pending
buys and reconcile all outcomes. Preserve real holdings and protection fallbacks.
Scanner rank -> filter watching/metadata -> detail; single scan task doesn't stall
timers/fallback/index. Status reports configured mode/start outcome/reason and
feed age/backlog/drop counters. Existing /health remains liveness.
Tests boundary/stale120s/epochs/overflow/recovery+loss guards/partial grid and
runtime lifecycle; API bootstrap refused reason and recommended no-order behavior.

## Task 5: Naver theme contract repair

Owner maps/data/naver_themes.py, tests/test_naver_themes.py, data docs.
Catalog list paging stays. Member requests use marketType KOSPI and KOSDAQ,
size50, cursor paging dict items/hasNext/cursor, itemCode/itemName. Empty market
is valid. Validate shapes, market, duplicate memberships, cursor loops/max100
pages. Preserve mobile verification for catalog count discrepancy and final
catalog equality; publish only complete snapshot, retain last known good.
Tests recorded-shaped fixtures, both markets, pagination/empty/failure/retention.

## Task 6: Evidence-based recovery CLI and operational recovery

Implement dry-run inventory/manifest/apply/rollback CLI using existing collection,
historical OHLCV and audit primitives; exact source evidence and optimistic
before/after checks, no schema. Record source raw hash, observation timestamps,
full-row price basis, only trusted KRX per-security data for OHLCV. Reuse DART
bounded resumable collectors and preserve availability. No invented EPS reviews.
Recollect current complete themes; unavailable past themes remain unknown.
Audit current strategy input windows and recent rejected/missing data; normal
halt/nontrading gaps are classified not filled. Apply independently verified
manifest after backup restore check. Regenerate current derived evidence only.
Record counts, unrepairable gaps, unchanged decision evidence and remaining gates.
Tests dry-run zero changes, malformed source, idempotent apply, conflict, rollback,
source identity, original evidence preservation; operation tests stay mock/offline.

## Task 7: Integration, review, deployment and observation

Run all isolated tests tests + maps/tests, mobile test/build, diff check and review.
Use a separate review agent after task units and broad final review. Inspect current
production commit/config, backup restore readability, deploy while orders OFF,
verify APIs/job outputs without executing orders or unauthorized notifications.
Rollback code to predeploy version; only manifest rows for data rollback, never
restore whole DB over newer audit/orders. Full next trading-session plus afterclose
observation is pending until it actually occurs; unavailable natural signal/fill
evidence must not be called verified. Record implementation vs operational result.

## Progress / results

- 2026-10-22: Approved design saved; isolated worktree created.
- User workspace pre-existing modifications preserved in source checkout.
- Tasks 1–3 implemented and independently reviewed. Scheduler review found and
  fixed misleading closed-market sync failures and skip transitions lost across
  restart; latest-job/heartbeat-aware dedup preserves real failures and partials.
- Task 4 implemented; 225 scoped checks initially passed. Review additionally
  found silent quote timers and cancel-failure/late-fill recovery gaps. Timer
  freshness, delayed-cleanup recheck and shared-runtime status fixed (95 focused
  checks passed); final cancellation recovery verification in progress.
- Task 5 implemented/reviewed; 145 scoped checks passed. Three live public probe
  responses confirmed KOSPI empty terminal/null cursor and KOSDAQ 50+18 pages.
- Task 6 local tooling implemented/reviewed. Review hardened prior-KST-day-only
  volume evidence, exact Decimal volume parsing and immutable repository receipts.
  Expanded 133 checks passed; independent re-review 15 checks passed.
- Operational read-only inventory/source fetch started with orders OFF and
  recommend_only unchanged. Recent Oct16/19/20/21 missing cells699, stored invalid
  OHLC ranges0. Missing observations are being classified against actual KRX
  per-security raw responses, including legitimate no-trade and unavailable ISIN.
- Full-suite preliminary run: 2418 passed/4 failed (490.35s). Four source-inspection
  failures read shifted lines while root/agent source edits were in progress;
  a fresh full run on the stable final tree is required before deployment.
- Final stable-tree suite: **2452 passed, 24 warnings in 483.54s**;
  mobile **36 passed** plus TypeScript/Vite production build passed. Final
  independent integration review approved tasks 1–6; no open code blockers.
- Final Task 4 cancellation/late-fill recovery regressions: 130 passed.
- Production backup (2,606,871,742 bytes) successfully restored into a disposable
  database: 5,991,328 OHLCV rows, 272,766 Fujimoto evidence rows, 6 order intents.
  Restore exit 0; scratch database removed. Backup/source artifacts remain private
  under /opt/maps/backups/operational-recovery-20261022.
- Read-only source preparation: 520 exact raw envelopes / 508 tickers / 9844 rows,
  all parsed without errors or duplicates. Recent 699 gaps: 239 repairable bars
  (all Oct21), 439 official no-trade, 21 unavailable. Current candidate-window
  1648 gaps: 1602 no-trade, 46 unavailable. Eligible ordinary-share historical
  4447 gaps were all official no-trade, with no synthetic fill warranted.
- Operational preparation scripts independently approved, including durable
  sector failure records and stopping quarterly work after incomplete annual
  collection. Actual manifest review/apply and deployment remain pending.
- Implementation commit 2247849 was fast-forwarded to master and deployed at
  15:05:22 KST. Service active; internal/external health and orders, upper-limit,
  batch-monitor and Fujimoto APIs all HTTP 200. Environment/config/order counts
  unchanged; orders blocked by trading_disabled, no pending/today fills;
  recommend_only and observe retained. This short check is not full-session proof.
- Actual price manifest narrowed from 633 to 239 missing Oct21 inserts. The 394
  historical rows have identical OHLC and different volume, with no established
  root cause; structurally valid existing bars remain outside this repair scope.
  All 520 envelopes match preserved source audits; all 239 targets/source hashes
  match independent review. Final narrowed manifest checksum/CAS approval pending.
- During bounded current-sector/theme and targeted annual source operations,
  SSH/HTTP stopped responding around 15:08 KST. No price apply occurred. Root is
  attempting to terminate only one-off collectors and recover server access.
  OOM is a hypothesis until kernel logs can be read. AWS API denied Lightsail
  access; existing browser has no AWS session, so user login assistance requested.
- Local investigation confirmed targeted AnnualCollector materialized every
  candidate's full/raw payload. Narrow projection, target/time SQL filters and
  streamed rows preserve latest-ID/as-of priority semantics. Regression initially
  failed on full payload SELECT, then 54 source/docs checks passed; independent
  PostgreSQL compilation/code review approved. Final full suite **2453 passed,
  24 warnings in 450.06s**; fix committed as de86422. This additional fix is
  not deployed while production SSH/HTTP remains unavailable. Source operation
  termination/result is unconfirmed; no price apply has been attempted.

## Decisions / risks

- No exchange fill-time ledger exists: retain compatible API key and explicitly
  label order-date cumulative fills, do not fake exact execution times.
- One service process is the verified credential concurrency scope. Do not claim
  multi-host token coordination or broker-internal arrival timing guarantees.
- Data/vendor availability and next full trading-day observation may remain pending;
  maintain explicit evidence and never equate no trades with a system failure.
- KRX per-security volume includes all trading, so repair envelopes require each
  price date strictly before the source observation's KST date. Same-day bars
  remain out of recovery scope even after the regular session closes.
- DART's 500-request/900-second budget is per collection client run, not proof
  of a daily exchange quota. Preserve bounded resumable collection and actual
  availability rather than forcing partial source coverage to pass screening.
