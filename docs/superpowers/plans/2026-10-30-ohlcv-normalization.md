# Daily OHLCV normalization — ExecPlan (2026-10-30)

## Purpose and binding design
Normalize all new daily input to KRX per-security regular-session OHLC (MDCSTAT01701, adjStkPrc=1), with separately observed adjusted close (2). Never clamp prices or fabricate no-trade bars. Preserve existing historical records and immutable decisions. User selected full normalization and completion-dependent downstream jobs, retaining configured times as earliest start times.

Production investigation: 355 rejected on October 30: 239 range failures and 116 OHLV-zero rows. Current bulk/per-security samples differ even inside valid ranges; source and stored prices cannot be treated as regular-session equivalents. Baseline 68 focused tests passed.

## Implementation units
1. Strict bounded official source and fixtures: exact date/identity, numeric/range validation, no_trade, independent adjusted close, 1s pacing, 6500 requests/3h budget, timeout3/15, one transient retry, login guard.
2. Add collection run/item and daily stage tables (no backfill), checkpoint observations, atomically publish validated bars and quality with source provenance, preserve existing good rows on failed refetch; durable collection reconstruction.
3. Daily collect -> candidates -> validation dependency coordination, durable claims, no candidate self-collection, minimum scheduled times and restart/manual deduplication.
4. Additive quality reporting, docs, focused/full isolated tests, independent review.

## Interfaces
SourceItem and SourceBatch carry typed prices plus evidence/status; normalized collection result returns existing CollectionResult with additive ohlcv_quality. OhlcvCollectionRun records ref_date/status/owner/lease/snapshot/quality; items checkpoint per-ticker outcomes. DailyPipelineStage unique(ref_date,name) claims jobs. load_collection(db,ref_date) restores completed input.

## Acceptance
Run root .venv/Scripts/python.exe scripts/run_isolated_tests.py tests maps/tests -q --tb=short in this worktree. Cover mismatched bulk closes, no_trade vs positive zero-volume, missing adjusted close, identity/date/numeric errors, timeout/auth/budgets, atomic publication, restart/concurrency, delayed/partial collection and downstream stage ordering. Check migration and compile/diff. No production writes or live broker/network in tests.

## Progress
- Isolated branch/worktree created; root user edits preserved.
- Source and orchestration implementation delegated to separate owners; integration/model/persistence owned by lead.

## Decisions / limits
Historical repair remains separate audited operation. Volume is observed, not claimed finalized. No production deploy or activation. Missing evidence stays blocking. New tables retained on rollback if populated; old code must not resume mixed-basis daily publishing.

## Rollback
Before rollout disable new daily publication; preserve audit tables and existing data. Deployment requires DB backup and Alembic upgrade before app restart, outside 16:00–16:45 protected window. Do not downgrade populated audit tables or restore old mixed-source collection automatically.

## User-requested stop (2026-10-30)
Implementation INCOMPLETE. All implementation/review writers stopped. Focused source
25, source+persistence38, scheduler67 and collector48 tests passed in separate runs.
Newest public-quality raw evidence regression is RED and not fixed. Full-suite run
was stopped at25%; no final pass. Docs-index fix still needs recheck. Whole
branch review incomplete; auxiliary network/implicit DB transaction boundary needs
regression verification. See docs/handoffs/2026-10-30-ohlcv-normalization.md for exact
state and commands. No production changes. Commit/push is a WIP handoff only.

