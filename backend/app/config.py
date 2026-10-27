"""Application configuration loaded from environment variables.

Uses pydantic-settings to validate and type-check all configuration at startup.
Fails fast if required variables are missing.
"""

import os
from functools import lru_cache

from pydantic import AliasChoices, Field
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
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("app_env", "environment"),
    )
    app_debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("app_debug", "debug"),
    )
    app_log_level: str = "INFO"
    enable_demo_data_seeding: bool = Field(
        default=False,
        validation_alias=AliasChoices("enable_demo_data_seeding", "cfi_enable_demo_seeding"),
        description="Explicit opt-in required to generate mock/demo data. Never runs by default.",
    )
    enable_demo_fallback: bool = Field(
        default=False,
        validation_alias=AliasChoices("enable_demo_fallback", "cfi_enable_demo_fallback"),
        description="Explicit opt-in required to allow heuristic fallback for simulated workflows. Disabled by default in production.",
    )
    enable_cross_bank_simulation: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "enable_cross_bank_simulation",
            "cfi_enable_cross_bank_simulation",
        ),
        description="Explicit opt-in required to allow cross-bank research simulation. Strictly disabled in production.",
    )
    cfi_coordinator_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cfi_coordinator_url",
            "coordinator_url",
            "cfi_matching_coordinator_url",
        ),
        description="Base URL for the established PSI matching coordinator daemon.",
    )
    cross_bank_per_counterparty_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        validation_alias=AliasChoices(
            "cross_bank_per_counterparty_timeout_seconds",
            "cfi_counterparty_timeout_seconds",
            "cfi_counterparty_execution_timeout_seconds",
        ),
        description="Default bounded execution timeout in seconds allocated per counterpart institution in cross-bank queries.",
    )
    cross_bank_total_batch_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        validation_alias=AliasChoices(
            "cross_bank_total_batch_timeout_seconds",
            "cfi_total_batch_timeout_seconds",
            "cfi_batch_execution_timeout_seconds",
        ),
        description="Default bounded total execution timeout in seconds for an entire multi-counterparty query batch.",
    )

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
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_pool_timeout: float = 30.0
    database_pool_recycle: int = 3600

    # ── Redis ─────────────────────────────────
    # Leave redis_host empty to disable Redis and use in-memory storage silently.
    # Set REDIS_HOST env var to enable Redis.
    redis_host: str = ""
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_tls: bool = False

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
    default_simulation_id: str = Field(
        default="",
        validation_alias=AliasChoices("default_simulation_id", "cfi_default_simulation_id"),
        description="Explicit platform-default simulation identifier for unscoped model serving and champion resolution.",
    )

    # ── Candidate Model Evaluation & Quality Gate ─────────
    holdout_manifest_path: str = Field(
        default="",
        validation_alias=AliasChoices("holdout_manifest_path", "cfi_holdout_manifest_path"),
        description="Path to designated holdout dataset manifest JSON file.",
    )
    holdout_dataset_path: str = Field(
        default="",
        validation_alias=AliasChoices("holdout_dataset_path", "cfi_holdout_dataset_path"),
        description="Direct path to designated holdout data file (npz/parquet/csv).",
    )

    # ── API Gateway Security ──────────────────
    gateway_require_auth: bool = Field(
        default=True,
        validation_alias=AliasChoices("gateway_require_auth", "cfi_gateway_require_auth"),
        description="Enforces mandatory OIDC Bearer token or registered API key authentication on ingress routes.",
    )
    gateway_rate_limit: int = 120  # requests per minute
    gateway_api_keys: str = "key_bank_a:bank_a:bank,key_bank_b:bank_b:bank,key_bank_c:bank_c:bank,key_analyst:analyst:analyst"
    payload_signing_secret: str = Field(
        default="cfi_local_secret_key_2026_change_me_in_production",
        validation_alias=AliasChoices("payload_signing_secret", "cfi_payload_signing_secret"),
    )
    internal_context_signing_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "internal_context_signing_secret",
            "cfi_internal_context_secret",
            "cfi_internal_trusted_context_secret",
        ),
        description="Dedicated high-entropy secret key for internal authority execution context signing.",
    )
    internal_nonce_store_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "internal_nonce_store_path",
            "cfi_internal_nonce_store_path",
        ),
        description="Filesystem path for durable cross-process nonce consumption store.",
    )

    # ── Training Provenance & Verification ───
    training_receipt_public_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_receipt_public_key",
            "cfi_training_receipt_public_key",
        ),
        description="Authorized Ed25519 public key (hex or PEM) for training receipt provenance verification.",
    )
    training_receipt_key_id: str = Field(
        default="cfi_training_ed25519_v1",
        validation_alias=AliasChoices(
            "training_receipt_key_id",
            "cfi_training_receipt_key_id",
        ),
        description="Expected key ID for authorized training receipt issuer.",
    )
    training_coordinator_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_coordinator_token",
            "cfi_training_coordinator_token",
        ),
        description="Authorization secret for training coordinators to schedule jobs.",
    )
    training_worker_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_worker_token",
            "cfi_training_worker_token",
        ),
        description="Authorization secret for training workers to register completed job executions.",
    )
    training_bank_tokens: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "training_bank_tokens",
            "cfi_training_bank_tokens",
        ),
        description="Per-bank workload credentials mapping bank IDs (e.g., BANK_A, BANK_B, BANK_C) to their independent high-entropy secrets.",
    )
    training_job_db_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_job_db_url",
            "cfi_training_job_db_url",
        ),
        description="Durable SQLite or PostgreSQL connection URL for training job authority records.",
    )
    training_job_store_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_job_store_path",
            "cfi_training_job_store_path",
        ),
        description="Path to durable training job execution records file or sqlite database.",
    )
    training_receipt_trust_store_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "training_receipt_trust_store_path",
            "cfi_training_receipt_trust_store",
        ),
        description="Path to durable training receipt trust store registry file.",
    )

    # ── Enterprise Security Suite ─────────────
    mtls_enabled: bool = True
    mtls_ca_cn: str = "CFI-Root-CA"
    mtls_cert_dir: str = "certs"

    oidc_enabled: bool = True
    oidc_issuer_url: str = "https://auth.cfi-platform.internal/realms/cfi"
    oidc_client_id: str = "cfi-api"
    oidc_jwt_signing_secret: str = Field(
        default="cfi_oidc_jwt_secret_key_2026_enterprise_hs256",
        validation_alias=AliasChoices(
            "oidc_jwt_signing_secret",
            "cfi_oidc_jwt_signing_secret",
        ),
        description="High-entropy JWT signing secret for enterprise OIDC authentication.",
    )
    identity_store_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "identity_store_path",
            "cfi_identity_store_path",
        ),
        description="Path to durable identity store file tracking authoritative users and token revocations across processes.",
    )
    identity_store_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "identity_store_secret",
            "cfi_identity_store_secret",
            "identity_store_hmac_secret",
            "cfi_identity_store_hmac_secret",
        ),
        description="Independently provisioned authority secret for signing durable identity store state.",
    )
    identity_store_authority_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "identity_store_authority_key",
            "cfi_identity_store_authority_key",
        ),
        description="Independently pinned Ed25519 authority public key for verifying identity store state.",
    )

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

    # ── Distributed Streaming & Kafka CloudEvents ───────────
    database_type: str = "sqlite"  # "sqlite", "postgres", "cockroachdb"
    use_kafka: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "cfi-fraud-detection-group"
    kafka_transaction_topic: str = "org.cfi.finint.transactions.v1"
    kafka_alert_topic: str = "org.cfi.finint.alerts.v1"
    kafka_dlq_topic: str = "org.cfi.finint.dlq.v1"
    kafka_recall_topic: str = "org.cfi.finint.recalls.v1"
    kafka_ticket_topic: str = "org.cfi.finint.tickets.v1"
    kafka_enabled: bool = False

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
        """Async connection URL."""
        if self.database_type == "sqlite":
            from app.infrastructure.storage.storage_utils import get_storage_dir

            db_path = os.path.abspath(os.path.join(get_storage_dir(), "cfi_central.db")).replace("\\", "/")
            return f"sqlite+aiosqlite:///{db_path}"
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync connection URL for Alembic migrations and synchronous scripts."""
        if self.database_type == "sqlite":
            from app.infrastructure.storage.storage_utils import get_storage_dir

            db_path = os.path.abspath(os.path.join(get_storage_dir(), "cfi_central.db")).replace("\\", "/")
            return f"sqlite:///{db_path}"
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str | None:
        """Redis connection URL, or None if Redis is not configured."""
        env_url = os.getenv("REDIS_URL")
        if env_url:
            return env_url
        if not self.redis_host:
            return None
        scheme = "rediss" if self.redis_tls else "redis"
        if self.redis_password:
            return f"{scheme}://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"{scheme}://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def redacted_redis_url(self) -> str:
        """Safe Redis URL with credentials redacted for logging and telemetry."""
        return redact_redis_url(self.redis_url)

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

    @property
    def is_production(self) -> bool:
        """Return True if running in production mode."""
        return self.app_env.lower() in ("production", "prod")

    def is_cross_bank_simulation_allowed(self) -> bool:
        """Determines whether software-emulated cross-bank research simulation is permitted.

        Requires explicit opt-in via enable_cross_bank_simulation even in development or test.
        Strictly forbidden in production environments.
        """
        if self.is_production:
            return False
        return self.enable_cross_bank_simulation

    def validate_production_invariants(self) -> None:
        """Enforces security-by-default and fail-closed validation when operating in production or staging mode.

        Fails fast if insecure development defaults, fallback secrets, or open debug flags are retained.
        """
        if self.app_env.lower() not in ("production", "staging"):
            return

        violations: list[str] = []

        if self.app_debug:
            violations.append("app_debug must be False in production.")

        if self.enable_cross_bank_simulation:
            violations.append("enable_cross_bank_simulation must be False in production.")

        if self.payload_signing_secret in (
            "cfi_local_secret_key_2026_change_me_in_production",
            "change_me_in_production",
            "",
        ):
            violations.append(
                "payload_signing_secret must be set to a high-entropy secret in production (cannot be default placeholder)."
            )

        if self.database_type == "postgres" and (
            self.postgres_password in ("change_me_in_production", "")
            or "change_me" in self.postgres_password
        ):
            violations.append(
                "postgres_password must be customized in production (cannot contain 'change_me')."
            )

        if "*" in self.cors_allowed_origins.split(","):
            violations.append("cors_allowed_origins cannot contain wildcard '*' in production.")

        forbidden_tokens = {
            "",
            "cfi_authorized_training_worker_secret_v1",
            "cfi_authorized_training_coordinator_secret_v1",
            "secret",
            "changeme",
            "admin",
            "password",
            "token",
            "default",
        }
        if (
            not self.training_worker_token
            or self.training_worker_token in forbidden_tokens
            or "change_me" in self.training_worker_token
        ):
            violations.append(
                "training_worker_token must be set to an independently configured high-entropy secret in production (cannot be empty or default placeholder)."
            )

        if (
            not self.training_coordinator_token
            or self.training_coordinator_token in forbidden_tokens
            or "change_me" in self.training_coordinator_token
        ):
            violations.append(
                "training_coordinator_token must be set to an independently configured high-entropy secret in production (cannot be empty or default placeholder)."
            )

        if (
            self.training_worker_token
            and self.training_coordinator_token
            and self.training_worker_token == self.training_coordinator_token
        ):
            violations.append(
                "training_worker_token and training_coordinator_token must be distinct credentials in production (privilege separation violation)."
            )

        if self.training_bank_tokens:
            seen_tokens: set[str] = set()
            for b_id, b_tok in self.training_bank_tokens.items():
                if not b_tok or b_tok in forbidden_tokens or "change_me" in b_tok:
                    violations.append(
                        f"training_bank_tokens['{b_id}'] must be set to an independently configured high-entropy secret in production (cannot be empty or placeholder)."
                    )
                if b_tok == self.training_coordinator_token:
                    violations.append(
                        f"training_bank_tokens['{b_id}'] and training_coordinator_token must be distinct credentials in production (privilege separation violation)."
                    )
                if b_tok in seen_tokens:
                    violations.append(
                        f"training_bank_tokens['{b_id}'] reuses a credential from another bank in production (bank isolation violation)."
                    )
                seen_tokens.add(b_tok)

        if not self.training_job_db_url and not self.training_job_store_path:
            violations.append(
                "training_job_db_url (or training_job_store_path) must be explicitly configured in production. In-memory fallback is strictly forbidden."
            )
        elif self.training_job_db_url and not (
            self.training_job_db_url.startswith("postgresql://")
            or self.training_job_db_url.startswith("postgres://")
        ):
            violations.append(
                "training_job_db_url in production must be a PostgreSQL connection URL (e.g. postgresql://...). SQLite URLs are forbidden in production."
            )

        forbidden_jwt_secrets = {
            "",
            "cfi_oidc_jwt_secret_key_2026_enterprise_hs256",
            "cfi_oidc_jwt_secret_key_2026_super_secure_256bit_key_!",
            "secret",
            "changeme",
            "admin",
            "password",
            "default",
            "token",
        }
        if (
            not self.oidc_jwt_signing_secret
            or self.oidc_jwt_signing_secret in forbidden_jwt_secrets
            or "change_me" in self.oidc_jwt_signing_secret
            or len(self.oidc_jwt_signing_secret.strip()) < 32
        ):
            violations.append(
                "oidc_jwt_signing_secret must be set to an independently configured high-entropy secret in production (minimum 32 characters, cannot be default repository placeholder)."
            )

        if not self.identity_store_secret and not self.identity_store_authority_key:
            violations.append(
                "identity_store_secret (or identity_store_authority_key) must be explicitly configured in production to establish authenticated identity state."
            )
        elif self.identity_store_secret and (
            self.identity_store_secret in forbidden_tokens or "change_me" in self.identity_store_secret
        ):
            violations.append(
                "identity_store_secret cannot be a forbidden placeholder in production."
            )

        if violations:
            msg = "Production Security Configuration Violations:\n - " + "\n - ".join(violations)
            raise ValueError(msg)


def redact_redis_url(url: str | None) -> str:
    """Redact passwords and tokens from a Redis connection URL for safe logging and telemetry.

    Example:
        rediss://default:secret_token@us1-fast-cat.upstash.io:6379/0
        -> rediss://default:***@us1-fast-cat.upstash.io:6379/0
    """
    if not url:
        return "not configured"
    try:
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme or "redis"
        port_part = f":{parsed.port}" if parsed.port else ""
        if parsed.password or parsed.username:
            user_part = f"{parsed.username}:***@" if parsed.username else ":***@"
            return f"{scheme}://{user_part}{parsed.hostname}{port_part}{parsed.path}"
        return f"{scheme}://{parsed.hostname}{port_part}{parsed.path}"
    except Exception:
        return "configured-redis-endpoint"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance. Parsed once at startup."""
    return Settings()


