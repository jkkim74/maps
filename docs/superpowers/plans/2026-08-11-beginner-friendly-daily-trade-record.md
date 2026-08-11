# Beginner-Friendly Daily Trade Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the generated SCR-20 daily trade record so beginners read a plain-Korean summary first and retain the original technical values in a final audit section.

**Architecture:** Keep `DailyDigest`, the blog API, the SCR-20 template, and the cron pipeline unchanged. Change only the writing contract and style guide, then extend the existing warning-only Naver checker with a `readability` category that scans the easy-body portion before `6. 상세 기록`.

**Tech Stack:** Markdown prompt files, Python 3.12 standard library (`re`, `pathlib`), pytest, existing shell cron pipeline

## Global Constraints

- The original must use seven sections in this order: `오늘의 매매 한눈에`, `오늘 시장은 어땠나요?`, `시스템은 왜 이렇게 움직였나요?`, `실제 매수·매도 기록`, `내일 예정된 행동`, `상세 기록`, `투자 유의사항`.
- A core technical term must first appear as `쉬운 설명(원래 용어)`, for example `상승과 하락 신호가 섞인 시장(MIXED)`.
- The easy body comes before `6. 상세 기록`; raw values and identifiers remain available in the detail section.
- The digest JSON remains the sole fact source. Do not invent facts or numbers, interpret `measured: false`, fill `null`, or hide section errors.
- Keep the existing no-recommendation wording, KIS mock-account disclaimer, Naver plain-text rules, numeric verification, and warning-only validator exit behavior.
- Do not change the database, `DailyDigest` schema, API, SCR-20 layout, cron schedule, or dependencies.

---

### Task 1: Beginner-Oriented Writing Contract

**Files:**
- Create: `tests/test_beginner_blog_prompt.py`
- Modify: `.claude/commands/blog.md:5-148`
- Modify: `docs/blog_style_naver.md:87-157`

**Interfaces:**
- Consumes: Existing `DailyDigest` JSON fields documented in `.claude/commands/blog.md`.
- Produces: A deterministic prompt contract with `BEGINNER_SECTIONS: tuple[str, ...]` and `BEGINNER_TERMS: tuple[str, ...]` mirrored in test constants; no runtime Python interface changes.

- [ ] **Step 1: Write the failing prompt-contract tests**

Create `tests/test_beginner_blog_prompt.py` with exact structural and safety assertions:

```python
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


def test_beginner_sections_are_present_in_order():
    positions = [PROMPT.index(section) for section in BEGINNER_SECTIONS]
    assert positions == sorted(positions)


def test_beginner_terms_are_shared_by_prompt_and_style_guide():
    for phrase in BEGINNER_TERMS:
        assert phrase in PROMPT
        assert phrase in STYLE


def test_prompt_preserves_fact_and_failure_boundaries():
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
```

- [ ] **Step 2: Run the tests and verify the old prompt fails**

Run:

```powershell
python -m pytest tests/test_beginner_blog_prompt.py -q
```

Expected: failures for the seven new section headings and beginner-term phrases.

- [ ] **Step 3: Replace the prompt's eight-section outline with the approved seven-section contract**

In `.claude/commands/blog.md`, retain the input, absolute fact rules, Naver formatting rules, year rule, and output path. Replace `## 구성` with these exact responsibilities:

```text
1. 오늘의 매매 한눈에
  세 문장 이내로 체결 건수, 신규매수 중단 여부, 확인 필요 데이터를 요약한다.

2. 오늘 시장은 어땠나요?
  regime, raw_regime, weekly_trend, vol_regime, breadth_pct,
  entry_limit_ratio와 실제 한도 보정을 쉬운 문장으로 연결한다.

3. 시스템은 왜 이렇게 움직였나요?
  실제 행동에 영향을 준 활성 전략과 차단 이유만 우선 설명한다.

4. 실제 매수·매도 기록
  무엇을 했나, 왜 했나, 얼마나 체결됐나, 무엇을 확인해야 하나 순서로 쓴다.

5. 내일 예정된 행동
  매수 예정, 조건 대기, 주문 제외를 행동 중심으로 설명한다.

6. 상세 기록
  기존 시장·업종·전략·후보·주문 원시 값과 식별자를 보존한다.

7. 투자 유의사항
  기존 KIS 모의계좌·비추천·투자자 책임 문구를 그대로 쓴다.
```

Add an exact beginner glossary containing every string in `BEGINNER_TERMS`. State that the first easy-body occurrence uses the full Korean-plus-parenthetical phrase, repeated mentions may use Korean only, and an unknown strategy ID must not be guessed.

- [ ] **Step 4: Extend the style guide with the same readability contract**

Add `## 4. 초보자용 매매 기록 문체` before the current file-structure section and renumber later headings. Include:

```text
전문용어는 쉬운 뜻을 먼저 쓴다.
좋음: 상승과 하락 신호가 섞인 시장(MIXED)
나쁨: 오늘은 MIXED 국면이다.

숫자는 오늘 행동에 준 의미를 한 번만 설명한다.
손익만으로 좋은 거래·나쁜 거래를 판정하지 않는다.
미측정·누락·수집 실패는 추정으로 메우지 않는다.
```

Copy the exact `BEGINNER_TERMS` glossary into this section. Document that `6. 상세 기록` is the raw audit area and is not subject to the easy-body terminology check.

- [ ] **Step 5: Run the prompt and existing style tests**

Run:

```powershell
python -m pytest tests/test_beginner_blog_prompt.py tests/test_naver_blog_format.py -q
```

Expected: all tests pass; existing strategy guides remain valid.

- [ ] **Step 6: Commit the writing contract**

```powershell
git add .claude/commands/blog.md docs/blog_style_naver.md tests/test_beginner_blog_prompt.py
git commit -m "feat: simplify daily trade record language"
```

---

### Task 2: Warning-Only Readability Validation

**Files:**
- Modify: `scripts/check_naver_format.py:24-118`
- Modify: `tests/test_naver_blog_format.py:19-103`

**Interfaces:**
- Consumes: Plain-text post body with the exact heading `6. 상세 기록`.
- Produces: `READABILITY = "readability"`; `find_violations(text: str, categories: tuple[str, ...] = (PASTE, STYLE, READABILITY)) -> list[Violation]`.
- Preserves: `main(argv: list[str]) -> int` always returns `0` after checking existing files; `--paste-only` checks only `PASTE`.

- [ ] **Step 1: Write failing readability tests**

Extend the checker imports:

```python
PASTE, STYLE, READABILITY = _checker.PASTE, _checker.STYLE, _checker.READABILITY
```

Add focused tests:

```python
def test_readability_accepts_explained_term_and_ignores_detail_raw_values():
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "상승과 하락 신호가 섞인 시장(MIXED)이었습니다.",
        "6. 상세 기록",
        "적용 국면 : MIXED",
    ])
    assert find_violations(sample, (READABILITY,)) == []


def test_readability_rejects_unexplained_easy_body_term():
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "오늘은 MIXED 국면이었습니다.",
        "6. 상세 기록",
    ])
    violations = find_violations(sample, (READABILITY,))
    assert [name for _line, _cat, name, _body in violations] == [
        "쉬운 설명 누락(MIXED)"
    ]


def test_readability_checks_each_beginner_term_family():
    sample = "\n".join([
        "2. 오늘 시장은 어땠나요?",
        "Breadth 42%, entry limit 0%, HIGH 변동성이었습니다.",
        "4. 실제 매수·매도 기록",
        "partially filled 뒤 stop loss가 실행됐습니다.",
        "6. 상세 기록",
    ])
    names = {name for _line, _cat, name, _body in find_violations(sample, (READABILITY,))}
    assert names == {
        "쉬운 설명 누락(Breadth)",
        "쉬운 설명 누락(entry limit)",
        "쉬운 설명 누락(HIGH 변동성)",
        "쉬운 설명 누락(partially filled)",
        "쉬운 설명 누락(stop loss)",
    }
```

- [ ] **Step 2: Run the focused tests and verify `READABILITY` is missing**

Run:

```powershell
python -m pytest tests/test_naver_blog_format.py -q
```

Expected: collection error or failure because `READABILITY` and readability rules do not exist.

- [ ] **Step 3: Add the readability category and curated rule table**

In `scripts/check_naver_format.py`, add:

```python
READABILITY = "readability"
_DETAIL_HEADING = re.compile(r"(?m)^\s*6\.\s*상세 기록\s*$")
_READABILITY_TERMS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("STRONG", re.compile(r"\bSTRONG\b"), "상승 흐름이 뚜렷한 시장(STRONG)"),
    ("MIXED", re.compile(r"\bMIXED\b"), "상승과 하락 신호가 섞인 시장(MIXED)"),
    ("WEAK", re.compile(r"\bWEAK\b"), "하락 압력이 큰 시장(WEAK)"),
    ("LOW 변동성", re.compile(r"\bLOW\s+변동성\b"), "가격 움직임이 평소보다 작은 상태(LOW 변동성)"),
    ("NORMAL 변동성", re.compile(r"\bNORMAL\s+변동성\b"), "가격 움직임이 평소 수준인 상태(NORMAL 변동성)"),
    ("HIGH 변동성", re.compile(r"\bHIGH\s+변동성\b"), "가격 움직임이 평소보다 큰 상태(HIGH 변동성)"),
    ("Breadth", re.compile(r"\bBreadth\b"), "오르는 종목의 비율(Breadth)"),
    ("entry limit", re.compile(r"\bentry\s+limit\b"), "오늘 허용된 최대 신규매수 비율(entry limit)"),
    ("partially filled", re.compile(r"\bpartially\s+filled\b"), "주문 수량 중 일부만 거래된 상태(partially filled)"),
    ("filled", re.compile(r"(?<!partially )\bfilled\b"), "주문 수량이 모두 거래된 상태(filled)"),
    ("stop loss", re.compile(r"\bstop\s+loss\b"), "손실 제한 가격에 도달한 매도(stop loss)"),
    ("take profit", re.compile(r"\btake\s+profit\b"), "목표 가격에 도달한 매도(take profit)"),
)
```

- [ ] **Step 4: Implement easy-body-only violations**

Add a focused helper and call it from `find_violations` only when `READABILITY` is requested:

```python
def _find_readability_violations(text: str) -> list[Violation]:
    detail = _DETAIL_HEADING.search(text)
    easy_body = text[:detail.start()] if detail else text
    out: list[Violation] = []
    for label, raw_pattern, approved in _READABILITY_TERMS:
        raw = raw_pattern.search(easy_body)
        approved_at = easy_body.find(approved)
        if raw is None or (approved_at >= 0 and approved_at <= raw.start()):
            continue
        lineno = easy_body.count("\n", 0, raw.start()) + 1
        line = easy_body.splitlines()[lineno - 1].strip()
        out.append((lineno, READABILITY, f"쉬운 설명 누락({label})", line))
    return out
```

Change the default categories to `(PASTE, STYLE, READABILITY)` and append the helper result after existing line rules. Keep explicit `(PASTE, STYLE)` guide tests unchanged.

- [ ] **Step 5: Include readability in CLI reporting without changing exit behavior**

Count `readability_hits` beside `paste_hits` and `style_hits`, then print:

```python
f"가독성 위반 {len(readability_hits)}건 — 발행 전 확인 필요"
```

When no violations exist, report that the post is paste-safe, has no AI-style markers, and has beginner explanations. Keep `return 0` for checked files and `return 2` only for missing arguments.

- [ ] **Step 6: Run focused and script-level checks**

Run:

```powershell
python -m pytest tests/test_naver_blog_format.py tests/test_beginner_blog_prompt.py -q
python scripts/check_naver_format.py docs/strategy_guides/01_pullback_v3.txt
```

Expected: pytest passes; the CLI exits `0`. The strategy guide may be checked for readability by the CLI, but its repository contract test remains limited to `PASTE` and `STYLE`.

- [ ] **Step 7: Commit the checker**

```powershell
git add scripts/check_naver_format.py tests/test_naver_blog_format.py
git commit -m "feat: check beginner explanations in trade records"
```

---

### Task 3: Handoff and Full Regression Verification

**Files:**
- Modify: `HANDOFF.md:1-90`

**Interfaces:**
- Consumes: Task 1 writing contract and Task 2 `READABILITY` checker.
- Produces: Current-session handoff with the feature scope, exact verification results, and the unchanged deployment state.

- [ ] **Step 1: Update the handoff with the completed behavior**

Add a dated SCR-20 subsection recording:

```text
- 원고 앞부분을 초보자용 5개 섹션, 뒷부분을 상세 기록·투자 유의사항으로 재구성
- 핵심 용어는 쉬운 설명(원래 용어) 형식으로 첫 노출
- DailyDigest·DB·API·화면·cron 일정은 변경하지 않음
- 기존 숫자 검증과 Naver 평문 검사를 유지하고 warning-only readability 검사를 추가
- 운영 서버와 원격 브랜치에는 아직 배포하지 않음
```

- [ ] **Step 2: Run the complete blog and digest test surface**

Run:

```powershell
python -m pytest tests/test_beginner_blog_prompt.py tests/test_naver_blog_format.py tests/test_daily_digest.py tests/test_blog_api.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run the complete Python regression suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass; document the exact pass and warning counts in `HANDOFF.md` after this run.

- [ ] **Step 4: Run static and repository checks**

Run:

```powershell
python scripts/check_naver_format.py docs/strategy_guides/01_pullback_v3.txt
git diff --check
git status --short
```

Expected: checker exits `0`, `git diff --check` emits no errors, and status lists only the planned handoff change before commit.

- [ ] **Step 5: Commit the handoff**

```powershell
git add HANDOFF.md
git commit -m "docs: hand off beginner-friendly trade records"
```

- [ ] **Step 6: Confirm the final branch state**

Run:

```powershell
git status --short
git log --oneline -5
```

Expected: clean worktree with the design, plan, writing-contract, checker, and handoff commits at the branch tip. Do not push, merge, or deploy without a separate user instruction.
