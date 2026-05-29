from __future__ import annotations

from maps.api import market


def test_market_api_uses_weekly_index_provider(monkeypatch) -> None:
    def fake_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name == "KOSPI":
            return [float(100 + i) for i in range(n_weeks)]
        if asset_name == "KOSDAQ":
            return [float(100 - i) for i in range(n_weeks)]
        return []

    monkeypatch.setattr(
        market._CombinedWeeklyProvider,
        "get_weekly_closes",
        fake_weekly_closes,
    )

    response = market.get_market()

    assets = {item.name: item for item in response.assets}
    assert response.regime == "mixed"
    assert response.weekly_trend == "pass"
    assert response.limit_ratio == 0.5
    assert assets["KOSPI"].direction == "up"
    assert assets["KOSPI"].value == 105.0
    assert assets["KOSDAQ"].direction == "down"
    assert assets["KOSDAQ"].value == 95.0
    assert assets["S&P 500"].direction == "flat"


def test_market_api_treats_missing_index_data_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        market._CombinedWeeklyProvider,
        "get_weekly_closes",
        lambda self, asset_name, n_weeks: [],
    )

    response = market.get_market()

    assert response.regime == "mixed"
    assert response.weekly_trend == "pass"
    assert response.limit_ratio == 0.5
    assert all(item.direction == "flat" for item in response.assets)
