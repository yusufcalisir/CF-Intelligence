"""Focused regression tests for 01-J Final Production/Staging Security Boundary Correction.

Verifies:
1. APP_ENV=production activates mandatory serving authentication and fail-closed security.
2. Canonical Compose, .env, and Helm manifests enforce APP_ENV=production.
3. Protected Redis writes reject unavailable durable storage when strict_production=True.
4. Production training authority rejects in-memory/unsubstantiated claims.
5. StreamingEngine avoids unauthorized in-memory Kafka substitutes when Kafka is disabled.
6. Development workflows remain compatible in APP_ENV=development.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.application.services.streaming_engine import StreamingEngine
from app.config import Settings
from app.infrastructure.redis_store import RedisStore

root_dir = Path(__file__).resolve().parents[3]


def test_compose_and_manifests_enforce_production_env() -> None:
    """Verify that Compose, .env.example, and Helm charts declare APP_ENV=production."""
    compose_path = root_dir / "docker-compose.yml"
    assert compose_path.exists()
    compose_content = compose_path.read_text(encoding="utf-8")
    assert "APP_ENV=${APP_ENV:-production}" in compose_content
    assert "APP_ENV=${APP_ENV:-staging}" not in compose_content

    env_example_path = root_dir / ".env.example"
    assert env_example_path.exists()
    env_content = env_example_path.read_text(encoding="utf-8")
    assert "APP_ENV=production" in env_content
    assert "KAFKA_ENABLED=false" in env_content

    helm_values_path = root_dir / "helm" / "cfi-platform" / "values.yaml"
    assert helm_values_path.exists()
    helm_content = helm_values_path.read_text(encoding="utf-8")
    assert "APP_ENV: production" in helm_content


def test_production_env_activates_serving_auth_boundary() -> None:
    """Verify serving authorization rejects unauthenticated principals in production."""
    from fastapi import HTTPException

    from app.application.services.model_serving_authorization import (
        resolve_effective_caller_tenant,
    )
    from app.config import get_settings

    with patch.dict(os.environ, {"APP_ENV": "production"}):
        get_settings.cache_clear()
        with pytest.raises(HTTPException) as exc_info:
            resolve_effective_caller_tenant(principal=None)
        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    with patch.dict(os.environ, {"APP_ENV": "development"}):
        get_settings.cache_clear()
        # In development, unauthenticated access does not raise 401
        principal, tenant = resolve_effective_caller_tenant(principal=None)
        assert principal is None or not principal.is_authenticated
        assert tenant == "bank_a"


def test_redis_store_strict_production_fails_closed() -> None:
    """Verify that RedisStore in production fails closed on unreachable Redis."""
    from app.config import get_settings

    with patch.dict(os.environ, {"APP_ENV": "production", "REDIS_URL": "redis://unreachable-redis-host:6379/0"}):
        get_settings.cache_clear()
        store = RedisStore("test_evidence", strict_production=True)
        assert store.is_production is True
        # In production, writes without a live Redis server must raise RuntimeError
        with pytest.raises(RuntimeError, match="Redis store unavailable in production"):
            store.set("test_key", "test_val")

    with patch.dict(os.environ, {"APP_ENV": "development"}):
        get_settings.cache_clear()
        store_dev = RedisStore("test_evidence_dev", strict_production=False)
        assert store_dev.is_production is False
        # In development, writes degrade to local in-memory storage without raising
        store_dev.set("test_key", "test_val")
        assert store_dev.get("test_key") == "test_val"


def test_training_authority_fails_closed_in_production() -> None:
    """Verify that training job authority strictly rejects in-memory stores in production."""
    from app.application.services.training_provenance_service import (
        TrainingJobAuthorityDatabase,
        TrainingProvenanceConfigurationError,
        TrustedTrainingJobRecordSource,
    )

    # In production, in-memory DB or SQLite target must raise ConfigurationError
    with pytest.raises(TrainingProvenanceConfigurationError, match="Production training job authority database must be a PostgreSQL connection URL"):
        TrainingJobAuthorityDatabase(
            db_target=":memory:",
            in_memory=True,
            app_env="production",
        )

    with pytest.raises(TrainingProvenanceConfigurationError, match="Production training coordinator credential is not configured"):
        TrustedTrainingJobRecordSource(
            app_env="production",
            store_path="postgresql://user:pass@host:5432/db",
            coordinator_token="",
        )


async def test_streaming_engine_disables_kafka_cleanly_in_production() -> None:
    """Verify StreamingEngine does not instantiate an unauthorized in-memory Kafka substitute in production."""
    from app.config import get_settings

    with patch.dict(os.environ, {"APP_ENV": "production", "KAFKA_ENABLED": "false"}):
        get_settings.cache_clear()
        engine = StreamingEngine()
        assert engine.kafka_connector is None

        # Invoking streaming when Kafka is disabled in production must raise RuntimeError
        with pytest.raises(RuntimeError, match="Kafka streaming connector is not enabled in production"):
            await engine.stream_cloudevent(
                event={"event_type": "test.event", "source": "test_source", "data": {}},
            )


def test_development_workflows_remain_compatible() -> None:
    """Verify development mode retains support for in-memory and local dev workflows."""
    from app.config import get_settings

    with patch.dict(os.environ, {"APP_ENV": "development", "KAFKA_ENABLED": "false"}):
        get_settings.cache_clear()
        engine = StreamingEngine()
        # In development, an in-memory/development connector is permissible
        assert engine.kafka_connector is not None

    settings = Settings(app_env="development")
    assert settings.app_env == "development"
