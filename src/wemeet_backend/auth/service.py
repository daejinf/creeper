"""Signup / login / logout rules. No HTTP here; routers translate AuthError to responses."""

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from . import passwords, store
from .models import LoginRequest, SignupRequest, UserOut, UserProfileOut


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def username_taken() -> AuthError:
    return AuthError("USERNAME_TAKEN", "이미 사용 중인 아이디입니다.", 409)


def invalid_credentials() -> AuthError:
    return AuthError("INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.", 401)


def unauthenticated() -> AuthError:
    return AuthError("UNAUTHENTICATED", "로그인이 필요합니다.", 401)


@dataclass(frozen=True)
class Authenticated:
    user: UserOut  # UserProfileOut (with nickname) for signup, plain UserOut for login
    token: str  # raw token: goes only into the cookie, never stored or logged


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _open_session(conn: Connection, user_id, session_days: int) -> str:
    token = new_token()
    store.create_session(conn, hash_token(token), user_id, session_days)
    return token


def signup(conn: Connection, req: SignupRequest, session_days: int = 7) -> Authenticated:
    user_id = store.create_user(conn, req.username, passwords.hash_password(req.password), req.nickname)
    if user_id is None:
        raise username_taken()
    store.touch_last_login(conn, user_id)
    token = _open_session(conn, user_id, session_days)
    return Authenticated(UserProfileOut(user_id=user_id, username=req.username, nickname=req.nickname), token)


def login(conn: Connection, req: LoginRequest, session_days: int = 7) -> Authenticated:
    row = store.get_user_by_username(conn, req.username)
    # Always verify against some hash so unknown users cost the same time as wrong passwords.
    ok = passwords.verify_password(req.password, row.password_hash if row else passwords.dummy_hash())
    if row is None or not ok:
        raise invalid_credentials()
    store.touch_last_login(conn, row.user_id)
    token = _open_session(conn, row.user_id, session_days)
    return Authenticated(UserOut(user_id=row.user_id, username=row.username), token)


def logout(conn: Connection, token: str | None) -> None:
    """Idempotent: an unknown, expired or missing token is not an error."""
    if token:
        store.delete_session(conn, hash_token(token))


def authenticate(conn: Connection, token: str | None) -> UserProfileOut:
    if not token:
        raise unauthenticated()
    row = store.get_user_by_session(conn, hash_token(token))
    if row is None:
        raise unauthenticated()
    return UserProfileOut(user_id=row.user_id, username=row.username, nickname=row.nickname)
