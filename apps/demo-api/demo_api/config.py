# apps/demo-api/app/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "demo-api"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/self_healing"
    redis_url: str = "redis://redis:6379/0"
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    fault_injector_secret: str = "injector-secret-token"
    cpu_budget_cores: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
