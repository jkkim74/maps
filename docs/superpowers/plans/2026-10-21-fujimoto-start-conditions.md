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

- [ ] Trace queue time, account-lock wait and broker-sync time. Existing evidence: 22/220 quotes over 3 seconds without diagnostics; account oldest snapshot estimated 41.7 seconds despite a 20-second interval.
- [ ] Write a deterministic regression using a real held account lock and production quote path. Actual recording must remain possible during slow reconciliation, without order submission or concurrent Session use.
- [ ] Write a stale-after-wait regression: a quote received before a blocking operation must never authorize a decision using the pre-wait clock. Original received time remains unchanged.
- [ ] Implement the minimal correct path. Observation-only quote/timer work must not acquire an execution lock unnecessarily. If active order processing requires separate recording, isolate its Session/worker and retain event ordering/reset/capacity and safe drain semantics. Do not introduce an unbounded background executor or drop raw evidence silently.
- [ ] Add bounded diagnostic timing (queue/lock/processing as justified) so production can distinguish delay contributors without logging every tick or secrets.
- [ ] Run focused runtime/feed/Fujimoto/execution tests. Include reconnect, capacity, shutdown and existing ownership guards. Record red/green commands/results and implementation tradeoffs in the task report; commit task-owned changes only.

## Task 2: Account freshness and source evidence

- [ ] Read production request/sync timings and current collection result. Reduce demonstrated redundant work only when independent evidence and tests prove the same completeness; keep conservative observation age and pacing. Do not blindly shorten the timer.
- [ ] Inspect actual annual/DPS/PER availability for eligible companies and compatible peer groups. Collect missing supported source history through existing collectors, preserve raw responses and true availability. Identify genuine strategy failures separately from missing data.
- [ ] If a PER applicability policy change is required, document its exact evidence/contract rather than silently changing screening criteria.

## Task 3: Review, deployment and live readiness

- [ ] Run independent task and full-change reviews, isolated tests, compilation and diff/secrets checks.
- [ ] Deploy exact reviewed commit after verifying service state and analysis lock; preserve DB/env backups and disabled execution.
- [ ] Check actual collection/screen results at their real scheduled times or a valid current-session manual screen. Use actual selected candidate subscriptions and live quotes for acceptance; after-hours silence cannot prove the next market session.
- [ ] Recompute status and account/cost/order/config/storage checks. Record remaining gates; if none remain except deliberate execution OFF, present the concrete scoped trial activation state for final handling. Never manufacture a candidate or a BUY.

## Progress / decisions

- 2026-10-21: User approved the ordered readiness work. Reused clean managed worktree at revision 869b814; original four local files preserved.
- Code inspection: runtime queue is serialized; `on_quote` records only after account lock; observe `tick` also locks; `now` is captured before lock waiting. Active tick additionally reconciles, while scheduler separately reconciles every 20 seconds.

## Acceptance and rollback

Acceptance distinguishes tested code behavior from actual market-session evidence. Trial success is not claimed from tests or read-only diagnostics. Restore the previous verified code if health or recording fails, without rewriting immutable observations or source data. Keep execution OFF; no schema change is planned.
