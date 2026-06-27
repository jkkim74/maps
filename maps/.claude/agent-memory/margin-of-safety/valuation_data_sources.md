---
name: valuation-data-sources
description: Where margin-of-safety valuation inputs (PER/PBR/EPS/BPS, price, historical band) live in the MAPS repo, and the current data gap blocking valuation
metadata:
  type: reference
---

Fundamental/valuation data sources in this repo (verified 2026-06-23):

- `maps/data/fundamental_repo.py` — `FundamentalRepository.get_as_of(ticker, ref_date)` reads the `security_fundamental` table (ORM `SecurityFundamental` in `maps.common.models`). Fields: per, pbr, eps, bps, date. pykrx-loaded; forward EPS not provided (uses trailing EPS).
  - `historical_avg(ticker, ref_date, field)` — avg per/pbr over lookback (default 365d).
  - `historical_band(ticker, ref_date, current_per)` — current PER's position 0(bottom)-100(top) in its historical range. Needs >=2 history rows.
  - `FundamentalValuationProvider.get(...)` returns `ValuationMarginInput`; `.price_fundamentals(...)` returns per/pbr/eps/bps + historical averages for value-target calc.
- `maps/ai/valuation_margin.py` — `ValuationMarginScorer.score()` produces a 0-100 conservative valuation score (NOT a margin-of-safety ratio). Inputs via `ValuationMarginInput`. If no components present -> returns neutral 50 "valuation data unavailable". Higher score = cheaper. This is the repo's native valuation primitive; band component = 100 - historical_band (conservative: high band -> low score).
- Price data: `maps/data/ohlcv_repo.py` `HistoricalOHLCVRepository.to_dataframe(ticker, end, start)` reads `historical_ohlcv` table.

**CRITICAL GAP (as of 2026-06-23):** the `security_fundamental` table does NOT exist in `maps.db`. So FundamentalRepository returns None for every ticker -> no PER/PBR/EPS/BPS, no historical band, no DCF inputs. Any margin-of-safety run against this DB must reject all candidates as "insufficient data" rather than fabricate intrinsic values. Re-check table existence each run before evaluating.

Also: `historical_ohlcv` only covers 2026-02-03..2026-05-04 (60 bars/ticker). Price series is stale relative to later as_of dates; cannot corroborate screener-provided close prices past 2026-05-04.

See [[candidate-snapshot]] for where candidates and their (currently NULL) valuation outputs are stored.
