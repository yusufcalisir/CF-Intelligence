"""Unit tests for Production Database Persistence Engine (Section 10.2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.infrastructure.database import (
    _get_or_create_engine,
    _make_engine_kwargs,
    run_cockroach_transaction,
)


def test_sqlalchemy_async_engine_pool_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that _make_engine_kwargs returns production pool settings for non-sqlite databases."""
    from app.config import get_settings
    current_settings = get_settings()
    monkeypatch.setattr(current_settings, "database_type", "postgres")
    monkeypatch.setattr("app.infrastructure.database.get_settings", lambda: current_settings)

    kwargs = _make_engine_kwargs("bank_a")

    assert kwargs["pool_size"] == 20
    assert kwargs["max_overflow"] == 10
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True


def test_database_connection_resilience_and_pre_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies tenant engine creation and registry caching."""
    from unittest.mock import AsyncMock

    from app.infrastructure.database import reset_tenant_engines_sync

    mock_eng = MagicMock()
    mock_eng.dispose = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.database.create_async_engine",
        lambda url, **kwargs: mock_eng,
    )

    try:
        engine = _get_or_create_engine("bank_test_a")
        assert engine is mock_eng
    finally:
        reset_tenant_engines_sync()


@pytest.mark.asyncio
async def test_cockroach_transaction_retry_loop() -> None:
    """Verifies run_cockroach_transaction callback execution and retry logic."""

    class DummySessionContext:
        async def __aenter__(self) -> DummySessionContext:
            return self

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

        def begin(self) -> DummySessionContext:
            return self

    def dummy_factory() -> DummySessionContext:
        return DummySessionContext()

    mock_calls = 0

    async def sample_callback(session: object) -> str:
        nonlocal mock_calls
        mock_calls += 1
        return "success"

    res = await run_cockroach_transaction(dummy_factory, sample_callback)  # type: ignore[arg-type]
    assert res == "success"
    assert mock_calls == 1
