"""Central runtime settings for MAPS.

All external integration values live here and are loaded from environment
variables or a local .env file. Do not import os.getenv directly from feature
modules when the value belongs to application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BrokerMode = Literal["mock", "kis", "kiwoom"]
DataProvider = Literal["pykrx", "mock"]


class MapsSettings(BaseSettings):
    """Typed settings loaded from .env and process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    maps_env: str = "development"
    maps_log_level: str = "INFO"
    maps_log_dir: str = "logs"
    maps_log_file: str = "maps.log"
    maps_log_max_bytes: int = Field(default=10_485_760, ge=1024)
    maps_log_backup_count: int = Field(default=10, ge=1)
    maps_db_url: str = "sqlite:///./maps.db"

    maps_broker_mode: BrokerMode = "mock"
    maps_live_trading_enabled: bool = False
    maps_data_provider: DataProvider = "pykrx"
    maps_scheduler_enabled: bool = False
    maps_scheduler_timezone: str = "Asia/Seoul"
    maps_data_collection_time: str = "16:10"
    maps_candidate_time: str = "16:20"
    maps_validation_time: str = "16:40"
    maps_order_time: str = "08:55"
    maps_eod_time: str = "15:35"
    maps_broker_sync_interval_seconds: int = Field(default=60, ge=10)
    maps_order_retry_attempts: int = Field(default=3, ge=1)
    maps_order_retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    maps_kis_timeout: float = Field(default=30.0, ge=1.0)  # KIS API read timeout (초). 모의서버 지연 대응
    maps_order_slippage_pct: float = Field(default=0.01, ge=0.0)   # 지정가 = 최신종가 * (1 + slippage)
    maps_order_max_gap_pct: float = Field(default=0.02, ge=0.0)    # 신호 이후 갭 상승 허용 상한 (초과 시 주문 스킵)
    maps_stock_report_path: str = "/opt/stock-report"              # stock-report 소스 경로

    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_real_trading: bool = False
    kis_real_base_url: str = "https://openapi.koreainvestment.com:9443"
    kis_paper_base_url: str = "https://openapivts.koreainvestment.com:29443"

    kiwoom_account_no: str = ""
    kiwoom_password: str = ""

    dart_api_key: str = ""
    slack_webhook_url: str = ""

    daily_loss_limit: float = Field(default=0.015, ge=0.0)
    max_single_exposure: float = Field(default=0.10, ge=0.0)
    account_risk_per_trade: float = Field(default=0.005, ge=0.0)

    @property
    def kis_account_prefix(self) -> str:
        """Return the first 8 digits of the KIS account number."""
        return self.kis_account_no.split("-", 1)[0].strip()

    @property
    def kis_account_product_code(self) -> str:
        """Return the 2-digit KIS account product code."""
        if "-" in self.kis_account_no:
            return self.kis_account_no.split("-", 1)[1].strip()
        if len(self.kis_account_no.strip()) == 8:
            return "01"
        return ""

    @property
    def kis_base_url(self) -> str:
        """KIS base URL selected by paper/live trading mode."""
        return self.kis_real_base_url if self.kis_real_trading else self.kis_paper_base_url


@dataclass(frozen=True)
class ConfigFieldStatus:
    name: str
    env_var: str
    configured: bool
    required: bool
    value: str
    description: str


@dataclass(frozen=True)
class ConfigSectionStatus:
    key: str
    title: str
    status: str
    fields: list[ConfigFieldStatus]


def mask_config_value(value: str | bool | float | int | None, *, secret: bool = False) -> str:
    """Return a display-safe representation of a config value."""
    if value is None or value == "":
        return ""
    text = str(value)
    if not secret:
        return text
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def _field(
    settings: MapsSettings,
    attr: str,
    env_var: str,
    description: str,
    *,
    required: bool = False,
    secret: bool = False,
) -> ConfigFieldStatus:
    value = getattr(settings, attr)
    configured = bool(value)
    return ConfigFieldStatus(
        name=attr,
        env_var=env_var,
        configured=configured,
        required=required,
        value=mask_config_value(value, secret=secret),
        description=description,
    )


def _section(key: str, title: str, fields: list[ConfigFieldStatus]) -> ConfigSectionStatus:
    required_fields = [f for f in fields if f.required]
    missing = [f for f in required_fields if not f.configured]
    if missing:
        status = "missing"
    elif required_fields:
        status = "ready"
    else:
        status = "optional"
    return ConfigSectionStatus(key=key, title=title, status=status, fields=fields)


def get_config_status(settings: MapsSettings | None = None) -> list[ConfigSectionStatus]:
    """Build a grouped, masked readiness view for external integrations."""
    s = settings or get_settings()
    kis_required = s.maps_broker_mode == "kis"
    kiwoom_required = s.maps_broker_mode == "kiwoom"

    return [
        _section(
            "runtime",
            "Runtime",
            [
                _field(s, "maps_env", "MAPS_ENV", "Application environment", required=True),
                _field(s, "maps_log_level", "MAPS_LOG_LEVEL", "Application log level", required=True),
                _field(s, "maps_log_dir", "MAPS_LOG_DIR", "Directory for rotating application logs", required=True),
                _field(s, "maps_db_url", "MAPS_DB_URL", "Database connection URL", required=True, secret=True),
                _field(s, "maps_broker_mode", "MAPS_BROKER_MODE", "Broker adapter: mock, kis, or kiwoom", required=True),
                _field(s, "maps_live_trading_enabled", "MAPS_LIVE_TRADING_ENABLED", "Explicit live-order safety switch"),
                _field(s, "maps_data_provider", "MAPS_DATA_PROVIDER", "Market data provider: pykrx or mock", required=True),
                _field(s, "maps_scheduler_enabled", "MAPS_SCHEDULER_ENABLED", "Enable APScheduler jobs inside the API process"),
                _field(s, "maps_scheduler_timezone", "MAPS_SCHEDULER_TIMEZONE", "Scheduler timezone", required=True),
                _field(s, "maps_broker_sync_interval_seconds", "MAPS_BROKER_SYNC_INTERVAL_SECONDS", "Broker balance/fill sync interval seconds"),
            ],
        ),
        _section(
            "kis",
            "KIS Korea Investment",
            [
                _field(s, "kis_app_key", "KIS_APP_KEY", "KIS Open API app key", required=kis_required, secret=True),
                _field(s, "kis_app_secret", "KIS_APP_SECRET", "KIS Open API app secret", required=kis_required, secret=True),
                _field(s, "kis_account_no", "KIS_ACCOUNT_NO", "Account number, for example 12345678-01", required=kis_required, secret=True),
                _field(s, "kis_real_trading", "KIS_REAL_TRADING", "Use KIS live endpoint instead of paper endpoint"),
                _field(s, "kis_real_base_url", "KIS_REAL_BASE_URL", "KIS live base URL"),
                _field(s, "kis_paper_base_url", "KIS_PAPER_BASE_URL", "KIS paper base URL"),
            ],
        ),
        _section(
            "kiwoom",
            "Kiwoom",
            [
                _field(s, "kiwoom_account_no", "KIWOOM_ACCOUNT_NO", "Kiwoom account number", required=kiwoom_required, secret=True),
                _field(s, "kiwoom_password", "KIWOOM_PASSWORD", "Kiwoom account password", required=kiwoom_required, secret=True),
            ],
        ),
        _section(
            "data",
            "External Data",
            [
                _field(s, "dart_api_key", "DART_API_KEY", "DART API key for managed/delisted stock metadata", secret=True),
            ],
        ),
        _section(
            "risk",
            "Risk Limits",
            [
                _field(s, "daily_loss_limit", "DAILY_LOSS_LIMIT", "Daily loss limit ratio", required=True),
                _field(s, "max_single_exposure", "MAX_SINGLE_EXPOSURE", "Maximum single-position exposure ratio", required=True),
                _field(s, "account_risk_per_trade", "ACCOUNT_RISK_PER_TRADE", "Risk budget per trade ratio", required=True),
            ],
        ),
        _section(
            "notifications",
            "Notifications",
            [
                _field(s, "slack_webhook_url", "SLACK_WEBHOOK_URL", "Optional Slack incoming webhook for alerts", secret=True),
            ],
        ),
    ]


def get_missing_required_settings(settings: MapsSettings | None = None) -> list[str]:
    """Return env var names required for the selected operating mode."""
    missing: list[str] = []
    for section in get_config_status(settings):
        missing.extend(f.env_var for f in section.fields if f.required and not f.configured)
    return missing


@lru_cache
def get_settings() -> MapsSettings:
    """Return cached application settings."""
    return MapsSettings()


def reload_settings() -> MapsSettings:
    """Clear and reload cached settings. Mainly useful for tests and admin tools."""
    get_settings.cache_clear()
    return get_settings()
