from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    entsoe_api_key: str
    default_country: str = "CZ"
    history_window_hours: int = 24
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
