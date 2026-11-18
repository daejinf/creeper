"""Input normalization and validation for auth fields. Messages are user-facing Korean text and match the design."""
from __future__ import annotations

import re
import unicodedata

USERNAME_RE = re.compile(r"^[a-z0-9]{4,16}$")
NAME_MAX = 30
PASSWORD_MIN = 8
PASSWORD_MAX = 128  # bounds Argon2 work per request
# Reserved names; the original signup demo already treats the first four as taken.
RESERVED_USERNAMES = frozenset({"admin", "moim", "test", "user", "root", "system", "support", "administrator"})

MSG_USERNAME_EMPTY = "아이디를 입력해주세요."
MSG_USERNAME_FORMAT = "영문 소문자와 숫자 4~16자로 입력해주세요."
MSG_USERNAME_TAKEN = "이미 사용 중인 아이디예요."
MSG_USERNAME_OK = "사용할 수 있는 아이디예요."
MSG_NAME_EMPTY = "이름을 입력해주세요."
MSG_NAME_LONG = f"이름은 {NAME_MAX}자 이하로 입력해주세요."
MSG_NAME_CHARS = "이름에 사용할 수 없는 문자가 있어요."
MSG_PASSWORD_RULE = "비밀번호는 8자 이상, 영문과 숫자를 포함해야 해요."
MSG_PASSWORD_LONG = f"비밀번호는 {PASSWORD_MAX}자 이하로 입력해주세요."
MSG_PASSWORD_MISMATCH = "비밀번호가 서로 달라요."
MSG_TERMS = "이용약관 및 개인정보 처리방침에 동의해주세요."


class FieldError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def normalize_username(raw: str) -> str:
    """NFKC + trim. Case is not folded: the signup rule is lowercase only, as in the design."""
    return unicodedata.normalize("NFKC", raw).strip()


def validate_username(raw: str) -> str:
    value = normalize_username(raw)
    if not value:
        raise FieldError("username", MSG_USERNAME_EMPTY)
    if not USERNAME_RE.fullmatch(value):
        raise FieldError("username", MSG_USERNAME_FORMAT)
    return value


def is_reserved(username: str) -> bool:
    return username in RESERVED_USERNAMES


def validate_name(raw: str) -> str:
    value = unicodedata.normalize("NFC", raw).strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise FieldError("name", MSG_NAME_EMPTY)
    if len(value) > NAME_MAX:
        raise FieldError("name", MSG_NAME_LONG)
    if any(unicodedata.category(ch).startswith("C") for ch in value):  # control, format, unassigned, surrogate
        raise FieldError("name", MSG_NAME_CHARS)
    return value


def validate_password(raw: str) -> str:
    """Passwords are never trimmed or normalized, only checked."""
    if len(raw) > PASSWORD_MAX:
        raise FieldError("password", MSG_PASSWORD_LONG)
    if len(raw) < PASSWORD_MIN or "\x00" in raw:
        raise FieldError("password", MSG_PASSWORD_RULE)
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"[0-9]", raw):
        raise FieldError("password", MSG_PASSWORD_RULE)
    return raw
