"""비밀번호 해시·검증 — 표준 라이브러리 `hashlib.scrypt` 만 쓴다.

`passlib`/`bcrypt` 같은 새 의존성을 넣지 않는다. scrypt 는 메모리 하드라 GPU 대량
대입에 강하고 파이썬 표준 라이브러리에 들어 있다.

저장 형식: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``
파라미터를 값 안에 담아 두면 나중에 세기를 올려도 기존 해시를 계속 검증할 수 있다.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

_ALGORITHM = "scrypt"
_SALT_BYTES = 16
_KEY_BYTES = 32

# 현재 기준 파라미터. 올릴 때는 값만 바꾸면 되고, 기존 해시는 저장된 값으로 검증된다.
_N = 16384
_R = 8
_P = 1


def _b64(raw: bytes) -> str:
    """패딩 없는 URL-safe base64 문자열."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    """`_b64` 의 역변환. 형식이 깨졌으면 예외를 낸다."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """scrypt 키 유도. maxmem 은 파라미터에 맞춰 넉넉히 준다."""
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_KEY_BYTES,
        maxmem=n * r * 256 * 2,
    )


def hash_password(password: str) -> str:
    """평문 비밀번호를 저장 가능한 해시 문자열로 만든다.

    Args:
        password: 평문 비밀번호. 빈 값은 허용하지 않는다.

    Returns:
        ``scrypt$n$r$p$salt$hash`` 형식 문자열.

    Raises:
        ValueError: 비밀번호가 비어 있을 때.
    """
    if not password:
        raise ValueError("비밀번호는 비어 있을 수 없다")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _derive(password, salt, _N, _R, _P)
    return f"{_ALGORITHM}${_N}${_R}${_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """평문이 저장된 해시와 일치하는지 상수 시간으로 비교한다.

    저장값 형식이 깨졌거나 알고리즘이 다르면 예외 대신 `False` 를 돌려준다
    (fail-closed). 호출부가 예외 처리를 잊어 로그인이 열리는 일을 막는다.
    """
    if not password or not stored:
        return False
    try:
        algorithm, n_text, r_text, p_text, salt_text, hash_text = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        derived = _derive(password, _unb64(salt_text), int(n_text), int(r_text), int(p_text))
        return secrets.compare_digest(derived, _unb64(hash_text))
    except (ValueError, TypeError, MemoryError):
        return False


def needs_rehash(stored: str) -> bool:
    """저장된 해시가 현재 기준 파라미터보다 약한지 판단한다.

    로그인 성공 시점에 호출해 참이면 새 해시로 갱신한다. 형식이 깨진 값도 참이다.
    """
    try:
        algorithm, n_text, r_text, p_text, _salt, _hash = stored.split("$")
    except (ValueError, AttributeError):
        return True
    if algorithm != _ALGORITHM:
        return True
    try:
        return (int(n_text), int(r_text), int(p_text)) < (_N, _R, _P)
    except ValueError:
        return True
