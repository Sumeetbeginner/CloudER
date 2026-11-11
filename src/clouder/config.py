from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///.data/clouder.db"
    data_dir: Path = Path(".data")
    encryption_key: str = ""
    provider: Literal["simulated", "docker"] = "simulated"
    docker_socket: str = "/var/run/docker.sock"
    docker_api: str = "v1.47"
    compute_image: str = "nginxinc/nginx-unprivileged:1.28-alpine"
    database_image: str = "postgres:17-alpine"
    loadbalancer_image: str = "haproxy:3.0-alpine"
    operation_lease_seconds: int = Field(default=180, ge=120)
    worker_heartbeat_seconds: int = Field(default=10, ge=1, le=30)
    operation_timeout_seconds: int = Field(default=600, ge=120)
    max_attempts: int = Field(default=4, ge=1, le=10)
    object_limit_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    resource_quota: int = Field(default=20, ge=1)
    storage_quota_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    api_key_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def validate_key(self) -> "Settings":
        if not self.encryption_key:
            raise ValueError("CLOUDER_ENCRYPTION_KEY is required; run scripts/configure.py")
        Fernet(self.encryption_key.encode())
        if self.operation_timeout_seconds < self.operation_lease_seconds:
            raise ValueError("Operation timeout must be at least the lease duration")
        return self
