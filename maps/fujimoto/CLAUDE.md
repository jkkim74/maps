# fujimoto/

Fujimoto-inspired research contracts and shared pure decisions. No persistence,
orders, activation or adoption of existing holdings occurs in this package yet.

## Directory structure

```
fujimoto/
├── __init__.py    # Package marker
├── evidence.py    # First-seen annual/financial, sector and PER evidence; screening
├── indicators.py  # Completed-session Wilder RSI/ATR, MACD, shifted Ichimoku
└── domain.py      # Mode, fill-derived CycleState, RuleEvidence, Decision, evaluate
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
No stale arbitrary raw quote may be passed as live_price. Account limits, quantity
sizing, persistence, next-session signal expiry and execution approvals are later
integration responsibilities. These unit contracts establish no profitability.
