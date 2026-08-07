"""MAPS 커스텀 예외 정의.

설계서 v2.6.3 §9 기준. 모든 예외는 MAPSError를 상속한다.
"""


class MAPSError(Exception):
    """MAPS 최상위 예외."""


# --- 주문 / 실행 ---

class KillSwitchError(MAPSError):
    """Kill Switch 발동 시 신규 주문 차단."""


class DuplicateOrderError(MAPSError):
    """동일 종목 중복 주문 시도."""

    def __init__(self, ticker: str) -> None:
        super().__init__(f"Duplicate order for ticker: {ticker}")
        self.ticker = ticker


class ExposureCapError(MAPSError):
    """단일 종목 노출 한도(10%) 초과."""

    def __init__(
        self,
        ticker: str,
        exposure: float | None = None,
        detail: str | None = None,
    ) -> None:
        msg = f"Exposure cap exceeded for {ticker}"
        if exposure is not None:
            msg += f" ({exposure:.1%})"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.ticker = ticker
        self.exposure = exposure
        self.detail = detail


class ResearchStrategyError(MAPSError):
    """Research/alert_only 단계 전략의 자동 주문 시도."""

    def __init__(self, strategy_id: str, stage: str) -> None:
        super().__init__(
            f"Strategy '{strategy_id}' is in stage '{stage}' — auto-order not allowed"
        )
        self.strategy_id = strategy_id
        self.stage = stage


# --- 데이터 ---

class DataQualityError(MAPSError):
    """데이터 품질 기준 미충족."""


class DataCollectionError(MAPSError):
    """데이터 수집 실패."""


# --- AI scoring ---

class AIScoringError(MAPSError):
    """Base error for bounded candidate AI scoring."""


class AIScoringUnavailableError(AIScoringError):
    """AI scoring cannot start because required configuration is absent."""


class AIScoringProviderError(AIScoringError):
    """The Bedrock provider failed before a valid response was available."""


class AIScoringResponseError(AIScoringError):
    """The provider response violated the approved scoring schema."""


# --- 승격 ---

class PromotionGateError(MAPSError):
    """PromotionGate fail with reason. KeyError로 죽지 않는다."""

    def __init__(self, strategy_id: str, reasons: list[str]) -> None:
        super().__init__(
            f"PromotionGate failed for '{strategy_id}': {'; '.join(reasons)}"
        )
        self.strategy_id = strategy_id
        self.reasons = reasons


class UnknownStrategyError(MAPSError):
    """STRATEGY_GROUP_MAP에 없는 전략 ID."""

    def __init__(self, strategy_id: str) -> None:
        super().__init__(f"Unknown strategy ID: '{strategy_id}'")
        self.strategy_id = strategy_id


# --- 브로커 ---

class BrokerAdapterError(MAPSError):
    """브로커 어댑터 오류 (연결 실패, API 오류 등)."""


# --- 하위 호환 별칭 (구 코드가 참조하는 이름들) ---

class ValidationError(MAPSError):
    """검증 실패."""


class BacktestError(MAPSError):
    """백테스트 실행 오류."""


class StrategyConfigError(MAPSError):
    """전략 설정 오류."""


class PlateauDetectedError(ValidationError):
    """수익 고원(Plateau) 감지."""


class UnauthorizedLiquidationError(KillSwitchError):
    """사용자 승인 없는 청산 시도."""


class MissingMetricError(PromotionGateError):
    """필수 지표 누락 (PromotionGate)."""

    def __init__(self, metric: str, strategy_id: str) -> None:
        super().__init__(strategy_id, [f"Missing metric: '{metric}'"])
        self.metric = metric
