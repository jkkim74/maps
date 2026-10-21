# Fujimoto operational readiness execution plan

## Goal and approved scope

Prepare the existing 20-KRX-session KIS paper trial (KRW 5m safe + KRW 5m original) and existing strategies using actual production data. Repair demonstrated data/software faults, deploy verified changes, prepare current-account configuration and observation. Do not activate automatic trading, approve the trial, promote strategies, adopt or liquidate existing holdings. No credentials or account-identifying data in Git.

## Production evidence (2026-10-21 KST)

Release d4e1b70, schema 0043. Eight nightly Fujimoto screens failed with invalid_ohlcv_range; no candidate/screen evidence. The 900-calendar-day input contains 6,157 malformed bars across 1,890 tickers (dates 2026-09-14 through 2026-10-20). AJ Networks 095570 has close 4185 > high 4180 on Sep 21 and close 4370 > high 4340 on Sep 30. Collector accepts positive amounts without range/finite validation. A single bad ticker aborts the all-market screen.

Existing KRW 10m configs and 100,000 quote rows belong to a prior account. Current account has no Fujimoto config/evidence, no holdings/orders, latest account READY and paper cost coverage complete. Upper-limit automatic plus global execution disabled refuses the shared runtime. Annual sources: 64 tickers/220 rows, 26 eligible tickers with >=3 periods, no comparability review. PER coverage complete in only one of 29 sectors. Account poll 60s versus freshness 30s. Default candidate limit 10,000 and quote limit 100,000 are insufficient for a trial.

## Global constraints

Use production raw responses and timestamps to establish data truth; do not infer missing values or rewrite prices to fit checks. Tests use isolated DB/mock brokers. Fail-closed eligibility, historical availability, immutable evidence, ownership and account locks remain mandatory. Preserve unrelated local files. No schema change initially required. Follow index -> package CLAUDE -> source. Do not deploy during 16:00-16:45 KST or while the analysis lock is held. Keep global execution OFF, KIS real trading false, trial unapproved throughout preparation.

## Task 1: Reject malformed collected prices

Ownership: maps/data/collector.py, tests/test_historical_ohlcv.py (or a focused collector test), maps/data/CLAUDE.md.

Use TDD. Reproduce production high<close and low>open; test nonfinite and invalid amounts, valid flat bars, and preservation of an existing good row when a bad update arrives. Add finite, positive-price, nonnegative-volume and OHLC-range validation to the existing collector boundary. Surface rejected ticker/date/reason through logging and existing collection result/log metadata without silently reporting complete success. Preserve call contracts where possible; no broad refactor. Run focused collector tests. Do not commit other tasks' changes.

## Task 2: Isolate screening data failures

Ownership: maps/fujimoto/sources.py, tests/test_fujimoto_sources.py, maps/fujimoto/CLAUDE.md.

Use TDD against actual production-shaped malformed bars. A bad ticker must produce an explicit blocked candidate and original finite raw evidence, followed by processing the next ticker; it must never rank or become orderable. Record screen-level counts/reasons for operator inspection. Catch specific data-quality failures, not DB/programming errors. Nonfinite input must not break JSON serialization or turn into usable evidence. Keep replay/readiness equivalently fail closed. Test malformed-then-valid order, all-invalid inputs, and persistence failure propagation. Reuse existing RuleEvidence/evidence structures. Do not commit other tasks' changes.

## Task 3: Establish and repair source truth

Root controller: inspect production-installed provider code, actual raw responses and bad-row provenance; determine whether current response corrections exist. Back up affected rows and source responses on the production server before any repair. Only update rows supported by identified authoritative source responses with compatible date/adjustment/session scope; log before/after/hash/count. If source is itself inconsistent, quarantine through Task 1/2 and report the remaining upstream dependency. Never manufacture high/low/close. Investigate annual collection failures and PER missingness using actual sources; record substantive comparability reviews only when the documents prove them. Do not relax the strategy's criteria.

## Task 4: Prepare current-account observation

After verified deployment and environment backup, set upper-limit mode explicitly recommend_only while global execution stays OFF, allowing the shared feed to observe. Keep other strategy switches unchanged. Configure current-account Fujimoto budget through the running authenticated owner-scoped API, not an independent lock-bypassing writer. Preserve orderbook=True, entries=False, sell_consent=False. Evaluate a 20s broker sync interval against existing KIS pacing and latency; freshness stays 30s. Increase recording limits based on measured row/byte rates for 22 sessions with 30% margin; candidate budget assumes two full screens/day. Verify disk headroom before raising caps. Never delete immutable prior-account evidence. No trial approval or orders.

## Task 5: Verification, review and deployment

Run focused regressions, full tests and maps/tests once changes settle, compilation, diff/secrets review, and independent whole-branch review. Use the existing Windows venv from the original checkout; set isolated DB/mock broker, and fixtures' simulated execution flags as required (never real endpoints). Commit and push reviewed changes, fast-forward master preserving its unrelated user modifications, back up production DB/env, deploy the exact clean revision and restart outside the prohibited window. Verify health/service/logs/schema and that execution remains disabled. Inspect real current-session collection/screen without fabricating historical availability. Record raw-source gaps, current account readiness and each existing strategy's remaining gates.

## Acceptance and rollback

Malformed bars cannot overwrite good rows; one malformed instrument cannot erase all-market screening; bad evidence never authorizes a trade. Actual account/feed/config match the current paper account. Trial approval remains separate and unavailable until real readiness passes. No guarantee of a candidate or natural BUY. Revert code to prior verified revision and restore environment if service/health regress, preserving all evidence and new valid rows; restore price rows only from audited backup if the repair itself is invalid. Never downgrade DB or overwrite unrelated server files.

## Progress

- [x] Production audit and approved goal/scope.
- [x] Isolated managed worktree created.
- [ ] Task 1, with regression proof and review.
- [ ] Task 2, with regression proof and review.
- [ ] Task 3, actual-source investigation and audited repair where supported.
- [ ] Task 4, current-account observation preparation.
- [ ] Task 5, full verification/review/deployment and final evidence.

## Decisions and verification log

2026-10-21: Original checkout has unrelated user documents; preserve them. Initial planning audit ran 22 focused tests successfully. Full implementation verification is pending. User explicitly requested production data as the source of truth and preparation for existing strategies too. Automatic execution/trial approval is a later concrete review step.
