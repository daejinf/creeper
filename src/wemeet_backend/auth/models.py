"""Request/response models. Passwords are never included in repr."""

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_username(value: str) -> str:
    return value.strip().lower()


class SignupRequest(Contract):
    username: str
    password: str = Field(repr=False)

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
    user_id: uuid.UUID
    username: str
