"""Test configuration and shared fixtures."""

from typing import Any

try:
    import pyarrow  # Pre-load pyarrow on Windows to initialize C++ DLLs cleanly
except ImportError:
    pass

import pytest

from tests.factories.data_factory import TestDataFactory


@pytest.fixture
def sample_config() -> dict:
    """Default simulation config for tests."""
    return {
        "num_rounds": 3,
        "local_epochs": 2,
        "learning_rate": 0.001,
        "batch_size": 32,
        "min_clients_per_round": 2,
        "enable_latency_simulation": False,
        "latency_range_ms": (50, 500),
        "enable_dropout_simulation": False,
        "dropout_probability": 0.2,
        "enable_reconnect_simulation": True,
        "enable_differential_privacy": False,
        "dp_epsilon": 1.0,
        "dp_delta": 1e-5,
        "dp_max_grad_norm": 1.0,
        "enable_secure_aggregation": False,
        "bank_a_transactions": 1000,
        "bank_b_transactions": 800,
        "bank_c_transactions": 600,
    }


@pytest.fixture
def data_factory() -> type[TestDataFactory]:
    """Provides centralized TestDataFactory instance."""
    return TestDataFactory


@pytest.fixture
def canonical_case() -> dict[str, Any]:
    """Standard canonical case dictionary."""
    return TestDataFactory.create_case_dict(
        id="CASE-CANONICAL-PY-01",
        title="Structuring Ring Investigation",
        status="investigating",
        priority="critical",
    )


@pytest.fixture
def canonical_alert() -> dict[str, Any]:
    """Standard canonical alert dictionary."""
    return TestDataFactory.create_alert_dict(
        id="ALT-CANONICAL-PY-01",
        severity="CRITICAL",
        composite_score=920.0,
    )


@pytest.fixture(autouse=True)
def clear_fallback_stores():
    """Clear shared in-memory fallback stores between tests."""
    from app.infrastructure.redis_store import RedisStore

    RedisStore._shared_fallback_stores.clear()
    yield
    RedisStore._shared_fallback_stores.clear()
