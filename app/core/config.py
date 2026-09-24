"""Environment settings. Client-specific rules never live here: they belong in account settings."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+psycopg://dt_admin@127.0.0.1:5434/demand_tracker"
    secret_key: str = "dev-only-change-me"

    # Dev-only persona switcher. Must be off in production; SSO replaces it in Phase 7.
    view_switcher_enabled: bool = True

    mail_backend: Literal["console", "smtp", "ses"] = "console"
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: str = "var/files"

    @model_validator(mode="after")
    def _no_switcher_in_production(self) -> "Settings":
        if self.env == "production" and self.view_switcher_enabled:
            raise ValueError("VIEW_SWITCHER_ENABLED must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
