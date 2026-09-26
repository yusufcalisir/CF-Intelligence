"""Test configuration and shared fixtures."""

import contextlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path for cross-cutting benchmark experiment modules
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Pre-load pyarrow on Windows to initialize C++ DLLs cleanly before pytest collects tests
if importlib.util.find_spec("pyarrow") is not None:
    with contextlib.suppress(ImportError):
        import pyarrow as _pyarrow  # noqa: F401

import pytest  # noqa: E402

from tests.factories.data_factory import TestDataFactory  # noqa: E402

# ── DDoS Throttle bypass ───────────────────────────────────────────────────────
# Setting TESTING=1 before the app module is imported causes DDoSProtectionMiddleware
# to skip all volumetric counting, preventing cross-test 429 bleed from the
# shared class-level _requests dict.
os.environ.setdefault("TESTING", "1")


@pytest.fixture(scope="session", autouse=True)
def _set_testing_env():
    """Ensure TESTING=1 is present for the entire pytest session."""
    os.environ["TESTING"] = "1"
    yield
    os.environ.pop("TESTING", None)


@pytest.fixture(autouse=True)
def _reset_ddos_state():
    """Reset DDoSProtectionMiddleware's class-level shared request counter before each test."""
    try:
        from app.main import DDoSProtectionMiddleware

        with DDoSProtectionMiddleware._lock:
            DDoSProtectionMiddleware._requests.clear()
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from app.main import DDoSProtectionMiddleware

        with DDoSProtectionMiddleware._lock:
            DDoSProtectionMiddleware._requests.clear()
    except Exception:  # noqa: BLE001
        pass


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


# ── Clean C++ Extension Interpreter Teardown ──────────────────────────────────
_session_exitstatus: int | None = None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Record pytest session exit status after all test items have run."""
    global _session_exitstatus
    _session_exitstatus = exitstatus


def pytest_unconfigure(config: pytest.Config) -> None:
    """Safely flush streams and terminate cleanly during interpreter teardown.

    Prevents C++ native extension background worker threads (PyTorch OpenMP,
    PyArrow, TenSEAL, Ray) from aborting with SIGABRT (exit code 134:
    'terminate called without an active exception') during CPython 3.12
    Py_FinalizeEx thread unwinding on Linux CI environments.
    """
    global _session_exitstatus
    if _session_exitstatus is not None and (
        os.environ.get("CI") == "true"
        or os.environ.get("TESTING") == "1"
        or sys.platform.startswith("linux")
    ):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_session_exitstatus)

