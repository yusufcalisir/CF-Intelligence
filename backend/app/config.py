"""Application configuration loaded from environment variables.

Uses pydantic-settings to validate and type-check all configuration at startup.
Fails fast if required variables are missing.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────
    app_env: str = "development"
    app_debug: bool = True
    app_log_level: str = "INFO"

    # ── FastAPI & CORS ────────────────────────
    api_host: str = "0.0.0.0"  # nosec B104
    api_port: int = 8000
    api_workers: int = 1
    cors_allowed_origins: str = (
        "https://cf-intelligence.vercel.app,"
        "https://cfi-platform.vercel.app,"
        "http://localhost:5173,"
        "http://localhost:3000,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:3000,"
        "http://localhost:8000,"
        "http://127.0.0.1:8000"
    )
    cors_allow_origin_regex: str = r"^https:\/\/(cf-intelligence|cf-intelligence-git-[a-z0-9-]+-yusufcalisirs-projects)\.vercel\.app$"

    # ── PostgreSQL ────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "fraud_intelligence"
    postgres_user: str = "fraud_user"
    postgres_password: str = "change_me_in_production"

    # ── Redis ─────────────────────────────────
    # Leave redis_host empty to disable Redis and use in-memory storage silently.
    # Set REDIS_HOST env var to enable Redis.
    redis_host: str = ""
    redis_port: int = 6379
    redis_db: int = 0

    # ── Celery ────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── FL Simulation Defaults ────────────────
    fl_default_rounds: int = 10
    fl_default_local_epochs: int = 3
    fl_default_learning_rate: float = 0.001
    fl_default_batch_size: int = 64
    fl_min_clients_per_round: int = 2

    # ── Advanced Federated Optimization ───────
    fedprox_mu: float = 0.0
    moon_mu: float = 0.0
    moon_temperature: float = 0.5
    fedopt_server_lr: float = 0.01
    fedopt_beta1: float = 0.9
    fedopt_beta2: float = 0.999
    fedopt_tau: float = 1e-3

    # ── API Gateway Security ──────────────────
    gateway_require_auth: bool = False
    gateway_rate_limit: int = 120  # requests per minute
    gateway_api_keys: str = "key_bank_a:bank_a:bank,key_bank_b:bank_b:bank,key_bank_c:bank_c:bank,key_analyst:analyst:analyst"
    payload_signing_secret: str = "cfi_local_secret_key_2026_change_me_in_production"

    # ── Enterprise Security Suite ─────────────
    mtls_enabled: bool = True
    mtls_ca_cn: str = "CFI-Root-CA"
    mtls_cert_dir: str = "certs"

    oidc_enabled: bool = True
    oidc_issuer_url: str = "https://auth.cfi-platform.internal/realms/cfi"
    oidc_client_id: str = "cfi-api"
    oidc_jwt_signing_secret: str = "cfi_oidc_jwt_secret_key_2026_enterprise_hs256"

    abac_enabled: bool = True
    vault_enabled: bool = False
    vault_url: str = "http://vault.internal:8200"
    vault_token: str = "vault_token_dev_2026"
    immutable_audit_chain_enabled: bool = True

    # ── Observability (OpenTelemetry & Sentry) ─────────
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://jaeger:4317"
    otel_service_name: str = "cfi-backend"
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.1

    # ── MLflow Experiment Tracking ────────────
    mlflow_enabled: bool = True
    mlflow_tracking_uri: str = "mlruns"
    mlflow_experiment_name: str = "Collaborative-Fraud-Intelligence"

    # ── Bank Connector Configuration ──────────
    bank_a_connector_type: str = "mock"
    bank_b_connector_type: str = "mock"
    bank_c_connector_type: str = "mock"

    bank_a_auth_type: str = "none"
    bank_b_auth_type: str = "none"
    bank_c_auth_type: str = "none"

    bank_a_api_key: str = ""
    bank_b_api_key: str = ""
    bank_c_api_key: str = ""

    oauth_token_url: str = "http://localhost:8000/oauth/token"
    oauth_client_id: str = "cfi_coordinator_client"
    oauth_client_secret: str = "cfi_coordinator_secret_change_me"
    psd2_jwt_secret: str = "psd2_signing_secret_key_change_me"
    client_cert_path: str = ""
    client_key_path: str = ""
    mq_broker_uri: str = "amqp://guest:guest@localhost:5672//"
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"

    # ── Distributed Streaming & DBs ───────────
    database_type: str = "sqlite"  # "sqlite", "postgres", "cockroachdb"
    use_kafka: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"

    # ── Graph Database Configuration ──────────
    graph_db_type: str = "redis"  # "redis", "neo4j", "memgraph"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change_me_in_production"

    # ── Feature Store Configuration ───────────
    feature_store_enabled: bool = True
    feature_store_provider: str = "feast"  # "feast" or "hopsworks"
    feature_store_latency_ms: float = 2.0  # simulated latency overhead

    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync PostgreSQL connection URL for Alembic migrations."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str | None:
        """Redis connection URL, or None if Redis is not configured."""
        if not self.redis_host:
            return None
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def bank_urls(self) -> dict[str, str]:
        """HTTP endpoints for the distributed bank containers."""
        if self.app_env != "development":
            return {
                "bank_a": "http://bank-a:8011",
                "bank_b": "http://bank-b:8012",
                "bank_c": "http://bank-c:8013",
            }
        return {
            "bank_a": "http://localhost:8011",
            "bank_b": "http://localhost:8012",
            "bank_c": "http://localhost:8013",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance. Parsed once at startup."""
    return Settings()
