"""Unit tests for BankRepository, MetricsRepository, and SimulationRepository using SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import SimulationRun
from app.domain.enums import SimulationStatus
from app.domain.value_objects import SimulationConfig
from app.infrastructure.models import Base
from app.infrastructure.repositories.alert_repository import AlertRepository
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


@pytest.mark.asyncio
async def test_alert_repository_lifecycle_and_crud(db_session: AsyncSession):
    """Verify AlertRepository create, get_by_id, get_by_transaction_id, update_status, and delete."""
    repo = AlertRepository(db_session)

    alert = await repo.create(
        bank_id="bank_alpha",
        transaction_id="txn_unit_001",
        risk_score=890.5,
        severity="critical",
        reason_codes=["VEL-001", "GEO-RISK"],
        confidence=0.89,
        involved_entity_ids=["cust_001"],
        triage_priority="p1_critical",
        triage_action="escalate_immediate",
        sla_minutes=15,
        triage_reasons=["Critical severity with geo-risk"],
        dedup_key="dedup_hash_001",
        dedup_count=1,
    )

    assert alert.id is not None
    assert alert.bank_id == "bank_alpha"
    assert alert.transaction_id == "txn_unit_001"
    assert alert.triage_priority == "p1_critical"
    assert alert.sla_minutes == 15

    # Fetch by ID
    by_id = await repo.get_by_id(alert.id)
    assert by_id is not None
    assert by_id.id == alert.id

    # Fetch by transaction ID
    by_txn = await repo.get_by_transaction_id("txn_unit_001")
    assert by_txn is not None
    assert by_txn.id == alert.id

    # Update status
    updated = await repo.update_status(alert.id, "escalated")
    assert updated is not None
    assert updated.status == "escalated"
    assert updated.updated_at is not None

    # Delete
    deleted = await repo.delete(alert.id)
    assert deleted is True
    assert await repo.get_by_id(alert.id) is None
    assert await repo.get_by_transaction_id("txn_unit_001") is None


@pytest.mark.asyncio
async def test_alert_repository_filtering_and_counts(db_session: AsyncSession):
    """Verify AlertRepository list_by_bank filtering by status & severity and count_by_bank."""
    repo = AlertRepository(db_session)

    await repo.create(
        bank_id="bank_omega",
        transaction_id="tx_om_1",
        risk_score=920.0,
        severity="critical",
    )
    a2 = await repo.create(
        bank_id="bank_omega",
        transaction_id="tx_om_2",
        risk_score=780.0,
        severity="high",
    )
    await repo.update_status(a2.id, "under_review")

    await repo.create(
        bank_id="bank_other",
        transaction_id="tx_oth_1",
        risk_score=510.0,
        severity="medium",
    )

    # List by bank
    omega_all = await repo.list_by_bank("bank_omega")
    assert len(omega_all) == 2

    # Filter by severity
    omega_crit = await repo.list_by_bank("bank_omega", severity="critical")
    assert len(omega_crit) == 1
    assert omega_crit[0].transaction_id == "tx_om_1"

    # Filter by status
    omega_review = await repo.list_by_bank("bank_omega", status="under_review")
    assert len(omega_review) == 1
    assert omega_review[0].transaction_id == "tx_om_2"

    # Counts
    assert await repo.count_by_bank("bank_omega") == 2
    assert await repo.count_by_bank("bank_omega", severity="critical") == 1
    assert await repo.count_by_bank("bank_omega", status="under_review") == 1
    assert await repo.count_by_bank("bank_other") == 1
