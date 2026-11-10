"""New-project environment only. No implicit original DB configuration."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "TIMELY_API_KEY",
    "TIMELY_MODEL",
    "TIMELY_BASE_URL",
    "WEMEET_COACH_DATABASE_URL",
    "WEMEET_COACH_RECOVERY_KEY",
)


def user_env(name):
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError):
        return None


def load():
    for name in NAMES:
        if not os.getenv(name) and (value := user_env(name)):
            os.environ[name] = value


def save_user(name, value):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value
