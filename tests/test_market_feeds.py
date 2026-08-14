from __future__ import annotations

import datetime as dt
import json
import urllib.request

from maps.common.settings import MapsSettings
from maps.market.feeds import _fetch_naver_headlines, _reconcile_news_counts


class _Response:
    """Small context-manager response used by the URL regression test."""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        """Return one same-day news item."""
        return json.dumps(
            {
                "items": [
                    {
                        "title": "국내 증시",
                        "description": "장 마감",
                        "originallink": "https://example.com/news/1",
                        "pubDate": "Wed, 12 Aug 2026 16:00:00 +0900",
                    }
                ]
            }
        ).encode()


def test_fetch_naver_headlines_uses_api_hub_endpoint_and_headers(monkeypatch) -> None:
    """New API HUB credentials must not be sent to the retired Developers endpoint."""
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        requests.append(request)
        assert timeout == 15
        return _Response()

    monkeypatch.setattr("maps.market.feeds.urllib.request.urlopen", fake_urlopen)
    settings = MapsSettings(
        naver_client_id="client-id",
        naver_client_secret="client-secret",
        maps_market_news_query_limit=10,
    )

    headlines = _fetch_naver_headlines(settings, dt.date(2026, 8, 12))

    assert len(requests) == 3
    assert len(headlines) == 1
    for request in requests:
        assert request.full_url.startswith(
            "https://naverapihub.apigw.ntruss.com/search/v1/news?"
        )
        assert request.get_header("X-ncp-apigw-api-key-id") == "client-id"
        assert request.get_header("X-ncp-apigw-api-key") == "client-secret"
        assert request.get_header("X-naver-client-id") is None


def test_reconcile_news_counts_preserves_proportions_and_exact_total() -> None:
    """Small model arithmetic drift must not discard an otherwise valid score."""
    result: dict[str, object] = {
        "positive_count": 72,
        "neutral_count": 15,
        "negative_count": 12,
    }

    _reconcile_news_counts(result, 100)

    assert result == {
        "positive_count": 73,
        "neutral_count": 15,
        "negative_count": 12,
    }


def _flow_row(ticker: str, foreign, institutional, individual):
    """수급 스냅샷 한 행. None 은 pykrx 결과에 그 투자자 유형이 없었다는 뜻이다."""
    from maps.common.models import InvestorFlowSnapshot

    return InvestorFlowSnapshot(
        date=dt.date(2026, 8, 13),
        ticker=ticker,
        market="KOSPI",
        foreign_net_value=foreign,
        institutional_net_value=institutional,
        individual_net_value=individual,
    )


def _memory_db():
    """인메모리 세션 팩토리. 테스트 간 상태를 공유하지 않는다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import maps.common.models  # noqa: F401
    from maps.common.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_flow_observations_counts_missing_investor_values_as_zero() -> None:
    """일부 종목의 결측이 그날 수급 전체를 무효화하면 안 된다.

    NULL 은 수집 실패가 아니라 pykrx 가 그 종목·투자자 유형을 결과에 넣지 않은 것이다
    (우선주·저유동성 종목에 흔하다). 운영 실측 2026-08-13 은 2,622행 중 기관 NULL 이
    538행(20.5%)이라, 전량 무효 판정이면 매일 커버리지가 0.65 에 고정돼 신규 매수가
    전면 차단된다.
    """
    from maps.market.feeds import _flow_observations

    factory = _memory_db()
    db = factory()
    db.add_all([
        _flow_row("000001", 100.0, 200.0, -300.0),
        _flow_row("000002", None, 50.0, -50.0),      # 외국인 기록 없음
        _flow_row("000003", 20.0, None, -20.0),      # 기관 기록 없음
    ])
    db.commit()

    result = _flow_observations(db, dt.date(2026, 8, 13), 1_000_000.0)

    assert result is not None
    # NULL 을 0 으로 더한 합계와 일치해야 한다 (foreign 120, institution 250, individual -370)
    assert result["foreign_score"] == round(50.0 + 120.0 / 1_000_000.0 * 500.0, 2)
    assert result["institution_score"] == round(50.0 + 250.0 / 1_000_000.0 * 500.0, 2)
    assert result["retail_score"] == round(50.0 - (-370.0) / 1_000_000.0 * 500.0, 2)
    db.close()


def test_flow_observations_returns_none_when_a_field_is_missing_on_every_row() -> None:
    """한 필드가 전 행에서 결측이면 그 투자자 프레임 자체가 안 온 것이다 — fail-closed."""
    from maps.market.feeds import _flow_observations

    factory = _memory_db()
    db = factory()
    db.add_all([
        _flow_row("000001", 100.0, None, -100.0),
        _flow_row("000002", 50.0, None, -50.0),
    ])
    db.commit()

    assert _flow_observations(db, dt.date(2026, 8, 13), 1_000_000.0) is None
    db.close()


def test_flow_observations_returns_none_without_rows() -> None:
    """수집이 실패해 그 날짜 행이 0건이면 기존대로 막는다."""
    from maps.market.feeds import _flow_observations

    factory = _memory_db()
    db = factory()

    assert _flow_observations(db, dt.date(2026, 8, 13), 1_000_000.0) is None
    db.close()


def test_enrich_measures_liquidity_and_psychology_with_partial_investor_nulls(monkeypatch) -> None:
    """일부 결측이 섞인 정상 수급일에는 커버리지가 1.0 이 되어야 한다.

    2026-08-12~14 운영에서 coverage 가 0.65 에 고정돼 모든 신규 매수가 막혔다.
    liquidity(0.25)와 psychology(0.10)가 동반 사망한 것이 정확히 0.35 결손이다.
    """
    from maps.common.models import HistoricalOHLCV, MarketNewsSentiment
    from maps.market.feeds import DatabaseKostolanyDataProvider
    from maps.market.regime import MarketRegimeCompositeScorer, MarketRegimeInput

    ref_date = dt.date(2026, 8, 13)
    factory = _memory_db()
    db = factory()
    # _market_observations 는 ref_date 로 끝나는 서로 다른 20거래일 이상을 요구한다.
    for offset in range(25):
        date = ref_date - dt.timedelta(days=offset)
        for ticker, base in (("000001", 10_000.0), ("000002", 20_000.0)):
            db.add(HistoricalOHLCV(
                date=date, ticker=ticker,
                open=base, high=base, low=base,
                close=base + offset, volume=1_000,
            ))
    db.add_all([
        _flow_row("000001", 100.0, 200.0, -300.0),
        _flow_row("000002", None, 50.0, -50.0),
        _flow_row("000003", 20.0, None, -20.0),
    ])
    db.add(MarketNewsSentiment(ref_date=ref_date, status="success", score=88.0))
    db.commit()
    db.close()

    monkeypatch.setattr("maps.market.feeds.SessionLocal", factory)
    base_input = MarketRegimeInput(
        legacy_regime="mixed",
        vol_regime="normal",
        weekly_trend="pass",
        price_trend_score=60.0,
        volatility_score=50.0,
        foreign_fx_score=50.0,
        factor_sources={},
    )

    enriched = DatabaseKostolanyDataProvider(ref_date).enrich(base_input)
    result = MarketRegimeCompositeScorer().score(enriched)

    assert enriched.measured_liquidity_score is not None
    assert enriched.measured_psychology_score is not None
    assert result.coverage_ratio == 1.0
    assert result.score_ready is True
    assert result.missing_factors == ()
