"""일일 블로그 조회 API — 생성기가 써 둔 Markdown 파일을 읽어 반환한다.

글은 여기서 만들지 않는다. `scripts/run_blog_cron.sh` 가 저녁에 생성해 둔
`{maps_blog_dir}/YYYY-MM-DD.md` 를 읽기만 한다.
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

_FILENAME_GLOB = "????-??-??.md"


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
    """검증된 날짜 문자열로만 경로를 조립한다 — 경로 순회(../) 차단."""
    try:
        ref_date = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {date}") from exc
    # fromisoformat 을 통과한 값만 isoformat 으로 되돌려 쓴다. 입력 문자열을
    # 그대로 파일명에 붙이지 않으므로 어떤 구분자도 경로를 벗어날 수 없다.
    return _blog_dir() / f"{ref_date.isoformat()}.md"


@router.get("", response_model=BlogListResponse)
def list_posts() -> BlogListResponse:
    """생성된 글 목록을 최신순으로 반환한다."""
    directory = _blog_dir()
    if not directory.is_dir():
        return BlogListResponse(entries=[], directory=str(directory))
    entries = [
        BlogEntry(date=path.stem, size=path.stat().st_size)
        for path in sorted(directory.glob(_FILENAME_GLOB), reverse=True)
    ]
    return BlogListResponse(entries=entries, directory=str(directory))


@router.get("/{date}", response_model=BlogPostResponse)
def get_post(date: str) -> BlogPostResponse:
    """해당 날짜 글 본문(Markdown 원문)을 반환한다."""
    path = _post_path(date)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no post for {date}")
    return BlogPostResponse(date=date, content=path.read_text(encoding="utf-8"))
