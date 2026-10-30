"""Integration tests for Section 35.1 - Production Database Persistence.

Tests every repository against a real (or in-memory SQLite) database to verify
that domain objects survive across session boundaries.

Run with:
    pytest tests/integration/test_db_persistence.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, CaseModel
from app.infrastructure.repositories.alert_repository import AlertRepository
from app.infrastructure.repositories.case_repository import CaseRepository
from app.infrastructure.repositories.entity_repository import EntityRepository
from app.infrastructure.repositories.round_repository import RoundRepository

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provide a transactional AsyncSession backed by in-memory SQLite.

    Uses SQLite+aiosqlite so the tests run without a running PostgreSQL instance.
    Each test function gets a fresh empty database.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ── AlertRepository Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_create_and_retrieve(db_session: AsyncSession) -> None:
    """Created alert must be retrievable by primary key with all fields intact."""
    repo = AlertRepository(db_session)
    alert = await repo.create(
        bank_id="bank_alpha",
        transaction_id="txn-001",
        risk_score=0.92,
        severity="high",
        reason_codes=["velocity_spike", "cross_border"],
        confidence=0.87,
    )

    assert alert.id is not None
    assert alert.bank_id == "bank_alpha"
    assert alert.risk_score == pytest.approx(0.92)
    assert "velocity_spike" in alert.reason_codes

    fetched = await repo.get_by_id(alert.id)
    assert fetched is not None
    assert fetched.id == alert.id
    assert fetched.severity == "high"


@pytest.mark.asyncio
async def test_alert_status_update(db_session: AsyncSession) -> None:
    """Updating alert status must persist and return the updated model."""
    repo = AlertRepository(db_session)
    alert = await repo.create(
        bank_id="bank_beta",
        transaction_id="txn-002",
        risk_score=0.75,
        severity="medium",
    )
    updated = await repo.update_status(alert.id, "under_review")
    assert updated is not None
    assert updated.status == "under_review"
    assert updated.updated_at is not None


@pytest.mark.asyncio
async def test_alert_list_by_bank(db_session: AsyncSession) -> None:
    """list_by_bank must return only alerts belonging to the specified bank."""
    repo = AlertRepository(db_session)
    await repo.create(bank_id="bank_alpha", transaction_id="t1", risk_score=0.5, severity="low")
    await repo.create(bank_id="bank_alpha", transaction_id="t2", risk_score=0.6, severity="medium")
    await repo.create(bank_id="bank_beta", transaction_id="t3", risk_score=0.7, severity="high")

    alpha_alerts = await repo.list_by_bank("bank_alpha")
    assert len(alpha_alerts) == 2
    assert all(a.bank_id == "bank_alpha" for a in alpha_alerts)

    beta_alerts = await repo.list_by_bank("bank_beta")
    assert len(beta_alerts) == 1


# ── CaseRepository Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_create_and_status_update(db_session: AsyncSession) -> None:
    """Case must be created with 'open' status and transition correctly."""
    repo = CaseRepository(db_session)
    case = await repo.create(
        title="Suspicious cross-border transfers",
        priority="p1_critical",
        alert_ids=["alert-001", "alert-002"],
        total_risk_score=0.95,
    )

    assert case.id is not None
    assert case.status == "open"
    assert case.priority == "p1_critical"

    updated = await repo.update_status(case.id, "investigating")
    assert updated is not None
    assert updated.status == "investigating"
    # closed_at must NOT be set for non-terminal statuses
    assert updated.closed_at is None

    closed = await repo.update_status(case.id, "resolved_confirmed_fraud")
    assert closed is not None
    assert closed.status == "resolved_confirmed_fraud"
    assert closed.closed_at is not None


@pytest.mark.asyncio
async def test_case_assign_and_note(db_session: AsyncSession) -> None:
    """Case assignment and note appending must persist correctly."""
    repo = CaseRepository(db_session)
    case = await repo.create(title="AML investigation", priority="p2_high")

    assigned = await repo.assign_to(case.id, "analyst_jones")
    assert assigned is not None
    assert assigned.assigned_to == "analyst_jones"

    noted = await repo.append_note(case.id, {"text": "Escalated to compliance team"})
    assert noted is not None
    assert len(noted.notes) == 1
    assert "Escalated" in noted.notes[0]["text"]
    assert "timestamp" in noted.notes[0]


@pytest.mark.asyncio
async def test_case_institutional_ownership_create_and_reload(db_session: AsyncSession) -> None:
    """Case creation with institutional owner persists bank_id across fresh session reload.
    Standalone case without bank_id remains unbound (None).
    """
    repo = CaseRepository(db_session)

    # 1. Standalone unbound case
    unbound_case = await repo.create(title="Unbound investigation")
    assert unbound_case.bank_id is None

    # 2. Institution-owned case
    owned_case = await repo.create(
        title="Institution-specific investigation",
        bank_id="bank_alpha",
        priority="p1_critical",
    )
    assert owned_case.bank_id == "bank_alpha"

    # 3. Fresh session reload on the same engine
    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded_unbound = await fresh_repo.get_by_id(unbound_case.id)
        assert reloaded_unbound is not None
        assert reloaded_unbound.bank_id is None

        reloaded_owned = await fresh_repo.get_by_id(owned_case.id)
        assert reloaded_owned is not None
        assert reloaded_owned.bank_id == "bank_alpha"


@pytest.mark.asyncio
async def test_case_link_alert_binds_unbound_ownership(db_session: AsyncSession) -> None:
    """Linking an institutional alert to an initially unbound case durably binds bank_id."""
    repo = CaseRepository(db_session)
    case = await repo.create(title="Pending triage case")
    assert case.bank_id is None
    assert case.alert_ids == []

    # Link alert with verified bank_id
    linked = await repo.link_alert(case.id, "alert-alpha-001", bank_id="bank_alpha")
    assert linked is not None
    assert linked.bank_id == "bank_alpha"
    assert "alert-alpha-001" in linked.alert_ids

    # Verify durability across fresh session
    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded = await fresh_repo.get_by_id(case.id)
        assert reloaded is not None
        assert reloaded.bank_id == "bank_alpha"
        assert reloaded.alert_ids == ["alert-alpha-001"]


@pytest.mark.asyncio
async def test_case_link_alert_idempotent(db_session: AsyncSession) -> None:
    """Repeatedly linking the same alert with compatible bank_id is idempotent."""
    repo = CaseRepository(db_session)
    case = await repo.create(title="Idempotency test case", bank_id="bank_alpha")

    # Link alert once
    linked1 = await repo.link_alert(case.id, "alert-alpha-001", bank_id="bank_alpha")
    assert linked1 is not None
    assert linked1.alert_ids == ["alert-alpha-001"]

    # Link same alert again with same bank_id
    linked2 = await repo.link_alert(case.id, "alert-alpha-001", bank_id="bank_alpha")
    assert linked2 is not None
    assert linked2.alert_ids == ["alert-alpha-001"]
    assert linked2.bank_id == "bank_alpha"


@pytest.mark.asyncio
async def test_case_link_alert_cross_tenant_rejection(db_session: AsyncSession) -> None:
    """Linking an alert from a different institution to an already bound case must fail
    with ValueError and leave existing case associations unchanged."""
    repo = CaseRepository(db_session)
    case = await repo.create(
        title="Alpha-owned Case",
        bank_id="bank_alpha",
        alert_ids=["alert-alpha-001"],
    )

    # Attempt cross-tenant link from bank_beta
    with pytest.raises(ValueError, match="Cross-tenant alert linking rejected"):
        await repo.link_alert(case.id, "alert-beta-999", bank_id="bank_beta")

    # Verify in fresh session that bank_id and alert_ids were NOT modified
    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded = await fresh_repo.get_by_id(case.id)
        assert reloaded is not None
        assert reloaded.bank_id == "bank_alpha"
        assert reloaded.alert_ids == ["alert-alpha-001"]
        assert "alert-beta-999" not in reloaded.alert_ids


@pytest.mark.asyncio
async def test_case_list_by_bank(db_session: AsyncSession) -> None:
    """list_by_bank returns only cases belonging to the specified institution."""
    repo = CaseRepository(db_session)
    await repo.create(title="Alpha 1", bank_id="bank_alpha", priority="p1_critical")
    await repo.create(title="Alpha 2", bank_id="bank_alpha", priority="p2_high")
    await repo.create(title="Beta 1", bank_id="bank_beta", priority="p3_medium")
    await repo.create(title="Unbound", bank_id=None, priority="p3_medium")

    alpha_cases = await repo.list_by_bank("bank_alpha")
    assert len(alpha_cases) == 2
    assert all(c.bank_id == "bank_alpha" for c in alpha_cases)

    beta_cases = await repo.list_by_bank("bank_beta")
    assert len(beta_cases) == 1
    assert beta_cases[0].title == "Beta 1"


@pytest.mark.asyncio
async def test_case_link_alert_concurrency_conflicting_institutions(
    db_session: AsyncSession,
) -> None:
    """Verify that concurrent conflicting alert links on an unbound case are safely handled:
    exactly one institution binds the case, and the conflicting session is rejected with ValueError.
    """
    repo = CaseRepository(db_session)
    case = await repo.create(title="Concurrent Unbound SQLite Case", bank_id=None)
    case_id = case.id

    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)

    async def worker(bank: str, alert: str) -> Any:
        async with factory() as session:
            r = CaseRepository(session)
            return await r.link_alert(case_id, alert, bank_id=bank)

    results = await asyncio.gather(
        worker("bank_alpha", "alert-alpha-race"),
        worker("bank_beta", "alert-beta-race"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}: {results}"
    assert isinstance(failures[0], ValueError)
    assert "Cross-tenant alert linking rejected" in str(failures[0])

    winning_case = successes[0]
    assert isinstance(winning_case, CaseModel)
    winning_bank = winning_case.bank_id
    assert winning_bank in ("bank_alpha", "bank_beta")
    losing_alert = "alert-beta-race" if winning_bank == "bank_alpha" else "alert-alpha-race"
    winning_alert = "alert-alpha-race" if winning_bank == "bank_alpha" else "alert-beta-race"

    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        final_case = await fresh_repo.get_by_id(case_id)
        assert final_case is not None
        assert final_case.bank_id == winning_bank
        assert final_case.alert_ids == [winning_alert]
        assert losing_alert not in final_case.alert_ids


@pytest.mark.asyncio
async def test_case_link_alert_concurrency_same_institution(
    db_session: AsyncSession,
) -> None:
    """Verify that concurrent alert links from the same institution do not lose alert IDs."""
    repo = CaseRepository(db_session)
    case = await repo.create(
        title="Concurrent Shared SQLite Case",
        bank_id="bank_alpha",
        alert_ids=["alert-000"],
    )
    case_id = case.id

    assert db_session.bind is not None
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)

    async def worker(alert: str) -> Any:
        async with factory() as session:
            r = CaseRepository(session)
            return await r.link_alert(case_id, alert, bank_id="bank_alpha")

    results = await asyncio.gather(
        worker("alert-001"),
        worker("alert-002"),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 0, f"Unexpected failures in concurrent same-tenant linking: {failures}"

    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        final_case = await fresh_repo.get_by_id(case_id)
        assert final_case is not None
        assert final_case.bank_id == "bank_alpha"
        assert "alert-000" in final_case.alert_ids
        assert "alert-001" in final_case.alert_ids
        assert "alert-002" in final_case.alert_ids
        assert len(final_case.alert_ids) == 3


# ── EntityRepository Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entity_privacy_hash_lookup(db_session: AsyncSession) -> None:
    """Entity must be retrievable by its HMAC-SHA256 privacy_id hash."""
    repo = EntityRepository(db_session)
    entity = await repo.create(
        entity_type="account",
        privacy_id="deadbeef" * 8,  # 64-char hex digest
        bank_id="bank_alpha",
        display_label="ACC-****1234",
        risk_level="elevated",
    )

    fetched = await repo.get_by_privacy_id("deadbeef" * 8)
    assert fetched is not None
    assert fetched.id == entity.id
    assert fetched.risk_level == "elevated"

    # Unknown hash returns None - never raises
    not_found = await repo.get_by_privacy_id("0000" * 16)
    assert not_found is None


@pytest.mark.asyncio
async def test_entity_risk_update(db_session: AsyncSession) -> None:
    """Risk level update must persist and be reflected on next read."""
    repo = EntityRepository(db_session)
    entity = await repo.create(
        entity_type="customer",
        privacy_id="abcd1234" * 8,
        bank_id="bank_beta",
        display_label="CUST-****5678",
    )
    assert entity.risk_level == "minimal"

    updated = await repo.update_risk_score(entity.id, "high", alert_count=5)
    assert updated is not None
    assert updated.risk_level == "high"
    assert updated.alert_count == 5


# ── RoundRepository Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_round_lifecycle(db_session: AsyncSession) -> None:
    """Full round lifecycle: create → record submissions → check quorum → mark complete."""
    repo = RoundRepository(db_session)
    round_model = await repo.create_round(
        consortium_id="consortium-eu-aml",
        round_number=1,
        quorum_required=2,
    )
    assert round_model.status == "collecting_gradients"
    assert round_model.submitted_bank_ids == []

    # Submit gradient from bank_alpha
    await repo.record_gradient_submission(
        round_id=round_model.id,
        bank_id="bank_alpha",
        gradient_hash="a" * 64,
        dp_epsilon_used=0.3,
        participant_count=1000,
    )
    assert not await repo.quorum_reached(round_model.id)

    # Submit gradient from bank_beta - quorum met (2 of 2)
    await repo.record_gradient_submission(
        round_id=round_model.id,
        bank_id="bank_beta",
        gradient_hash="b" * 64,
        dp_epsilon_used=0.25,
        participant_count=1200,
    )
    assert await repo.quorum_reached(round_model.id)

    # Mark round complete
    completed = await repo.mark_complete(round_model.id, global_model_id="model-v1")
    assert completed is not None
    assert completed.status == "complete"
    assert completed.global_model_id == "model-v1"
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_cross_restart_persistence(db_session: AsyncSession) -> None:
    """Objects created in one session must be visible in a fresh session on the same engine.

    Simulates a coordinator restart scenario.
    """
    repo = AlertRepository(db_session)
    alert = await repo.create(
        bank_id="bank_alpha",
        transaction_id="txn-persist-test",
        risk_score=0.88,
        severity="high",
    )
    alert_id = alert.id

    # Simulate opening a new session (same underlying engine / DB)
    repo2 = AlertRepository(db_session)
    fetched = await repo2.get_by_id(alert_id)
    assert fetched is not None, "Object must survive across session handle boundaries"
    assert fetched.bank_id == "bank_alpha"
