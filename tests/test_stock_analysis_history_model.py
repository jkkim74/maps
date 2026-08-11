"""종목분석 이력 모델 회귀 테스트."""

from __future__ import annotations

import datetime

import maps.common.models as models


def test_same_ticker_analysis_rows_append_without_overwrite(db) -> None:
    """같은 종목의 반복 분석도 독립 이력으로 보존한다."""
    assert hasattr(models, "StockAnalysisHistory")
    first = models.StockAnalysisHistory(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        ref_date=datetime.date(2026, 8, 11),
        snapshot={"기술적분석": {"현재가": 70_000}},
        narrative="첫 분석",
        trade_plan={"recommendation": "WATCH"},
        recommendation="WATCH",
        analyzed_price=70_000,
    )
    second = models.StockAnalysisHistory(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        ref_date=datetime.date(2026, 8, 11),
        snapshot={"기술적분석": {"현재가": 71_000}},
        narrative="두 번째 분석",
        trade_plan={"recommendation": "BUY"},
        recommendation="BUY",
        analyzed_price=71_000,
    )
    db.add_all([first, second])
    db.commit()

    rows = db.query(models.StockAnalysisHistory).order_by(
        models.StockAnalysisHistory.id
    ).all()
    assert [row.analyzed_price for row in rows] == [70_000, 71_000]
