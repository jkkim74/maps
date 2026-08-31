"""등급·권한 화면설계서가 코드와 어긋나지 않는지 검사하는 계약 테스트.

값은 문서에 적힌 것을 다시 적지 않고 **코드에서 읽어와** 비교한다. 양쪽에 값을
복사해 두면 둘이 같이 낡는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maps.api.auth import _PUBLIC_PATHS, _USER_ALLOWED, is_allowed
from maps.api.schemas import UserPreferences
from maps.api.users import router as users_router
from maps.common.settings import get_config_status, get_settings

DOC = Path(__file__).resolve().parents[1] / "docs" / "ui-design" / "maps-auth-screen-design.html"

SCREEN_IDS = (
    "NAV-01", "NAV-02", "NAV-03",
    "AUTH-01", "AUTH-02", "AUTH-03",
    "SET-01",
    "USR-01", "USR-02", "USR-03", "USR-04",
    "PAY-01", "PAY-02", "PAY-03", "PAY-04",
    "OPS-01", "OPS-02", "OPS-03",
)
# 서버가 아직 없는 화면. 구현되면 이 목록을 줄이고 문서에서 배지를 떼야 한다.
UNBUILT_IDS = ("NAV-03", "PAY-01", "PAY-02", "PAY-03", "PAY-04", "OPS-02", "OPS-03")
DANGEROUS_ENV_VARS = (
    "MAPS_LIVE_TRADING_ENABLED",
    "KIS_REAL_TRADING",
    "MAPS_BROKER_MODE",
    "MAPS_STRATEGY_TRADE_ENABLED",
    "MAPS_SCORE_READINESS_REQUIRED",
)


@pytest.fixture(scope="module")
def doc() -> str:
    """설계서 본문."""
    assert DOC.exists(), f"{DOC} 가 없다"
    return DOC.read_text(encoding="utf-8")


def screen_map() -> dict[str, str]:
    """main.py 의 화면 키 → 라벨. import 시점을 테스트 안으로 미룬다."""
    import main

    return main._SCREEN_MAP


def screen_path(key: str) -> str:
    """화면 키의 실제 경로."""
    import main

    return main._screen_path(key)


def normalize_label(text: str) -> str:
    """라벨 표기 차이를 흡수한다.

    `_SCREEN_MAP` 은 `장세/팩터`, 실제 사이드바는 `장세 · 팩터` 로 쓴다. 문서 목업은
    사용자가 보는 쪽을 그리므로 공백과 구분자만 정규화해 비교한다.
    """
    return re.sub(r"[\s·/]+", "", text)


def secret_env_vars() -> list[str]:
    """`settings.py` 에서 `secret=True` 로 선언된 환경변수.

    `ConfigFieldStatus` 는 비밀 여부를 노출하지 않고 값만 마스킹하므로(미설정 비밀과
    미설정 일반이 똑같이 빈 문자열이다) 선언부를 직접 읽는다.
    """
    import maps.common.settings as settings_module

    source = Path(settings_module.__file__).read_text(encoding="utf-8")
    block = source[source.index("def get_config_status"): source.index("def get_missing_required_settings")]
    attrs = re.findall(r'_field\([^)]*?"(\w+)",\s*"(\w+)"[^)]*?secret=True', block)
    return [env_var for _attr, env_var in attrs]


def test_every_screen_id_and_path_is_documented(doc: str) -> None:
    """18화면 ID 가 모두 문서에 있다."""
    missing = [screen_id for screen_id in SCREEN_IDS if screen_id not in doc]
    assert not missing, f"문서에 없는 화면 ID: {missing}"


def test_unbuilt_screens_are_marked(doc: str) -> None:
    """서버가 없는 화면은 '서버 미구현' 으로 표시돼 있다."""
    assert doc.count("서버 미구현") >= len(UNBUILT_IDS), (
        "미구현 화면 표기가 부족하다 — 구현했다면 문서와 UNBUILT_IDS 를 함께 갱신한다"
    )


def test_permission_matrix_covers_every_screen(doc: str) -> None:
    """`_SCREEN_MAP` 의 모든 키와 한글 라벨이 권한 매트릭스에 있다."""
    missing_keys = [key for key in screen_map() if f"<code>{key}</code>" not in doc]
    missing_labels = [label for label in screen_map().values() if label not in doc]
    assert not missing_keys, f"권한 매트릭스에 없는 화면 키: {missing_keys}"
    assert not missing_labels, f"권한 매트릭스에 없는 화면 이름: {missing_labels}"


def test_free_user_menu_matches_is_allowed(doc: str) -> None:
    """무료 사용자에게 열린 화면 목록이 실제 판정과 일치한다.

    문서의 사이드바 그림은 손으로 쓴 것이므로, 코드가 허용하는 화면이 그림에서 빠지거나
    막힌 화면이 그림에 남아 있으면 실패해야 한다.
    """
    allowed = {key for key in screen_map() if is_allowed("user", screen_path(key), "GET")}
    blocked = set(screen_map()) - allowed

    nav_section = normalize_label(doc[doc.index('id="s1"'): doc.index('id="s2"')])
    for key in allowed:
        label = screen_map()[key]
        assert normalize_label(label) in nav_section, f"무료 메뉴 그림에 빠진 화면: {key} ({label})"

    # 화면 개수는 코드에서 세므로 제목에 박아 두지 않는다 — 화면 하나를 추가하면
    # 문서가 아니라 이 테스트가 먼저 낡는다.
    matrix = doc[doc.index(f"화면 {len(screen_map())}개 권한 매트릭스"):]
    for key in blocked:
        assert f"<code>{key}</code>" in matrix, f"권한 매트릭스에 빠진 관리자 전용 화면: {key}"


def test_public_paths_are_documented(doc: str) -> None:
    """공개 경로가 모두 문서에 있다."""
    missing = [path for path in _PUBLIC_PATHS if f"<code>{path}</code>" not in doc]
    assert not missing, f"문서에 없는 공개 경로: {missing}"
    assert "/static/" in doc, "정적 파일 공개 접두사가 문서에 없다"


def test_user_allowlist_is_documented(doc: str) -> None:
    """일반 사용자 허용목록의 모든 경로가 문서에 있다."""
    missing = [path for path in _USER_ALLOWED if f"<code>{path}</code>" not in doc]
    assert not missing, f"허용목록 표에 없는 경로: {missing}"


def test_user_api_routes_are_documented(doc: str) -> None:
    """회원 API 의 모든 경로가 문서에 있다."""
    paths = {route.path for route in users_router.routes}
    missing = [path for path in paths if f"<code>{path}</code>" not in doc]
    assert not missing, f"API 표에 없는 회원 API 경로: {missing}"


def test_preference_keys_are_documented(doc: str) -> None:
    """개인 설정 키가 전부 항목정의서에 있다(`UserPreferences` 기준)."""
    missing = [name for name in UserPreferences.model_fields if f"<code>{name}</code>" not in doc]
    assert not missing, f"내 설정 항목정의서에 없는 키: {missing}"


def test_config_sections_and_counts_are_documented(doc: str) -> None:
    """운영 설정 6섹션 키와 총 항목 수가 문서와 일치한다."""
    sections = get_config_status(get_settings())
    for section in sections:
        assert f"<code>{section.key}</code>" in doc, f"문서에 없는 설정 섹션: {section.key}"
        assert f"<td class=\"num\">{len(section.fields)}</td>" in doc, (
            f"{section.key} 섹션 항목 수가 문서와 다르다 (실제 {len(section.fields)}개)"
        )
    total = sum(len(section.fields) for section in sections)
    assert f"{total}개" in doc, f"운영 설정 총 항목 수({total})가 문서에 없다"


def test_dangerous_env_vars_are_documented(doc: str) -> None:
    """2단계 확인 대상 환경변수가 모두 문서에 있다."""
    missing = [name for name in DANGEROUS_ENV_VARS if f"<code>{name}</code>" not in doc]
    assert not missing, f"위험 항목 표에 없는 환경변수: {missing}"


def test_secret_fields_are_documented_with_reread_rule(doc: str) -> None:
    """비밀 항목 전체와 '재열람 불가' 규칙이 문서에 있다."""
    secrets = secret_env_vars()
    assert secrets, "비밀 항목 판별에 실패했다 — settings.py 의 _field 선언 형태가 바뀌었는지 확인한다"
    missing = [name for name in secrets if f"<code>{name}</code>" not in doc]
    assert not missing, f"비밀 항목 목록에 없는 환경변수: {missing}"
    assert f"{len(secrets)}개가 비밀 항목" in doc, f"비밀 항목 수({len(secrets)})가 문서와 다르다"
    assert "재열람 불가" in doc


def test_roles_statuses_plans_and_codes_are_documented(doc: str) -> None:
    """값 사전과 응답 코드가 문서에 있다."""
    for value in ("admin", "user", "active", "pending", "disabled", "free", "paid"):
        assert f"<code>{value}</code>" in doc, f"값 사전에 없는 값: {value}"
    for code in ("401", "403", "409", "422", "429"):
        assert f"<td>{code}</td>" in doc, f"응답 코드 표에 없는 코드: {code}"


def test_document_has_five_flow_diagrams(doc: str) -> None:
    """흐름도 5개가 있다."""
    assert doc.count('class="mermaid"') >= 5, "흐름도가 5개 미만이다"


def test_document_is_static(doc: str) -> None:
    """설계서는 정적 문서다 — 네트워크 호출 코드가 들어가면 안 된다."""
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket"):
        assert forbidden not in doc, f"설계서에 {forbidden} 가 들어 있다"
