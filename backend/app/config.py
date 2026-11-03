from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "WE-MEET AI"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://wemeet:wemeet@localhost:5432/wemeet"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    ai_provider: str = "deepseek"
    cors_origins: str = "http://localhost:5173"
    scheduler_enabled: bool = True

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

