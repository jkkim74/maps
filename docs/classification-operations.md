# Classification collection and staged rollout

Sector and theme observations are independent from basic security metadata quality.
`classification_run` records every attempt; only complete runs with `published_at`
are readable as snapshots. `classification_member` retains each ticker/code/name
relationship at publication. A ticker in `expected_tickers` with no theme relations
means verified no-theme; a ticker outside that universe remains unknown. Failed
attempts never erase or supersede the latest complete publication.

## Settings and schedule

| Setting | Default | Purpose |
|---|---|---|
| `MAPS_THEME_COLLECTION_ENABLED` | `false` | Enable public Npay theme collection |
| `MAPS_THEME_COLLECTION_TIME` | `17:00` | KST weekday theme job, after sector collection |
| `MAPS_CLASSIFICATION_CHECK_TIME` | `17:25` | KST deadline/missing-run check |
| `MAPS_CLASSIFICATION_SNAPSHOT_ENFORCED` | `false` | Switch normal BUY classification checks to snapshots |

The configured daily collection records KRX sector quality separately (currently
16:40 in production; the application default is 16:10). Theme
collection requires that day's complete sector snapshot as its explicit universe;
old unmaintained metadata rows are not a denominator. Public source catalog and
member pages use zero-based page numbers, not row offsets. The HTTP collector has
a 20-minute deadline and bounded retries. Its quote timestamps are not treated as
classification change timestamps.

Counts are derived from accepted records, not trusted caller flags. Monitoring
shows latest attempt separately from last successful publication, including source,
dates, assigned/unassigned counts, relation/catalog counts and failure reasons.
The watchdog is calendar-only and never collects HTTP data during application
startup. Existing configured notification channels report failed/partial/missed
attempts and recovery; absent notification credentials still leave durable DB and
batch-monitor evidence.

Notification claims prevent simultaneous checkers from sending the same event.
An external webhook may nevertheless receive a retry if the process dies after
delivery but before committing `notified_at`; delivery is not exactly-once.

## Rollout (explicit operations, not performed by local tests)

1. Back up production PostgreSQL in custom format using the existing runbook. Confirm
   the current head is `0038_execution_safety` and the release has a single Alembic
   head. Do not deploy during the 16:00–16:45 analyze window.
2. Apply `alembic upgrade head` from this release **before** starting the new code.
   Revision `0039_classification_snapshots` adds two tables and nullable
   `collection_log.classification_quality`; it does not backfill or modify positions.
3. Keep snapshot enforcement false. Enable theme collection in the configured
   environment and restart using the existing deployment process. Allow the 16:40
   sector collection and 17:00 theme job to finish. Do not run the price collection
   intraday merely to obtain sector data.
4. For an explicit same-day retry, use the existing authenticated administrator
   endpoint `POST /api/v1/ops/scheduler/run/theme_collection` after a complete sector
   snapshot exists. It only collects classifications; it does not submit orders.
   `POST /api/v1/ops/scheduler/run/classification_check` reconciles diagnostics.
5. Inspect the batch monitor/API: both kinds complete, expected universe correct,
   no error, last-successful publication present, member counts reconciled. Verify
   selected multi-theme and verified-no-theme examples against the public source.
   Never fill missing classifications with fabricated labels.
6. Enable snapshot enforcement only after this evidence exists for the previous
   trading session required by the next order day. This flag is not enabled by
   migrations or collection success. Keep the old theme column until rollout and
   rollback compatibility are no longer needed.

## Trading and historical interpretation

With enforcement enabled, normal BUY paths require the previous KRX trading day's
published sector/theme snapshot for each enabled cap. Current-day publications do
not substitute for a missing previous-day snapshot. Unknown or stale data blocks
these entries; verified no-theme passes the theme check. Each ticker's whole
holding plus pending amount counts once per shared theme. Cash, single-stock and
account-wide limits remain in force.

Validated watchlist and limit-up V1 orders retain their classification exemption,
including freshness/missing checks. Their exposure still contributes to other
strategies' classification caps. Exit ownership and sell behavior are unchanged.

Theme backtests select a snapshot actually published by midnight KST at the start
of the requested period. With no explicit start, the earliest stored price date is
used. No available publication returns `theme_snapshot_unavailable`; today's
membership is never assigned retrospectively. The selected membership pool is
fixed for that run; this does not claim daily historical constituent rebalancing.

## Failure and rollback

Upstream HTTP/schema/empty/duplicate/count errors keep the last successful snapshot
for inspection but cannot make it fresh. Partial sector classification preserves
OHLCV/investor-flow collection and basic metadata quality. Retrying appends evidence
and never hides earlier failures. If a watchdog closes an attempt, a late worker
cannot publish over it.

Disabling snapshot enforcement returns to legacy classification checks, which will
still block ordinary entries where the legacy theme column is NULL. It is not a
way to silently allow unclassified trades. Preserve snapshot tables on application
rollback. Alembic downgrade drops classification history and is only for an explicit
database restoration procedure, never an automatic application rollback.

## Offline UI verification

The [batch quality preview](screenshots/classification-quality.png) uses fixture
data and the actual renderer/styles. It illustrates a failed theme attempt next to
the preserved successful snapshot; it is not a production incident screenshot.
