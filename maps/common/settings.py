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
AIAnalysisMode = Literal["technical_only", "all"]


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
    # 실거래(real) 안전 확인 스위치. KIS_REAL_TRADING=true + 주문 활성 상태에서
    # 이 값이 true가 아니면 기동을 거부한다(모의투자 paper 운영을 실거래로 오인 방지).
    maps_confirm_real_trading: bool = False
    maps_data_provider: DataProvider = "pykrx"
    maps_scheduler_enabled: bool = False
    maps_scheduler_timezone: str = "Asia/Seoul"
    maps_data_collection_time: str = "16:10"
    maps_candidate_time: str = "16:20"
    maps_validation_time: str = "16:40"
    maps_order_time: str = "08:55"
    maps_eod_time: str = "15:35"
    maps_stock_report_time: str = "15:00"
    maps_broker_sync_interval_seconds: int = Field(default=60, ge=10)
    maps_order_retry_attempts: int = Field(default=3, ge=1)
    maps_order_retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    maps_kis_timeout: float = Field(default=30.0, ge=1.0)  # KIS API read timeout (초). 모의서버 지연 대응
    maps_order_slippage_pct: float = Field(default=0.01, ge=0.0)   # 지정가 = 최신종가 * (1 + slippage)
    maps_order_max_gap_pct: float = Field(default=0.02, ge=0.0)    # 신호 이후 갭 상승 허용 상한 (초과 시 주문 스킵)
    maps_candidate_min_score: float = Field(default=10.0, ge=0.0)  # CandidateSnapshot final_score 최소 기준 (미만 종목 주문 제외)
    maps_trade_rr_ratio: float = Field(default=2.0, ge=0.5)        # 목표가 = 매수가 + 손절폭 × 이 값 (MAPS_TRADE_RR_RATIO)
    # 전략매매(분석 워치리스트 브래킷 실행) 마스터 스위치. live_trading_enabled + 픽별 무장과 AND 게이트.
    maps_strategy_trade_enabled: bool = False
    # pick.qty 미지정 시 진입 수량 산정용 계좌 리스크 비율 (손절폭 기준).
    maps_strategy_trade_account_risk_pct: float = Field(default=0.01, gt=0.0, le=0.1)
    maps_stock_report_path: str = "/opt/stock_report"              # stock-report 소스 경로

    maps_krx_closed_dates: str = ""

    # 시황 분석 수동 오버라이드 (auto 이면 pykrx/yfinance 실데이터 분석)
    maps_market_regime_override: Literal["auto", "strong", "mixed", "weak"] = "auto"
    maps_weekly_trend_override: Literal["auto", "pass", "fail"] = "auto"
    maps_kostolany_regime_enabled: bool = False
    maps_contrarian_accumulation_enabled: bool = False
    maps_contrarian_max_entry_ratio: float = Field(default=0.25, ge=0.0, le=1.0)

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

    # Telegram 봇 알림 + 무장/무장해제 인라인 버튼 콜백
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    # 비운영(MAPS_ENV != production) 환경에서 텔레그램 실발송을 막는 안전 가드.
    # 로컬/테스트가 운영 봇 토큰을 가진 .env로 실행돼도 운영 채팅에 더미 픽이 새지 않게 한다.
    # 운영이 아닌 곳에서 일부러 발송하려면 이 값을 true로 명시한다.
    maps_telegram_allow_nonprod: bool = False

    # AWS Bedrock (Claude AI 분석)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    aws_bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6"

    # AI 기술적 분석 (후보 생성 시점, 16:20 KST)
    maps_ai_analysis_mode: AIAnalysisMode = "technical_only"
    maps_ai_technical_scoring_enabled: bool = False  # 명시적으로 켜야 작동
    maps_ai_technical_score_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    maps_ai_candidate_top_n: int = Field(default=5, ge=0, le=100)
    # 안전마진 스코어링(요건 5) — pykrx 펀더멘털 연동 완료로 기본 활성.
    # 후보 점수/스냅샷에만 영향하며, 실주문은 maps_live_trading_enabled로 별도 게이트된다.
    maps_valuation_margin_enabled: bool = True
    maps_strategy_aware_scoring_enabled: bool = False

    # 7단계: AI 역발상 검증 (코스톨라니식 투자 논리 검증)
    maps_ai_contrarian_check_enabled: bool = False

    # 업종 필터 (Phase B)
    maps_sector_filter_enabled: bool = False   # 명시적으로 켜야 작동
    maps_sector_kostolany_mode_enabled: bool = False
    maps_sector_top_n: int = Field(default=5, ge=1, le=30)
    maps_sector_lookback_days: int = Field(default=20, ge=5, le=120)

    # 시장폭(breadth) 가드 — KOSPI 하한선으로 빌려온 MIXED에서 좁은 장이면
    # 추격성 모멘텀·돌파·추세추종 전략을 보류(방어/되돌림 전략만 허용).
    maps_breadth_guard_enabled: bool = True
    maps_breadth_ma_window: int = Field(default=20, ge=5, le=200)
    maps_breadth_weak_threshold: float = Field(default=0.40, ge=0.0, le=1.0)

    # 로그인(단일 공용 비밀번호) — 기본 비활성(옵트인). 운영에서만 켠다.
    # 활성화하면 모든 HTML 페이지/`/api/*`가 세션 쿠키를 요구한다.
    maps_auth_enabled: bool = False
    maps_auth_username: str = "admin"
    maps_auth_password: str = ""                 # 비어 있으면 로그인 불가(모든 시도 거부)
    maps_session_secret_key: str = ""            # 세션 쿠키 서명 키(운영 필수, 미설정 시 프로세스마다 랜덤)
    maps_session_max_age: int = Field(default=60 * 60 * 24 * 14, ge=300)  # 세션 유지(초), 기본 14일
    maps_session_https_only: bool = False        # True면 세션 쿠키에 Secure 플래그(HTTPS 전용). HTTPS 적용 후 운영에서 켠다.

    # Tier 1a 청산 — 매매계획(목표가/계획손절) + 트레일링 스탑 기반 전량 청산.
    # 기본 OFF(옵트인). 켜면 _submit_exit_orders가 candidate_snapshot의 계획 필드를 사용한다.
    maps_plan_based_exits_enabled: bool = False
    maps_trailing_activate_pct: float = Field(default=0.05, ge=0.0, le=1.0)  # 진입 대비 +5% 도달 후 트레일링 활성
    maps_trailing_stop_pct: float = Field(default=0.08, ge=0.0, le=1.0)      # 고점 대비 -8% 이탈 시 청산

    # 8단계: 테마·섹터·상관관계 노출 한도
    maps_theme_exposure_limit_enabled: bool = False
    maps_sector_exposure_limit_enabled: bool = False
    maps_min_cash_ratio_strong: float = Field(default=0.15, ge=0.0, le=1.0)
    maps_min_cash_ratio_mixed: float = Field(default=0.25, ge=0.0, le=1.0)
    maps_min_cash_ratio_weak: float = Field(default=0.35, ge=0.0, le=1.0)
    maps_max_sector_exposure: float = Field(default=0.25, ge=0.0, le=1.0)
    maps_max_theme_exposure: float = Field(default=0.35, ge=0.0, le=1.0)

    # 9단계: 보유 성격 분류 (CORE/SWING/TRADING/WATCH/BAN)
    maps_holding_type_classification_enabled: bool = False

    # 10단계: 코스톨라니 가격 산출(요건 7·8) — 룰 기반, API 불필요. 기본 활성.
    # 모든 후보에 매수가·손절가·목표가(단기/가치)를 기록한다. 실주문 게이트와 무관.
    maps_kostolany_price_calculator_enabled: bool = True

    # 12단계: 드라이런/백테스트 비교 모드
    maps_dry_run: bool = False
    maps_backtest_mode: bool = False

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

    @property
    def krx_closed_dates(self):
        """Return explicitly configured KRX closure dates."""
        from maps.market.trading_rules import parse_closed_dates

        return parse_closed_dates(self.maps_krx_closed_dates)


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
                _field(s, "maps_strategy_trade_enabled", "MAPS_STRATEGY_TRADE_ENABLED", "Master switch for watchlist bracket (strategy-trade) execution"),
                _field(s, "maps_data_provider", "MAPS_DATA_PROVIDER", "Market data provider: pykrx or mock", required=True),
                _field(s, "maps_scheduler_enabled", "MAPS_SCHEDULER_ENABLED", "Enable APScheduler jobs inside the API process"),
                _field(s, "maps_scheduler_timezone", "MAPS_SCHEDULER_TIMEZONE", "Scheduler timezone", required=True),
                _field(s, "maps_broker_sync_interval_seconds", "MAPS_BROKER_SYNC_INTERVAL_SECONDS", "Broker balance/fill sync interval seconds"),
                _field(s, "maps_stock_report_time", "MAPS_STOCK_REPORT_TIME", "Daily stock report generation time"),
                _field(s, "maps_krx_closed_dates", "MAPS_KRX_CLOSED_DATES", "Additional comma-separated KRX closure dates"),
                _field(s, "maps_kostolany_regime_enabled", "MAPS_KOSTOLANY_REGIME_ENABLED", "Enable Kostolany-style composite market regime scoring"),
                _field(s, "maps_contrarian_accumulation_enabled", "MAPS_CONTRARIAN_ACCUMULATION_ENABLED", "Allow limited contrarian-quality accumulation in weak/high-volatility markets"),
                _field(s, "maps_contrarian_max_entry_ratio", "MAPS_CONTRARIAN_MAX_ENTRY_RATIO", "Maximum entry ratio for contrarian-quality accumulation"),
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
                _field(s, "aws_access_key_id", "AWS_ACCESS_KEY_ID", "AWS access key for Bedrock AI analysis", secret=True),
                _field(s, "aws_secret_access_key", "AWS_SECRET_ACCESS_KEY", "AWS secret key for Bedrock AI analysis", secret=True),
                _field(s, "aws_region", "AWS_REGION", "AWS region for Bedrock (default: us-east-1)"),
                _field(s, "aws_bedrock_model_id", "AWS_BEDROCK_MODEL_ID", "Bedrock Claude model ID"),
                _field(s, "maps_ai_analysis_mode", "MAPS_AI_ANALYSIS_MODE", "AI validation mode: technical_only or all"),
                _field(s, "maps_ai_candidate_top_n", "MAPS_AI_CANDIDATE_TOP_N", "Maximum rule-based top candidates sent to Bedrock AI"),
                _field(s, "maps_valuation_margin_enabled", "MAPS_VALUATION_MARGIN_ENABLED", "Enable valuation margin scoring on candidate snapshots"),
                _field(s, "maps_strategy_aware_scoring_enabled", "MAPS_STRATEGY_AWARE_SCORING_ENABLED", "Enable strategy-specific final_score formulas"),
                _field(s, "maps_sector_filter_enabled", "MAPS_SECTOR_FILTER_ENABLED", "Enable sector filter before candidate generation"),
                _field(s, "maps_sector_kostolany_mode_enabled", "MAPS_SECTOR_KOSTOLANY_MODE_ENABLED", "Enable Kostolany-style sector cycle selector"),
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
                _field(s, "telegram_bot_token", "TELEGRAM_BOT_TOKEN", "Telegram bot token for analyze-result alerts", secret=True),
                _field(s, "telegram_chat_id", "TELEGRAM_CHAT_ID", "Telegram chat id that receives alerts and may trigger arm/disarm"),
                _field(s, "telegram_webhook_secret", "TELEGRAM_WEBHOOK_SECRET", "Secret token validating inbound Telegram webhook calls", secret=True),
                _field(s, "maps_telegram_allow_nonprod", "MAPS_TELEGRAM_ALLOW_NONPROD", "Allow real Telegram sends when MAPS_ENV is not production (default off)"),
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


def describe_trading_mode(settings: MapsSettings | None = None) -> str:
    """현재 트레이딩 모드를 사람이 읽을 수 있는 문자열로 반환한다."""
    s = settings or get_settings()
    if s.maps_broker_mode == "mock":
        return "MOCK (인메모리 시뮬레이션)"
    broker = s.maps_broker_mode.upper()
    if not s.maps_live_trading_enabled:
        return f"{broker} 연결 · 주문 비활성 (MAPS_LIVE_TRADING_ENABLED=false)"
    if s.kis_real_trading:
        return f"{broker} 실거래(REAL) — ⚠️ 실제 주문/체결"
    return f"{broker} 모의투자(PAPER) — 모의 주문/체결"


def real_trading_unconfirmed(settings: MapsSettings | None = None) -> bool:
    """실거래가 활성화됐는데 명시적 확인(MAPS_CONFIRM_REAL_TRADING)이 없는 위험 상태인지 반환한다."""
    s = settings or get_settings()
    return (
        s.maps_broker_mode in ("kis", "kiwoom")
        and s.maps_live_trading_enabled
        and s.kis_real_trading
        and not s.maps_confirm_real_trading
    )
