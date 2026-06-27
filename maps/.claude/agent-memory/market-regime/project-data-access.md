---
name: project-data-access
description: Reliable data-access patterns for KOSPI/KOSDAQ OHLCV via pykrx and MAPS regime analyzer, including credentials, ticker mapping, and computation conventions
metadata:
  type: project
---

## pykrx KRX credentials

KRX login requires env vars `KRX_ID` and `KRX_PW`. Values are in `.env` at the project root (`D:/workspace2/maps/maps/maps/.env`). Must be set before importing pykrx or calls will fail silently with garbled ticker name errors.

## KOSPI / KOSDAQ index tickers

| Ticker | Index | Typical range (2024-2026) |
|--------|-------|--------------------------|
| `1001` | KOSPI composite | ~2,400 (Dec 2024) → ~8,200+ (Jun 2026) |
| `2001` | KOSDAQ composite | ~700–1,200 |

Ticker `1001` IS the main KOSPI composite index used by `maps/market/regime.py`.

## Data fetch pattern

```python
import os
# KRX_ID / KRX_PW live in the project .env — load them, never hardcode here.
# (e.g. python-dotenv, or export them in the shell before running)
assert os.getenv('KRX_ID') and os.getenv('KRX_PW'), "set KRX_ID/KRX_PW from .env first"
from pykrx import stock
df = stock.get_index_ohlcv_by_date(start_yyyymmdd, end_yyyymmdd, '1001', freq='d')
df.columns = ['open','high','low','close','volume','tr_val','mktcap']
```

Returns a DatetimeIndex DataFrame. Must fetch from at least 2 years back to get 200+ trading days.

## yfinance not installed in project .venv, but available in system Python

As of Jun 2026, `yfinance` is NOT installed in the project `.venv` (C:\Python312 base, now missing). However, `yfinance` IS installed in the system Anaconda Python at `C:\ProgramData\anaconda3\python.exe`. Use system Python (`python` on PATH) for standalone computation scripts.

## pykrx KRX API: HTTP 400 / LOGOUT in Jun 2026

As of Jun 2026, the KRX public data endpoint `data.krx.co.kr` returns HTTP 400 for all pykrx/FinanceDataReader calls regardless of credentials. Both libraries fail. **Use yfinance with tickers `^KS11` (KOSPI) and `^KQ11` (KOSDAQ) as the primary data source for this agent.**

yfinance ^KS11 returns 601 rows from 2024-01-01 to 2026-06-23 (well above 200-row minimum). Columns after flatten: Open, High, Low, Close, Volume.

```python
import yfinance as yf
import pandas as pd
df = yf.download('^KS11', start='2024-01-01', end='2026-06-24',
                 interval='1d', progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
```

## MAPS RegimeAnalyzer output (Jun 2026 context)

With only KOSPI and KOSDAQ available (yfinance missing), 6 of 8 assets return "flat" (no data). This causes the MAPS analyzer to use only 2 of 8 assets, distorting the up-ratio calculation. Use the MAPS analyzer result for `weekly_trend` (MA10W > MA20W > MA40W) and `vol_regime` (20-week annualized vol thresholds 12%/20%), but compute BULL/BEAR/VOLATILE regime gates independently.

## MAPS taxonomy mapping for this agent's output

- BULL → `strong`
- BEAR → `weak`  
- VOLATILE → `mixed`

This agent's BULL/BEAR/VOLATILE classification is a complement to MAPS' strong/mixed/weak. Do not conflate unless explicitly asked.

## Breadth proxy (no constituent data available)

Full constituent data (individual stock 52-week highs, advancing issues) is not available via pykrx index API. Use index-level proxy:
- `adv_ratio_1y`: fraction of last 252 trading days with positive log return
- `wk52_high_ratio`: fraction of last 252 days where close >= rolling 252-day max * 0.99

Lower confidence by ~0.10 when using these proxies instead of true breadth data.

## KOSPI market context (Jun 2026, computed 2026-06-23)

KOSPI is in an extraordinary bull run: ~2,400 (Dec 2024) → ~8,204 (Jun 23 2026), a ~3.4x gain in 18 months. Computed values as of 2026-06-23:
- last close: 8,203.84 | 50MA: 7,485.95 | 200MA: 5,200.55
- close/200MA ratio: 1.5775 (57.7% above 200MA)
- 20d realized vol (annualized): 72.78%
- ATR percentile rank: 0.9983 (99.8th percentile — extreme)
- Breadth proxy (adv_ratio_1y): 0.6627; wk52_high_ratio: 0.4841
- Weekly trend: MA10W=7793, MA20W=6760.5, MA40W=5468.92 → PASS

Regime: VOLATILE (trend=BULL but ATR pctile 0.9983 >= 0.75 overrides); confidence=0.85

## Weekly trend computation (MAPS method)

```python
weekly = kospi_close.resample('W').last().dropna()
arr = numpy_array_of_last_40_weeks
weekly_trend = 'pass' if (arr[-10:].mean() > arr[-20:].mean() > arr[-40:].mean()) else 'fail'
```

As of Jun 23, 2026: MA10W=7793, MA20W=6761, MA40W=5469 → `pass`.
