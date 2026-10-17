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


# ── Real-Data Integration Test Flags & Gating ──────────────────────────────────
def pytest_addoption(parser: pytest.Parser) -> None:
    """Register custom CLI options for real-data integration gating."""
    parser.addoption(
        "--require-real-data",
        action="store_true",
        default=False,
        help="Enforce execution of real-data integration tests; fails closed if physical datasets are missing.",
    )
    parser.addoption(
        "--include-real-data",
        action="store_true",
        default=False,
        help="Include real-data integration tests in execution (skips if physical datasets are missing).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Gate tests marked with @pytest.mark.real_data so standard CI runs dataset-independent tests."""
    require_real = (
        config.getoption("--require-real-data", default=False)
        or os.environ.get("CFI_REQUIRE_REAL_DATA") == "1"
    )
    include_real = (
        config.getoption("--include-real-data", default=False)
        or os.environ.get("CFI_INCLUDE_REAL_DATA") == "1"
    )

    if not require_real and not include_real:
        skip_real = pytest.mark.skip(
            reason="Real-data integration test skipped by default. Run with --require-real-data or --include-real-data to execute."
        )
        for item in items:
            if "real_data" in item.keywords:
                item.add_marker(skip_real)


@pytest.fixture
def require_real_data(request: pytest.FixtureRequest) -> bool:
    """Fixture returning True if real data execution is mandatory."""
    return bool(
        request.config.getoption("--require-real-data", default=False)
        or os.environ.get("CFI_REQUIRE_REAL_DATA") == "1"
    )


# ── DDoS Throttle bypass ───────────────────────────────────────────────────────
# Setting TESTING=1 before the app module is imported causes DDoSProtectionMiddleware
# to skip all volumetric counting, preventing cross-test 429 bleed from the
# shared class-level _requests dict.
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CFI_AUDIT_LEDGER_SECRET", "cfi_test_prod_audit_signing_secret_64bytes_long!")


def _is_local_redis_live(host: str = "127.0.0.1", port: int = 6379) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# Configure backend tests to use the actual Redis endpoint through REDIS_URL
if _is_local_redis_live():
    current_redis_url = os.environ.get("REDIS_URL", "")
    if (
        not current_redis_url
        or "@redis:6379" in current_redis_url
        or "redis://redis:" in current_redis_url
    ):
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"


@pytest.fixture(scope="session", autouse=True)
def _set_testing_env():
    """Ensure TESTING=1 and verified REDIS_URL are present for the entire pytest session."""
    os.environ["TESTING"] = "1"
    if _is_local_redis_live():
        current_redis_url = os.environ.get("REDIS_URL", "")
        if (
            not current_redis_url
            or "@redis:6379" in current_redis_url
            or "redis://redis:" in current_redis_url
        ):
            os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.pop("TESTING", None)


@pytest.fixture(autouse=True)
def _reset_redis_store_state():
    """Reset RedisStore global unavailability and settings cache between tests."""
    from app.config import get_settings
    from app.infrastructure.redis_store import RedisStore

    RedisStore._global_redis_unavailable = False
    yield
    RedisStore._global_redis_unavailable = False
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_audit_chain_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure every test has a clean, isolated ImmutableAuditChain ledger file."""
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

    if "CFI_AUDIT_LEDGER_PATH" not in os.environ:
        test_ledger = tmp_path / "test_audit_ledger.json"
        monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", str(test_ledger))
    ImmutableAuditChain.reset_instance()
    yield
    ImmutableAuditChain.reset_instance()


@pytest.fixture(autouse=True)
def _isolate_nonce_store_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure every test has a clean, isolated DurableNonceStore file."""
    from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

    if "CFI_INTERNAL_NONCE_STORE_PATH" not in os.environ:
        test_nonce_file = tmp_path / "test_consumed_nonces.json"
        monkeypatch.setenv("CFI_INTERNAL_NONCE_STORE_PATH", str(test_nonce_file))
    reset_default_nonce_store()
    yield
    reset_default_nonce_store()


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
def _restore_settings_and_security_singletons():
    """Snapshot settings and security singletons before each test and restore cleanly after."""
    from app.application.services.training_provenance_service import (
        reset_default_training_issuer,
        reset_default_training_job_record_source,
        reset_default_training_verifier,
        reset_default_trust_store,
    )
    from app.config import get_settings
    from app.infrastructure.security.auth_service import AuthenticationService
    from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
    from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
    from app.presentation.routers.model_registry import deployer
    from app.presentation.routers.realtime_inference import (
        reset_circuit_breaker,
        reset_model_cache,
    )

    settings = get_settings()
    saved_env = {
        "app_env": settings.app_env,
        "payload_signing_secret": settings.payload_signing_secret,
        "oidc_jwt_signing_secret": settings.oidc_jwt_signing_secret,
        "identity_store_path": settings.identity_store_path,
        "identity_store_secret": settings.identity_store_secret,
    }
    saved_os_env = dict(os.environ)
    yield
    for k, v in saved_env.items():
        setattr(settings, k, v)
    for k in list(os.environ.keys()):
        if k not in saved_os_env:
            os.environ.pop(k, None)
    for k, v in saved_os_env.items():
        if os.environ.get(k) != v:
            os.environ[k] = v
    get_settings.cache_clear()
    AuthenticationService.reset_instance()
    reset_default_nonce_store()
    ImmutableAuditChain.reset_instance()
    reset_default_trust_store()
    reset_default_training_verifier()
    reset_default_training_issuer()
    reset_default_training_job_record_source()
    reset_circuit_breaker()
    reset_model_cache()
    deployer.reset()
    DurableConsortiumStore._instance = None
    from app.infrastructure.database import reset_tenant_engines_sync
    reset_tenant_engines_sync()


@pytest.fixture(autouse=True)
def clear_fallback_stores():
    """Clear shared in-memory fallback stores and dedup caches between tests.

    CRITICAL REPOSITORY INVARIANT: Never delete live Redis keys globally across
    shared application prefixes or wipe out unowned idempotency keys. Live Redis
    data must only be touched within dedicated, test-owned namespaces.
    """
    from app.infrastructure.redis_store import RedisStore

    RedisStore._shared_fallback_stores.clear()
    with contextlib.suppress(Exception):
        RedisStore("entity").clear()
        RedisStore("relationship").clear()
        RedisStore("intelligence").clear()
        RedisStore("alert").clear()
        RedisStore("sim_results").clear()
        RedisStore("sim_events").clear()
        RedisStore("model_evaluation").clear()
    with contextlib.suppress(Exception):
        from app.presentation.routers.simulation import _tombstones
        _tombstones.clear()
    try:
        from app.application.services.idempotency import IdempotencyService

        idem_svc = IdempotencyService.get()
        with idem_svc._fallback_lock:
            idem_svc._fallback.clear()
    except Exception:
        pass

    try:
        from app.presentation.routers.predict import _alert_service as p_svc

        p_svc.reset_dedup_records()
    except Exception:
        pass
    try:
        from app.presentation.routers.alerts import _alert_service as a_svc

        a_svc.reset_dedup_records()
    except Exception:
        pass
    for k in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        val = os.environ.get(k)
        if val and (val.startswith(("test", "mock")) or val == "mock_key"):
            os.environ.pop(k, None)
    yield
    RedisStore._shared_fallback_stores.clear()
    with contextlib.suppress(Exception):
        RedisStore("entity").clear()
        RedisStore("relationship").clear()
    try:
        from app.application.services.idempotency import IdempotencyService

        idem_svc = IdempotencyService.get()
        with idem_svc._fallback_lock:
            idem_svc._fallback.clear()
    except Exception:
        pass

    try:
        from app.presentation.routers.predict import _alert_service as p_svc

        p_svc.reset_dedup_records()
    except Exception:
        pass
    try:
        from app.presentation.routers.alerts import _alert_service as a_svc

        a_svc.reset_dedup_records()
    except Exception:
        pass
    for k in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        val = os.environ.get(k)
        if val and (val.startswith(("test", "mock")) or val == "mock_key"):
            os.environ.pop(k, None)


@pytest.fixture
def isolated_redis_namespace():
    """Provides a dedicated, test-scoped Redis namespace that cleans up only its own keys."""
    import uuid

    import redis

    from app.config import get_settings

    settings = get_settings()
    url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
    client: Any | None = None
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=1, decode_responses=True)
        client.ping()
    except Exception:
        client = None

    test_uid = uuid.uuid4().hex[:12]
    scoped_prefix = f"test_isolated_{test_uid}"
    tracked_keys: set[str] = set()

    class ScopedNamespace:
        def __init__(self, prefix: str, redis_client: Any | None) -> None:
            self.prefix = prefix
            self.client = redis_client

        def make_key(self, suffix: str) -> str:
            k = f"{self.prefix}:{suffix}"
            tracked_keys.add(k)
            return k

    yield ScopedNamespace(scoped_prefix, client)

    if client is not None and tracked_keys:
        with contextlib.suppress(Exception):
            client.delete(*tracked_keys)



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
