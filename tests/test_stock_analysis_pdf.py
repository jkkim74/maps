"""종목분석 이력 PDF 내려받기 테스트."""

from __future__ import annotations

import datetime
import os
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import StockAnalysisHistory


@pytest.fixture
def client():
    """독립 인메모리 DB를 사용하는 API 클라이언트."""
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.session_factory = factory
    yield test_client
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _full_snapshot() -> dict:
    """분석기가 실제로 만드는 모양의 스냅샷."""
    return {
        "종목명": "삼성전자",
        "종목코드": "005930",
        "수집시각": "2026-08-14 09:30:00",
        "기술적분석": {
            "기준일": "2026-08-13",
            "현재가": 71_200,
            "전일대비_pct": 1.42,
            "52주_고가": 88_800,
            "52주_저가": 49_900,
            "이동평균선": {"MA5": 70_100.0, "MA20": 69_400.0, "MA60": 66_800.0},
            "정배열_여부": True,
            "20_60_크로스": "golden_cross",
            "RSI14": 58.3,
            "RSI_상태": "중립",
            "MACD": 812.4,
            "MACD_signal": 640.1,
            "MACD_히스토그램": 172.3,
            "MACD_방향": "상승",
            "차트_6개월": [
                {"date": f"2026-0{2 + i // 20}-{1 + i % 20:02d}", "close": 60_000 + i * 300,
                 "volume": 10_000_000}
                for i in range(26)
            ],
        },
        "밸류에이션": {
            "PER": 12.4, "PBR": 1.08, "EPS": 5_740.0, "BPS": 65_900.0,
            "DIV_배당수익률": 2.05, "시가총액_억원": 4_250_000.0, "상장주식수": 5_969_782_550,
        },
        "재무제표_3개년": {
            "2025": {"매출액": 3_009_000_000_000, "영업이익": 65_700_000_000,
                     "당기순이익": 55_400_000_000, "자산총계": 5_140_000_000_000,
                     "부채총계": 1_020_000_000_000, "자본총계": 4_120_000_000_000,
                     "부채비율_pct": 24.8, "ROE_pct": 13.4, "영업이익률_pct": 21.8},
            "2024": {"매출액": 2_589_000_000_000, "영업이익": 32_700_000_000,
                     "부채비율_pct": 25.4, "ROE_pct": 8.1},
        },
    }


def _trade_plan() -> dict:
    return {
        "recommendation": "BUY",
        "entries": [70_000, 68_000, 66_000],
        "target": 82_000,
        "stop": 63_500,
        "rationale": "정배열 유지 중 눌림목 분할 진입",
        "source": "AI",
    }


def _seed(client: TestClient, *, snapshot: dict | None = None,
          trade_plan: dict | None = None, narrative: str = "종합 의견입니다.") -> int:
    with client.session_factory() as db:
        row = StockAnalysisHistory(
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            ref_date=datetime.date(2026, 8, 13),
            snapshot=_full_snapshot() if snapshot is None else snapshot,
            narrative=narrative,
            trade_plan=_trade_plan() if trade_plan is None else trade_plan,
            recommendation="BUY",
            analyzed_price=71_200,
            created_at=datetime.datetime(2026, 8, 14, 0, 30, 0),
        )
        db.add(row)
        db.commit()
        return row.id


def test_returns_a_real_pdf(client: TestClient) -> None:
    """응답이 실제 PDF 바이트여야 한다."""
    history_id = _seed(client)

    res = client.get(f"/api/v1/stock-analysis/history/{history_id}/pdf")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert len(res.content) > 1_000


def test_embeds_the_korean_font(client: TestClient) -> None:
    """한글 폰트를 PDF에 임베드해야 뷰어에 폰트가 없어도 글자가 보인다."""
    history_id = _seed(client)

    body = client.get(f"/api/v1/stock-analysis/history/{history_id}/pdf").content

    assert b"FontFile2" in body


def test_attachment_filename_carries_korean_via_rfc5987(client: TestClient) -> None:
    """한글 파일명은 RFC 5987 로 인코딩하고 ASCII 폴백을 함께 준다."""
    history_id = _seed(client)

    disposition = client.get(
        f"/api/v1/stock-analysis/history/{history_id}/pdf"
    ).headers["content-disposition"]

    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    encoded = disposition.split("filename*=UTF-8''")[1].strip()
    assert urllib.parse.unquote(encoded) == "종목분석_005930_삼성전자_20260813.pdf"


def test_missing_history_is_404(client: TestClient) -> None:
    """없는 이력은 404 다."""
    assert client.get("/api/v1/stock-analysis/history/9999/pdf").status_code == 404


def test_survives_empty_chart_and_failed_sections(client: TestClient) -> None:
    """차트가 비고 재무·밸류에이션이 실패해도 PDF 는 만들어져야 한다."""
    history_id = _seed(
        client,
        snapshot={
            "종목명": "삼성전자",
            "종목코드": "005930",
            "기술적분석": {"기준일": "2026-08-13", "현재가": 71_200, "차트_6개월": []},
            "밸류에이션": {"error": "조회 실패"},
            "재무제표_3개년": {"error": "DART_API_KEY 미설정"},
        },
        trade_plan={"recommendation": "WATCH", "source": "MANUAL_REQUIRED"},
        narrative="",
    )

    res = client.get(f"/api/v1/stock-analysis/history/{history_id}/pdf")

    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")


def test_falls_back_to_the_bundled_font(monkeypatch) -> None:
    """시스템에 한글 폰트가 하나도 없어도 pykrx 동봉 폰트로 렌더링된다."""
    from reportlab.pdfbase import pdfmetrics

    from maps.stock_analysis import pdf as pdf_module

    monkeypatch.setattr(pdf_module, "_FONT_CANDIDATES", ())
    monkeypatch.setattr(
        pdfmetrics, "getRegisteredFontNames", lambda: [], raising=True
    )

    pdf_module._register_fonts()  # 예외 없이 끝나야 한다

    assert os.path.exists(pdf_module._bundled_font()[0])


def test_raises_when_no_korean_font_exists(monkeypatch) -> None:
    """폰트를 못 찾으면 빈 네모짜리 PDF 를 내보내는 대신 실패한다."""
    from reportlab.pdfbase import pdfmetrics

    from maps.stock_analysis import pdf as pdf_module

    monkeypatch.setattr(pdf_module, "_FONT_CANDIDATES", ())
    monkeypatch.setattr(pdf_module, "_bundled_font", lambda: ("", ""))
    monkeypatch.setattr(pdfmetrics, "getRegisteredFontNames", lambda: [], raising=True)

    with pytest.raises(pdf_module.FontUnavailableError):
        pdf_module._register_fonts()


def test_renderer_ignores_the_current_price_overlay(client: TestClient) -> None:
    """이력은 불변이다 — 갱신된 현재가가 PDF 본문을 바꾸면 안 된다.

    렌더러는 결정적(invariant)이므로, 오버레이만 바뀌었을 때 바이트가 그대로면
    오버레이가 본문에 들어가지 않았다는 뜻이다.
    """
    from maps.common.models import StockAnalysisHistory as Row
    from maps.stock_analysis.pdf import render_history_pdf

    history_id = _seed(client)
    with client.session_factory() as db:
        row = db.get(Row, history_id)
        before = render_history_pdf(row)

        row.latest_price = 99_999
        row.latest_reference_close = 98_000
        row.latest_price_source = "broker"
        row.price_refreshed_at = datetime.datetime(2026, 8, 15, 1, 0, 0)
        db.commit()
        db.refresh(row)
        after = render_history_pdf(row)

    assert before.startswith(b"%PDF")
    assert after == before
