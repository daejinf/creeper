"""Environment -> Settings. Reads only os.environ (no .env auto-load).

Secrets (DB URL) are never included in error messages or repr.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DB_URL_VAR = "WEMEET_AUTH_DATABASE_URL"


class ConfigError(RuntimeError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    db_pool_mode: str = "null"
    db_prepare_off: bool = False
    session_days: int = 7
    cookie_name: str = "wemeet_session"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None
    cors_origins: tuple[str, ...] = ()
    enable_docs: bool = False


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"환경변수 {name} 값이 올바르지 않습니다. true/false 중 하나여야 합니다.")


def normalize_db_url(url: str) -> str:
    """Force the psycopg v3 driver for plain postgres URLs."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    if url.startswith("postgresql+psycopg://"):
        return url
    raise ConfigError(f"환경변수 {DB_URL_VAR}는 postgresql:// 또는 postgresql+psycopg:// 로 시작해야 합니다.")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url = (env.get(DB_URL_VAR) or "").strip()
    if not url:
        raise ConfigError(f"필수 환경변수 {DB_URL_VAR}가 설정되지 않았습니다.")

    pool_mode = (env.get("WEMEET_DB_POOL_MODE") or "null").strip().lower()
    if pool_mode not in ("null", "small"):
        raise ConfigError("환경변수 WEMEET_DB_POOL_MODE는 null 또는 small 이어야 합니다.")

    raw_days = (env.get("WEMEET_AUTH_SESSION_DAYS") or "7").strip()
    try:
        days = int(raw_days)
    except ValueError:
        raise ConfigError("환경변수 WEMEET_AUTH_SESSION_DAYS는 정수여야 합니다.") from None
    if not 1 <= days <= 90:
        raise ConfigError("환경변수 WEMEET_AUTH_SESSION_DAYS는 1~90 사이여야 합니다.")

    samesite = (env.get("WEMEET_AUTH_COOKIE_SAMESITE") or "lax").strip().lower()
    if samesite not in ("lax", "none"):
        raise ConfigError("환경변수 WEMEET_AUTH_COOKIE_SAMESITE는 lax 또는 none 이어야 합니다.")
    secure = _bool(env, "WEMEET_AUTH_COOKIE_SECURE", False)
    if samesite == "none" and not secure:
        raise ConfigError("SameSite=none 쿠키는 WEMEET_AUTH_COOKIE_SECURE=true 가 필요합니다.")

    name = (env.get("WEMEET_AUTH_COOKIE_NAME") or "wemeet_session").strip()
    domain = (env.get("WEMEET_AUTH_COOKIE_DOMAIN") or "").strip() or None
    if name.startswith("__Host-") and (domain or not secure):
        raise ConfigError("__Host- 쿠키는 Secure가 필요하고 Domain을 지정할 수 없습니다.")

    origins = tuple(
        o.strip().rstrip("/") for o in (env.get("WEMEET_CORS_ORIGINS") or "").split(",") if o.strip()
    )
    if "*" in origins:
        raise ConfigError("WEMEET_CORS_ORIGINS에 와일드카드는 쓸 수 없습니다. 출처를 명시하세요.")

    return Settings(
        database_url=normalize_db_url(url),
        db_pool_mode=pool_mode,
        db_prepare_off=_bool(env, "WEMEET_DB_PREPARE_OFF", False),
        session_days=days,
        cookie_name=name,
        cookie_secure=secure,
        cookie_samesite=samesite,
        cookie_domain=domain,
        cors_origins=origins,
        enable_docs=_bool(env, "WEMEET_ENABLE_DOCS", False),
    )
