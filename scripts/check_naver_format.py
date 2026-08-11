#!/usr/bin/env python3
"""원고가 네이버에 붙여넣을 수 있고 사람이 쓴 글로 읽히는지 검사한다.

검사는 두 갈래다.

paste — 붙여넣으면 깨지는 것. 스마트에디터는 마크다운을 렌더링하지 않아
        `##`, `**`, 백틱, `|표|` 가 남아 있으면 기호 그대로 독자에게 보인다.
style — AI가 쓴 티가 나는 표기. 이모지, em dash(—), 블로그 상투구.
        깨지지는 않지만 글의 신뢰를 깎는다. 투자 글에서는 이쪽이 더 치명적이다.
readability — SCR-20 쉬운 본문에서 설명 없이 먼저 등장한 핵심 전문용어.

규약 전문은 `docs/blog_style_naver.md`. 이 파일이 규약의 **유일한 구현**이다 —
저녁 배치(`run_blog_cron.sh`)와 테스트가 같은 함수를 쓴다. 셸과 파이썬에 패턴을
따로 두면 한쪽만 고쳐지고 조용히 어긋난다.

사용법:
    python scripts/check_naver_format.py <원고.txt> [원고2.txt ...]
    python scripts/check_naver_format.py --paste-only <원고.txt>

종료 코드는 항상 0이다. 원고 생성은 사람이 검토해 발행하므로, 배치를 실패시키기보다
위반 목록을 남겨 붙여넣기 전에 고치게 한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PASTE = "paste"
STYLE = "style"
READABILITY = "readability"

_DETAIL_HEADING = re.compile(r"(?m)^\s*6\.\s*상세 기록\s*$")
_READABILITY_TERMS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "STRONG",
        re.compile(r"(?<![A-Za-z0-9_])STRONG(?![A-Za-z0-9_])"),
        "상승 흐름이 뚜렷한 시장(STRONG)",
    ),
    (
        "MIXED",
        re.compile(r"(?<![A-Za-z0-9_])MIXED(?![A-Za-z0-9_])"),
        "상승과 하락 신호가 섞인 시장(MIXED)",
    ),
    (
        "WEAK",
        re.compile(r"(?<![A-Za-z0-9_])WEAK(?![A-Za-z0-9_])"),
        "하락 압력이 큰 시장(WEAK)",
    ),
    (
        "LOW 변동성",
        re.compile(r"(?<![A-Za-z0-9_])LOW\s+변동성"),
        "가격 움직임이 평소보다 작은 상태(LOW 변동성)",
    ),
    (
        "NORMAL 변동성",
        re.compile(r"(?<![A-Za-z0-9_])NORMAL\s+변동성"),
        "가격 움직임이 평소 수준인 상태(NORMAL 변동성)",
    ),
    (
        "HIGH 변동성",
        re.compile(r"(?<![A-Za-z0-9_])HIGH\s+변동성"),
        "가격 움직임이 평소보다 큰 상태(HIGH 변동성)",
    ),
    (
        "Breadth",
        re.compile(r"(?<![A-Za-z0-9_])Breadth(?![A-Za-z0-9_])"),
        "오르는 종목의 비율(Breadth)",
    ),
    (
        "entry limit",
        re.compile(r"(?<![A-Za-z0-9_])entry\s+limit(?![A-Za-z0-9_])"),
        "오늘 허용된 최대 신규매수 비율(entry limit)",
    ),
    (
        "partially filled",
        re.compile(r"(?<![A-Za-z0-9_])partially\s+filled(?![A-Za-z0-9_])"),
        "주문 수량 중 일부만 거래된 상태(partially filled)",
    ),
    (
        "filled",
        re.compile(r"(?<!partially )(?<![A-Za-z0-9_])filled(?![A-Za-z0-9_])"),
        "주문 수량이 모두 거래된 상태(filled)",
    ),
    (
        "stop loss",
        re.compile(r"(?<![A-Za-z0-9_])stop\s+loss(?![A-Za-z0-9_])"),
        "손실 제한 가격에 도달한 매도(stop loss)",
    ),
    (
        "take profit",
        re.compile(r"(?<![A-Za-z0-9_])take\s+profit(?![A-Za-z0-9_])"),
        "목표 가격에 도달한 매도(take profit)",
    ),
)

# 이모지 — 붙여넣기는 되지만 글이 AI 생성물로 읽힌다.
# 구분선(─ U+2500)·화살표(▶ U+25B6)·원문자(① U+2460)는 기하 도형이라 걸리지 않는다.
_EMOJI = (
    "[\U0001F300-\U0001FAFF"   # 그림문자·이모티콘·기호 (📌 💡 🟢 🛑 …)
    "\U0001F1E6-\U0001F1FF"    # 국기
    "☀-➿"            # 기타 기호·딩벳 (⚠ ✓ ★ ⚖ …)
    "⬀-⯿"            # 화살표·별
    "️]"                  # 이모지 변형 선택자
)

# 블로그 상투구. 흔하지만 정보가 0인 표현만 고른다 — 정상적인 한국어를 막으면
# 글쓴이가 규칙을 무시하게 되므로 목록을 짧게 유지한다.
_CLICHES = (
    "결론적으로",
    "종합적으로",
    "라고 할 수 있습니다",
    "주목할 필요",
    "알아보겠습니다",
    "살펴보겠습니다",
    "하시기 바랍니다",
    "여러분",
)

# (갈래, 이름, 패턴) — 줄 단위로 검사한다.
_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (PASTE, "제목 기호(#)", re.compile(r"^#{1,6}\s")),
    (PASTE, "굵게(**)", re.compile(r"\*\*")),
    (PASTE, "백틱(`)", re.compile(r"`")),
    (PASTE, "표(|)", re.compile(r"^\s*\|")),
    (PASTE, "구분선(---)", re.compile(r"^-{3,}\s*$")),
    (PASTE, "인용(>)", re.compile(r"^\s*>\s")),
    (PASTE, "링크([](...))", re.compile(r"\]\(")),
    (STYLE, "이모지", re.compile(_EMOJI)),
    (STYLE, "em dash(—)", re.compile("—")),
    (STYLE, "상투구", re.compile("|".join(re.escape(c) for c in _CLICHES))),
)

Violation = tuple[int, str, str, str]


def _find_readability_violations(text: str) -> list[Violation]:
    """상세 감사 영역 전의 전문용어가 쉬운 설명으로 먼저 소개됐는지 검사한다."""
    detail = _DETAIL_HEADING.search(text)
    easy_body = text[:detail.start()] if detail else text
    lines = easy_body.splitlines()
    out: list[Violation] = []
    for label, raw_pattern, approved in _READABILITY_TERMS:
        raw = raw_pattern.search(easy_body)
        approved_at = easy_body.find(approved)
        if raw is None or (approved_at >= 0 and approved_at <= raw.start()):
            continue
        lineno = easy_body.count("\n", 0, raw.start()) + 1
        out.append(
            (lineno, READABILITY, f"쉬운 설명 누락({label})", lines[lineno - 1].strip())
        )
    return out


def find_violations(
    text: str,
    categories: tuple[str, ...] = (PASTE, STYLE, READABILITY),
) -> list[Violation]:
    """(줄번호, 갈래, 규칙명, 줄내용) 목록. 규약을 지키면 빈 리스트."""
    out: list[Violation] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for category, name, pattern in _RULES:
            if category in categories and pattern.search(line):
                out.append((lineno, category, name, line.strip()))
    if READABILITY in categories:
        out.extend(_find_readability_violations(text))
    return out


def main(argv: list[str]) -> int:
    # 원고에는 구분선(─)·이모지가 들어간다. 윈도우 콘솔 기본 코덱(cp949)은 이를 못 찍고
    # UnicodeEncodeError 로 죽는다 — 검사기가 검사 대상 때문에 실패하면 안 된다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = [a for a in argv[1:] if not a.startswith("--")]
    categories = (
        (PASTE,)
        if "--paste-only" in argv
        else (PASTE, STYLE, READABILITY)
    )
    if not args:
        print(__doc__)
        return 2

    for arg in args:
        path = Path(arg)
        if not path.is_file():
            print(f"[포맷] 파일 없음: {path}")
            continue
        violations = find_violations(path.read_text(encoding="utf-8"), categories)
        paste_hits = [v for v in violations if v[1] == PASTE]
        style_hits = [v for v in violations if v[1] == STYLE]
        readability_hits = [v for v in violations if v[1] == READABILITY]
        if not violations:
            print(f"[포맷] {path.name}: 붙여넣기 안전, AI 표기 없음, 초보자 설명 확인")
            continue
        print(
            f"[포맷] {path.name}: 붙여넣기 위반 {len(paste_hits)}건, "
            f"문체 위반 {len(style_hits)}건, 가독성 위반 {len(readability_hits)}건 "
            "— 발행 전 확인 필요"
        )
        for lineno, _category, name, line in violations[:20]:
            print(f"    {lineno}행 {name}: {line[:60]}")
        if len(violations) > 20:
            print(f"    ... 외 {len(violations) - 20}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
