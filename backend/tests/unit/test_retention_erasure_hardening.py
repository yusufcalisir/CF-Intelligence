"""Hardened Unit & Integration Test Suite for Enterprise Data Retention & GDPR Art. 17 Erasure.

Verifies:
1. Positive TTL validation and error handling (ValueError / RetentionErasureError).
2. Multi-tenant policy isolation across distinct bank institutions.
3. Standalone mode zero fake deletion verification (zero-mock invariant).
4. Explicit in-memory entity registration, lookup, and deletion.
5. Cryptographic SHA-256 hash chaining (prev_erasure_hash) and ledger integrity.
6. Tamper detection when an erasure hash or record is altered.
7. Persistent on-disk JSONL ledger survival across engine reload.
8. Cross-subsystem ImmutableAuditChain event propagation.
9. Real database cascade deletion resolving privacy_id to entity PK and purging relationships/alerts.
10. REST API endpoints on compliance router (/retention/policies, /retention/purge, /gdpr/erasure, /retention/audit-trail/verify).
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.application.services.retention_engine import (
    GENESIS_ERASURE_HASH,
    AutomatedRetentionEngine,
)
from app.domain.retention_policy import (
    DataCategory,
    ErasureMethod,
    RetentionPolicy,
)
from app.infrastructure.database import Base
from app.infrastructure.models import (
    AlertModel,
    EntityModel,
    RelationshipModel,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.main import app


def test_retention_policy_validation_positive_ttl() -> None:
    """Verifies that non-positive TTL schedules raise ValueError."""
    with pytest.raises(ValueError, match="ttl_days must be positive"):
        RetentionPolicy(category=DataCategory.TRANSACTION_LOGS, ttl_days=0)

    with pytest.raises(ValueError, match="ttl_days must be positive"):
        RetentionPolicy(category=DataCategory.CUSTOMER_ENTITIES, ttl_days=-10)

    valid_policy = RetentionPolicy(
        category=DataCategory.CUSTOMER_ENTITIES,
        ttl_days=365,
        erasure_method=ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION,
    )
    assert valid_policy.ttl_days == 365
    assert valid_policy.category == DataCategory.CUSTOMER_ENTITIES


def test_multi_tenant_policy_isolation() -> None:
    """Ensures retention policy configurations are strictly isolated per tenant."""
    engine = AutomatedRetentionEngine()
    engine.configure_tenant_policy("bank_alpha", DataCategory.TRANSACTION_LOGS, ttl_days=90)
    engine.configure_tenant_policy("bank_beta", DataCategory.TRANSACTION_LOGS, ttl_days=180)
    engine.configure_tenant_policy("bank_alpha", DataCategory.GRAPH_EDGES, ttl_days=30)

    alpha_policies = {p.category: p.ttl_days for p in engine.get_tenant_policies("bank_alpha")}
    beta_policies = {p.category: p.ttl_days for p in engine.get_tenant_policies("bank_beta")}
    gamma_policies = engine.get_tenant_policies("bank_gamma")

    assert alpha_policies[DataCategory.TRANSACTION_LOGS] == 90
    assert alpha_policies[DataCategory.GRAPH_EDGES] == 30
    assert beta_policies[DataCategory.TRANSACTION_LOGS] == 180
    assert DataCategory.GRAPH_EDGES not in beta_policies
    assert len(gamma_policies) == 0


def test_standalone_zero_fake_deletion_when_entity_absent() -> None:
    """Confirms zero mock deletion count when an entity does not exist with auto_seed_missing=False."""
    engine = AutomatedRetentionEngine()
    record = engine.execute_gdpr_right_to_be_forgotten(
        tenant_id="bank_gamma",
        entity_id_hash="non_existent_entity_hash_9999",
        auto_seed_missing=False,
    )
    assert record.records_erased_count == 0
    assert record.affected_tables == []
    assert record.status == "VERIFIED_ERASED"
    assert len(record.erasure_hash) == 64


def test_in_memory_explicit_entity_registration_and_erasure() -> None:
    """Verifies in-memory entity registration, accurate deletion counting, and table tracking."""
    engine = AutomatedRetentionEngine()
    tenant = "bank_delta"
    entity_hash = "known_privacy_hash_12345"

    engine.register_in_memory_entity(tenant, entity_id="ent_delta_01", privacy_id=entity_hash)
    record = engine.execute_gdpr_right_to_be_forgotten(
        tenant_id=tenant,
        entity_id_hash=entity_hash,
        auto_seed_missing=False,
    )

    assert record.records_erased_count == 1
    assert "in_memory_entities" in record.affected_tables
    assert record.tenant_id == tenant


def test_cryptographic_hash_chain_linking() -> None:
    """Validates sequential SHA-256 hash chaining (prev_erasure_hash) across multiple erasures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = AutomatedRetentionEngine(ledger_dir=tmpdir)
        tenant = "bank_chain_test"

        rec1 = engine.execute_gdpr_right_to_be_forgotten(
            tenant_id=tenant,
            entity_id_hash="hash_ent_001",
        )
        rec2 = engine.execute_gdpr_right_to_be_forgotten(
            tenant_id=tenant,
            entity_id_hash="hash_ent_002",
        )
        rec3 = engine.execute_gdpr_right_to_be_forgotten(
            tenant_id=tenant,
            entity_id_hash="hash_ent_003",
        )

        assert rec1.prev_erasure_hash == GENESIS_ERASURE_HASH
        assert rec2.prev_erasure_hash == rec1.erasure_hash
        assert rec3.prev_erasure_hash == rec2.erasure_hash

        verification = engine.verify_erasure_chain_integrity(tenant)
        assert verification["valid"] is True
        assert verification["total_records"] == 3
        assert verification["last_hash"] == rec3.erasure_hash
        assert verification["tamper_reason"] is None


def test_cryptographic_tamper_detection_broken_chain() -> None:
    """Verifies that tampering with an audit record breaks cryptographic chain integrity."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = AutomatedRetentionEngine(ledger_dir=tmpdir)
        tenant = "bank_tamper_test"

        engine.execute_gdpr_right_to_be_forgotten(tenant, "hash_tamper_01")
        engine.execute_gdpr_right_to_be_forgotten(tenant, "hash_tamper_02")
        engine.execute_gdpr_right_to_be_forgotten(tenant, "hash_tamper_03")

        trail = engine.get_erasure_audit_trail(tenant)
        assert len(trail) == 3

        # Tamper with the middle record's erased row count
        trail[1].records_erased_count = 9999

        verification = engine.verify_erasure_chain_integrity(tenant)
        assert verification["valid"] is False
        assert verification["broken_index"] == 1
        assert "Hash digest corruption" in verification["tamper_reason"]


def test_persistent_on_disk_jsonl_ledger_recovery() -> None:
    """Verifies that erasure records are persisted to JSONL and recovered across engine re-instantiation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine1 = AutomatedRetentionEngine(ledger_dir=tmpdir)
        tenant = "bank_persist_test"

        r1 = engine1.execute_gdpr_right_to_be_forgotten(tenant, "entity_p1")
        r2 = engine1.execute_gdpr_right_to_be_forgotten(tenant, "entity_p2")

        # Create a fresh engine instance pointing to the same ledger directory
        engine2 = AutomatedRetentionEngine(ledger_dir=tmpdir)
        recovered_trail = engine2.get_erasure_audit_trail(tenant)

        assert len(recovered_trail) == 2
        assert recovered_trail[0].erasure_id == r1.erasure_id
        assert recovered_trail[1].erasure_id == r2.erasure_id
        assert recovered_trail[1].prev_erasure_hash == r1.erasure_hash

        verification = engine2.verify_erasure_chain_integrity(tenant)
        assert verification["valid"] is True
        assert verification["total_records"] == 2


def test_immutable_audit_chain_cross_system_event_recording() -> None:
    """Verifies that GDPR erasures and TTL purges append events to the global ImmutableAuditChain."""
    engine = AutomatedRetentionEngine()
    tenant = "bank_audit_chain_test"

    engine.configure_tenant_policy(tenant, DataCategory.TRANSACTION_LOGS, ttl_days=30)
    engine.purge_expired_records(tenant)
    engine.execute_gdpr_right_to_be_forgotten(tenant, "hash_global_chain_001")

    snapshot = ImmutableAuditChain.get_instance().get_snapshot()
    event_types = [e.event_type for e in snapshot]

    assert "GDPR_RTBF_ERASURE" in event_types
    assert "TTL_RETENTION_PURGE" in event_types


def test_database_real_cascade_deletion_with_privacy_id_and_relationships() -> None:
    """Verifies physical SQL multi-table deletion resolving privacy_id to primary keys."""
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    Session = sessionmaker(bind=db_engine)
    session = Session()

    tenant = "bank_cascade_alpha"
    other_tenant = "bank_other"

    # 1. Create target entity
    target_ent = EntityModel(
        id="ent_target_primary_100",
        entity_type="account",
        privacy_id="privacy_hash_target_999",
        bank_id=tenant,
        display_label="Customer Alpha",
        attributes={"balance": 50000.0},
    )
    # Other bank's entity
    other_ent = EntityModel(
        id="ent_other_200",
        entity_type="account",
        privacy_id="privacy_hash_other_888",
        bank_id=other_tenant,
        display_label="Customer Beta",
        attributes={"balance": 20000.0},
    )
    session.add_all([target_ent, other_ent])

    # 2. Relationships involving target entity by primary key
    rel1 = RelationshipModel(
        id="rel_target_01",
        source_entity_id="ent_target_primary_100",
        target_entity_id="ent_other_200",
        relationship_type="TRANSACTION",
        confidence=0.95,
    )
    rel_other = RelationshipModel(
        id="rel_other_02",
        source_entity_id="ent_other_200",
        target_entity_id="ent_other_300",
        relationship_type="ASSOCIATE",
        confidence=0.80,
    )
    session.add_all([rel1, rel_other])

    # 3. Alerts involving target entity
    alert1 = AlertModel(
        id="alert_target_01",
        bank_id=tenant,
        transaction_id="privacy_hash_target_999",
        risk_score=0.91,
    )
    alert_other = AlertModel(
        id="alert_other_02",
        bank_id=other_tenant,
        transaction_id="tx_other_555",
        risk_score=0.30,
    )
    session.add_all([alert1, alert_other])
    session.commit()

    # Pre-condition verification
    assert len(session.execute(select(EntityModel)).scalars().all()) == 2
    assert len(session.execute(select(RelationshipModel)).scalars().all()) == 2
    assert len(session.execute(select(AlertModel)).scalars().all()) == 2

    # Execute GDPR erasure using privacy_id
    engine = AutomatedRetentionEngine(db_session=session)
    erasure = engine.execute_gdpr_right_to_be_forgotten(
        tenant_id=tenant,
        entity_id_hash="privacy_hash_target_999",
        db=session,
    )

    assert erasure.records_erased_count == 3  # 1 entity + 1 relationship + 1 alert
    assert "entities" in erasure.affected_tables
    assert "relationships" in erasure.affected_tables
    assert "alerts" in erasure.affected_tables

    # Confirm rows are physically removed from database
    assert session.execute(select(EntityModel).where(EntityModel.id == "ent_target_primary_100")).scalar_one_or_none() is None
    assert session.execute(select(RelationshipModel).where(RelationshipModel.id == "rel_target_01")).scalar_one_or_none() is None
    assert session.execute(select(AlertModel).where(AlertModel.id == "alert_target_01")).scalar_one_or_none() is None

    # Confirm other tenant records remain untouched
    assert session.execute(select(EntityModel).where(EntityModel.id == "ent_other_200")).scalar_one_or_none() is not None
    assert session.execute(select(RelationshipModel).where(RelationshipModel.id == "rel_other_02")).scalar_one_or_none() is not None
    assert session.execute(select(AlertModel).where(AlertModel.id == "alert_other_02")).scalar_one_or_none() is not None

    session.close()


def test_compliance_api_retention_and_gdpr_endpoints() -> None:
    """Verifies REST endpoints for retention policy config, TTL purge, GDPR erasure, and chain verification."""
    client = TestClient(app)
    tenant = "bank_api_test"

    # 1. Configure policy
    res_cfg = client.post(
        "/api/v1/compliance/retention/policies",
        json={
            "tenant_id": tenant,
            "category": "TRANSACTION_LOGS",
            "ttl_days": 60,
            "erasure_method": "CRYPTOGRAPHIC_ZEROIZATION",
        },
    )
    assert res_cfg.status_code == 200
    data_cfg = res_cfg.json()
    assert data_cfg["tenant_id"] == tenant
    assert data_cfg["category"] == "TRANSACTION_LOGS"
    assert data_cfg["ttl_days"] == 60

    # 2. Get policies
    res_get_pol = client.get(f"/api/v1/compliance/retention/policies?tenant_id={tenant}")
    assert res_get_pol.status_code == 200
    pols = res_get_pol.json()
    assert len(pols) >= 1
    assert any(p["category"] == "TRANSACTION_LOGS" for p in pols)

    # 3. Trigger TTL purge
    res_purge = client.post(
        "/api/v1/compliance/retention/purge",
        json={"tenant_id": tenant},
    )
    assert res_purge.status_code == 200
    purged = res_purge.json()
    assert isinstance(purged, list)
    assert len(purged) >= 1

    # 4. Execute GDPR erasure
    res_erase = client.post(
        "/api/v1/compliance/gdpr/erasure",
        json={
            "tenant_id": tenant,
            "entity_id_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "category": "CUSTOMER_ENTITIES",
        },
    )
    assert res_erase.status_code == 200
    erasure_rec = res_erase.json()
    assert erasure_rec["tenant_id"] == tenant
    assert erasure_rec["category"] == "CUSTOMER_ENTITIES"
    assert len(erasure_rec["erasure_hash"]) == 64

    # 5. Retrieve audit trail
    res_trail = client.get(f"/api/v1/compliance/retention/audit-trail?tenant_id={tenant}")
    assert res_trail.status_code == 200
    trail = res_trail.json()
    assert len(trail) >= 2

    # 6. Verify chain integrity
    res_verify = client.get(f"/api/v1/compliance/retention/audit-trail/verify?tenant_id={tenant}")
    assert res_verify.status_code == 200
    report = res_verify.json()
    assert report["valid"] is True
    assert report["total_records"] >= 2
    assert len(report["last_hash"]) == 64
