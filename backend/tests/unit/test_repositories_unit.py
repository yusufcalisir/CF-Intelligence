"""Unit tests for BankRepository, MetricsRepository, and SimulationRepository using SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import SimulationRun
from app.domain.enums import SimulationStatus
from app.domain.value_objects import SimulationConfig
from app.infrastructure.models import Base
from app.infrastructure.repositories.bank_repository import BankRepository
from app.infrastructure.repositories.metrics_repository import MetricsRepository
from app.infrastructure.repositories.simulation_repository import SimulationRepository


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_bank_repository_save_and_retrieve(db_session: AsyncSession):
    """Verify BankRepository can save banks and query them by simulation or bank ID."""
    repo = BankRepository(db_session)
    sim_id = "sim_test_001"

    banks = [
        {
            "id": "bank_a",
            "name": "Bank Alpha",
            "tier": "large",
            "fraud_ratio": 0.05,
            "num_transactions": 5000,
            "data_profile": {"features": 10},
            "local_metrics": {"auc": 0.82},
            "federated_metrics": {"auc": 0.88},
        },
        {
            "id": "bank_b",
            "name": "Bank Beta",
            "tier": "medium",
            "fraud_ratio": 0.02,
            "num_transactions": 2500,
            "data_profile": {"features": 10},
            "local_metrics": {"auc": 0.79},
            "federated_metrics": {"auc": 0.86},
        },
    ]

    await repo.save_banks(sim_id, banks)

    sim_banks = await repo.get_by_simulation(sim_id)
    assert len(sim_banks) == 2
    assert sim_banks[0]["name"] == "Bank Alpha"

    single_bank = await repo.get_by_id("bank_a")
    assert single_bank is not None
    assert single_bank["id"] == "bank_a"
    assert single_bank["num_transactions"] == 5000


@pytest.mark.asyncio
async def test_metrics_repository_save_and_query(db_session: AsyncSession):
    """Verify MetricsRepository can save rounds and query summary metrics."""
    repo = MetricsRepository(db_session)
    sim_id = "sim_test_002"

    rounds = [
        {
            "round_number": 1,
            "global_loss": 0.45,
            "participating_bank_ids": ["bank_a", "bank_b"],
            "dropped_bank_ids": [],
            "per_bank_loss": {"bank_a": 0.46, "bank_b": 0.44},
            "per_bank_samples": {"bank_a": 100, "bank_b": 100},
            "aggregation_time_ms": 12.5,
            "round_duration_ms": 150.0,
        },
        {
            "round_number": 2,
            "global_loss": 0.32,
            "participating_bank_ids": ["bank_a", "bank_b"],
            "dropped_bank_ids": [],
            "per_bank_loss": {"bank_a": 0.33, "bank_b": 0.31},
            "per_bank_samples": {"bank_a": 100, "bank_b": 100},
            "aggregation_time_ms": 11.2,
            "round_duration_ms": 140.0,
        },
    ]

    await repo.save_rounds(sim_id, rounds)

    res_rounds = await repo.get_by_simulation(sim_id)
    assert len(res_rounds) == 2
    assert res_rounds[0]["round_number"] == 1
    assert res_rounds[1]["global_loss"] == 0.32


@pytest.mark.asyncio
async def test_simulation_repository_create_and_lifecycle(db_session: AsyncSession):
    """Verify SimulationRepository create, update, list, and delete operations."""
    repo = SimulationRepository(db_session)
    config = SimulationConfig(num_rounds=5, batch_size=32)

    sim = SimulationRun(
        id="sim_crud_test",
        config=config,
        status=SimulationStatus.PENDING,
        total_rounds=5,
    )

    created = await repo.create(sim)
    assert created.id == "sim_crud_test"

    fetched = await repo.get_by_id("sim_crud_test")
    assert fetched is not None
    assert fetched.status == SimulationStatus.PENDING

    sim.status = SimulationStatus.TRAINING_FEDERATED
    sim.current_round = 2
    await repo.update(sim)

    updated = await repo.get_by_id("sim_crud_test")
    assert updated is not None
    assert updated.status == SimulationStatus.TRAINING_FEDERATED
    assert updated.current_round == 2

    all_sims = await repo.list_all(limit=10)
    assert len(all_sims) >= 1

    deleted = await repo.delete("sim_crud_test")
    assert deleted is True
    assert await repo.get_by_id("sim_crud_test") is None
