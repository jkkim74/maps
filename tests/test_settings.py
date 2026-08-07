"""Operational settings tests."""

from __future__ import annotations

from maps.common.settings import MapsSettings, get_config_status, get_missing_required_settings


def test_ai_scoring_defaults_are_safe_and_bounded() -> None:
    """AI scoring stays disabled and bounded unless explicitly configured."""
    settings = MapsSettings()

    assert settings.maps_ai_scoring_mode == "off"
    assert settings.maps_ai_daily_call_limit == 5
    assert settings.maps_ai_rerank_weight == 0.20
    assert settings.maps_ai_scoring_model_id == "us.anthropic.claude-sonnet-4-6"
    assert settings.maps_ai_request_timeout_seconds == 60.0


def test_ai_scoring_settings_accept_explicit_replace_overrides() -> None:
    """Explicit Phase 2 settings select replace mode and custom bounds."""
    settings = MapsSettings(
        maps_ai_scoring_mode="replace",
        maps_ai_daily_call_limit=9,
        maps_ai_rerank_weight=0.35,
        maps_ai_scoring_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )

    assert settings.maps_ai_scoring_mode == "replace"
    assert settings.maps_ai_daily_call_limit == 9
    assert settings.maps_ai_rerank_weight == 0.35


def test_legacy_ai_enabled_maps_to_rerank_when_new_mode_is_absent() -> None:
    """Legacy opt-in values map to the equivalent Phase 2 settings."""
    settings = MapsSettings(
        maps_ai_technical_scoring_enabled=True,
        maps_ai_technical_score_weight=0.30,
        maps_ai_candidate_top_n=7,
    )

    assert settings.maps_ai_scoring_mode == "rerank"
    assert settings.maps_ai_rerank_weight == 0.30
    assert settings.maps_ai_daily_call_limit == 7


def test_explicit_new_ai_settings_win_over_legacy_values() -> None:
    """Explicit Phase 2 values take precedence even when equal to defaults."""
    settings = MapsSettings(
        maps_ai_scoring_mode="off",
        maps_ai_daily_call_limit=3,
        maps_ai_rerank_weight=0.10,
        maps_ai_technical_scoring_enabled=True,
        maps_ai_technical_score_weight=0.80,
        maps_ai_candidate_top_n=50,
    )

    assert settings.maps_ai_scoring_mode == "off"
    assert settings.maps_ai_daily_call_limit == 3
    assert settings.maps_ai_rerank_weight == 0.10


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


def test_ai_scoring_settings_appear_in_config_status() -> None:
    """Operators can inspect every Phase 2 control without exposing secrets."""
    envs = {
        field.env_var
        for section in get_config_status(MapsSettings())
        for field in section.fields
    }

    assert {
        "MAPS_AI_SCORING_MODE",
        "MAPS_AI_DAILY_CALL_LIMIT",
        "MAPS_AI_RERANK_WEIGHT",
        "MAPS_AI_SCORING_MODEL_ID",
        "MAPS_AI_REQUEST_TIMEOUT_SECONDS",
    } <= envs
