from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Settings are loaded from .env file and environment variables.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: Literal["development", "testing", "production"] = "development"
    PROJECT_NAME: str = "Self-Healing Control Plane"
    API_V1_STR: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # Infrastructure Connection URLs
    DATABASE_URL: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/control_plane"
    )
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000

    # Phase 5 & M1-B: Webhook Secrets and Ingestion Settings
    ALERTMANAGER_WEBHOOK_SECRET: str = "alertmanager-secret-token"
    GENERIC_EVENT_SECRET: str = "generic-secret-token"
    SYSTEM_STATUS_SECRET: str = "system-status-secret-token"
    INCIDENT_STREAM_KEY: str = "stream:incidents"
    INCIDENT_DLQ_KEY: str = "stream:incidents:dlq"
    EVENT_DEDUP_WINDOW_SECONDS: int = 60
    ALERT_CORRELATION_WINDOW_SECONDS: int = 300

    # M1-E CORS and Session Settings
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid LOG_LEVEL: {v}. Must be one of {valid_levels}")
        return v.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
