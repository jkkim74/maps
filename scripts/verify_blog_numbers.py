#!/usr/bin/env python3
"""생성된 블로그 글의 숫자가 다이제스트에서 왔는지 대조한다.

글쓰기 에이전트의 도구를 제한해 두긴 했지만, 그건 **차단 목록**이라 본질적으로
빈틈이 생길 수 있다(실제로 `--allowedTools`만으로는 Bash가 막히지 않았고,
`ToolSearch`로 지연 도구를 불러와 우회한 사례가 있었다). 예방에만 기대지 않고
결과물을 직접 검증한다.

다이제스트에 없는 숫자를 전부 "위반"으로 볼 수는 없다. 두 값의 차이·비율을
계산하는 것은 허용된 서술이기 때문이다(예: 체결가 79,500 − 60,800 = 18,700).
따라서 이 스크립트는 **판정하지 않고 보고**한다. 사람이 목록을 훑어 그것들이
다이제스트 값의 산술 결과인지 확인하면 된다.

원고는 네이버 붙여넣기용 평문(`.txt`)이지만 이 검사는 포맷과 무관하다 —
본문 전체에서 숫자 토큰만 뽑아 대조하므로 `.md` 원고에도 그대로 쓸 수 있다.

사용법:
    python scripts/verify_blog_numbers.py <digest.json> <post.txt>
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# 날짜·시각은 숫자 추출 전에 지운다 — "2026-07-27"이나 "2026년 7월 27일"이
# 2026/7/27로 쪼개져 잡히면 보고서가 노이즈로 덮인다.
_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:\d{2}|Z)?)?"
    r"|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
)
# 천단위 콤마와 소수점을 허용하는 숫자 토큰
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _renderings(value: Decimal) -> set[Decimal]:
    """같은 수를 여러 자리수로 반올림한 표현 — 글은 보통 반올림해서 쓴다."""
    out = {value}
    for places in ("1", "0.1", "0.01"):
        try:
            out.add(value.quantize(Decimal(places)))
        except InvalidOperation:
            pass
    # 비율(0.235)을 퍼센트(23.5)로 쓰는 경우
    out.add(value * 100)
    try:
        out.add((value * 100).quantize(Decimal("0.1")))
    except InvalidOperation:
        pass
    return out


def _collect(node: object, acc: set[Decimal]) -> None:
    """다이제스트 전체를 훑어 숫자값을 모은다(숫자 문자열 포함 — 티커 등)."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        acc |= _renderings(Decimal(str(node)))
    elif isinstance(node, str):
        for token in _NUM.findall(_ISO.sub(" ", node)):
            dec = _to_decimal(token)
            if dec is not None:
                acc |= _renderings(dec)
    elif isinstance(node, dict):
        for value in node.values():
            _collect(value, acc)
    elif isinstance(node, list):
        for item in node:
            _collect(item, acc)


def main(argv: list[str]) -> int:
    # 원고에는 구분선(─)·이모지가 들어간다. 윈도우 콘솔 기본 코덱(cp949)은 이를 못 찍고
    # UnicodeEncodeError 로 죽는다 — 검증기가 검증 대상 때문에 실패하면 안 된다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(argv) != 3:
        print(__doc__)
        return 2
    digest_path, post_path = Path(argv[1]), Path(argv[2])
    digest = json.loads(digest_path.read_text(encoding="utf-8"))

    known: set[Decimal] = set()
    _collect(digest, known)

    body = _ISO.sub(" ", post_path.read_text(encoding="utf-8"))
    unmatched: list[str] = []
    for token in _NUM.findall(body):
        dec = _to_decimal(token)
        if dec is None:
            continue
        if not (_renderings(dec) & known):
            unmatched.append(token)

    if not unmatched:
        print("[검증] 글의 모든 숫자가 다이제스트에서 확인됨")
        return 0

    # 중복 제거하되 등장 순서는 유지 — 사람이 글에서 찾아보기 쉽게
    seen: set[str] = set()
    ordered = [t for t in unmatched if not (t in seen or seen.add(t))]
    print(f"[검증] 다이제스트에서 못 찾은 숫자 {len(ordered)}개 — 파생값(차이·비율)인지 확인 필요:")
    for token in ordered:
        print(f"    {token}")
    return 0   # 파생값이 정상이므로 실패로 처리하지 않는다


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
