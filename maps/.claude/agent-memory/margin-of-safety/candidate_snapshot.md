---
name: candidate-snapshot
description: candidate_snapshot DB table — source of candidates and home of valuation output columns (value_target, valuation_margin_score)
metadata:
  type: reference
---

`candidate_snapshot` table in `maps.db` (SQLite, repo root) holds pipeline candidates keyed by (ref_date, strategy_id, ticker). Query latest per ticker: `... WHERE ticker=? ORDER BY ref_date DESC LIMIT 1`.

Valuation-relevant columns: `value_target`, `valuation_margin_score`, `valuation_margin_reason`, `trading_target`, `ai_target_price`, `ai_stop_price`, `technical_stop`, `thesis_stop`, `emergency_stop`.

As of 2026-06-23 the latest rows (ref_date 2026-05-04) have value_target / valuation_margin_score / valuation_margin_reason all NULL for the screener tickers — pipeline has never computed valuations there (consistent with the missing security_fundamental table; see [[valuation-data-sources]]).

Note: name column stores EUC-KR/CP949-encoded Korean; reads as mojibake over a default sqlite3 connection.
