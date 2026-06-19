"""MAPS 시스템 전역 상수.

설계서 v2.6.3 §2 · 기획안 v2.6.3 §4 기준.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# 전략군별 허용 MDD (기획안 v2.6.3 §4 유지)
# expected: 전략 설계 시 목표 MDD 한도
# mc_p95_limit: Monte Carlo p95 통과 기준 (절대값)
# ---------------------------------------------------------------------------
ALLOWED_MDD: Final[dict[str, dict[str, float]]] = {
    "pullback_short": {
        "expected": 0.10,
        "mc_p95_limit": 0.18,
    },
    "ath_outlier": {
        "expected": 0.20,
        "mc_p95_limit": 0.35,
    },
    "multi_asset": {
        "expected": 0.13,
        "mc_p95_limit": 0.22,
    },
    "donchian_research": {
        "expected": 0.18,
        "mc_p95_limit": 0.30,
    },
    # 역발상 분할 매수는 공포 구간 진입 특성상 MDD 허용치를 높게 설정
    "contrarian_quality": {
        "expected": 0.15,
        "mc_p95_limit": 0.25,
    },
    "portfolio_total": {
        "expected": 0.16,
        "mc_p95_limit": 0.28,
    },
}

# ---------------------------------------------------------------------------
# Trend Strength 5단계 버킷 [lo, hi) 반개구간 (점수: 0~100)
# S1 최약 → S5 최강 추세
# ---------------------------------------------------------------------------
TREND_STRENGTH_BUCKETS: Final[list[dict]] = [
    {"grade": "S1", "lo": 0,  "hi": 20,  "label": "매우 약한 추세"},
    {"grade": "S2", "lo": 20, "hi": 40,  "label": "약한 추세"},
    {"grade": "S3", "lo": 40, "hi": 60,  "label": "중립"},
    {"grade": "S4", "lo": 60, "hi": 80,  "label": "강한 추세"},
    {"grade": "S5", "lo": 80, "hi": 101, "label": "매우 강한 추세"},
]

# ---------------------------------------------------------------------------
# 파라미터 Plateau 유효 MA 최소 개수
# ---------------------------------------------------------------------------
MIN_VALID_MAS: Final[int] = 5

# ---------------------------------------------------------------------------
# Plateau 등급 (견고성 그리드 통과 비율 기준)
# ratio: 전체 파라미터 조합 중 양수 수익 비율
# ---------------------------------------------------------------------------
PLATEAU_GRADES: Final[dict[str, dict]] = {
    "A": {"min_ratio": 0.80, "label": "견고함 — 넓은 파라미터 영역에서 수익"},
    "B": {"min_ratio": 0.60, "label": "양호 — 대부분의 파라미터에서 수익"},
    "C": {"min_ratio": 0.40, "label": "보통 — 절반 이상에서 수익"},
    "D": {"min_ratio": 0.20, "label": "취약 — 일부 파라미터에서만 수익"},
    "F": {"min_ratio": 0.00, "label": "실패 — 대부분 손실"},
}

# ---------------------------------------------------------------------------
# 전략 ID → 전략군 매핑
# ---------------------------------------------------------------------------
STRATEGY_GROUP_MAP: Final[dict[str, str]] = {
    "pullback_v3":          "pullback_short",
    "pullback_v2":          "pullback_short",
    "ath_breakout_v1":      "ath_outlier",
    "ath_breakout_v2":      "ath_outlier",
    "multi_asset_trend_v1": "multi_asset",
    "donchian_v1":          "donchian_research",
    "donchian_v2":          "donchian_research",
    "contrarian_quality_accumulation_v1": "contrarian_quality",
}

# ---------------------------------------------------------------------------
# 승격 게이트 단계별 통과 조건
# 각 단계의 조건은 AND 로 평가한다.
# mc_within_limit: ALLOWED_MDD[group]["mc_p95_limit"] 이내 여부
# ---------------------------------------------------------------------------
PROMOTION_GATES: Final[dict[str, dict]] = {
    "research": {
        "description": "연구 단계 — 자동 주문 없음",
        "min_tradeability_score": 0,
        "wfa_required": False,
        "mc_within_limit": False,
        "min_sharpe": None,
        "next_stage": "alert_only",
    },
    "alert_only": {
        "description": "알림 전용 — 신호 모니터링만",
        "min_tradeability_score": 40,
        "wfa_required": False,
        "mc_within_limit": False,
        "min_sharpe": 0.0,
        "next_stage": "mock_candidate",
    },
    "mock_candidate": {
        "description": "Mock 후보 — Mock 브로커로 시뮬레이션",
        "min_tradeability_score": 60,
        "wfa_required": True,
        "mc_within_limit": True,     # ← PromotionGate 핵심 조건
        "min_sharpe": 0.3,
        "plateau_grade_min": "C",
        "next_stage": "live_candidate",
    },
    "live_candidate": {
        "description": "Live 후보 — 소규모 실거래",
        "min_tradeability_score": 75,
        "wfa_required": True,
        "mc_within_limit": True,
        "min_sharpe": 0.5,
        "plateau_grade_min": "B",
        "min_mock_months": 3,
        "next_stage": "live",
    },
    "live": {
        "description": "실거래 — 전체 비중 운용",
        "min_tradeability_score": 75,
        "wfa_required": True,
        "mc_within_limit": True,
        "min_sharpe": 0.5,
        "plateau_grade_min": "B",
        "min_mock_months": 3,
        "next_stage": None,
    },
}

# ---------------------------------------------------------------------------
# Tradeability 승격 임계 (가중치와 무관, 고정값)
# ---------------------------------------------------------------------------
TRADEABILITY_THRESHOLDS: Final[dict[str, int]] = {
    "mock_candidate": 60,
    "live_candidate": 75,
}

# ---------------------------------------------------------------------------
# Tradeability 가중치 프리셋 (합계 = 1.0)
# ---------------------------------------------------------------------------
WEIGHT_PRESETS: Final[dict[str, dict[str, float]]] = {
    "conservative": {
        "robustness": 0.40,
        "risk":       0.35,
        "recovery":   0.15,
        "return":     0.10,
    },
    "balanced": {
        "robustness": 0.30,
        "risk":       0.30,
        "recovery":   0.20,
        "return":     0.20,
    },
    "growth": {
        "robustness": 0.20,
        "risk":       0.20,
        "recovery":   0.25,
        "return":     0.35,
    },
}

# ---------------------------------------------------------------------------
# WalkForward 통과 조건 (4가지 AND)
# ---------------------------------------------------------------------------
WF_SHARPE_MEAN_MIN: Final[float] = 0.0       # sharpe_mean > 0
# WF_STD_MEAN_RATIO_MAX 제거 (std/|mean| 조건 폐기)
# 사유: 임계값 선택 근거 없음 (0.5→2.0 모두 임의적).
#       나머지 3개 조건이 의미적으로 충분:
#         - sharpe_mean > 0  : 평균 수익
#         - neg_folds ≤ 1    : 5번 중 4번 플러스 (일관성)
#         - mean_g2p ≥ 0.6   : 과적합 없음 (OOS/IS 재현율)
#       CV 조건은 조건 3(음수 fold)과 의미가 중복되면서 근거 없는 수치에 의존.
WF_NEGATIVE_FOLD_MAX: Final[int] = 1          # 음수 fold <= 1개
WF_OOS_IS_G2P_MIN: Final[float] = 0.6        # OOS/IS G2P >= 0.6

# ---------------------------------------------------------------------------
# RiskManager 기본 한도
# ---------------------------------------------------------------------------
RISK_DAILY_LOSS_LIMIT: Final[float] = 0.015   # 1.5%
RISK_MAX_CONSEC_FAILURES: Final[int] = 5
RISK_API_TIMEOUT_SEC: Final[int] = 10
RISK_MAX_SINGLE_EXPOSURE: Final[float] = 0.10  # 단일 종목 10%
