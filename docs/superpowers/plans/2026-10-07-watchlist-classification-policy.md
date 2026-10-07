# Watchlist classification policy implementation plan

**Goal:** Exempt validated analysis-watchlist buys from sector/theme classification
requirements and exposure caps, retaining all other execution safeguards.

**Baseline:** `183d0e5258dfce5726ac48d930b7b808798bd464`, isolated branch
`fix/watchlist-classification-policy`. Do not include the local shadow-probe
migration or change production settings, data, armed picks, or deployments.

**Architecture:** Add keyword-only `check_classification_limits: bool = True` to
`RiskManager.check_before_order`. After the existing entry-source validation,
`OrderManager` passes false only for `ExecutionContext.source == "analysis_pick"`.
Keep all cash, valuation, cumulative position, portfolio, loss, ownership,
freshness, account reconciliation and duplicate-order checks. Sells retain their
existing path. Other strategies still count watchlist holdings in exposure.
No API, schema, dependency or environment-variable changes.

## Progress

- [x] Inspect production baseline and create an isolated worktree.
- [x] Establish baseline: 93 relevant tests pass with the isolated runner.
- [x] Add failing behavior tests, then implement the minimal policy change.
- [x] Document policy and run risk, execution and watchlist regression tests.
- [x] Review final diff and record verification results.

## Validation

Use the existing virtualenv and run `python scripts/run_isolated_tests.py
tests/test_risk_manager.py tests/test_execution_safety.py
tests/test_strategy_trade.py -q --tb=short`. The runner disables external network
and dotenv loading and uses mock execution with isolated SQLite databases.

Exercise single/split entry and protective exits with absent classifications or
exceeded classification caps; ordinary sources must still fail. A strategy name
alone or an invalid pick context must not gain exemption. Retest retained cash,
position, portfolio and loss checks with the exemption enabled.

## Discoveries and decisions

- `.agent/PLANS.md` is absent from both the local checkout and production baseline;
  this self-contained plan follows the existing `docs/superpowers/plans` location.
- Running pytest directly with execution disabled blocks even mock orders. The
  repository's isolated runner is required for the execution integration tests.
- Existing ARMED picks will use the new policy after a future deployment; a buy
  may then be submitted if its price and remaining safety conditions pass.

## Outcome

Implemented the default-on classification-check argument and source-based
watchlist exemption in the two execution/risk modules. Existing exit handling,
source validation and all other guards remain in place.

- Red: 12 watchlist entry/exit cases failed on missing classification or exposure
  caps before the production change.
- Green: 148 tests passed in 20.09 seconds using the isolated runner on
  `test_risk_manager.py`, `test_execution_safety.py`, `test_strategy_trade.py`,
  `test_order_manager.py`, `test_order_manager_sync.py` and
  `test_paper_trading_safety.py`.
- Source compilation and `git diff --check` passed.
- Independent review found no production-code defects. Its focused finding was
  addressed: the cumulative-exposure test now supplies a valued holding and
  asserts the 55% exposure with classification checks both enabled and disabled.
- Production resources were not accessed or modified during implementation;
  deployment and merging into master are outside this change.
