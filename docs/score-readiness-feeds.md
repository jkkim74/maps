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

`NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET` are Naver Search API credentials.
AWS Bedrock uses the existing `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_REGION`, and `MAPS_AI_SCORING_MODEL_ID` settings. Missing credentials or
failed feeds are persisted as unavailable and never converted to neutral 50.

After migration, recent investor flows can be populated with:

```bash
python scripts/backfill_score_feeds.py --calendar-days 10
```
