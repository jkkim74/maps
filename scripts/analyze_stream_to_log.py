#!/usr/bin/env python3
"""analyze_stream_to_log.py — claude `-p --output-format stream-json` 출력 → 사람이 읽는 진행로그.

run_analyze_cron.sh가 headless로 `/analyze`를 돌릴 때, 기본 text 출력은 **마지막 결과**만
남아 중간 단계(시장국면→전략→섹터→스크리닝→안전마진→기술→트레이드플래너)가 로그에 안 보인다.
이 필터는 stream-json 이벤트를 한 줄씩 받아 각 단계(에이전트 호출·도구 실행·텍스트·완료)를
타임스탬프와 함께 로그파일에 append하고, 동시에 stdout으로도 흘려보낸다.

사용:
    claude -p "/analyze" --verbose --output-format stream-json | \
        python scripts/analyze_stream_to_log.py /path/to/progress.log
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any


def _ts() -> str:
    """현재 시각을 'YYYY-MM-DD HH:MM:SS TZ' 형식으로 반환한다."""
    return datetime.now().astimezone().strftime("%F %T %Z")


def _clip(text: str, limit: int = 200) -> str:
    """여러 줄 텍스트를 한 줄로 합치고 limit자로 자른다."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _describe_tool(name: str, tool_input: dict[str, Any]) -> str:
    """tool_use 블록을 사람이 읽을 수 있는 한 줄로 요약한다."""
    if name == "Task":
        agent = tool_input.get("subagent_type", "?")
        desc = tool_input.get("description") or _clip(str(tool_input.get("prompt", "")), 80)
        return f"▶ 에이전트 호출: {agent} — {desc}"
    if name == "Bash":
        cmd = _clip(str(tool_input.get("command", "")), 160)
        return f"▶ Bash: {cmd}"
    if name in ("Write", "Edit", "Read"):
        return f"▶ {name}: {tool_input.get('file_path', '?')}"
    if name in ("Glob", "Grep"):
        return f"▶ {name}: {tool_input.get('pattern', '?')}"
    return f"▶ {name}"


def _emit(out: Any, line: str) -> None:
    """진행로그 한 줄을 로그파일과 stdout에 동시에 쓴다."""
    rendered = f"[{_ts()}] {line}"
    out.write(rendered + "\n")
    out.flush()
    print(rendered, flush=True)


def _handle(event: dict[str, Any], out: Any) -> None:
    """stream-json 이벤트 1건을 해석해 진행로그로 변환한다."""
    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model", "?")
        _emit(out, f"세션 시작 (model={model})")
        return

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "").strip()
                if text:
                    _emit(out, f"💬 {_clip(text)}")
            elif btype == "tool_use":
                _emit(out, _describe_tool(block.get("name", "?"), block.get("input", {}) or {}))
        return

    if etype == "result":
        if event.get("is_error"):
            _emit(out, f"❌ 종료(에러): {_clip(str(event.get('result', '')))}")
        else:
            _emit(out, f"✅ 완료: {_clip(str(event.get('result', '')))}")
        dur = event.get("duration_ms")
        cost = event.get("total_cost_usd")
        if dur is not None or cost is not None:
            _emit(out, f"   소요 {dur}ms · 비용 ${cost}")
        return


def main() -> int:
    """stdin의 stream-json을 한 줄씩 읽어 진행로그로 변환한다."""
    log_path = sys.argv[1] if len(sys.argv) > 1 else None
    out = open(log_path, "a", encoding="utf-8") if log_path else sys.stdout

    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                # JSON이 아닌 라인은 원문 그대로 보존한다.
                _emit(out, raw)
                continue
            _handle(event, out)
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
