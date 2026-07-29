"""텔레그램 봇 웹훅을 1회 등록/조회한다.

운영 배포 후 한 번 실행하면, 텔레그램이 인라인 버튼 콜백을
`https://maps.magable.kr/api/telegram/webhook` 으로 전달하도록 설정한다. secret_token을
함께 등록해 서버측(maps.api.telegram)에서 검증한다.

**도메인이 바뀌면 반드시 다시 실행해야 한다.** 웹훅 URL은 텔레그램 쪽에 저장돼 있어서
서버를 재배포해도 갱신되지 않는다. 2026-07-29 `magable.kr` → `maps.magable.kr` 이전 때
구 도메인이 다른 서버를 가리키게 되면서 콜백이 그쪽으로 전달됐다.
`--info` 의 `ip_address` 가 우리 서버인지 확인하는 것이 가장 확실한 점검이다.

사용법 (프로젝트 루트, .env에 TELEGRAM_* 설정 후):
    python scripts/setup_telegram_webhook.py            # setWebhook + getWebhookInfo
    python scripts/setup_telegram_webhook.py --info      # 현재 등록 상태만 조회
    python scripts/setup_telegram_webhook.py --delete    # 웹훅 해제
    python scripts/setup_telegram_webhook.py --url https://maps.magable.kr/api/telegram/webhook
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.common.settings import get_settings

_DEFAULT_URL = "https://maps.magable.kr/api/telegram/webhook"
_API = "https://api.telegram.org/bot{token}/{method}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="텔레그램 봇 웹훅 등록/조회")
    parser.add_argument("--url", default=_DEFAULT_URL, help=f"웹훅 URL. 기본={_DEFAULT_URL}")
    parser.add_argument("--info", action="store_true", help="getWebhookInfo만 출력.")
    parser.add_argument("--delete", action="store_true", help="deleteWebhook으로 해제.")
    return parser.parse_args(argv)


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    """Bot API 메서드를 호출하고 JSON 응답을 반환한다."""
    resp = requests.post(_API.format(token=token, method=method), json=payload or {}, timeout=10)
    return resp.json()


def main(argv: list[str]) -> int:
    """엔트리포인트."""
    args = _parse_args(argv)
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        print("TELEGRAM_BOT_TOKEN이 비어 있습니다. .env를 확인하세요.", file=sys.stderr)
        return 1

    if args.info:
        print(_call(token, "getWebhookInfo"))
        return 0

    if args.delete:
        print(_call(token, "deleteWebhook", {"drop_pending_updates": True}))
        return 0

    secret = settings.telegram_webhook_secret
    if not secret:
        print("TELEGRAM_WEBHOOK_SECRET이 비어 있습니다(검증 불가). .env를 채우세요.", file=sys.stderr)
        return 1

    result = _call(
        token,
        "setWebhook",
        {
            "url": args.url,
            "secret_token": secret,
            "allowed_updates": ["callback_query"],
            "drop_pending_updates": True,
        },
    )
    print("setWebhook:", result)
    print("getWebhookInfo:", _call(token, "getWebhookInfo"))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
