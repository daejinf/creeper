"""Argon2id password hashing (argon2-cffi defaults: RFC 9106 low-memory profile)."""
from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher(type=Type.ID)
_DUMMY_HASH = _hasher.hash("moim-timing-equalizer-0")  # built at import so no request pays the extra hash


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def burn_verify(password: str) -> None:
    """Spend the same Argon2 time as a real check so an unknown username is not distinguishable by latency."""
    verify_password(_DUMMY_HASH, password)
