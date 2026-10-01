# Close report remediation implementation contract

Approved 2026-10-01. Implementation only; no production deployment, account change,
strategy enablement, or historical candidate/report rewriting.

## Metadata

Use dated KOSPI/KOSDAQ ticker lists as membership, fresh KRX basic information for
name/listing date, validated string-only name fallback. Per-ticker failures must
not truncate a market. Preserve alphanumeric codes. Unknown listing dates never
become year 2000. Retain existing metadata when new listing date is missing.
Add MetadataCollection(items, markets, status), preserve get_security_meta list
compatibility. Per-market quality: expected_count, valid_count, coverage_ratio,
missing_tickers, listing_date_missing_tickers, error. Empty/failed membership is
unavailable. Each market >=95% permits valid candidates; lower/unavailable blocks
candidate generation. Preserve successfully collected OHLCV/flow. Persist quality
in CollectionLog.metadata_quality and CollectionResult; partial collection is
visible in batch UI and close report, even if later retry succeeds. Order readiness
must not reuse older candidates when expected collection quality is missing or
insufficient (legacy_unknown); exits remain available.

## KIS

Replace future reservations with lock/check current monotonic/grant or unlock,
sleep, recheck loop. All adapters for same account/environment share gate; never
hold lock during sleep or HTTP. Preserve configured intervals/timeouts/retries and
unknown-order non-retry contract. Guarantee grant spacing, not server arrival.
Add secret-free DEBUG request diagnostics (PID, path, TR ID, attempt, gate wait,
HTTP start gap, latency, outcome). Extend minute summaries with endpoint outcomes,
gate-wait p95, minimum start gap, gap violations. Distinguish failed attempts from
failed jobs in report.

## Contrarian scoring

Research only: flag remains OFF. Version contrarian_quality_20261001. Weights:
valuation .30, earnings_improvement_score .25, crowd_neglect_score .20,
accumulation_flow_score .15, technical_bottom_score .10. No fabricated missing
values. Existing partial-score math/valuation exclusions retained. Old
earnings_revision_score JSON is never relabeled or rewritten.

clip(x)=max(0,min(100,x)). Earnings: g=revenue/prior_revenue-1;
m=operating_profit/revenue-prior_operating_profit/prior_revenue;
score=.3*clip(50+250*g)+.7*clip(50+1000*m). Both revenues positive.
Neglect: T=close*volume; A=last20 mean T; B=preceding60 mean T;
clip(100*(1-A/B)), A/B positive (requires80 trading days).
Flow: F=sum20(foreign+institution net); T=sum20(close*volume);
clip(50+1000*F/T); all20 aligned dates measured, real zero is valid.
Bottom: L20=min20 lows, L5=min last5 lows, P15=min preceding15 lows;
M5=last5 close mean, PM5=previous5 close mean, C=last close;
.4*clip(50+1000*(L5/P15-1))+.3*clip(1000*(C/L20-1))
+.3*clip(50+2500*(M5/PM5-1)). Requires20 valid aligned trading days.
Store score_version, score_scope (research when contrarian flag OFF), score_evidence
with periods, raw aggregates, sources, receipt IDs, missing reasons. Expose optional
API fields, preserve existing totals, split research diagnostics from operational
warnings; don't suppress research incompleteness.

## DART

Use existing DART_API_KEY. New weekday job dart_financial_collection at21:10 KST,
recent20 trading days' unique contrarian candidates; uncollected, due retries,
oldest checked first. Corp mapping fetched once per run. Query last400 calendar
days regular filings, all pages; preserve correction receipts. CFS preferred,
OFS only on explicit CFS absence, never on transient/auth errors. IS preferred
then CIS, require unambiguous revenue/operating profit pair. Standard account IDs
ifrs-full_Revenue, dart_OperatingIncomeLoss; exact existing Korean aliases fallback.
Quarter/half-year use thstrm_add_amount/frmtrm_add_amount; annual uses
thstrm_amount/frmtrm_amount. Never mix periods/basis/currency. Receipt mismatch
is unusable. Decimal/Numeric amounts.
Immutable financial snapshots keyed(ticker,receipt,basis,raw hash), raw response,
period, publication date, first collected UTC timestamp, availability date.
Availability=next KRX trading day after max(publication date, first-seen KST date).
At cutoff select latest available fiscal period then latest correction; known but
unparsed correction makes period pending_revision. Expire periods older180days.
No lookahead/backfill of past decisions. Collection state tracks checks, receipts,
retry timing/status/errors so work resumes across runs. 500 requests/900seconds per
run; >=1second request spacing, connect/read3/15seconds, at most2 attempts transient
only. Auth/rate limit stops run. Candidate scoring never calls DART HTTP.

## Validation / rollout

Tests before implementation: malformed metadata must not drop following tickers,
95% boundary per market, failed markets/legacy readiness/exits; late/early waking
and concurrent gate callers, retry/unknown order; formulas and real zero vs missing,
period/currency/receipt/correction/as-of restrictions, no orders with flag OFF,
old history compatibility. Migrations must upgrade fresh SQLite. Run targeted tests,
whole suite, docs index, mobile build if display changes. No credentials/network in
tests. Keep independent commits. Deployment later: metadata first, KIS observe3
trading days, research score observe10 trading days; never auto-enable trading.
