"""Operational settings tests."""

from __future__ import annotations

from maps.common.settings import MapsSettings, get_config_status, get_missing_required_settings


def test_kis_required_fields_when_kis_selected() -> None:
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_app_key="",
        kis_app_secret="",
        kis_account_no="",
    )

    missing = get_missing_required_settings(settings)

    assert "KIS_APP_KEY" in missing
    assert "KIS_APP_SECRET" in missing
    assert "KIS_ACCOUNT_NO" in missing


def test_mock_mode_does_not_require_broker_credentials() -> None:
    settings = MapsSettings(maps_broker_mode="mock")

    missing = get_missing_required_settings(settings)

    assert "KIS_APP_KEY" not in missing
    assert "KIWOOM_ACCOUNT_NO" not in missing


def test_config_status_masks_secret_values() -> None:
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_app_key="abcdef123456",
        kis_app_secret="secret123456",
        kis_account_no="12345678-01",
    )

    sections = get_config_status(settings)
    kis_fields = {field.env_var: field for section in sections if section.key == "kis" for field in section.fields}

    assert kis_fields["KIS_APP_KEY"].value == "abc***456"
    assert kis_fields["KIS_APP_SECRET"].value == "sec***456"
    assert kis_fields["KIS_ACCOUNT_NO"].value == "123***-01"


def test_kis_account_product_code_defaults_to_stock_product_for_8_digit_account() -> None:
    settings = MapsSettings(kis_account_no="12345678")

    assert settings.kis_account_prefix == "12345678"
    assert settings.kis_account_product_code == "01"


def test_analysis_pick_max_age_default_and_override() -> None:
    """픽 만료 기준은 기본 5거래일이며 환경변수로 조정된다.

    0 으로 두면 저녁 analyze 결과가 다음날 08:55 주문 전에 죽으므로 기본값이 중요하다.
    """
    assert MapsSettings().maps_analysis_pick_max_age_trading_days == 5
    assert MapsSettings(maps_analysis_pick_max_age_trading_days=10).maps_analysis_pick_max_age_trading_days == 10


def test_analysis_pick_max_age_appears_in_config_status() -> None:
    envs = {
        field.env_var
        for section in get_config_status(MapsSettings())
        for field in section.fields
    }
    assert "MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS" in envs
