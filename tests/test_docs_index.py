"""문서 지도가 코드와 어긋나지 않는지 검사하는 계약 테스트.

낡은 지도는 없는 지도보다 나쁘다 — 틀린 경로를 확신하고 답하게 만든다.
그래서 각 패키지 `CLAUDE.md` 의 ``## Directory structure`` 트리는 실제 `.py`
목록과 **정확히 일치**해야 하고, 루트 `index.md` 는 모든 패키지 문서를 가리켜야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPS_ROOT = REPO_ROOT / "maps"
SKIP_DIRS = {"__pycache__", "tests", "templates", "logs"}
# 파이썬 패키지가 아니어서 파일 단위 대신 문서 존재만 검사하는 디렉터리
EXTRA_DOC_DIRS = ("tests", "scripts", "alembic", "apps/mobile")


def python_packages() -> list[Path]:
    """`.py` 를 1개 이상 가진 `maps/` 하위 디렉터리 목록."""
    return sorted(
        path
        for path in MAPS_ROOT.iterdir()
        if path.is_dir() and path.name not in SKIP_DIRS and any(path.glob("*.py"))
    )


def documented_modules(doc: Path) -> set[str]:
    """`## Directory structure` 다음 첫 코드블록에 적힌 `.py` 파일명."""
    text = doc.read_text(encoding="utf-8")
    match = re.search(r"## Directory structure\s*\n+```[^\n]*\n(.*?)```", text, re.S)
    assert match, f"{doc} 에 '## Directory structure' 코드블록이 없다"
    return set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*\.py)", match.group(1)))


def actual_modules(package: Path) -> set[str]:
    """패키지의 실제 `.py` 파일명 (`__init__.py` 포함)."""
    return {path.name for path in package.glob("*.py")}


@pytest.mark.parametrize("package", python_packages(), ids=lambda path: path.name)
def test_package_tree_matches_files(package: Path) -> None:
    """패키지 문서의 트리가 실제 파일 목록과 정확히 일치한다."""
    doc = package / "CLAUDE.md"
    assert doc.exists(), f"maps/{package.name}/CLAUDE.md 가 없다 — 패키지 문서를 먼저 쓴다"

    documented = documented_modules(doc)
    actual = actual_modules(package)
    missing = sorted(actual - documented)
    ghost = sorted(documented - actual)
    assert not missing, f"maps/{package.name}/CLAUDE.md 트리에 빠진 모듈: {missing}"
    assert not ghost, f"maps/{package.name}/CLAUDE.md 트리의 유령 모듈(파일 없음): {ghost}"


@pytest.mark.parametrize("relative", EXTRA_DOC_DIRS)
def test_extra_directories_have_docs(relative: str) -> None:
    """코드 밖 디렉터리에도 진입용 문서가 있다."""
    doc = REPO_ROOT / relative / "CLAUDE.md"
    assert doc.exists(), f"{relative}/CLAUDE.md 가 없다"


def test_index_links_every_package_doc() -> None:
    """루트 index.md 가 모든 패키지·부속 문서 경로를 담고 있다."""
    index = REPO_ROOT / "index.md"
    assert index.exists(), "루트 index.md 가 없다 — 코드 탐색의 진입점이다"

    text = index.read_text(encoding="utf-8")
    expected = [f"maps/{package.name}/CLAUDE.md" for package in python_packages()]
    expected += [f"{relative}/CLAUDE.md" for relative in EXTRA_DOC_DIRS]
    unlinked = [path for path in expected if path not in text]
    assert not unlinked, f"index.md 에 없는 문서: {unlinked}"
