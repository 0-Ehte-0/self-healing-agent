from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    fault_injector_secret: str = "injector-secret-token"
    auto_expiry_seconds: int = 600
    demo_api_url: str = "http://demo-api:8000"
    redis_url: str = "redis://redis:6379/0"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/demo"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
