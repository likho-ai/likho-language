"""Configuration, read from environment variables (and a local .env file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    likho_env: str = "development"
    log_level: str = "INFO"

    grpc_port: int = 5030
    http_port: int = 4030  # /healthz and /readyz

    # Defaults match the likho-infra local stack.
    database_url: str = "postgresql+asyncpg://likho_language:likho_language@localhost:5433/likho_language"
    nats_url: str = "nats://localhost:4222"

    # Create or update the tables when the service starts.
    migrate_on_start: bool = True
    # How long a worker may keep using a workspace's vocabulary before checking for a newer version.
    vocabulary_ttl_seconds: float = 2.0
