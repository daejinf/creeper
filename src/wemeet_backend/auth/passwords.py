"""scrypt password hashing. Standard library only; no DB or wemeet_ai imports.

Stored format: ``scrypt$<N>$<r>$<p>$<salt_b64>$<hash_b64>`` so parameters can
be raised later without breaking existing hashes.
"""

import base64
import hashlib
import hmac
import secrets

N = 2**15
R = 8
P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MAX_PASSWORD_BYTES = 1024  # guard against huge inputs being hashed
# Upper bounds applied to parameters read from a stored hash (anti-DoS).
_MAX_N = 2**17
_MAX_R = 16
_MAX_P = 4
_MAXMEM = 256 * 1024 * 1024

_dummy_hash: str | None = None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=length
    )


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be str")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("password too long")
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, N, R, P, KEY_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Return True only for a matching password. Never raises on bad input."""
    try:
        if not isinstance(password, str) or not isinstance(stored, str):
            return False
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            return False
        scheme, n_s, r_s, p_s, salt_s, key_s = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        if not (2 <= n <= _MAX_N and n & (n - 1) == 0 and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P):
            return False
        salt = base64.b64decode(salt_s, validate=True)
        expected = base64.b64decode(key_s, validate=True)
        if not salt or not expected:
            return False
        actual = _derive(password, salt, n, r, p, len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def dummy_hash() -> str:
    """A valid hash of a random secret, for equalizing timing when a user is unknown."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash
