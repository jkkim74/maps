# fujimoto/

Fujimoto-inspired causal research, durable mode-owned cycles and shared pure decisions.
The guarded runtime routes orders only through OrderManager. Default is off/observe,
without dedicated budget; no existing broker holding is adopted.

## Directory structure

```
fujimoto/
├── __init__.py    # Package marker
├── evidence.py    # First-seen annual/financial, sector and PER evidence; screening
├── indicators.py  # Completed-session Wilder RSI/ATR, MACD, shifted Ichimoku
├── domain.py      # Mode, fill-derived CycleState, RuleEvidence, Decision, evaluate
├── repository.py  # Immutable evidence/configs, reservations, cumulative fills, sizing
├── replay.py      # Two-mode stateful replay and actual quote continuity
├── sources.py     # Bounded DART annual collection, provenance review, market screening
├── feed.py        # Actual arrival tape, continuity, recording/subscription bounds
├── service.py     # Account-locked controls, source ownership, reconciliation and costs
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
`evaluate(..., decision_date=...)` separates the current runtime session from
`RuleEvidence.as_of`, which remains the completed-bar date. Today's confirmed
fill therefore permits current protective/financial/book exits, while any further
BUY requires a completed signal date after the previous fill date. Technical
orders still require exactly the next session of the unchanged evidence date.
Replay defaults decision_date to evidence.as_of; genuinely future fills remain invalid.

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
Historical terminal observations and sell monetary corrections preserve newer
pending reservations and buy stages; only a newly terminal order advances state.
`apply_fill_transition` is the same pure accounting function used by replay.
Rebound basis/sold quantities freeze the one-third target through partial cancellations.

`ReplayInput` requires explicit budget, completed RuleEvidence by date/ticker, SessionBar
execution data, costs and participation. It executes only next-session capped limits,
models partial liquidity shared across modes, halts and missing bars. After-close
fundamental deterioration cannot execute at the same day's open; financial sells
queue for the next session. Safe daily lows alone never fabricate intraday stop fills.
BUY and next-session SELL intentions both wait for an actual fresh, subscribed
quote. A daily opening price or low can never create ownership or liquidation.
BUY uses the recorded ask/ask_size within its cap; SELL uses bid/bid_size. Quote
and daily participation still bound modeled fills. Fill `executed_at` is the actual
receive timestamp, and new ownership is unavailable to earlier quotes. An actual
later quote can prove a later acquisition after reconnect, but only after a renewed
subscription. Missing ask_size is never inferred from bid_size or daily volume.
A daily touch with no executable quote remains `missing_submission_tape` /
`unknown_intraday_fill_order`, with zero modeled fills. Known whole-session outages
prevent both acquisitions and next-session sales; owned shares stay marked.
If a resting intention could already have been submitted before a recording gap
or outage, a later modeled fill retains `unknown_order_execution_timing`; a renewed
quote cannot prove that ownership did not start in the unobserved interval.
`ReplayResult.reasons` contains unavailable evidence; `diagnostics` separately
records position/budget waits, observed halts/outages and window-end intention
expiry. Unsent next-session intentions expire at each research-window boundary;
owned shares remain marked and no closing fill is invented. Missing real next-session
bars, held-tape coverage, valuation and independent samples still block validation.
`quote_signal` accepts exact recorded quote
keys exchange_at/received_at (ISO UTC), connected, gap, bid, ask, bid_size, total_bid,
total_ask, ticker and resets invalid/stale/out-of-order/gapped continuity. Actual quotes
are needed for intraday stop/imbalance exits; no synthetic tape is generated.
`candidate_order[date]` must preserve the actual ranked candidate ticker tuple; omitted
ranking falls back to ticker order for exploratory runs and cannot pass validation.
`quote_session_date` slices actual received timestamps into KST sessions for replay/WFA.
`ReplayInput.recording` contains actual sent subscription snapshots and completed
disconnect/reconnect intervals, with export evidence IDs. Every replay/neighbor/WFA
checks the held interval from 09:00 for carry-over shares, or the actual acquisition
quote for new shares, until exit or 15:20 KST. Subscription
requests alone are insufficient: fresh arrivals must cover it within three seconds.
Unrelated instruments, a morning fragment, invalid/gapped quotes and silent missing
sessions cannot prove safe-stop or book coverage. This applies with book disabled too.
A bounded outage observed by the same recorder instance models unavailable execution
and remains a diagnostic; process restart does not invent an outage endpoint. Known
whole-session exchange halts are measured without pretending there was an executable bid.
The strict coverage policy may reject quiet instruments; silence is never assumed
to prove that a stop could not have triggered. Old exports lack recording evidence
and require remeasurement with actual coverage, never synthetic repair.
The recording index parses subscription timestamps once per replay and uses binary
search for quote eligibility; outage intervals are indexed by ticker and timestamp.
Unchanged subscription sets are stored once per connection, regardless of refresh
order. Reconnect forces a new sent-subscription observation before new acquisitions.

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
`report["variants"]["with_orderbook" | "without_orderbook"]` stores each variant's
baseline, neighbors, cost_double and wfa; top-level research keys remain book-off.
`store_replay(inputs,report,account_key=None)` records immutable inputs/results;
`validation(repo,replay_id,account_mdd_limit=.28,with_orderbook=True)` recomputes and verifies them, rejecting
forged results or code changes. Missing tape, raw screening, provenance, 30 independent
completed cycles or WFA coverage is insufficient, never passed. Partial sells are not
independent completed trades. Both modes and combined account must pass.
The selected operational variant supplies every performance/MC/neighborhood/cost/WFA
gate and propagates missing reasons from all its runs. Book-on requires at least thirty
actual imbalance-exit cycles separately for safe and original; aggregated samples do
not satisfy either mode. Both on/off comparisons remain available. The result fingerprint
binds the report, selected with_orderbook flag and account MDD limit.
Direction cohorts use the observed input basket's mean daily price change, with ±0.3%
research boundaries and at least ten observations in each rising/falling/sideways cohort;
they are not claimed to be the existing official composite market regime model.
`persist_validation` writes linked standard ValidationRun/Plateau/WFA/MC rows and
folds using existing promotion fingerprints. Insufficient stays INSUFFICIENT; no
PromotionHistory is written. Existing live promotion gates remain mandatory.
It accepts the same keyword arguments. Standard component metrics describe the selected
variant; the manifest persists with_orderbook, account_mdd_limit and execution_params_hash.
The standard params_hash remains the catalog default fingerprint; Task3 must additionally
require the manifest variant and limit to match the configuration and account gate.

No historical source coverage or profitability has been measured. stress_losses
reports uncapped 68%/total-loss/halts/three-lower-limit scenario exposure; stops are
not loss guarantees. The guarded operational path is described below.

## Operational flow and defaults

`sources.AnnualCollector.collect` uses the existing paced DART client and receipt /
revision repository, visiting the least recently checked ordinary KOSPI/KOSDAQ
stocks (20 per scheduled run, at most 500 requests/900 seconds). The existing
after-close collection job records actual current universe eligibility; nightly
`fujimoto_screen` builds sector-relative PER, causal annual/quarterly inputs and
daily/weekly indicators for that observed universe, then ranks by 20-day turnover.
Missing data remains a visible blocked candidate. No historical universe is inferred
from current metadata. Annual cash DPS requires the exact common-share row, fiscal
period and receipt. This does NOT prove split/issuance comparability: an administrator
must supply three recorded annual receipts, an actual HTTPS source, SHA256 and
substantive unit-comparability rationale through POST `/api/v1/fujimoto/comparability`.
The review stores source IDs, periods, reviewer and current observation time. It is
usable from the next KRX session and cannot create historical research coverage.

Safe environment placeholders (no credentials or implicit money):

```dotenv
MAPS_BROKER_MODE=mock
MAPS_LIVE_TRADING_ENABLED=false
MAPS_DRY_RUN=true
MAPS_FUJIMOTO_ENABLED=false
MAPS_FUJIMOTO_SCREEN_TIME=22:00
MAPS_FUJIMOTO_COLLECT_BATCH=20
MAPS_SHARED_FEED_CAPACITY=40
MAPS_FUJIMOTO_TAPE_ROWS=100000
MAPS_FUJIMOTO_CANDIDATE_ROWS=10000
```

Real-time observation requires the existing KIS connection. Shared lifespan starts
when either Fujimoto or upper-limit collection is enabled; enabling Fujimoto alone
does not start upper-limit scans/orders. The 40 capacity units are channel slots:
two per ticker, plus one for the upper-limit index when enabled. All account held /
pending tickers precede candidates. Overflow is recorded; no second socket is opened.
Received UTC is captured before queueing. Missing/old/out-of-order/exchange-clock
gaps and reconnects reset the continuous imbalance timer. Book exits additionally
require confirmed net profitability and 30 seconds of actual depth observations.

`FujimotoService.configure(key,owner,budget,deposit=0,with_orderbook=True)` splits
explicit dedication equally; cycles retain inception budgets. Configuration stops
new entries. `stop`/`observe` persist a separate switch even with pending orders and
retain prior owned-position exit consent. `activate(... execution_mode, replay_id,
sell_consent)` requires owner, correct broker environment, explicit consent only for
new Fujimoto acquisitions, both existing promotions and current measured combined
validation. Paper uses existing mock_candidate-or-later stage and current mock gates
(60 score; only live track-record requirement excluded). Live retains the existing
live eligibility gate. Neither path manufactures promotion history.

`ranked_admission` is shared by replay and runtime: existing owned/pending/watch
cycles have priority, then the recorded ranked eligible list reserves the remaining
five-position slots. A missing/stale higher-ranked quote leaves its slot unused for
that session; lower ranks cannot win by arriving first. Runtime creates a cycle only
on its own fresh quote. `empty_cycle_expired` frees unfunded first-leg watches at the
next session boundary after terminal nonfill; pending/UNKNOWN ownership never expires.

Activation replays outside the account lock; immediately under the lock, and again
before each BUY, it checks immutable replay fingerprint, current code, config IDs,
account budget/limits/cost parameters, variant, standard evidence freshness and both
promotion bindings. The entry path does not load/replay the large research payload.
Runtime costs are fee .00015, tax .002, slippage .001; cash floor is the maximum of
32.5% and all configured regime floors (default 35%, conservative for all regimes).
Research export binds these values; a mismatched exploratory report cannot activate.
Runtime and replay share `expected_net_sale`: bid × quantity × (1−slippage) ×
(1−fee−tax) must strictly exceed the remaining acquisition basis. Cost-double runs
multiply all three costs before both signal and fill calculation; equality is no profit.
`RuleEvidence.atr14` is derived from the same completed, cutoff-sliced daily bars.
Runtime and replay pass it to shared sizing; reservation preserves the planned
ATR stop rather than replacing it with the fixed-percent fallback. Subsequent
reservations/fills cannot lower the existing safe stop. Absent ATR retains the
existing fixed 8% fallback; original mode intentionally has no price stop.

Reservation commits before OrderManager's own session and broker send. Context is
`source="fujimoto"`, `source_id=cycle.id`, `event_key="fujimoto:<reservation id>"`.
Entry AND exit verify account/mode/ticker/quantity/limit and intent linkage. All
account holdings, broker opens, intents and unbound reservations count once in
sizing. Existing classification and account guards still apply. Recovery binds an
exact committed intent; SENDING/UNKNOWN is never resent. A no-intent reservation can
expire without assuming any fill. Cancellation retains reservations until a broker
terminal observation. Zero-filled expired cycles free watch slots, not capital.

Reconciliation separates terminal quantity truth from cost completeness. Unknown
fee/tax values are retained as null evidence, distinct from known zero. Approved,
quantity-confirmed shares can exit for stop/financial/technical rules while costs
are provisional. Missing costs block new/additional BUY and net-profit book exits.
`settle_costs` / POST `/orders/{id}/costs` requires exact broker order ID, source URL
and document hash. A late BUY cost-only increase after SELL-only activity allocates
remaining/realized cost by original post-buy quantity, with an audit fill. Changed
gross/quantity, later BUY or other ambiguity still requires audited reconstruction;
no ownership/cost guess is made. The dashboard labels unsettled cost/P&L provisional.

## Recording ceiling and research

Actual quote rows are capped at 100,000 per account (configurable up to 100,000,000).
Candidate snapshots are zlib/base64 compressed, capped at 10,000 total (up to
2,000,000). Prices are bounded to 900 calendar days per snapshot. Before a market-wide
screen, enough remaining candidate capacity for the whole universe is required.
The defaults are a deliberately small recording pilot, not months of full-market
history: quote capacity may fill intraday and candidate capacity within days.
For a bounded 60-session research campaign, 3,000 names × two full screens × 60
requires 360,000 candidate rows; twenty subscribed names × 22,800 seconds × one
actual quote/second × 60 requires 27,360,000 tape rows. Both are supported settings,
not claims about market history or expected quote rates. Provision additional rows
for existing evidence, re-screens, quote bursts and the longer horizon needed for
independent trade samples. Configure the campaign bounds before collecting; raising
a ceiling later resumes recording but cannot repair an already observed gap.
Unchanged decisions/blocks are deduplicated. On capacity exhaustion the feed records
an evidence gap and disables imbalance duration; fresh protective bids still work.
Exported gaps make validation insufficient. Nothing automatically deletes immutable
evidence or a validation reference. Monitor DB bytes and disk free space before
raising limits; row ceilings are not a fixed byte guarantee. There is no automatic
archive/compaction or retention deletion workflow; export and retain all referenced
evidence before any separately audited storage migration. A complete long-running
dataset and storage plan remain prerequisites for promotion, not deployment defaults.

`scripts/fujimoto_research.py --demo --output report.json` is offline and explicitly
grants no execution permission. `--export --database URL --account-key HASH --budget
AMOUNT --output observations.json` reads recorded snapshots/tape only. Then `--input
observations.json --output report.json` runs the shared research; optional `--persist
--database URL --account-key HASH` stores replay/validation only. Select the same
`--without-orderbook` and `--account-mdd-limit` as the intended configuration.
No CLI path promotes, activates, adopts holdings or sends orders. Use an explicitly
isolated database and mock settings for development.
