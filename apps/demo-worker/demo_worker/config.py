import os
import socket
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    stream_key: str = "demo:jobs"
    consumer_group: str = "demo-worker-group"
    metrics_port: int = 9102
    hostname: str = socket.gethostname()
    pid: int = os.getpid()

    @property
    def consumer_name(self) -> str:
        return f"demo-worker-{self.hostname}-{self.pid}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
