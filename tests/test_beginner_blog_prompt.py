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
