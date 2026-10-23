from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local", env_file_encoding="utf-8", extra="ignore"
    )
    
    environment: Literal["development", "staging", "production"] = "development"

    database_url: str

    jwt_secret_key: str
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    refresh_cookie_secure: bool | None = None

    allowed_origins: list[str] = ["http://localhost:3000"]

    outbox_relay_interval_seconds: int = 2

    email_backend: Literal["acs", "smtp"] = "smtp"
    email_from_address: str = "no-reply@ludens.local"
    email_from_name: str = "Ludens"
    acs_connection_string: str = ""
    acs_sender_address: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    frontend_base_url: str = "http://localhost:3000"

    @property
    def db_connect_args(self) -> dict:
        return {"ssl": "require"} if self.environment != "development" else {}

settings = Settings()  # type: ignore[call-arg]
