"""운영 DB의 최신 전략 승격 단계를 strategy-selector 입력 JSON으로 출력한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# `python scripts/export_strategy_stages.py` 직접 실행 시에도 최상위 maps 패키지를 찾는다.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.common.db import SessionLocal
from maps.promotion.stage_snapshot import build_strategy_stage_context


def main() -> int:
    """최신 성공 승격 이력을 stdout에 JSON 한 줄로 출력한다."""
    db = SessionLocal()
    try:
        payload = build_strategy_stage_context(db)
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
