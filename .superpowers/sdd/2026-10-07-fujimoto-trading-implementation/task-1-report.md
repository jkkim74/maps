# Task 1 report: causal evidence and shared rules

Status: implemented, self-reviewed, focused checks pass; ready for independent
controller review. Implementation commit: `e72651d` (`feat: add causal Fujimoto
evidence and pure staged rules`). All work occurred in the isolated
`.worktrees/fujimoto-trading` checkout, with no network, persistence, execution,
broker/account access, activation or existing holding changes.

## Changed files

- `maps/fujimoto/{__init__.py,evidence.py,indicators.py,domain.py,CLAUDE.md}`
- `maps/strategy/live_rules.py`: safe mode fixed 8%, ATR multiplier 3, using the
  existing width cap and tick rounding. Original mode is intentionally unregistered.
- `maps/strategy/CLAUDE.md`, `index.md`: public contracts and documentation map.
- `tests/test_fujimoto_evidence.py`, `tests/test_fujimoto_rules.py`: 29 tests.

## Evidence APIs

All evidence records and rule/state outputs are frozen dataclasses.

`AnnualRecord(ticker, period_end, receipt, publication_date, first_observed_at,
available_date, basis, currency, share_basis, revenue, operating_profit,
dividend_per_share)`. Values may be null to represent known pending corrections.
Basis is CFS/OFS; currency must be explicit; share_basis must be an explicit
comparable corporate-action unit identifier for screening to pass. It must come
from verified source annual dividends, never a daily fundamental DPS projection.
Provenance availability is validated against existing DART `available_date`:
the first KRX session strictly after max(publication date, first observation KST
date). Delayed observations retain their actual first-seen date. The latest
available receipt per period masks older receipts even when amounts are missing.
Later parsed observations of the same receipt cannot leak before availability.

`FinancialRecord(ticker, period_end, receipt, publication_date, first_observed_at,
available_date, basis, currency, revenue, prior_revenue, operating_profit,
prior_operating_profit, comparable=True)`. Prior values must be the comparable
same fiscal cumulative period, as in the existing DART repository.
`financial_status(records, cutoff)` returns `missing`, `maintained`, or
`deteriorated`; expiration is existing DART's 180-day period coverage. Missing,
pending, incomparable, or expired data never implies deterioration.

`annual_quality(records, cutoff) -> QualityResult(passed, reasons)` selects three
consecutive fiscal years with matching basis/currency/share basis; positive,
strictly increasing revenue and operating profit; positive nondecreasing DPS.
Research freshness policy added during implementation: the newest fiscal year's
data expires after the following fiscal year end +90 days. This allows previous
year evidence while the next report is not yet expected and blocks indefinite
stale growth windows. It is a conservative research policy, not an assertion of
legal filing deadlines or a fabricated publication date. Missing newer evidence
after that boundary blocks entry until actual observations exist.

`SectorSnapshot(ref_date, available_at, memberships)` takes an immutable tuple of
`(ticker, sector_code)` pairs, with unique tickers and explicit sector labels.
It represents the existing published classification snapshot, not metadata.
`ValuationRecord(ticker, ref_date, available_at, per)` carries actually observed
historical PER. Nonpositive PER is excluded from median; missing peer coverage
blocks screening. Timestamp availability is evaluated in KST trading-date terms,
using the existing classification convention that naive database timestamps are
UTC. Exact cutoff dates are required; no current-sector fallback is used.

`screen(ticker, annual_records, valuations, sectors, prices, cutoff,
eligible=False) -> SelectionResult(ticker, passed, reasons, sector_median_per,
return_20, average_turnover_20)`. The caller must supply historical
KOSPI/KOSDAQ ordinary-share eligibility from existing data-quality checks; default
is blocked. Needs 21 closes for 20-session return, 20 explicitly supplied turnover
values, current cutoff bar and uninterrupted calendar sessions. Return >20%
blocks. `rank_candidates(results)` includes only passed results and sorts by
descending average turnover then ticker.

## Indicator APIs

`daily_bars(prices, cutoff)` validates required OHLCV, finite positive prices,
nonnegative volume/traded value, price ranges, ordered unique DatetimeIndex, and
optional `complete=True` flags. The input contract is **completed after-close
daily bars**; intraday bars must not be supplied without completed flags.
All processing slices before computing, including before validation of future rows.

`indicators(prices, cutoff)` returns copied OHLCV plus `rsi`, `atr14`, `macd`,
`macd_signal`, `macd_histogram`, `tenkan`, `kijun`, `span_b`, `cloud_a`,
`cloud_b`, `cloud_top`, `lag_confirm`, `ichimoku_bullish`, `cloud_bearish`,
`macd_golden`, `macd_dead`, `tenkan_cross_up`, `tenkan_cross_down`,
`rsi_cross_70`. Wilder RSI seed is first 14 deltas and recursion; flat=50,
gain-only=100, loss-only=0. ATR uses the first 14 true ranges, then Wilder
recursion. MACD is causal adjust=False EMA 12/26/9 with warmup. Cloud is shifted
26 sessions; lag_confirm compares current close to close 26 sessions earlier.
No negative shift or future-price lag comparison exists.

`completed_weeks(prices, cutoff, closed_dates=None)` requires all actual KRX
sessions in the week and the final session at/before cutoff. Friday holidays
allow Thursday completion; calendar/data omissions never create a partial weekly
bar. `weekly_indicators(...)` uses these completed weekly OHLCV records.
`has_session_gap(frame, cutoff, closed_dates=None)` compares explicit coverage
with the existing calendar. Configured KRX closures are used by default.

## Pure rule/state APIs

`Mode.SAFE.value == "safe"`, `Mode.ORIGINAL.value == "original"`.
`Mode.strategy_id` maps to `fujimoto_safe_v1` / `fujimoto_original_v1`.

`CycleState` fields: buy_stage=0, quantity=0, first_fill_price=None,
last_buy_date=None, stop_price=None, pending_order=False,
averaging_down=False, rebound_reduced=False, sell_basis_quantity=0,
ordinary_sold_quantity=0, sell_target_ninths=0. This is **confirmed fill-derived
state**, not mutable intended state. Ledger must advance buy stage only after
terminal confirmation with positive fills. Partial pending and UNKNOWN/cancel
requested orders keep pending_order=True.

`RuleEvidence` fields: as_of, close; selection_passed=False,
blocking_reasons=(), financial_status="missing", daily_rsi=None,
weekly_rsi=None; macd_golden/macd_dead/tenkan_cross_up/tenkan_cross_down/
ichimoku_bullish/cloud_bearish/weekly_macd_rising/weekly_histogram_rising/
rsi_cross_70 all default False; live_price=None,
orderbook_take_profit=False. Boolean trust boundaries and finite/ranged numeric
inputs are validated. The explicit live_price and orderbook flag contract is
**already validated feed evidence**: service must check exchange/receive
timestamps, freshness/continuity, net costs, ownership and approval before
setting these fields. Arbitrary stale raw prices cannot be passed as live_price.

`build_rule_evidence(prices, cutoff, selection, financial_status,
closed_dates=None) -> RuleEvidence` derives causal signals and blocking reasons.
It leaves all intraday evidence absent. Daily/weekly RSI warmup blocks buys;
later MACD/cloud warmup leaves those stage signals False, avoiding an unintended
weekly-MACD prerequisite for first-stage RSI entries. Calendar gaps block buying
without suppressing independently valid exits.

`evaluate(mode, evidence, cycle) -> Decision` returns action buy/sell/hold,
reason, reasons, buy_stage, buy_weight, price_cap, averaging_down,
sell_target_ninths, sell_basis_quantity, sell_quantity, stop_policy, timing.
Defaults are zero/no target, required stop and next_session timing.

- buy_weight is **monetary** 1/2/6. price_cap is the signal close; no quantity
  sizing or execution occurs. Next-session expiration is caller responsibility.
- Safe mode requires price stops; original returns explicit intentional_none.
  Safe quantity/stop loss sizing and nondecreasing persisted stops are Task 2.
- Emergency deterioration/price-stop exits precede ordinary targets, then original
  averaging-down rebound, then buys. Pending orders block all new submissions;
  future service must serialize emergency cancellation/reprioritization without
  releasing UNKNOWN reservations or overselling.
- Ordinary sell_basis_quantity is frozen at first ordinary target. Target ninths
  is cumulative 1/3/9; ordinary_sold_quantity counts only ordinary actual fills.
  Intermediate targets round down; target 9 exits all remaining owned shares.
- **Persist ordinary basis/target even when action=hold and sell_quantity=0**
  due to integer rounding. Once ordinary target exists, additions remain blocked.
- Rebound reduction reason is rebound_reduction, independent sell target=0;
  quantity is floor(current owned /3). Record rebound_reduced only on completed
  reduction (or the zero-rounded no-op), never replenish that budget. Third buy
  remains solely its planned 6 weight. Ordinary selling wins simultaneous rebound.
- last_buy_date equal to evidence.as_of blocks next buy leg. Empty completed
  cycles hold and cannot restart at later legs; initial CycleState begins leg 1.
- timing is next_session for technical exits, intraday for validated price stop
  or orderbook profit, first_available for available financial deterioration.

## Verification and self-review

Environment: PowerShell, Python
`D:/workspace/maps/maps/.venv/Scripts/python.exe`, MAPS_BROKER_MODE=mock,
PYTHONUTF8=1; worktree cwd.

1. Initial requested red command: `python -m pytest tests/test_fujimoto_evidence.py
   tests/test_fujimoto_rules.py -q` produced two collection errors because the
   entire new maps.fujimoto package did not yet exist (1.42s). This was a missing
   module red, not a behavior assertion; recorded explicitly rather than claiming
   all initial tests failed assertions.
2. Initial implementation and corrected test fixture row/range mistakes: 23 passed
   (1.42s). Test fixture corrections did not weaken production range validation.
3. Subsequent test-first assertions for annual expiration, ATR seed, terminal
   empty-cycle behavior and boolean validation: expected 5 failed, 23 passed
   (2.71s); after implementation 28 passed (6.07s).
4. Additional sector valuation-coverage and KST timestamp-boundary tests each
   observed an assertion failure before the corresponding guards were added.
5. Self-review identified an overbroad weekly MACD warmup prerequisite for leg 1.
   Added `test_first_leg_does_not_wait_for_weekly_macd_warmup`: failed as expected
   (1.88s); corrected builder readiness and reran all selected checks.
6. Final command: `python -m pytest tests/test_fujimoto_evidence.py
   tests/test_fujimoto_rules.py tests/test_effective_stop_price.py
   tests/test_trading_rules.py tests/test_strategy_catalog.py
   tests/test_docs_index.py tests/test_dart_financials.py -q`:
   **137 passed, 1 existing Starlette/httpx deprecation warning, 10.30s**.
7. `python -m compileall -q maps/fujimoto maps/strategy/live_rules.py` and staged
   `git diff --cached --check` passed. Final diff scope reviewed: only Task 1
   files, no secret/config/DB changes. Git reports expected LF-to-CRLF notices.

Controller independently established the full pre-implementation baseline:
1609 passed, 2588 existing warnings, 349.79s. I did not rerun the full suite.

## Remaining scope and risks

No persistence, budgets/sizing, replay, actual annual dividend collection,
classification/PER DB adapters, quote feed, order binding, API or scheduler was
implemented here. These are explicitly later tasks. Record adapters must preserve
receipt journal entries for pending corrections and must never fabricate unknown
share-basis comparability. Task 2/3 must honor the above fill, rounding, timing,
freshness and pending-order contracts. Source and historical tape coverage remain
unmeasured; test success does not imply profitability, promotion or live permission.
