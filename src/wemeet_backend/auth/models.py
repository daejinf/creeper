"""Request/response models. Passwords are never included in repr."""

import re
import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
# Display name: complete Hangul syllables, ASCII letters/digits, and _ . - (no spaces/emoji/newlines).
NICKNAME_RE = re.compile(r"^[가-힣A-Za-z0-9_.\-]{2,10}$")
NICKNAME_HAS_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]")  # not made of symbols only
NICKNAME_MESSAGE = "닉네임은 2~10자의 한글, 영문, 숫자, _ . - 만 사용할 수 있습니다."
PASSWORD_MIN = 8
PASSWORD_MAX = 128


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_username(value: str) -> str:
    return value.strip().lower()


def normalize_nickname(value: str) -> str:
    """Trim, then NFC-normalize so composed/decomposed Hangul count and compare the same."""
    return unicodedata.normalize("NFC", value.strip())


class SignupRequest(Contract):
    username: str
    password: str = Field(repr=False)
    nickname: str

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, v: str) -> str:
        v = normalize_nickname(v)
        if not NICKNAME_RE.fullmatch(v) or not NICKNAME_HAS_WORD_RE.search(v):
            raise ValueError(NICKNAME_MESSAGE)
        return v

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = normalize_username(v)
        if not USERNAME_RE.fullmatch(v):
            raise ValueError("아이디는 영문 소문자, 숫자, 밑줄(_)로 3~20자여야 합니다.")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if not PASSWORD_MIN <= len(v) <= PASSWORD_MAX:
            raise ValueError(f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자여야 합니다.")
        return v


class LoginRequest(Contract):
    """No format validation on username: any bad login yields the same generic failure."""

    username: str = Field(max_length=64)
    password: str = Field(max_length=PASSWORD_MAX, repr=False)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return normalize_username(v)


class UserOut(Contract):
    """Login response shape (unchanged: no nickname)."""

    user_id: uuid.UUID
    username: str


class UserProfileOut(UserOut):
    """Signup / me response shape: UserOut plus the display name."""

    nickname: str


# Response envelopes (documented in OpenAPI). Same JSON shape the handlers already return.
class LoginData(Contract):
    user: UserOut


class LoginEnvelope(Contract):
    status: Literal["OK"] = "OK"
    data: LoginData


class ProfileData(Contract):
    user: UserProfileOut


class ProfileEnvelope(Contract):
    status: Literal["OK"] = "OK"
    data: ProfileData
