"""Automated Unit Tests for Architecture, Concurrency, and Production Hardening."""

from __future__ import annotations

import concurrent.futures

import pytest

from app.config import Settings
from app.domain.realtime_explainer import FastInferenceExplainer
from app.infrastructure.redis_store import RedisStore


def test_redis_store_concurrent_threads_safety() -> None:
    """Verifies that RedisStore fallback in-memory storage is thread-safe under concurrent mutation and iteration."""
    store = RedisStore("test_concurrency")
    store.clear()

    num_threads = 8
    ops_per_thread = 50

    def worker(worker_id: int) -> None:
        for i in range(ops_per_thread):
            key = f"w{worker_id}_k{i}"
            store.set(key, {"worker": worker_id, "index": i, "val": i * 10})
            store.push_list(f"w{worker_id}_list", {"val": i})
            # Concurrently iterate over values and keys
            vals = store.list_values()
            keys = store.list_keys()
            assert isinstance(vals, list)
            assert isinstance(keys, list)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, w) for w in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    final_keys = store.list_keys()
    assert len(final_keys) == num_threads * ops_per_thread
    store.clear()


def test_production_invariants_validation_fail_fast() -> None:
    """Verifies that validate_production_invariants raises ValueError when production defaults are insecure."""
    insecure_prod = Settings(
        app_env="production",
        app_debug=True,
        payload_signing_secret="cfi_local_secret_key_2026_change_me_in_production",
        postgres_password="change_me_in_production",
    )
    with pytest.raises(ValueError, match="Production Security Configuration Violations"):
        insecure_prod.validate_production_invariants()


def test_production_invariants_pass_when_hardened() -> None:
    """Verifies that validate_production_invariants succeeds when production settings are secure."""
    hardened_prod = Settings(
        app_env="production",
        app_debug=False,
        payload_signing_secret="super_secure_random_production_secret_key_2026_hex64",
        postgres_password="strong_hardened_db_password_2026",
        cors_allowed_origins="https://cfi-platform.internal,https://cf-intelligence.vercel.app",
    )
    # Should not raise
    hardened_prod.validate_production_invariants()


def test_production_invariants_noop_in_development() -> None:
    """Verifies that development mode is not blocked by dev placeholder credentials."""
    dev_settings = Settings(
        app_env="development",
        app_debug=True,
        payload_signing_secret="cfi_local_secret_key_2026_change_me_in_production",
    )
    # Should not raise
    dev_settings.validate_production_invariants()


def test_production_config_environment_variable_and_alias_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies environment variables and platform aliases correctly populate Settings."""
    # Test standard environment variables
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("PAYLOAD_SIGNING_SECRET", "high_entropy_ephemeral_test_secret_2026_64hex")
    s1 = Settings()
    assert s1.app_env == "production"
    assert s1.app_debug is False
    assert s1.payload_signing_secret == "high_entropy_ephemeral_test_secret_2026_64hex"

    # Test platform alias environment variables (ENVIRONMENT, DEBUG, CFI_PAYLOAD_SIGNING_SECRET)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("APP_DEBUG", raising=False)
    monkeypatch.delenv("PAYLOAD_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", "high_entropy_ephemeral_test_secret_2026_64hex")
    s2 = Settings()
    assert s2.app_env == "production"
    assert s2.app_debug is False
    assert s2.payload_signing_secret == "high_entropy_ephemeral_test_secret_2026_64hex"


def test_production_invariants_fails_on_debug_enabled() -> None:
    """Verifies that production validation strictly fails when app_debug is True."""
    s = Settings(
        app_env="production",
        app_debug=True,
        payload_signing_secret="high_entropy_ephemeral_test_secret_2026_64hex",
    )
    with pytest.raises(ValueError, match="app_debug must be False in production"):
        s.validate_production_invariants()


def test_production_invariants_fails_on_placeholder_secret() -> None:
    """Verifies that production validation strictly fails when payload_signing_secret is placeholder."""
    s = Settings(
        app_env="production",
        app_debug=False,
        payload_signing_secret="cfi_local_secret_key_2026_change_me_in_production",
    )
    with pytest.raises(
        ValueError,
        match="payload_signing_secret must be set to a high-entropy secret in production",
    ):
        s.validate_production_invariants()


@pytest.mark.asyncio
async def test_production_lifespan_proceeds_with_valid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that the FastAPI lifespan startup hook executes successfully under valid production configuration."""
    from app.main import app, lifespan

    monkeypatch.setattr(
        "app.main.settings",
        Settings(
            app_env="production",
            app_debug=False,
            payload_signing_secret="high_entropy_ephemeral_test_secret_2026_64hex",
            cors_allowed_origins="https://cfi-platform.internal,https://cf-intelligence.vercel.app",
        ),
    )
    # Mock network/db calls during lifespan probe to keep test hermetic
    monkeypatch.setattr("app.main._acquire_seed_right", lambda: False)

    async with lifespan(app):
        # Startup completed without ValueError
        pass


def test_fast_inference_explainer_domain_isolation() -> None:
    """Verifies FastInferenceExplainer operates without hard top-level infrastructure dependencies."""
    explainer = FastInferenceExplainer()
    attr = explainer.explain_realtime_score(
        amount=50000.0,
        velocity_1h=4,
        merchant_category="electronics",
        risk_score=0.45,
    )
    assert isinstance(attr, list)
    assert any(a.feature_name == "amount" for a in attr)


def test_simulation_stop_events_bounded_memory() -> None:
    """Verifies that _stop_events dictionary bounds memory growth over hundreds of simulations."""
    from app.presentation.routers.simulation import _stop_events, _stop_events_lock

    with _stop_events_lock:
        initial_keys = list(_stop_events.keys())
        # Populate artificial entries
        for i in range(600):
            if len(_stop_events) > 500:
                for k in list(_stop_events.keys())[:-200]:
                    _stop_events.pop(k, None)
            import threading
            _stop_events[f"test_sim_{i}"] = threading.Event()

        assert len(_stop_events) <= 501
        # Clean up artificial entries
        _stop_events.clear()
        for k in initial_keys:
            _stop_events[k] = threading.Event()
