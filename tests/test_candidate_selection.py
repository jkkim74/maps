"""후보 점수 완성도 판정의 Python·SQL 공통 계약."""

from __future__ import annotations

import datetime as dt

import pytest

from maps.common.models import CandidateSnapshot
from maps.ops import candidate_selection


def _candidate(
    ticker: str,
    *,
    score_ready: bool,
    coverage: float,
) -> CandidateSnapshot:
    return CandidateSnapshot(
        ref_date=dt.date(2026, 8, 26),
        strategy_id="contrarian_quality_accumulation_v1",
        ticker=ticker,
        name=ticker,
        market="KOSPI",
        factor_score=50.0,
        trend_strength=50.0,
        ts_bucket="S3",
        final_score=100.0,
        score_ready=score_ready,
        score_coverage_ratio=coverage,
        weekly_pass=True,
    )


@pytest.mark.parametrize(
    ("score_ready", "coverage", "expected"),
    [
        (True, 1.0, True),
        (False, 1.0, False),
        (True, 0.9999, False),
        (False, 0.3, False),
    ],
)
def test_candidate_score_complete_requires_flag_and_full_coverage(
    score_ready: bool,
    coverage: float,
    expected: bool,
) -> None:
    """플래그나 커버리지 검증을 빼면 비정상 과거 행이 완성 후보로 섞인다."""
    row = _candidate("005930", score_ready=score_ready, coverage=coverage)

    assert candidate_selection.candidate_score_complete(row) is expected


def test_candidate_score_complete_expression_matches_python_contract(db) -> None:
    """API SQL 필터가 Python 저장·AI 판정과 다른 경계를 쓰면 목록이 어긋난다."""
    db.add_all(
        [
            _candidate("READY", score_ready=True, coverage=1.0),
            _candidate("FLAG_FALSE", score_ready=False, coverage=1.0),
            _candidate("LOW_COVERAGE", score_ready=True, coverage=0.9999),
        ]
    )
    db.commit()

    rows = (
        db.query(CandidateSnapshot)
        .filter(candidate_selection.candidate_score_complete_expression())
        .all()
    )

    assert [row.ticker for row in rows] == ["READY"]
