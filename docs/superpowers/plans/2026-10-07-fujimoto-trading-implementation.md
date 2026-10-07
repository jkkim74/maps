# Fujimoto Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Fujimoto-inspired two-mode system, from causal evidence and rules to persisted research and guarded operational integration.

**Architecture:** A dedicated `maps/fujimoto/` package owns evidence, pure decisions, fill-driven cycles, and replay. Existing data repositories, shared stop calculation, account risk checks, order intents, reconciliation, and authentication remain authoritative. No new dependency or parallel broker execution stack.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, FastAPI, pytest, existing KIS and mock adapters.

**Spec:** `docs/superpowers/specs/2026-10-07-fujimoto-trading-design.md` (read in full).

## Global Constraints

- Preserve existing local edits by implementing in `.worktrees/fujimoto-trading`, branch `feat/fujimoto-trading`.
- Development uses `MAPS_BROKER_MODE=mock`; never activate trading or adopt existing holdings. User subsequently authorized commit/push/production deployment after completion; deploy observation-default feature using repository runbook.
- Safe/original budgets default to 50:50 of an explicitly supplied budget. No implicit real-account budget.
- Incomplete evidence blocks buying; missing financials never imply fundamental deterioration.
- No future information, retroactive current classifications, or fabricated historical dividends/order books.
- Existing account risk, promotion, owner isolation, UNKNOWN reservations, and approval requirements apply.
- Hypothesis parameters and unmeasured performance remain labelled unvalidated.
- Tests use isolated databases and offline broker/data doubles; no new dependencies.
- Read root index and nearest CLAUDE.md before editing each area. Update package maps and guides.

## Progress

- [x] Read handoff and approved design; inspect existing contracts and isolate workspace.
- [x] Task 1: causal evidence, indicators, and pure rules (449bbb9; reviewed).
- [ ] Task 2: durable ledger, replay, portfolio budget and validation.
- [ ] Task 3: guarded execution, feed sharing, API, dashboard and scheduling.
- [ ] Final review and complete regression checks.

## Decisions

- 2026-10-07: Existing design authorizes implementation; do not repeat brainstorming or design approval. Follow handoff order, with no order integration until rules pass review.
- RSI uses Wilder's seed (first 14 deltas) and recursive smoothing; constant prices yield 50, rising-only 100, falling-only 0. MACD uses causal EMA (12/26/9) with warm-up. Ichimoku uses 26-session shifted cloud and only past prices for the lag comparison.
- A week is complete only after the last actual KRX session in that week has closed; use existing calendar and reject incomplete daily bars. Unknown corporate-action comparability blocks annual comparisons.
- A partially filled leg advances only on terminal confirmation with positive fills; zero-filled cancellation does not advance. A pending/UNKNOWN leg blocks a new leg. Signal validity is next trading session only; do not retry UNKNOWN or release a cancellation request before broker confirmation.
- Emergency exits precede ordinary cumulative sell targets; ordinary sales precede one-off rebound reduction. Integer ordinary targets round down, final exit sells all residual owned shares. Rebound reduction is independently recorded and never replenishes buy budget.
- Mode NAV includes cash and marked owned quantities; config/budget changes require no unresolved orders and apply prospectively with versioned evidence. Account checks still include other strategies and reservations.
- Annual-data research freshness expires after the following fiscal year end plus 90 days; this is a conservative entry gate, not a claim about a legal filing deadline. RuleEvidence intraday fields require timestamp/continuity/cost-validated feed inputs.
- Subscription capacity must be explicitly configured and shared with existing engine usage; never assume a provider limit or start a second uncoordinated connection.
- Financial source coverage, recorded tape coverage and independent trade samples must remain insufficient until real evidence exists. No unit test can certify live profitability.

## Task 1: Causal evidence and shared rule evaluation

**Files:** Create `maps/fujimoto/__init__.py`, `CLAUDE.md`, `evidence.py`, `indicators.py`, `domain.py`; tests `tests/test_fujimoto_evidence.py`, `tests/test_fujimoto_rules.py`; amend shared stop tables in `maps/strategy/live_rules.py`, package documentation and index.

**Interfaces:** Produce typed, validated evidence (annual revenue/profit/dividend with publication/first-observed/availability dates, basis, currency and share-basis), `Mode`, `CycleState`, `Decision`, and a pure `evaluate(...)` function. Names may be refined by the implementer and documented in its report for consumers. No persistence or orders in this task.

- [ ] Write failing tests for temporal cutoffs and delayed corrections, three consecutive comparable annual records, DPS not inferred from daily fundamentals, missing versus deteriorated fundamentals, sector median restricted to historical membership, 20-session return and turnover ranking.
- [ ] Run `python -m pytest tests/test_fujimoto_evidence.py tests/test_fujimoto_rules.py -q` and capture expected failure before implementation.
- [ ] Implement minimal validated dataclasses and pure functions. Reuse DART/classification semantics, calendar and common stop formula. Rules must implement every entry/exit stage in specification section 2, explicit intentional-no-stop policy, no same-day advancement, buy price cap, blocking reasons, and emergency exit priority.
- [ ] Test Wilder RSI boundaries, incomplete/holiday weeks, shifted cloud prefix invariance, safe versus original averaging down, one-time rebound reduction, stronger sell stage first, data gaps blocking buys but not legitimate exits.
- [ ] Run focused tests plus existing live-rule/calendar/catalog/doc-map checks; commit only task files and record output in report.

Example acceptance invariant (actual dataclass construction belongs in tests):

```python
assert evaluate(mode, missing_evidence, empty_cycle).action == "hold"
assert indicators(full_history, cutoff).equals(indicators(prefix_history, cutoff))
assert evaluate(mode, evidence, pending_cycle).action == "hold"
```

## Task 2: Durable ledger, replay and combined validation

**Files:** Create `maps/fujimoto/repository.py`, `replay.py`, `validation.py`; extend `maps/common/models.py`; migration `0041_fujimoto_trading.py`; tests `tests/test_fujimoto_ledger.py`, `test_fujimoto_replay.py`; catalog adapters/guides under `maps/strategy/`, `docs/strategy_guides/`; update affected CLAUDE.md files.

**Interfaces:** Consume Task 1 evidence/decisions. Produce persisted mode configurations, immutable evidence/candidate runs, cycles, leg/intent links and monotone cumulative fill application. Replay calls the same evaluate and fill transition functions. Validation output separates safe, original, combined, with/without orderbook and insufficient evidence.

- [ ] Test fill progression, duplicates, partial fills and confirmed cancellation, restart reconstruction, two-mode same-ticker ownership, PnL/cash conservation, oversell rejection and final rounding before implementing persistence.
- [ ] Persist config version, cycle state, exact evidence and order references; preserve immutable history. Add reversible migration following existing conventions and verify upgrade/downgrade on temporary SQLite.
- [ ] Implement 1:2:6 monetary budgets, mode position limits and reserve floors, safe risk cap and non-decreasing stop, reservation accounting and explicit deposit/budget changes. Replay must model next-session capped limits, fees/tax/slippage and volume-limited fills (touch alone is not full fill), exchange halts and unavailable data.
- [ ] Implement reproducible replay reports/stress checks, WFA/parameter-neighborhood/cost-double evaluation via existing primitives where appropriate. Require both mode validations and combined risk and tape evidence for promotion; missing input returns insufficient, never pass.
- [ ] Validation must derive metrics from stored replay inputs/results, not accept arbitrary caller-supplied pass flags. Persist fingerprints and distinguish a callable research runner from measured results. Annual, quote and candidate evidence storage must retain provenance and be queryable as of the actual observation time.
- [ ] Register two research-only strategy IDs `fujimoto_safe_v1` and `fujimoto_original_v1`, human prose/guides and explicit price-stop policy. Do not route stateful cycles through a legacy boolean-only engine that cannot reproduce them.
- [ ] Verify focused tests, migrations, catalog and docs tests, commit task files, report exact public interfaces to Task 3.

```python
assert repository.apply_fill(event) == repository.apply_fill(event)
assert safe.quantity + original.quantity <= broker_owned_quantity
assert validation(missing_tape).status == "insufficient"
```

## Task 3: Guarded runtime and operator interface

**Files:** Create `maps/fujimoto/service.py`, `feed.py`; `maps/api/fujimoto.py`; extend `maps/execution/order_manager.py`, existing reconciliation hooks as needed, `maps/limit_up/feed.py`/`runtime.py`, scheduler/settings, main router, existing dashboard templates/assets; tests `tests/test_fujimoto_execution.py`, `test_fujimoto_feed.py`, `test_fujimoto_api.py`; update documentation.

**Interfaces:** Consume persisted Task 2 state and Task 1 decisions. Feed receives normalized shared events, persists tape and maintains deterministic profit-duration state. Service uses existing OrderManager only, including a validated `fujimoto` source and cycle-bound ownership.

- [ ] Test source forgery/owner isolation, reserved exposures across modes, UNKNOWN/no resubmit, cancel race, partial-fill restart and reconciliation disagreement before adding execution paths.
- [ ] Add cycle-bound source validation and ownership with full existing entry risk checks; approvals apply only to newly acquired strategy shares. Default observe mode. Activation requires explicit dedicated budget, auto-sell rule consent, existing promotion gates and combined validation evidence; no bypass flag.
- [ ] Extend shared feed totals/exchange/receive timestamps and gap flags; one allocator prioritizes held and pending tickers then candidates. Persist actual events, reset 30-second continuity on zero ask size, crossed quotes, gaps >3s, reconnect, stale/out-of-order input; deduct sale costs before profit condition.
- [ ] Wire market-wide after-close screening using as-of repositories. Collect/source annual DPS only with exact comparable annual evidence. Missing coverage is visible and blocks entry. No network during tests.
- [ ] Add admin and owner-scoped configuration, candidates/cycles/evidence/validation queries and observe/paper/stop controls; activation gate remains authoritative. Add web dashboard mode comparison, stages/budgets/reservations, evidence/feed/block reasons. No dedicated mobile UI.
- [ ] Run new integration tests, existing execution/limit-up/API regressions, full `python -m pytest --tb=short` and `python -m pytest maps/tests -q`; render/check UI and docs map. Commit reviewed task files.

```python
assert second_submission_of_unknown_intent.calls_broker == 0
assert api_foreign_owner_cycle.status_code in (403, 404)
assert quote_profit_signal(gap_seconds=4).triggered is False
```

## Validation and recovery

Python executable: `D:/workspace/maps/maps/.venv/Scripts/python.exe`; commands run from this worktree root with mock mode and UTF-8. Record exact command, counts, duration and failures. Fix attributable regressions; document reproducible baseline failures separately.

Revert implementation commits to roll back code; run migration downgrade only against a disposable DB in development. Never delete strategy audit/ownership state to recover live execution. Disabling new entries leaves pending-order reservations and owned-share exits governed by existing rules. User authorized commit, push and production deployment; follow repository deployment windows, backup/migration instructions and health checks after completing development.

## Results

Baseline full suite: 1609 passed, 2588 existing warnings in 349.79 seconds. Task 1 implementation is e72651d; focused evidence/rules/stop/calendar/catalog/docs/DART checks: 137 passed with one pre-existing warning. Review fixed stale daily signals, mixed-ticker financial evidence and integer targets in 449bbb9; 33 focused tests pass, scoped re-review approved.

Strategy profitability and production data coverage require separate evidence and are not claimed by software acceptance tests.
