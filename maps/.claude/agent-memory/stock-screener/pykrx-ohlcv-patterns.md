---
name: pykrx-ohlcv-patterns
description: pykrx 1.0.48 working call patterns, column names, known encoding bugs, and available env
metadata:
  type: reference
---

## Environment

Python env that has pykrx working: `C:\Users\jack\.conda\envs\PyTrader\python.exe`
- pykrx version: 1.0.48
- Required extra packages: `multipledispatch`, `wrapt` (install via pip if missing)

## What WORKS (as of 2026-06-23)

### get_market_ohlcv(start_date, end_date, ticker) -> DataFrame
- Returns date-indexed DataFrame, 6 columns
- Column order (positional): [open, high, low, close, volume, chg_pct%]
- Column names are garbled Korean (cp949 encoding bug) -- access by position or rename
- Example: `krx.get_market_ohlcv("20260301", "20260623", "005930")`

### get_market_ticker_name(ticker) -> str
- Returns Korean company name (str, works correctly when written to UTF-8 file)
- Terminal may display garbled if cp949 stdout -- write to file with `encoding="utf-8"`

## What FAILS (encoding bug in pykrx 1.0.48 vs new KRX API response)

- `get_market_ticker_list(date, market=)` -- returns 0 tickers (KRX API changed)
- `get_market_cap(date, market=)` / `get_market_cap_by_ticker()` -- KeyError on garbled column names
- `get_market_fundamental_by_ticker(date)` -- KeyError: 'BPS','PER','PBR','EPS','DIV','DPS' not found
- `get_market_sector_classifications(date, market=)` -- KeyError on garbled column
- `get_market_ohlcv(date, market=)` (single-date market-wide) -- KeyError on garbled columns

## Workaround for sector classification

pykrx cannot reliably fetch sector/fundamental market-wide data as of 2026.
Use a curated ticker list per sector (see screener script pattern).
Verify ticker names via `get_market_ticker_name()` and write to UTF-8 file.

## Liquidity

- Computed as: `(close * volume).rolling(20).mean()` from OHLCV data
- Threshold used: 5억 KRW (500,000,000) per universe_filter.py MIN_TURNOVER_KRW
- Halt heuristic: `volume == 0` on ref_date → treat as trading halt

## Trend Strength (TrendStrengthCalculator pattern)

From `maps/indicator/trend_strength.py`:
- MA20 score (0-40): `(last - ma20)/ma20 * 200 + 20` clamped
- RSI14 score (0-30): `(RSI - 30)/40 * 30` clamped
- Volume ratio score (0-30): `(vol_ratio - 0.5)/1.5 * 30` clamped
- S1 [0,20), S2 [20,40), S3 [40,60), S4 [60,80), S5 [80,100]
- S1 tickers are excluded from candidates per MAPS design

## Composite Score Formula (screener)

```
score = 0.50 * ts_score + 0.30 * (sector_rs_score * 100) + 0.15 * liq_score + 0.05 * priority_bonus
liq_score = min(log10(max(turnover, 1) / 5e8 + 1) * 50, 100)
priority_bonus = (6 - sector_priority) * 1.0  # 1-5 points
```
