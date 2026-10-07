# fujimoto/

Fujimoto-inspired causal research, durable mode-owned cycles and shared pure decisions.
This package submits no broker orders, activates no trading and adopts no holdings.

## Directory structure

```
fujimoto/
├── __init__.py    # Package marker
├── evidence.py    # First-seen annual/financial, sector and PER evidence; screening
├── indicators.py  # Completed-session Wilder RSI/ATR, MACD, shifted Ichimoku
├── domain.py      # Mode, fill-derived CycleState, RuleEvidence, Decision, evaluate
├── repository.py  # Immutable evidence/configs, reservations, cumulative fills, sizing
├── replay.py      # Two-mode stateful replay and actual quote continuity
└── validation.py  # Measured WFA/plateau/MC, standard components, combined gate
```

`AnnualRecord` stores confirmed annual revenue, profit and explicit annual DPS,
or null amounts for a known pending revision. Its basis/currency/share_basis must
match three consecutive fiscal years. Availability cannot precede DART's
next-session-after-publication/first-observation boundary. Never construct annual
DPS from daily fundamentals. A known available correction masks old values until
its parsed observation becomes available. Available records must identify one
ticker before period/revision selection;
foreign same-period records cannot hide deterioration or mask annual evidence.
Annual freshness is a research policy:
the latest fiscal year remains usable until the following fiscal year's end plus
90 days; after that missing newer evidence blocks buys. This is not a legal
reporting deadline assertion. `financial_status` uses DART's 180-day coverage rule
and returns missing, maintained or deteriorated; missing does not cause liquidation.

`screen` requires explicit historical ordinary-share eligibility, exact-date
published sector/PER snapshots, full observed sector valuation coverage, 21 closes
for a 20-session return, and 20 traded-value observations. No current-membership
fallback or inferred historical turnover is used. `rank_candidates` orders passed
results by descending turnover then ticker. Ineligible/insufficient results retain
blocking reasons.

OHLCV is after-close data with a DatetimeIndex and open/high/low/close/volume;
turnover is needed for screening. Optional complete flags must be true. RSI uses
Wilder's 14-delta seed (flat=50, rising=100, falling=0); MACD is EMA 12/26/9;
Ichimoku is 9/26/52 with the cloud shifted 26 sessions and lag confirmation using
past closes. Weekly aggregation requires every actual KRX session in the week.
`build_rule_evidence` derives daily/weekly signals and blocks buys on warm-up,
missing current bars, or calendar gaps; valid exits remain independent of buy gates.
If the cutoff daily bar is absent, no fresh daily technical signals are emitted
from an older bar. Independent financial/live exits and persisted targets remain.
Its cutoff is after close. Date-only inputs must never contain unfinished bars.

`evaluate(Mode, RuleEvidence, CycleState)` is pure. buy_weight is monetary 1/2/6.
Only terminal-confirmed positive fills may advance the caller's buy_stage; pending
orders (including UNKNOWN/cancel requested) block new decisions. No same-day next
leg. Buy price_cap is the signal close and timing is next_session. A closed cycle
cannot resume later buy legs. Safe stop_policy is required; original is explicit
intentional_none. Safe stop calculation remains `effective_stop_price` with fixed
8%, ATR×3, max width 16%; callers must not lower an existing stop after additions.

Emergency fundamental/price exits precede ordinary cumulative 1/9, 3/9 and full
targets, then the original mode's one-time 1/3 rebound reduction. Ordinary basis
and target must be persisted even when integer rounding produces action=hold;
future buys then remain blocked. Rebound reason remains distinct and must be
recorded once even if quantity rounds to zero. No rebound proceeds replenish buy
budgets. Decisions are intentions, never fills. Pending-order emergency cancellation
and reservation coordination belongs to the later service.

RuleEvidence live_price and orderbook_take_profit are **validated feed inputs**:
integration must enforce exchange/receive timestamp freshness, continuous tape,
net sale profitability and approval before setting them. Builder leaves them absent.
No stale arbitrary raw quote may be passed as live_price. Runtime must provide fresh
combined account limits and approvals; repository/replay own sizing, persistence and
signal expiry. These unit contracts establish no profitability.

## Durable ledger and research

`FujimotoRepository(session)` uses the caller transaction and existing account lock.
`configure(account_key, owner_user_id, Mode, budget, deposit=0, settings=None)` creates
an immutable version; initial cash equals explicit budget. Subsequent positive or
negative deposits must exactly explain the budget change and cannot withdraw owned
or reserved cash. Any unresolved account order blocks configuration changes.
Settings default to observe; this repository grants no activation or auto-sell approval.
`create_cycle(config_id,ticker)` freezes a maximum cycle monetary budget at inception.
Each mode may own the same ticker independently; each mode has at most five active
cycles. `cycles`, `orders`, `configurations`, `state`, `owned_quantity`, `cash`, and
`reserved_cash` expose account-scoped state. No broker quantity becomes ownership.

`record_evidence(kind,ticker,observed_at,available_at,payload,account_key=None)` appends
annual/candidate/quote/decision/replay provenance and a SHA256 fingerprint.
`evidence_as_of(kind,ticker,cutoff,account_key=None)` filters both real observation and
availability timestamps. Naive DB times are UTC. Immutable ORM history cannot be
updated/deleted. Account-specific evidence queries must pass account_key; public
annual evidence may be shared. Actual annual receipt/share-basis facts belong in
payload; missing facts remain missing, never inferred from daily DPS.

`plan_buy(cycle_id,decision,AccountLimits,marks,atr14=None,fee_rate=.00015)` returns a
`BuyPlan(quantity,stop_price,monetary_budget,reason,limit_price)`; limit_price is rounded
down before quantity sizing. Marks must cover every owned mode share. AccountLimits
contains fresh broker totals for ALL strategies and both modes, including reservations
exactly once. Service must run this sizing under the account lock before reserve_order.
No budget borrowing, leg skipping or replenishment from rebound proceeds is permitted.
The fixed 1:2:6 cycle ceiling is further limited by current NAV, cash floors, ticker
exposure and safe cycle stop risk. Persisted safe stops never decrease.

`reserve_order(cycle_id,decision,evidence_id,quantity,limit_price,...)` persists pending
state before broker send. It validates owned sells and fixed remaining leg budget.
`record_decision` also persists zero-rounded ordinary targets and rebound no-ops.
`bind_intent(order_id,intent_id,broker_order_id=None)` verifies an existing OrderIntent's
account/ticker/strategy/side/quantity and fujimoto source cycle identity.
`apply_fill(FillEvent(order_id,account_key,intent_id,quantity,gross,fees,tax,status,fill_date))`
uses cumulative actual gross/fees/tax, never quantity multiplied by the newest average.
UNKNOWN and CANCEL_REQUESTED retain reservations. Terminal positive buys advance;
zero-filled cancellation does not. First-leg price is its cumulative weighted actual
average. Immediate terminal monetary corrections are idempotent; late buy-cost
corrections after later cycle orders fail closed for explicit reconstruction.
`apply_fill_transition` is the same pure accounting function used by replay.
Rebound basis/sold quantities freeze the one-third target through partial cancellations.

`ReplayInput` requires explicit budget, completed RuleEvidence by date/ticker, SessionBar
execution data, costs and participation. It executes only next-session capped limits,
models partial liquidity shared across modes, halts and missing bars. Safe daily lows
alone never fabricate intraday stop fills. `quote_signal` accepts exact recorded quote
keys exchange_at/received_at (ISO UTC), connected, gap, bid, ask, bid_size, total_bid,
total_ask, ticker and resets invalid/stale/out-of-order/gapped continuity. Actual quotes
are needed for intraday stop/imbalance exits; no synthetic tape is generated.
`candidate_order[date]` must preserve the actual ranked candidate ticker tuple; omitted
ranking falls back to ticker order for exploratory runs and cannot pass validation.
`quote_session_date` slices actual received timestamps into KST sessions for replay/WFA.

Optional `ReplayInput.screening[date][ticker]` is a frozen snapshot with annual_records,
financial_records and valuations (exact Task1 dataclass dictionaries), sectors
(SectorSnapshot dictionary), prices (OHLCV/turnover dictionaries with ISO date), and
eligible (historical DataQualityFilter result). `screening_evidence` re-runs annual,
PER, surge, financial and full daily/weekly indicators. `evaluate` accepts validated
first_rsi_threshold=40; `screen` accepts maximum_return_20=.2 and
minimum_dividend_growth=0. These research-only keyword defaults preserve baseline rules.

`run_research` runs baseline/tape on/off/double costs, first RSI 38/40/42, and (when
raw snapshots exist) surge .18/.20/.22 and dividend growth -.01/0/.01 neighborhoods.
Five chronological independent IS/OOS folds re-run shared rules with empty initial
cycles. Existing plateau and seeded block-bootstrap Monte Carlo are reused.
`store_replay(inputs,report,account_key=None)` records immutable inputs/results;
`validation(repo,replay_id,account_mdd_limit=.28)` recomputes and verifies them, rejecting
forged results or code changes. Missing tape, raw screening, provenance, 30 independent
completed cycles or WFA coverage is insufficient, never passed. Partial sells are not
independent completed trades. Both modes and combined account must pass.
Direction cohorts use the observed input basket's mean daily price change, with ±0.3%
research boundaries and at least ten observations in each rising/falling/sideways cohort;
they are not claimed to be the existing official composite market regime model.
`persist_validation` writes linked standard ValidationRun/Plateau/WFA/MC rows and
folds using existing promotion fingerprints. Insufficient stays INSUFFICIENT; no
PromotionHistory is written. Existing live promotion gates remain mandatory.

No historical source coverage or profitability has been measured. stress_losses
reports uncapped 68%/total-loss/halts/three-lower-limit scenario exposure; stops are
not loss guarantees. API/feed/order activation is the next integration task.
