from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    entsoe_api_key: str
    default_country: str = "CZ"
    history_window_hours: int = 24
    log_level: str = "INFO"

    # Anchored to the repo, not the process working directory: a relative
    # ".env" is silently missed when the server is started from anywhere else,
    # and the app then dies on a confusing "missing entsoe_api_key" error.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        case_sensitive=False,
        extra="ignore",
    )
