# Operational notification and broker query recovery

**Goal:** Deliver classification failure/recovery notifications through the configured operational channel and reduce redundant KIS reconciliation requests without weakening execution safety.

**Architecture:** A classification-only notifier prefers Telegram and falls back to Slack. Existing durable notification claims and retry behavior remain authoritative. A broker order-view method defaults to the existing separate calls; KIS builds history and current pending orders from one fully paginated all-orders response. No time-based order cache is introduced. Scheduler logs summarize classification watchdog results while retaining durable detailed records.

## Constraints and evidence

- User authorized the proposed notification and KIS latency work on 2026-10-29.
- Baseline revision: d341e0d. Isolated branch: fix/ops-readiness-20261029.
- Production has Telegram configured, no Slack webhook, 13 unnotified classification failures, and watchdog output accounts for approximately 79% of inspected logs.
- Reconciliation calls the same KIS daily order endpoint for all orders and pending orders on each 20-second cycle. Request pacing is 1.1 seconds; no pacing or freshness thresholds will be weakened.
- Preserve paper environment, observe/recommend_only modes, budgets, controls, existing holdings, unknown-order handling, pagination, account locks and full post-order reconciliation.
- No schema changes, historical evidence rewrites, automatic activation or liquidation.

## Tasks and acceptance

- [x] Establish isolated targeted test baseline: 100 passed.
- [x] Add failing notification tests for Telegram preference, Slack fallback, disabled channels, plain-text length, and scheduler wiring; implement classification-specific routing using existing clients.
- [x] Add failing order-view tests for one paginated request, pending/partial/cancelled/history selection, failure propagation, repeated fresh calls, and the generic broker fallback; integrate into reconciliation.
- [x] Add regression coverage for bounded classification log output with detailed results preserved.
- [x] Reproduce and fix empty recorded universe being reported as a zero-candidate screen without a source failure reason.
- [x] Update package contracts, run focused and full isolated suites, review final diff and safety boundaries.
- [ ] Record delivered changes, validation and remaining production observation requirements.

## Verification commands

Use the root virtualenv Python with `scripts/run_isolated_tests.py`; the wrapper disables dotenv and external network and uses isolated databases. Focus: notifications, classification jobs, KIS adapter, reconciliation, sync-fill reconciliation, scheduler outcomes and paper-cost reconciliation. Final: `tests maps/tests -q --tb=short`, `git diff --check`.

## Decisions

Prefer reducing duplicate requests over extending the sync interval: extending 20 seconds would conflict with account freshness. Prefer classification-only Telegram routing over changing every execution alert in this repair. Preserve failure rows and detailed diagnostics; only log serialization is summarized.

## Rollback

Revert this repair commit and restart maps if deployed. No migration or DB rollback is needed. Do not reset delivered notification markers or execution state. Compare service health and one-minute request/latency summaries before and after deployment; test success does not establish measured live latency improvement.

## Progress

Targeted existing baseline: 100 passed. First repair regressions failed as expected; focused implementation suite 42 passed, expanded safety/source suite 152 passed. Independent review identified malformed original quantity being coerced to REJECTED before pending validation. Four raw-response regressions reproduced the issue; quantity validation now precedes terminal filtering. Reviewer independently ran initial repair/KIS tests: 93 passed.

Read-only production readiness check after data recovery: trial not started, blockers `fresh_subscribed_recording_missing` and `usable_screen_candidate_source_missing`. No activation or configuration change performed. Successful new-source collection and real subscribed quote evidence remain necessary; code repair cannot manufacture these.

Final product verification: **2515 passed, 26 warnings in 745.06 seconds**, using the full isolated `tests maps/tests` suite. Targeted final repair/source checks: 48 passed. Changed modules compile, `git diff --check` passes. Independent reviewer approved the repaired quantity boundary and found no remaining critical/important issues.

Pre-deployment baseline 10:54:52–10:59:52 KST: 190 KIS attempts, 0 rate limits, 1 transport exception; per-minute request-gate p95 values 3873/3608/4760/4785/3719 ms. Classification failures awaiting notification: 13. This short window is a comparison baseline, not an availability or latency guarantee.
