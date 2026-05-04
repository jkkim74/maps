"""Historical OHLCV repository helpers."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from maps.common.models import HistoricalOHLCV


class HistoricalOHLCVRepository:
    """Read OHLCV history in shapes used by validation/backtests."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_tickers_with_history(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        min_bars: int = 1,
    ) -> list[str]:
        query = self._db.query(HistoricalOHLCV.ticker)
        if start is not None:
            query = query.filter(HistoricalOHLCV.date >= start)
        if end is not None:
            query = query.filter(HistoricalOHLCV.date <= end)
        rows = (
            query.group_by(HistoricalOHLCV.ticker)
            .having(pd_count(HistoricalOHLCV.id) >= min_bars)
            .order_by(HistoricalOHLCV.ticker.asc())
            .all()
        )
        return [row[0] for row in rows]

    def to_dataframe(
        self,
        ticker: str,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> pd.DataFrame:
        """Return one ticker OHLCV as a date-indexed DataFrame."""
        query = self._db.query(HistoricalOHLCV).filter(HistoricalOHLCV.ticker == ticker)
        if start is not None:
            query = query.filter(HistoricalOHLCV.date >= start)
        if end is not None:
            query = query.filter(HistoricalOHLCV.date <= end)
        rows = query.order_by(HistoricalOHLCV.date.asc()).all()
        data = [
            {
                "date": row.date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "adj_close": row.adj_close,
            }
            for row in rows
        ]
        if not data:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "adj_close"])
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")


def pd_count(column):
    """Small indirection keeps SQLAlchemy import local to this helper."""
    from sqlalchemy import func

    return func.count(column)
