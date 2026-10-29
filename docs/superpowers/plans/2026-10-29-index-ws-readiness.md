# KIS index WebSocket operational verification — 2026-10-29

## Objective and scope

Reduce avoidable REST contention using the existing index WebSocket path. Preserve
account sync cadence, quote freshness, REST fallback, trading modes, budgets,
execution policy and orders. This is an operational configuration change, with no
application-code or schema change.

## Investigation

At 11:35–11:42 KST the shared REST lane recorded 38–42 attempts per minute.
Index polling used 11–12, candidate ranking 10–12 and candidate detail 9–12.
Balance requests still intermittently returned EGW00215; request-gate p95 was
2402–4456 ms despite zero local spacing violations. The scan log showed one
halted candidate requiring repeated fresh status checks; caching that verdict
would delay detecting resumption. Index WebSocket was disabled (the default).

## Execution and verification

- Existing runtime tests: **44 passed in 8.83s**. Feed tests: **10 passed in 1.23s**.
- Backed up `.env` privately and captured execution/config/control/order invariants
  in `/opt/maps/backups/index-ws-probe-20261029/`.
- Added only `MAPS_LIMIT_UP_INDEX_WS_ENABLED=true` and restarted `maps` at 11:46:31 KST.
- Subscription acknowledged at 11:46:34; first actual index received at 11:46:40.
- Fresh index frames suppress REST. Occasional gaps exceeding the existing 10s
  threshold correctly resume 5s REST polling. Neither threshold was relaxed.
- At 11:48:03, service active, automatic restarts 0, internal/external health 200,
  four successful broker sync cycles and zero ERROR/CRITICAL records since startup.
- Latest minute: 949 quote records across 11 tickers. Recorded subscriptions also
  contain 11 tickers; index capacity reservation did not evict a current subscription.
- Environment bytes differ only by the added flag. Execution policy, Fujimoto
  configuration/control and order/fill counts match the before snapshot.
- Initial startup-inclusive summary: 33 REST attempts, 3 index requests, gate-wait
  p95 2925 ms, one balance rate limit. This establishes functioning call suppression,
  not complete resolution of KIS rate limiting or a controlled latency benchmark.
- Next full summary at 11:48:32: 32 successful REST attempts, 3 index requests,
  3 balance and 3 daily-order requests, gate-wait p95 2143 ms, zero rate limits
  or spacing violations. Continued observation is required across varying load.

## Remaining readiness and rollback

Read-only paper-test readiness remains `not_started`, with
`usable_screen_candidate_source_missing` and `fresh_subscribed_recording_missing`.
The latter concerns a qualifying screened candidate; the general quote stream is
active. Latest October 28 screening still has zero members/candidates. Do not
rewrite historical evidence or manufacture current eligibility from recovered rows.
Verify the next completed-session collection and subsequent screening before
claiming full readiness. No test trading was activated.

If actual index delivery ceases or sockets repeatedly disconnect, disable the
index flag and restart outside the 16:00–16:45 KST protected analysis window.
Preserve any later unrelated `.env` edits: the full backup is for comparison, not
an unconditional future overwrite. REST fallback remains active automatically.

## Next operation: metadata preflight and missing new listing

At 11:58:58 KST, fetched current official KRX metadata through the login guard.
Login succeeded, breaker unblocked with zero consecutive failures. Metadata was
complete: KOSPI 940/940 and KOSDAQ 1825/1825, with no missing names or listing dates.
Compared current source with all 2,806 stored metadata rows: no conflicting fields,
but today's newly listed `0053F0` (티앤이코리아, KOSDAQ, listing 2026-10-29) was absent.

At 12:00:06 inserted only that missing row (id 2807), preserving null sector/theme
and `has_adjusted_price=false`. No historical universe, price, screening or collection
success record was fabricated. Official response, comparison, insert intent,
pre/post invariants and verification are stored privately under
`/opt/maps/backups/metadata-preflight-20261029/`.

Verified all 2,765 current source records match name/market/type/listing date;
all pre-existing metadata rows retain their full-row aggregate hash. Execution
policy/config/control and order/fill counts are unchanged. At 12:00:09 the live
scanner began watching the new listing; persisted session is `watching` with
`execution_mode=observe_only`. The under-100-day restriction remains in force.
External health is 200; no restart or application code change was needed.

Validation: `scripts/run_isolated_tests.py tests/test_krx_adapter.py
tests/test_limit_up_runtime.py -q --tb=short`: **59 passed, 1 existing pykrx
deprecation warning, 13.89s**. This verifies metadata acquisition and runtime
gates, not completed-session candidate readiness. Today's universe capture and
screening must still use actual post-close collection.
