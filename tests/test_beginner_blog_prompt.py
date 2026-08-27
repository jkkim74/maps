"""초보자용 일일 매매 기록 생성 계약 테스트."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / ".claude" / "commands" / "blog.md").read_text(encoding="utf-8")
STYLE = (ROOT / "docs" / "blog_style_naver.md").read_text(encoding="utf-8")

BEGINNER_SECTIONS = (
    "1. 오늘의 매매 한눈에",
    "2. 오늘 시장은 어땠나요?",
    "3. 시스템은 왜 이렇게 움직였나요?",
    "4. 실제 매수·매도 기록",
    "5. 내일 예정된 행동",
    "6. 상세 기록",
    "7. 투자 유의사항",
)

BEGINNER_TERMS = (
    "상승 흐름이 뚜렷한 시장(STRONG)",
    "상승과 하락 신호가 섞인 시장(MIXED)",
    "하락 압력이 큰 시장(WEAK)",
    "가격 움직임이 평소보다 작은 상태(LOW 변동성)",
    "가격 움직임이 평소 수준인 상태(NORMAL 변동성)",
    "가격 움직임이 평소보다 큰 상태(HIGH 변동성)",
    "오르는 종목의 비율(Breadth)",
    "오늘 허용된 최대 신규매수 비율(entry limit)",
    "주문 수량이 모두 거래된 상태(filled)",
    "주문 수량 중 일부만 거래된 상태(partially filled)",
    "손실 제한 가격에 도달한 매도(stop loss)",
    "목표 가격에 도달한 매도(take profit)",
)


def test_beginner_sections_are_present_in_order() -> None:
    positions = [PROMPT.index(section) for section in BEGINNER_SECTIONS]
    assert positions == sorted(positions)


def test_beginner_terms_are_shared_by_prompt_and_style_guide() -> None:
    for phrase in BEGINNER_TERMS:
        assert phrase in PROMPT
        assert phrase in STYLE


def test_prompt_preserves_fact_and_failure_boundaries() -> None:
    required = (
        "JSON이 유일한 사실 출처",
        "measured: false",
        "null",
        "수집 실패",
        "특정 종목의 매수·매도를 권유하지 않습니다",
        "KIS 모의투자 계좌",
    )
    for phrase in required:
        assert phrase in PROMPT


def test_prompt_explains_korea_weak_guard_correction() -> None:
    assert "korea_weak_guard_applied" in PROMPT
    assert "한국 시장의 실제 흐름이 약해 WEAK로 낮춘 이유" in PROMPT


def test_detail_section_preserves_sector_and_candidate_audit_fields() -> None:
    required = (
        "sectors.selected",
        "score",
        "momentum_20d",
        "momentum_60d",
        "turnover_growth",
        "overheat_warning",
        "applied_to_trading: false",
        "관측 전용, 후보 선정에는 적용되지 않음",
        "placeholder_inputs",
        "중립값(50)",
        "selector",
        "legacy",
        "candidate_total",
        "candidate_excluded",
        "ai_analysis_memo",
        "ai_contrarian_thesis",
        "ai_contrarian_anti_thesis",
        "valuation_margin_reason",
        "excluded_reason",
    )
    for phrase in required:
        assert phrase in PROMPT


def test_prompt_uses_rule_data_when_ai_is_not_authoritative() -> None:
    assert "price_source가 rule이면" in PROMPT
    assert "AI 분석 실패가 명시된 경우에도 규칙 기반 데이터만 설명" in PROMPT
    assert "AI 결론으로 표현하지 않는다" in PROMPT


def test_prompt_keeps_incomplete_scores_out_of_candidate_ranking() -> None:
    """부분 산출값을 정상 100점 후보나 추천 순위로 쓰는 회귀를 막는다."""
    required = (
        "incomplete_candidates",
        "candidate_ready_total",
        "candidate_incomplete_total",
        "부분 산출값",
        "순위 비교 금지",
        "score_coverage_ratio",
        "missing_components",
        "누락 항목 미기록",
    )
    for phrase in required:
        assert phrase in PROMPT


def test_style_guide_describes_readability_validation() -> None:
    assert "핵심 전문용어의 쉬운 설명 누락까지 검사한다" in STYLE


def test_blog_prompt_requires_liquidity_cap_disclosure() -> None:
    """축소된 주문을 원래 계획대로 산 것처럼 쓰지 못하게 한다."""
    prompt = Path(".claude/commands/blog.md").read_text(encoding="utf-8")

    assert "liquidity_capped_total" in prompt
    assert "liquidity_blocked_total" in prompt
    assert "유동성 축소" in prompt
    assert "원래 계획대로" in prompt
