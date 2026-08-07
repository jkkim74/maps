#!/usr/bin/env python3
"""Redact configured secrets from line-oriented Claude stream output.

The cron wrappers place this filter before ``tee`` so raw JSONL and the
human-readable progress log never receive known credentials.  Secret values
come from explicit dotenv files and the current process environment; values
are never printed by this module.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

_MIN_SECRET_LENGTH = 8
_SENSITIVE_KEY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "APP_KEY",
    "DB_URL",
    "ACCOUNT_NO",
    "WEBHOOK",
)


def _is_sensitive_key(key: str) -> bool:
    """Return whether an environment key should be treated as sensitive."""
    upper = key.upper()
    return any(marker in upper for marker in _SENSITIVE_KEY_MARKERS)


def _add_secret(
    replacements: dict[str, str],
    *,
    key: str,
    value: str | None,
) -> None:
    """Add a non-trivial secret value and derived URL password replacement."""
    if value is None or len(value) < _MIN_SECRET_LENGTH:
        return
    replacements[value] = f"[REDACTED:{key}]"

    if "DB_URL" not in key.upper():
        return
    try:
        password = urlsplit(value).password
    except ValueError:
        password = None
    if password:
        for candidate in {password, unquote(password)}:
            if len(candidate) >= _MIN_SECRET_LENGTH:
                replacements[candidate] = "[REDACTED:DB_PASSWORD]"


def load_replacements(
    env_files: Iterable[Path],
    *,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Load sensitive values without returning or logging their key material."""
    replacements: dict[str, str] = {}
    for env_file in env_files:
        if not env_file.is_file():
            raise FileNotFoundError(f"secret source not found: {env_file}")
        for key, value in dotenv_values(env_file).items():
            if _is_sensitive_key(key):
                _add_secret(replacements, key=key, value=value)

    source = os.environ if environ is None else environ
    for key, value in source.items():
        if _is_sensitive_key(key):
            _add_secret(replacements, key=key, value=value)

    # Replace complete URLs and longer compound values before their substrings.
    return sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True)


def redact_text(text: str, replacements: Iterable[tuple[str, str]]) -> str:
    """Replace every configured secret occurrence in a string."""
    for secret, label in replacements:
        text = text.replace(secret, label)
    return text


def redact_value(value: Any, replacements: Iterable[tuple[str, str]]) -> Any:
    """Recursively redact strings while preserving a JSON event's structure."""
    if isinstance(value, str):
        return redact_text(value, replacements)
    if isinstance(value, list):
        return [redact_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: redact_value(item, replacements)
            for key, item in value.items()
        }
    return value


def redact_line(line: str, replacements: list[tuple[str, str]]) -> str:
    """Redact one JSONL event, falling back to plain text replacement."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return redact_text(line, replacements)
    redacted = redact_value(event, replacements)
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        type=Path,
        help="dotenv file containing values that must not reach logs",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate secret sources and exit without reading stdin",
    )
    return parser.parse_args()


def main() -> int:
    """Filter stdin to stdout, flushing each redacted line immediately."""
    args = _parse_args()
    try:
        replacements = load_replacements(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"secret redactor initialization failed: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return 0

    for raw in sys.stdin:
        ending = "\n" if raw.endswith("\n") else ""
        line = raw[:-1] if ending else raw
        print(redact_line(line, replacements), end=ending, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
