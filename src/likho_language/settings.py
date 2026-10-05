"""Configuration, read from environment variables and the .env files of the current environment.

LIKHO_ENV (development, staging or production; default development) picks the files. They are
read in this order, each one overriding the one before, and a real environment variable wins
over all of them:

    .env  .env.local  .env.<LIKHO_ENV>  .env.<LIKHO_ENV>.local

The .env.<LIKHO_ENV> files are committed and hold no secrets; the .local files are ignored by
git and hold the secrets of that environment on this machine.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

LIKHO_ENV = os.environ.get("LIKHO_ENV", "development")
ENV_FILES = (".env", ".env.local", f".env.{LIKHO_ENV}", f".env.{LIKHO_ENV}.local")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    likho_env: str = LIKHO_ENV
    log_level: str = "INFO"

    grpc_port: int = 5030
    http_port: int = 4030  # /healthz and /readyz

    # Defaults match the likho-infra local stack.
    database_url: str = "postgresql+asyncpg://likho_language:likho_language@localhost:5433/likho_language"
    nats_url: str = "nats://localhost:4222"
    # How long the start keeps trying to reach NATS before going on without it.
    nats_connect_timeout_seconds: float = 120.0
    # Metrics are always at GET /metrics (Prometheus text); set this to also push them (OTLP/HTTP, e.g. http://localhost:4318).
    otel_exporter_otlp_endpoint: str = ""

    # Create or update the tables when the service starts.
    migrate_on_start: bool = True
    # How long a worker may keep using a workspace's vocabulary before checking for a newer version.
    vocabulary_ttl_seconds: float = 2.0

    # Count the terms and spellings heard in the lines the workers publish (likho.live.segment).
    consumers_enabled: bool = True
    # Instances with the same name share the lines; "new" starts at the lines published from now on.
    segment_durable: str = "likho-language-segment"
    segment_start: str = "all"
    # How many of the last lines a spelling was applied to are kept as examples.
    examples_per_spelling: int = 3
