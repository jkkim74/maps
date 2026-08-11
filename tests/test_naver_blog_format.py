"""네이버 블로그 원고 규약 회귀 테스트.

원고는 스마트에디터에 사람이 복사해 붙여넣는다. 마크다운이 섞이면 기호가 그대로
독자에게 보이고, 이모지·상투구가 섞이면 AI가 쓴 글로 읽힌다. 투자 글에서는 뒤쪽이
더 치명적이다.

규약 전문은 `docs/blog_style_naver.md`, 검사기는 `scripts/check_naver_format.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_GUIDE_DIR = _ROOT / "docs" / "strategy_guides"


def _load_checker():
    """스크립트를 모듈로 불러온다 — 패키지가 아니라 경로로 지정해야 한다."""
    spec = importlib.util.spec_from_file_location(
        "check_naver_format", _ROOT / "scripts" / "check_naver_format.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_checker = _load_checker()
find_violations = _checker.find_violations
PASTE, STYLE, READABILITY = _checker.PASTE, _checker.STYLE, _checker.READABILITY


@pytest.mark.parametrize(
    "guide", sorted(_GUIDE_DIR.glob("*.txt")), ids=lambda p: p.name
)
def test_strategy_guides_follow_the_convention(guide: Path) -> None:
    """전략 가이드 8편은 붙여넣기용 원고의 본보기다.

    마크다운이 섞이면 발행된 글이 깨지고, 이모지·em dash·상투구가 섞이면
    AI가 쓴 글로 읽힌다. 본보기가 규약을 어기면 규약이 무의미해지므로 둘 다 검사한다.
    """
    violations = find_violations(guide.read_text(encoding="utf-8"), (PASTE, STYLE))
    assert not violations, "\n".join(
        f"{guide.name}:{lineno} {name} - {line[:60]}"
        for lineno, _cat, name, line in violations
    )


def test_checker_catches_each_broken_paste_form() -> None:
    """검사기가 실제로 잡는지 - 통과만 확인하면 빈 검사기도 초록으로 보인다."""
    sample = "\n".join(
        [
            "## 소제목",
            "**굵게**",
            "`코드`",
            "| 종목 | 수량 |",
            "---",
            "> 인용",
            "[링크](http://example.com)",
        ]
    )
    names = {name for _no, _cat, name, _line in find_violations(sample, (PASTE,))}
    assert names == {
        "제목 기호(#)",
        "굵게(**)",
        "백틱(`)",
        "표(|)",
        "구분선(---)",
        "인용(>)",
        "링크([](...))",
    }


def test_checker_catches_ai_tells() -> None:
    """이모지·em dash·상투구는 붙여넣기는 되지만 AI 생성물로 읽힌다."""
    sample = "\n".join(
        [
            "\U0001F4CC 한 줄 요약",
            "✅ 체결 완료",
            "지정가 — 체결가",
            "결론적으로 좋은 하루였습니다.",
            "여러분도 참고하시기 바랍니다.",
        ]
    )
    names = {name for _no, _cat, name, _line in find_violations(sample, (STYLE,))}
    assert names == {"이모지", "em dash(—)", "상투구"}


def test_style_check_allows_structural_marks() -> None:
    """구분선·화살표·원문자·가운뎃점은 이모지가 아니다. 이것까지 막으면 구조를 못 만든다."""
    sample = "\n".join(
        [
            "제목: MAPS가 오늘 신규 매수를 중단한 이유",
            "─" * 33,
            "1. 오늘의 결론",
            "─" * 33,
            "  · 약세 판정으로 8개 전략 전부 비활성",
            "  ▸ 082640 · donchian_v1 · 636주 · 8,180원 체결",
            "  ▶ 매수 1건",
            "  ① 조건 하나",
            "  단기 이동평균     : 5일",
            "태그: AI주식투자 자동매매 퀀트투자",
        ]
    )
    assert find_violations(sample) == []


def test_readability_accepts_explained_term_and_ignores_detail_raw_values() -> None:
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "상승과 하락 신호가 섞인 시장(MIXED)이었습니다.",
        "6. 상세 기록",
        "적용 국면 : MIXED",
    ])
    assert find_violations(sample, (READABILITY,)) == []


def test_readability_rejects_unexplained_easy_body_term() -> None:
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "오늘은 MIXED가 적용됐습니다.",
        "6. 상세 기록",
    ])
    violations = find_violations(sample, (READABILITY,))
    assert [name for _line, _cat, name, _body in violations] == [
        "쉬운 설명 누락(MIXED)"
    ]


def test_readability_rejects_explanation_after_first_raw_term() -> None:
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "오늘은 MIXED가 적용됐습니다.",
        "상승과 하락 신호가 섞인 시장(MIXED)이라는 뜻입니다.",
        "6. 상세 기록",
    ])
    violations = find_violations(sample, (READABILITY,))
    assert [name for _line, _cat, name, _body in violations] == [
        "쉬운 설명 누락(MIXED)"
    ]


def test_readability_checks_each_beginner_term_family() -> None:
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "Breadth 42%, entry limit 0%, HIGH 변동성이었습니다.",
        "4. 실제 매수·매도 기록",
        "partially filled 뒤 stop loss가 실행됐습니다.",
        "6. 상세 기록",
    ])
    names = {
        name
        for _line, _cat, name, _body in find_violations(sample, (READABILITY,))
    }
    assert names == {
        "쉬운 설명 누락(Breadth)",
        "쉬운 설명 누락(entry limit)",
        "쉬운 설명 누락(HIGH 변동성)",
        "쉬운 설명 누락(partially filled)",
        "쉬운 설명 누락(stop loss)",
    }
