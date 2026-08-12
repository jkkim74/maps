"""비밀번호 해시·검증 계약 테스트."""

from __future__ import annotations

import pytest

from maps.common.passwords import hash_password, needs_rehash, verify_password


def test_hash_is_not_plaintext_and_is_salted() -> None:
    """해시는 평문을 담지 않고, 같은 비밀번호도 매번 다른 값이 된다."""
    first = hash_password("hunter2")
    second = hash_password("hunter2")

    assert "hunter2" not in first
    assert first.startswith("scrypt$")
    assert first != second  # salt 가 매번 달라야 한다


def test_verify_accepts_correct_and_rejects_wrong() -> None:
    """정확한 비밀번호만 통과한다."""
    stored = hash_password("hunter2")

    assert verify_password("hunter2", stored) is True
    assert verify_password("hunter3", stored) is False
    assert verify_password("", stored) is False


@pytest.mark.parametrize(
    "stored",
    ["", "not-a-hash", "scrypt$1$2$3", "scrypt$a$b$c$d$e", "bcrypt$x$y$z$w$v"],
)
def test_verify_rejects_malformed_stored_value(stored: str) -> None:
    """형식이 깨진 저장값은 예외 없이 거부한다 — fail-closed."""
    assert verify_password("hunter2", stored) is False


def test_empty_password_cannot_be_hashed() -> None:
    """빈 비밀번호로는 계정을 만들 수 없다."""
    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_detects_outdated_parameters() -> None:
    """파라미터가 현재 기준보다 약하면 재해시 대상으로 표시한다."""
    current = hash_password("hunter2")
    weaker = current.replace("scrypt$16384$", "scrypt$1024$", 1)

    assert needs_rehash(current) is False
    assert needs_rehash(weaker) is True
    assert needs_rehash("garbage") is True
