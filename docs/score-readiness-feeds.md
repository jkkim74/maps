# Score readiness feeds

Automatic BUY orders and strategy promotion fail closed unless the persisted
market score and candidate score both have 100% measured coverage. SELL and
position-exit paths remain available.

Required production settings:

```dotenv
MAPS_KOSTOLANY_REGIME_ENABLED=true
MAPS_STRATEGY_AWARE_SCORING_ENABLED=true
MAPS_SCORE_READINESS_REQUIRED=true
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
MAPS_MARKET_NEWS_QUERY_LIMIT=100
```

`NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET` hold **NCP API Hub** keys — news search
goes through `naverapihub.apigw.ntruss.com` with the `X-NCP-APIGW-API-KEY-ID` and
`X-NCP-APIGW-API-KEY` headers. Legacy `openapi.naver.com` keys do not work.
AWS Bedrock uses the existing `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_REGION`, and `MAPS_AI_SCORING_MODEL_ID` settings. Missing credentials or
failed feeds are persisted as unavailable and never converted to neutral 50.

After migration, recent investor flows can be populated with:

```bash
python scripts/backfill_score_feeds.py --calendar-days 10
```

## Investor flow NULL semantics

A `NULL` in `investor_flow_snapshot.foreign_net_value` /
`institutional_net_value` / `individual_net_value` means **pykrx did not list
that ticker for that investor type on that date** — common for preferred shares
and low-liquidity names. It is not a collection failure, and it aggregates as
zero.

`_flow_observations()` returns `None` (fail closed) only when:

- the date has **no rows at all** — collection genuinely failed, or
- one field is **missing on every row** — that investor frame never arrived.

Do not restore a per-row "any NULL invalidates the day" guard. On 2026-08-13
production had 538 of 2,622 rows with a NULL institutional value (20.5%), so such
a guard fires every single day: `liquidity` (weight 0.25) and `psychology`
(weight 0.10, which also requires flows) both go unmeasured, coverage sticks at
0.65, and every automatic BUY is blocked. That is exactly what happened from
2026-08-12 to 2026-08-14.
