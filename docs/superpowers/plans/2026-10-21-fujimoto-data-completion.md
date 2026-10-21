# Fujimoto data completion ExecPlan

## Purpose and authorization

Implement the approved data-completion plan: distinguish verified non-applicable PER from unknown values, prioritize genuine candidate evidence, preserve causal annual/comparability and price-source checks, and report production screening before/after. The user explicitly selected excluding only source-verified non-applicable peers. No promise of a passing stock or automatic trial start.

## Global Constraints

- Keep real trading disabled, global execution disabled, Fujimoto observe, entries disabled and trial unapproved. Never liquidate existing holdings.
- Never fabricate data, turn '-' into zero, infer non-applicability from a blank/zero alone, backdate collection, or mix OHLCV fields from different feeds. Keep actual growth/dividend failures blocking.
- Exclude a peer only when official provider semantics AND actual ticker/date evidence establish PER non-applicability. Unknown peers block the sector; own PER and at least one comparison value must be positive.
- Preserve immutable raw provenance (source, reference date, actual observation time, hash, explanation), next-session annual availability, and legacy frozen snapshot replay semantics. New screening uses an explicit version.
- No broad refactors, dependencies or schema migration unless a concrete necessity is established. Reuse FujimotoEvidence for sidecar evidence. Preserve main-checkout user files.
- Use isolated mock tests via D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py. No real services in tests.
- Production source ambiguity remains blocked and is reported; a complete implementation is not a profitability or market-readiness claim.

## Task 1: Implement evidence and collection interfaces

Worktree: C:/Users/jack/.codex/worktrees/9f3a/maps; base 89477385e930c85e0229a5f95c23389e56aa372b.

Read index/package CLAUDE before source. TDD, then minimal changes, commit and self-review.

- Extend PER evidence with explicit numeric/verified-not-applicable/unknown states for new snapshots and a versioned screening rule. Existing unversioned snapshots must reproduce legacy rules (including legacy numeric nonpositive handling). Version 2 never excludes an unknown peer or allows a nonpositive own PER. No valid comparison PER means blocked.
- Store observed raw valuation and verified exclusions in the existing immutable evidence repository, not by overwriting shared SecurityFundamental values. Provide a strict source-review recording function analogous to record_comparability: exact ticker/ref_date, official contract URL and SHA256, actual source URL and SHA256/raw data, rationale, actual observed time; no bare status override or backdate. Make source precedence deterministic and compatible with newer observations/revisions. Retain source evidence in frozen candidate raw inputs and validate it on replay/readiness. No inferred N/A or automatic zero classification.
- Add optional target tickers to AnnualCollector.collect while preserving default bounded rotation, and expose through existing research CLI. Validate targets against actual current eligible universe; return/report rejected targets explicitly. Selection uses latest eligible universe and latest candidate reasons: prioritize those without demonstrated growth/dividend/ineligible failures, while background default rotation remains available. Avoid permanently starving unvisited targets; resume with oldest-check order inside priority group. Existing request/time/rate ceilings remain.
- Annual attempts report transient request-budget/transport failures separately from missing/unsupported source data; complete snapshots reused and journaled corrections still mask old observations. No new generic retry framework.
- Keep the current financial parser strict unless actual evidence supports a specific mapping. Keep comparability source review and whole-bar validation gates; controller will investigate production evidence for possible supplementation.
- Tests cover versioned PER exclusion/unknown/own PER/no positive peers, malformed and future provenance/review, newer observations versus older review, legacy replay, targeted eligible selection and continuation after budget stop. Cover existing annual exact receipt/year/dividend/revision and price invalidity regressions with existing suite.
- Update maps/fujimoto/CLAUDE.md and relevant CLI docs. Run focused tests then full tests maps/tests once at a stable code state; controller may own full run if stated in report. Report exact commands/results and any remaining risks.

## Task 2: Operational source review, supplementation and verification

- Root controller reads production aggregate DB/log evidence with secrets redacted; use /opt/maps/backups/fujimoto-start-conditions-20261021 raw artifacts first. Record current screen and trial safety before mutations.
- Find official primary-source contracts for PER non-applicability and canonical OHLCV semantics. If evidence is insufficient, retain unknown/block and report the exact missing proof. Do not force provider conclusions from code heuristics.
- Select actual eligible high-priority targets using latest screen; collect a bounded run under existing ceilings. Archive exact raw source responses and before/after counts in a private backup path. Review three-year accounting/share comparability only with actual source proof. Do not retry genuinely ineligible banks merely to fill boxes.
- Backfill price rows only when a complete canonical same-source bar and session contract are established; preserve originals/audit and immutable candidate snapshots. Otherwise publish unresolved discrepancies.
- Integrate/deploy reviewed code under existing deployment procedure and authorized scope; no market-hour lock conflict, backup before data writes, service/health/revision check and safe flags recheck. No schema changes expected.
- Run actual current completed-session screening after source availability permits. Newly observed annual evidence is usable next session, not immediately. Report before/after coverage, pass count, remaining blockers and next-session live-feed check separately.

## Validation and rollback

Targeted regression command: python scripts/run_isolated_tests.py tests/test_fujimoto_evidence.py tests/test_fujimoto_sources.py tests/test_fujimoto_replay.py tests/test_fujimoto_paper_trial.py -q --tb=short (adjust discovered filenames).
Full verification: python scripts/run_isolated_tests.py tests maps/tests -q --tb=short; git diff --check. Independent task review and final branch review precede integration.
Rollback code to previous revision; do not erase immutable evidence or restore old source observations as current. No trial activation in this task. Retain private data backups and versioned evidence for audit.

## Progress

- 2026-10-21: approved plan recorded; existing isolated worktree verified clean. Main checkout user edits untouched.
