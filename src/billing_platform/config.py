from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://billing:billing@postgres:5432/billing"
    # Tune down when processes share PgBouncer (docs/runbooks/pgbouncer-pools.md).
    database_pool_size: int = 20
    database_max_overflow: int = 10
    # Empty env → None (optional RO path for evaluate/reports).
    database_read_url: str | None = None
    # Fall back to primary when replica lag exceeds this.
    replica_lag_threshold_seconds: int = 30
    redis_url: str = "redis://redis:6379/0"
    kafka_bootstrap_servers: str = "kafka:9092"
    entitlement_cache_ttl_seconds: int = 60
    auth_cache_ttl_seconds: int = 2
    entitlement_l1_ttl_seconds: int = 1
    outbox_batch_size: int = 100
    outbox_max_attempts: int = 10
    webhook_timestamp_tolerance_seconds: int = 300
    mock_stripe_webhook_secret: str = ""
    # Previous secret during rotation overlap; empty env → None.
    mock_stripe_webhook_secret_previous: str | None = None
    api_rate_limit_per_minute: int = 120
    api_rate_limit_platform_admin_per_minute: int = 1000
    reconciliation_alert_amount_cents: int = 10000
    grace_enforcement_interval_seconds: int = 60
    dunning_enabled: bool = False
    mock_stripe_base_url: str = "http://mock-stripe:8001"
    otel_exporter_otlp_endpoint: str = ""  # Base URL; app appends /v1/traces|/v1/metrics
    otel_service_name: str = ""
    otel_sdk_disabled: bool = False
    shutdown_grace_seconds: int = 30
    # Kafka probe failure → ready=degraded (200) instead of unavailable (503).
    health_kafka_optional: bool = False

    @field_validator("database_read_url", "mock_stripe_webhook_secret_previous", mode="before")
    @classmethod
    def empty_optional_string_is_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    # Compose/.env often has `OTEL_SDK_DISABLED=` (empty) — pydantic bool rejects "".
    @field_validator(
        "otel_sdk_disabled",
        "dunning_enabled",
        "health_kafka_optional",
        mode="before",
    )
    @classmethod
    def empty_bool_is_false(cls, value: object) -> object:
        if value == "":
            return False
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
