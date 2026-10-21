# Fujimoto start conditions implementation plan

> For agentic workers: use subagent-driven-development for implementation and independent task review. Operational steps remain with the controller.

**Goal:** Execute the user's approved sequence: correct feed/account latency, establish genuine selected-source evidence, verify real screening/subscriptions, and recheck the bounded paper trial.

**Architecture:** Keep account/order mutation serialized. Actual quote recording must not wait for unrelated broker reconciliation; every decision must check real current time after any wait. Use existing database/feed/runtime primitives with the smallest justified change. Preserve conservative freshness and source availability.

**Tech Stack:** Python 3.11+, SQLAlchemy, asyncio, pytest; actual KIS paper/production PostgreSQL only for operational observations.

## Global Constraints

- No real trading, existing holding adoption or liquidation; no trial activation while any readiness condition is missing. Retain global execution OFF during repairs.
- Quote freshness stays 3 seconds; account freshness stays 30 seconds. Never refresh a timestamp to disguise waiting or incomplete broker observations.
- Preserve account/order locks, immutable evidence, owner/account identity, recording ceilings, feed resets and shutdown safety. ORM sessions must never be shared concurrently.
- Tests use isolated DB/mock broker via `D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py`.
- Production truth comes from actual source responses. Missing financials/PER/price gaps do not become zeros, inferred passes or backdated evidence.
- No deployment 16:00–16:45 KST or while analysis is locked. Do not change unrelated local files.
- Existing eight strategies' validation failures are separate from Fujimoto's explicit paper_test approval route.

## Task 1: Remove demonstrated feed/account waiting defects

Files/interfaces: `maps/limit_up/runtime.py`, `maps/fujimoto/service.py`, `maps/fujimoto/feed.py`, `maps/execution/safety.py` only where needed; corresponding tests and package CLAUDE documents. Inspect scheduler/reconciliation callers before any change. Controller owns production diagnostics and final deployment.

Review fix scope: a transient quote-validity check must survive an in-flight account-lock or broker-read wait. Pass it through the existing execution context to OrderManager's final pre-submit guard where necessary; do not persist callbacks, relax any existing guard, or change behavior for contexts without this check.

- [x] Trace queue time, account-lock wait and broker-sync time. Existing evidence: 22/220 quotes over 3 seconds without diagnostics; account oldest snapshot estimated 41.7 seconds despite a 20-second interval.
- [x] Write a deterministic regression using a real held account lock and production quote path. Actual recording must remain possible during slow reconciliation, without order submission or concurrent Session use.
- [x] Write a stale-after-wait regression: a quote received before a blocking operation must never authorize a decision using the pre-wait clock. Original received time remains unchanged.
- [x] Implement the minimal correct path. Observation-only quote/timer work must not acquire an execution lock unnecessarily. If active order processing requires separate recording, isolate its Session/worker and retain event ordering/reset/capacity and safe drain semantics. Do not introduce an unbounded background executor or drop raw evidence silently.
- [x] Add bounded diagnostic timing (queue/lock/processing as justified) so production can distinguish delay contributors without logging every tick or secrets.
- [x] Run focused runtime/feed/Fujimoto/execution tests. Include reconnect, capacity, shutdown and existing ownership guards. Record red/green commands/results and implementation tradeoffs in the task report; commit task-owned changes only.

## Task 2: Account freshness and source evidence

- [x] Read production request/sync timings and current collection result. Reduce demonstrated redundant work only when independent evidence and tests prove the same completeness; keep conservative observation age and pacing. Do not blindly shorten the timer.
- [x] Inspect actual annual/DPS/PER availability for eligible companies and compatible peer groups. Collect missing supported source history through existing collectors, preserve raw responses and true availability. Identify genuine strategy failures separately from missing data.
- [x] If a PER applicability policy change is required, document its exact evidence/contract rather than silently changing screening criteria.

Targeted collector selection was considered but not implemented: actual DART inspection first showed that the two PER-qualified bank targets cannot currently establish a passing candidate. Avoid adding an ingestion interface solely to collect data that cannot resolve the present gate. Existing bounded all-market collection and read-only targeted source inspection are sufficient for this step.

## Task 3: Review, deployment and live readiness

- [x] Run independent task and full-change reviews, isolated tests, compilation and diff/secrets checks.
- [ ] Deploy exact reviewed commit after verifying service state and analysis lock; preserve DB/env backups and disabled execution.
- [ ] Check actual collection/screen results at their real scheduled times or a valid current-session manual screen. Use actual selected candidate subscriptions and live quotes for acceptance; after-hours silence cannot prove the next market session.
- [ ] Recompute status and account/cost/order/config/storage checks. Record remaining gates; if none remain except deliberate execution OFF, present the concrete scoped trial activation state for final handling. Never manufacture a candidate or a BUY.

## Progress / decisions

- 2026-10-21: User approved the ordered readiness work. Reused clean managed worktree at revision 869b814; original four local files preserved.
- Code inspection: runtime queue is serialized; `on_quote` records only after account lock; observe `tick` also locks; `now` is captured before lock waiting. Active tick additionally reconciles, while scheduler separately reconciles every 20 seconds.
- Production historical audit at 16:49 KST: 10,912 quotes since 14:43:32; 2,836 exceeded 3 seconds. Of 4,394 received during broker-sync intervals, 2,832 were delayed (p95 13.563 seconds); outside intervals 4/6,518 delayed (p95 0.005 seconds). Sync completion includes portfolio work and is a proxy rather than exact lock ownership. After-close normal sync takes about 2.3 seconds with 3 broker reads; transient token/app-key errors occurred 16:09–16:31 and recovered. No polling interval or timestamp was weakened.
- Actual 16:40 collection: partial, 2,404 saved / 2,762 received / 358 rejected. Metadata and sector coverage complete. Re-query at 16:51 still returned 241 positive-price range violations plus 113 nonpositive bars. Three actual KRX unadjusted per-ticker day responses have matching O/H/L/volume/turnover but different coherent closing prices. Period-price endpoint confirms those closes, yet market-wide period-volume differs for 845 issues; combining those endpoint rows without a validated session contract is rejected. Raw responses remain private on server.
- Private aggregate audit/source directory: `/opt/maps/backups/fujimoto-start-conditions-20261021/`. API credentials and account identifiers are excluded from tracked artifacts.
- Bank sector is the only exact-date group with full non-null PER coverage: 006220 38.89, 024110 6.26, 279570 17.93, 323410 20.04 (median 18.985). All four are eligible in the actual universe, but none has recorded annual/financial source history. Only 024110 and 279570 satisfy this PER comparison; this is a data-preparation target, not a strategy pass or investment recommendation. Bounded targeted collection avoids changing peer coverage rules or waiting for the alphabetical all-market queue.
- Actual bounded DART run 16:57–17:07 KST stopped at its 500-request ceiling: 48 companies journaled (15 complete, 33 partial), 81 new annual source rows (220 to 301). Every new annual row becomes available October 22; no historical availability was rewritten. A complete PostgreSQL custom-format backup was validated before ingestion. Current-session manual screening started 17:08 KST with execution OFF.
- Read-only exact DART receipt inspection (18 requests, raw responses saved privately): IBK 2023/2024/2025 annual common-share DPS 984/1065/1048, so 2025 decreases; Kbank annual DPS is not parseable by the current exact-money contract. Both bank financial statements return `missing_accounts` under the current parser. No bank can currently be certified by the unchanged criteria (the other two fail PER). Targeted source-filter code was therefore dropped before implementation.
- Separate KRX unadjusted per-ticker histories have internally valid OHLCV but differ from stored data on 26/620 IBK dates and 99/155 Kbank dates. Raw full rows are preserved, not combined with another endpoint or silently written over historical evidence. Source/session semantics remain to be resolved before asserting source-complete readiness.
- Task 1 commits `cfe5f6f` and `cc9b8b4`: independent task review found an in-flight reset/failure gap, fixed and re-reviewed as addressed. Four initial regressions plus two in-flight regressions reproduced red; affected final suite 216 passed (one existing warning). Initial full suite on `cfe5f6f`: 2162 passed, 24 existing warnings, 431.76 seconds. Final full suite on the corrected code is running before deployment. No schema/dependency change.
- Task 2 is an evidence/preparation completion, not a source-readiness pass. After-close account reconciliation recovered and was READY with complete stable costs, no positions/orders and about 3.5 seconds age at 17:10 KST. No evidence justifies weakening cadence/freshness or changing PER missing-value semantics. Actual next-session continuity and candidate readiness remain open.
- Final corrected-code full suite: **2167 passed, 24 existing warnings, 427.80 seconds**. Whole-branch independent review approved `869b814..efcbcca`, no Critical/Important findings. Compilation, diff checks and added-line secret-pattern scan passed. The deliberate missing-source-id test's existing SQLAlchemy warning remains a separate optional cleanup, without suppressing its negative coverage.

## Acceptance and rollback

Acceptance distinguishes tested code behavior from actual market-session evidence. Trial success is not claimed from tests or read-only diagnostics. Restore the previous verified code if health or recording fails, without rewriting immutable observations or source data. Keep execution OFF; no schema change is planned.
