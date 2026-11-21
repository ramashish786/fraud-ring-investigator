"""Typed application settings loaded from environment variables.

Secrets have no defaults: the process fails fast at startup if they are missing,
instead of silently running with a well-known value. Docker Compose supplies
development values; production supplies real ones.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]

_WEAK_SECRETS = {"change-me", "changeme", "secret", "password", "dev-jwt-secret-change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app
    env: Environment = "dev"
    app_name: str = "fraud-ring-investigator"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_json: bool = True

    # --- PostgreSQL (async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pw@host:5432/db)
    database_url: SecretStr
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    # --- Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"
    neo4j_max_pool_size: int = Field(default=50, ge=1)
    neo4j_connection_timeout_s: float = Field(default=5.0, gt=0)
    neo4j_query_timeout_s: float = Field(default=15.0, gt=0)

    # --- auth
    jwt_secret: SecretStr
    jwt_access_ttl_minutes: int = Field(default=30, ge=1, le=24 * 60)

    # --- LLM (D-010): "fake" lets the stack run and tests pass without an API key
    llm_provider: Literal["openai", "fake"] = "fake"
    openai_api_key: SecretStr | None = None
    openai_chat_model: str | None = None
    openai_embedding_model: str | None = None
    # Vector index dimension. Must match the embedding model output; changing it means re-indexing documents.
    embedding_dimensions: int = Field(default=1536, ge=64, le=4096)
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_concurrency: int = Field(default=4, ge=1, le=64)

    # --- documents / RAG
    document_storage_dir: str = "/data/documents"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)

    # --- worker
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    job_lease_s: int = Field(default=120, ge=10)
    worker_metrics_port: int = Field(default=9101, ge=0, le=65535)  # 0 disables the worker metrics server
    workflow_max_rounds: int = Field(default=2, ge=0, le=5)

    # --- graph backend: "neo4j" in every deployed environment; "memory" only for tests/offline evaluation
    graph_backend: Literal["neo4j", "memory"] = "neo4j"
    memory_dataset_dir: str = "data"

    # --- observability
    otel_exporter_otlp_endpoint: str | None = None
    cors_origins: list[str] = Field(default_factory=list)

    # --- health checks
    health_check_timeout_s: float = Field(default=2.0, gt=0, le=30)

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.llm_provider == "openai":
            missing = [
                n
                for n, v in (
                    ("OPENAI_API_KEY", self.openai_api_key.get_secret_value() if self.openai_api_key else None),
                    ("OPENAI_CHAT_MODEL", self.openai_chat_model),
                    ("OPENAI_EMBEDDING_MODEL", self.openai_embedding_model),
                )
                if not v  # None or "" (compose passes empty strings for unset vars)
            ]
            if missing:
                raise ValueError(f"LLM_PROVIDER=openai requires: {', '.join(missing)}")
        if self.env == "prod":
            jwt = self.jwt_secret.get_secret_value()
            if len(jwt) < 32 or jwt.lower() in _WEAK_SECRETS:
                raise ValueError("JWT_SECRET must be at least 32 random characters in prod")
            if self.neo4j_password.get_secret_value().lower() in _WEAK_SECRETS:
                raise ValueError("NEO4J_PASSWORD is a known weak value")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
