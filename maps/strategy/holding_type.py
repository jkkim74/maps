"""종목 보유 성격 분류 — CORE / SWING / TRADING / WATCH / BAN.

코스톨라니 스타일에서는 모든 종목을 동일한 매매 룰로 처리하지 않는다.
보유 목적과 투자 성격에 따라 매수/매도/목표가/비중 규칙이 달라진다.

- CORE:    장기 보유, thesis 무효화 기준, 분할 매도 허용
- SWING:   중기 추세/이벤트, 기술적+재무 혼합 기준
- TRADING: 단기 모멘텀, 엄격한 손절, 비중 최소
- WATCH:   조건 미충족, 관심만 유지, 주문 제외
- BAN:     한계기업·관리종목·thesis 붕괴, 모든 전략에서 제외
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StockHoldingType(str, Enum):
    CORE = "CORE"
    SWING = "SWING"
    TRADING = "TRADING"
    WATCH = "WATCH"
    BAN = "BAN"


@dataclass(frozen=True)
class HoldingTypeRule:
    """holding_type별 매매 규칙."""

    holding_type: StockHoldingType
    max_position_size: float    # 단일 종목 최대 비중
    stop_priority: str          # thesis_stop | technical_stop
    allow_add_on_dip: bool      # 분할 매수 허용 여부
    allow_partial_sell: bool    # 분할 매도 허용 여부
    rr_target: float            # 목표 RR 비율
    use_value_target: bool      # value_target 사용 여부 (False면 trading_target)
    max_hold_days: int | None   # 최대 보유 기간 (None = 무제한)
    stop_pct_override: float | None  # 기본 손절 % 오버라이드


HOLDING_RULES: dict[StockHoldingType, HoldingTypeRule] = {
    StockHoldingType.CORE: HoldingTypeRule(
        holding_type=StockHoldingType.CORE,
        max_position_size=0.15,
        stop_priority="thesis_stop",
        allow_add_on_dip=True,
        allow_partial_sell=True,
        rr_target=3.0,
        use_value_target=True,
        max_hold_days=None,
        stop_pct_override=None,
    ),
    StockHoldingType.SWING: HoldingTypeRule(
        holding_type=StockHoldingType.SWING,
        max_position_size=0.10,
        stop_priority="technical_stop",
        allow_add_on_dip=False,
        allow_partial_sell=True,
        rr_target=2.0,
        use_value_target=False,
        max_hold_days=60,
        stop_pct_override=None,
    ),
    StockHoldingType.TRADING: HoldingTypeRule(
        holding_type=StockHoldingType.TRADING,
        max_position_size=0.05,
        stop_priority="technical_stop",
        allow_add_on_dip=False,
        allow_partial_sell=False,
        rr_target=1.5,
        use_value_target=False,
        max_hold_days=10,
        stop_pct_override=0.04,  # -4% 엄격한 손절
    ),
    StockHoldingType.WATCH: HoldingTypeRule(
        holding_type=StockHoldingType.WATCH,
        max_position_size=0.0,
        stop_priority="technical_stop",
        allow_add_on_dip=False,
        allow_partial_sell=False,
        rr_target=0.0,
        use_value_target=False,
        max_hold_days=0,
        stop_pct_override=None,
    ),
    StockHoldingType.BAN: HoldingTypeRule(
        holding_type=StockHoldingType.BAN,
        max_position_size=0.0,
        stop_priority="technical_stop",
        allow_add_on_dip=False,
        allow_partial_sell=False,
        rr_target=0.0,
        use_value_target=False,
        max_hold_days=0,
        stop_pct_override=None,
    ),
}


@dataclass(frozen=True)
class HoldingTypeInput:
    """종목 분류에 필요한 입력 데이터."""

    strategy_type: str | None = None           # CONTRARIAN_QUALITY, BREAKOUT 등
    valuation_margin_score: float | None = None
    excluded_reason: str | None = None         # scoring에서 제외된 사유
    is_managed_stock: bool = False             # 관리종목 여부
    debt_risk_flag: bool = False               # 부채 과다 플래그
    thesis_broken: bool = False                # thesis 붕괴 여부
    trend_strength: float | None = None
    buy_stage: int | None = None               # 역발상 분할 매수 단계
    ai_contrarian_opinion: str | None = None   # PASS|WATCH|REJECT


class HoldingTypeClassifier:
    """종목 보유 성격을 분류한다.

    분류 우선순위:
    1. BAN: 한계기업, 관리종목, thesis 붕괴, 부채 과다
    2. WATCH: 조건 미충족 (점수 낮음, AI REJECT)
    3. CORE: contrarian_quality 전략 + 높은 밸류에이션 마진
    4. SWING: pullback/momentum 전략
    5. TRADING: breakout/단기 전략
    """

    def classify(self, inp: HoldingTypeInput) -> StockHoldingType:
        """holding_type을 결정한다."""
        # BAN 조건
        if inp.is_managed_stock or inp.thesis_broken or inp.debt_risk_flag:
            return StockHoldingType.BAN

        if inp.excluded_reason and "valuation_margin_below" in (inp.excluded_reason or ""):
            return StockHoldingType.BAN

        # WATCH 조건 (AI REJECT 또는 점수 극히 낮음)
        if inp.ai_contrarian_opinion == "REJECT":
            return StockHoldingType.WATCH

        strategy = (inp.strategy_type or "").upper()

        # CORE: 역발상 전략 + 가치 안전마진 충분
        if strategy == "CONTRARIAN_QUALITY":
            val_score = inp.valuation_margin_score or 0.0
            if val_score >= 65:
                return StockHoldingType.CORE
            if val_score >= 50:
                return StockHoldingType.SWING
            return StockHoldingType.WATCH

        # SWING: pullback 계열
        if strategy in ("PULLBACK", "MULTI_ASSET_TREND"):
            return StockHoldingType.SWING

        # TRADING: breakout, momentum 계열
        if strategy in ("BREAKOUT", "MOMENTUM"):
            return StockHoldingType.TRADING

        # 기본: SWING
        return StockHoldingType.SWING

    def get_rule(self, holding_type: StockHoldingType) -> HoldingTypeRule:
        """holding_type에 해당하는 매매 규칙을 반환한다."""
        return HOLDING_RULES[holding_type]
