"""Claude stream 비밀 마스킹 테스트."""

from __future__ import annotations

import json

from scripts.redact_stream_secrets import (
    load_replacements,
    redact_line,
    redact_text,
)


def test_redacts_full_db_url_and_password_from_json(tmp_path) -> None:
    """DB URL 전체와 명령에 분리된 비밀번호를 모두 제거한다."""
    password = "correct-horse-battery-staple"
    db_url = f"postgresql://maps:{password}@db.internal/maps"
    env_file = tmp_path / ".env"
    env_file.write_text(f"MAPS_DB_URL={db_url}\n", encoding="utf-8")
    replacements = load_replacements([env_file], environ={})
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "input": {
                        "command": f"psql '{db_url}' --password {password}",
                    },
                }
            ]
        },
    }

    rendered = redact_line(json.dumps(event), replacements)

    assert db_url not in rendered
    assert password not in rendered
    assert "[REDACTED:MAPS_DB_URL]" in rendered
    assert "[REDACTED:DB_PASSWORD]" in rendered
    assert json.loads(rendered)["type"] == "assistant"


def test_redacts_process_environment_secrets_and_preserves_public_values() -> None:
    """실행 환경의 키는 숨기고 짧거나 공개 설정값은 유지한다."""
    replacements = load_replacements(
        [],
        environ={
            "ANTHROPIC_API_KEY": "sk-ant-sensitive-value",
            "MAPS_AUTH_PASSWORD": "long-password",
            "MAPS_ENV": "production",
            "TOKEN": "short",
        },
    )
    source = "key=sk-ant-sensitive-value password=long-password production short"

    rendered = redact_text(source, replacements)

    assert "sk-ant-sensitive-value" not in rendered
    assert "long-password" not in rendered
    assert "production short" in rendered


def test_plain_text_fallback_is_redacted(tmp_path) -> None:
    """JSON이 아닌 stderr·진행 텍스트도 동일하게 마스킹한다."""
    env_file = tmp_path / "secrets.env"
    env_file.write_text("KIS_APP_SECRET=super-secret-value\n", encoding="utf-8")
    replacements = load_replacements([env_file], environ={})

    rendered = redact_line("request failed: super-secret-value", replacements)

    assert rendered == "request failed: [REDACTED:KIS_APP_SECRET]"
