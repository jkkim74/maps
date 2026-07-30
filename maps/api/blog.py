"""일일 블로그 조회 API — 생성기가 써 둔 원고 파일을 읽어 반환한다.

글은 여기서 만들지 않는다. `scripts/run_blog_cron.sh` 가 저녁에 생성해 둔
`{maps_blog_dir}/YYYY-MM-DD.txt` 를 읽기만 한다.

원고는 **네이버 스마트에디터에 그대로 붙여넣는 평문**이다(`docs/blog_style_naver.md`).
마크다운이 아니므로 화면에서도 렌더링하지 말고 원문 그대로 보여줘야 한다 —
화면에서 가공하면 복사한 내용과 발행될 내용이 달라진다.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from maps.common.settings import get_settings

router = APIRouter(prefix="/api/v1/blog", tags=["Daily Blog"])
logger = logging.getLogger(__name__)

# 신규는 `.txt`(네이버 평문), 구 원고는 `.md`. 앞쪽이 정본이다.
_SUFFIXES = (".txt", ".md")
_FILENAME_GLOBS = tuple(f"????-??-??{suffix}" for suffix in _SUFFIXES)


class BlogEntry(BaseModel):
    date: str
    size: int


class BlogListResponse(BaseModel):
    entries: list[BlogEntry]
    directory: str


class BlogPostResponse(BaseModel):
    date: str
    content: str


def _blog_dir() -> Path:
    return Path(get_settings().maps_blog_dir)


def _post_path(date: str) -> Path:
    """검증된 날짜 문자열로만 경로를 조립한다 — 경로 순회(../) 차단.

    신규 원고는 `.txt`(네이버 붙여넣기용 평문), 2026-07 이전 원고는 `.md` 다.
    둘 다 읽되 없으면 `.txt` 경로를 돌려준다(404 메시지가 신규 규약을 가리키도록).
    """
    try:
        ref_date = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {date}") from exc
    # fromisoformat 을 통과한 값만 isoformat 으로 되돌려 쓴다. 입력 문자열을
    # 그대로 파일명에 붙이지 않으므로 어떤 구분자도 경로를 벗어날 수 없다.
    stem = ref_date.isoformat()
    directory = _blog_dir()
    for suffix in _SUFFIXES:
        candidate = directory / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return directory / f"{stem}{_SUFFIXES[0]}"


@router.get("", response_model=BlogListResponse)
def list_posts() -> BlogListResponse:
    """생성된 글 목록을 최신순으로 반환한다."""
    directory = _blog_dir()
    if not directory.is_dir():
        return BlogListResponse(entries=[], directory=str(directory))
    # 같은 날짜에 `.txt` 와 `.md` 가 함께 있으면 앞 확장자(.txt)만 노출한다.
    by_date: dict[str, BlogEntry] = {}
    for glob in _FILENAME_GLOBS:
        for path in directory.glob(glob):
            by_date.setdefault(
                path.stem, BlogEntry(date=path.stem, size=path.stat().st_size)
            )
    entries = [by_date[key] for key in sorted(by_date, reverse=True)]
    return BlogListResponse(entries=entries, directory=str(directory))


@router.get("/{date}", response_model=BlogPostResponse)
def get_post(date: str) -> BlogPostResponse:
    """해당 날짜 글 본문을 원문 그대로 반환한다."""
    path = _post_path(date)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no post for {date}")
    return BlogPostResponse(date=date, content=path.read_text(encoding="utf-8"))
