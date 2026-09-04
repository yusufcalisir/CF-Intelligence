# ruff: noqa: E402
"""Automated Unit Test Suite for Retention & Erasure Policy Engine."""

from __future__ import annotations

from app.application.services.retention_engine import AutomatedRetentionEngine
from app.domain.retention_policy import DataCategory, ErasureMethod


def test_tenant_retention_policy_configuration() -> None:
    """Test configuring per-tenant retention TTL policies."""
    engine = AutomatedRetentionEngine()

    policy = engine.configure_tenant_policy(
        tenant_id="bank_alpha",
        category=DataCategory.TRANSACTION_LOGS,
        ttl_days=90,
        erasure_method=ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION,
    )
    assert policy.category == DataCategory.TRANSACTION_LOGS
    assert policy.ttl_days == 90
    assert policy.erasure_method == ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION


def test_automated_ttl_purging_execution() -> None:
    """Test executing automated TTL purging for expired tenant records."""
    engine = AutomatedRetentionEngine()
    tenant = "bank_beta"

    engine.configure_tenant_policy(tenant, DataCategory.TRANSACTION_LOGS, ttl_days=30)
    engine.configure_tenant_policy(tenant, DataCategory.GRAPH_EDGES, ttl_days=15)

    purged = engine.purge_expired_records(tenant_id=tenant)
    assert len(purged) == 2
    assert any(p.category == DataCategory.TRANSACTION_LOGS for p in purged)
    assert any(p.category == DataCategory.GRAPH_EDGES for p in purged)
    assert all(p.records_erased_count > 0 for p in purged)
    assert all(len(p.erasure_hash) == 64 for p in purged)  # SHA-256 length check


def test_gdpr_article_17_right_to_be_forgotten_erasure() -> None:
    """Test executing GDPR Article 17 Right-to-be-Forgotten erasure for an entity."""
    engine = AutomatedRetentionEngine()
    tenant = "bank_gamma"
    entity_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    record = engine.execute_gdpr_right_to_be_forgotten(
        tenant_id=tenant,
        entity_id_hash=entity_hash,
    )
    assert record.tenant_id == tenant
    assert record.erasure_id.startswith("erase_gdpr_")
    assert record.records_erased_count == 1
    assert len(record.erasure_hash) == 64

    trail = engine.get_erasure_audit_trail(tenant)
    assert len(trail) == 1
    assert trail[0].erasure_id == record.erasure_id


def test_database_real_retention_purging_and_gdpr_zeroization() -> None:
    """Test connecting AutomatedRetentionEngine to a real database layer and verifying genuine row deletion."""
    from datetime import UTC, datetime, timedelta
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.infrastructure.database import Base
    from app.infrastructure.models import AlertModel, EntityModel

    # In-memory SQLite DB
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    Session = sessionmaker(bind=db_engine)
    session = Session()

    retention = AutomatedRetentionEngine(db_session=session)
    tenant = "bank_alpha"
    cutoff_30d = datetime.now(UTC) - timedelta(days=30)

    # 1. Insert an expired alert (created 45 days ago) and an active alert (created 5 days ago)
    alert_expired = AlertModel(
        id="alert_expired_001",
        bank_id=tenant,
        transaction_id="tx_old_999",
        risk_score=0.95,
        created_at=cutoff_30d - timedelta(days=15),
    )
    alert_active = AlertModel(
        id="alert_active_002",
        bank_id=tenant,
        transaction_id="tx_fresh_111",
        risk_score=0.25,
        created_at=cutoff_30d + timedelta(days=25),
    )
    session.add_all([alert_expired, alert_active])
    session.commit()

    # Verify initial database state
    initial_alerts = session.execute(select(AlertModel).where(AlertModel.bank_id == tenant)).scalars().all()
    assert len(initial_alerts) == 2

    # 2. Configure 30-day TTL and run purge
    retention.configure_tenant_policy(tenant, DataCategory.TRANSACTION_LOGS, ttl_days=30)
    purged_records = retention.purge_expired_records(tenant_id=tenant, db=session)

    assert len(purged_records) == 1
    assert purged_records[0].records_erased_count == 1

    # Directly query DB to confirm expired row is ACTUALLY gone from storage
    remaining_alerts = session.execute(select(AlertModel).where(AlertModel.bank_id == tenant)).scalars().all()
    assert len(remaining_alerts) == 1
    assert remaining_alerts[0].id == "alert_active_002"
    assert session.execute(select(AlertModel).where(AlertModel.id == "alert_expired_001")).scalar_one_or_none() is None

    # 3. Test GDPR Article 17 Right to be Forgotten on an EntityModel
    entity = EntityModel(
        id="ent_001",
        entity_type="account",
        privacy_id="user_hash_abc",
        bank_id=tenant,
        display_label="Customer Jane Doe",
        attributes={"email": "jane@bank.com", "balance": 15000.0},
    )
    session.add(entity)
    session.commit()

    # Confirm entity exists before RTBF
    assert session.execute(select(EntityModel).where(EntityModel.privacy_id == "user_hash_abc")).scalar_one_or_none() is not None

    # Execute Right to be Forgotten
    erasure_rec = retention.execute_gdpr_right_to_be_forgotten(
        tenant_id=tenant,
        entity_id_hash="user_hash_abc",
        db=session,
    )
    assert erasure_rec.records_erased_count >= 1

    # Directly query DB to confirm entity is ACTUALLY erased from the table
    erased_query = session.execute(select(EntityModel).where(EntityModel.privacy_id == "user_hash_abc")).scalar_one_or_none()
    assert erased_query is None

    session.close()

