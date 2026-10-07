# Limit-up V1 classification policy implementation plan

**Goal:** Extend the user-approved watchlist sector/theme exception to validated automatic `limit_up` BUY orders.

**Architecture:** Keep `_entry_policy` as the source-validation boundary. In `OrderManager._submit`, pass `check_classification_limits=False` for `analysis_pick` and `limit_up` only after validation; retain the default for catalog/mock orders. No schema, settings, strategy gate, exit or overnight changes.

**Tech stack:** Python, SQLAlchemy, pytest, existing isolated SQLite and broker fixtures.

## Constraints and scope

- Work only in `.worktrees/watchlist-classification-policy`; parent agent reviews, commits and deploys.
- Keep account checks, metadata quality gates, strategy limits and sell ownership unchanged.
- `.agent/PLANS.md` is absent; this document records scope, evidence and progress.

## Implementation

- [x] Add worker regression cases for missing sector/theme and known classifications over cap; run them before implementation and confirm classification rejection prevents both legs.
- [x] Add manager cases rejecting missing/invalid IDs, ticker mismatch, nonautomatic sessions/config and unknown sources. Extend catalog exposure regression to include limit-up holdings.
- [x] Change the risk call to `check_classification_limits=context.source not in ("analysis_pick", "limit_up")` and update its comment/docstring.
- [x] Update `maps/execution/CLAUDE.md`, `maps/risk/CLAUDE.md`, and `maps/limit_up/CLAUDE.md` to identify source validation and retained controls.
- [x] Run affected execution/risk/limit-up tests via `D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py`; inspect the final diff and report evidence.

## Evidence

- RED: `tests/test_limit_up_worker.py -k grid_ignores_classification_limits -q --tb=short` produced 4 expected failures: neither V1 leg submitted.
- RED: `tests/test_execution_safety.py -k 'limit_up_classification or requires_valid_automatic_limit_up' -q --tb=short` produced 4 expected classification failures (sector/theme missing and exceeded), with all 8 invalid-source cases passing.
- GREEN: `D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests/test_execution_safety.py tests/test_limit_up_worker.py tests/test_risk_manager.py tests/test_limit_up_domain.py tests/test_limit_up_service.py tests/test_limit_up_invariants.py tests/test_limit_up_after_hours.py tests/test_strategy_trade.py tests/test_docs_index.py -q --tb=short` completed with **313 passed, 1 warning in 36.27s**.
- The warning is the existing `db.get(LimitUpSession, None)` SQLAlchemy warning exercised by the new missing-ID rejection case. The source remains rejected; validation code was not changed.
- `git diff --check` passed. Final diff contains only the source-policy expression, comment/docstring, focused regression tests and documentation. No configuration, schema, remote or live-account writes.
- Parent agent owns final review, commit and deployment.
