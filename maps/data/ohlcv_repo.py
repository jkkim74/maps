"""Historical OHLCV repository helpers."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import func
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

    def list_tickers_with_counts(
        self,
        *,
        end: dt.date | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, int]]:
        """Return tickers and available bar counts up to ``end``."""
        query = self._db.query(
            HistoricalOHLCV.ticker,
            pd_count(HistoricalOHLCV.id).label("bar_count"),
        )
        if end is not None:
            query = query.filter(HistoricalOHLCV.date <= end)
        query = (
            query.group_by(HistoricalOHLCV.ticker)
            .order_by(HistoricalOHLCV.ticker.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        return [(row[0], int(row[1])) for row in query.all()]

    def list_tickers_on_date(
        self,
        ref_date: dt.date,
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Return tickers that have OHLCV on ``ref_date``."""
        query = (
            self._db.query(HistoricalOHLCV.ticker)
            .filter(HistoricalOHLCV.date == ref_date)
            .order_by(HistoricalOHLCV.ticker.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        return [row[0] for row in query.all()]

    def recent_dataframes(
        self,
        tickers: list[str],
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        bars: int,
    ) -> dict[str, pd.DataFrame]:
        """Return recent OHLCV frames for many tickers using one query."""
        if not tickers:
            return {}

        base_query = self._db.query(
            HistoricalOHLCV.ticker.label("ticker"),
            HistoricalOHLCV.date.label("date"),
            HistoricalOHLCV.open.label("open"),
            HistoricalOHLCV.high.label("high"),
            HistoricalOHLCV.low.label("low"),
            HistoricalOHLCV.close.label("close"),
            HistoricalOHLCV.volume.label("volume"),
            HistoricalOHLCV.adj_close.label("adj_close"),
            func.row_number()
            .over(
                partition_by=HistoricalOHLCV.ticker,
                order_by=HistoricalOHLCV.date.desc(),
            )
            .label("rn"),
        ).filter(HistoricalOHLCV.ticker.in_(tickers))
        if start is not None:
            base_query = base_query.filter(HistoricalOHLCV.date >= start)
        if end is not None:
            base_query = base_query.filter(HistoricalOHLCV.date <= end)

        recent = base_query.subquery()
        rows = (
            self._db.query(recent)
            .filter(recent.c.rn <= bars)
            .order_by(recent.c.ticker.asc(), recent.c.date.asc())
            .all()
        )

        grouped: dict[str, list[dict[str, object]]] = {ticker: [] for ticker in tickers}
        for row in rows:
            grouped[row.ticker].append(
                {
                    "date": row.date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "adj_close": row.adj_close,
                }
            )

        frames: dict[str, pd.DataFrame] = {}
        columns = ["open", "high", "low", "close", "volume", "adj_close"]
        for ticker, data in grouped.items():
            if not data:
                frames[ticker] = pd.DataFrame(columns=columns)
                continue
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            frames[ticker] = df.set_index("date")
        return frames

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
