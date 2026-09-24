"""Environment settings. Client-specific rules never live here: they belong in account settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+psycopg://dt_admin@127.0.0.1:5434/demand_tracker"
    secret_key: str = "dev-only-change-me"
    app_base_url: str = "http://localhost:8010"

    # Dev-only persona switcher. Must be off in production; SSO replaces it in Phase 7.
    view_switcher_enabled: bool = True

    # In-process jobs (daily admin mail). Off in tests, which call the services directly.
    scheduler_enabled: bool = True

    # console: log the mail and write it as .eml under mail_dir. smtp: send it.
    mail_backend: Literal["console", "smtp", "ses"] = "console"
    mail_from: str = "Demand Tracker <demand-tracker@localhost>"
    mail_dir: str = "var/mail"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True

    # Karat integration (interview results). Off unless a key is set.
    karat_api_key: str | None = None
    karat_account_id: int = 1

    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: str = "var/files"
    max_upload_mb: int = 5

    @model_validator(mode="after")
    def _no_switcher_in_production(self) -> "Settings":
        if self.env == "production" and self.view_switcher_enabled:
            raise ValueError("VIEW_SWITCHER_ENABLED must be false in production")
        return self

    def local_path(self, value: str) -> Path:
        """Relative paths are relative to the project, not to wherever the server was started."""
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
