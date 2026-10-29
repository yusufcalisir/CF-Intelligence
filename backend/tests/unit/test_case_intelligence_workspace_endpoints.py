"""Unit and integration tests for Case Intelligence Workspace Endpoints.

Verifies:
1. Authorized Bank A investigator retrieves legitimate institution-owned entities for a case.
2. Cross-tenant access to case eligible entities is strictly rejected (403 Forbidden).
3. Non-existent case returns 404 Not Found.
4. Authorized Bank A investigator retrieves persisted cross-bank intelligence evidence.
5. Anonymous disclosure mode strictly masks counterpart identity (target_bank_id is None).
6. Attributed disclosure mode preserves authorized counterpart identity (e.g. bank_b).
7. Active batch retains ACTIVE retention status with expiration timestamp.
8. Expired/purged batch truthfully reflects RETENTION_EXPIRED while immutable evidence ledger is intact.
9. Cross-tenant access to case intelligence evidence is strictly rejected (403 Forbidden).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService, EvidenceRegistryService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.enums import AlertSeverity, AlertStatus, CasePriority, EntityType
from app.domain.investigation_entities import Alert
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_batch_store import DurableCrossBankBatchStore
from app.main import app


@pytest.fixture(autouse=True)
def clean_isolated_environment(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate batch store and registries for clean test execution."""
    db_file = str(tmp_path / "test_workspace_batches.sqlite")
    monkeypatch.setenv("CFI_BATCH_DB_PATH", db_file)
    DurableCrossBankBatchStore.reset_instance()
    yield
    DurableCrossBankBatchStore.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("analyst_a1")
    u_b = auth_svc.get_user("analyst_b1")
    assert u_a and u_b
    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
    }


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


def test_get_case_eligible_entities_authenticated_bank_a(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank A investigator retrieves authentic entities linked to alerts and owned by Bank A."""
    case_svc = CaseManagementService()
    alert_svc = AlertIntelligenceService()
    entity_svc = EntityResolutionService()

    # Create Bank A entity
    ent_a = entity_svc.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="cust_alice_bank_a_999",
        bank_id="bank_a",
        attributes={"country": "US"},
    )

    # Create Bank A alert involving ent_a
    alert_a = Alert(
        id=f"alert_{uuid.uuid4().hex[:12]}",
        bank_id="bank_a",
        transaction_id="tx_test_999",
        risk_score=78.5,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["RAPID_VELOCITY"],
        confidence=0.92,
        involved_entity_ids=[ent_a.id],
    )
    alert_svc.create_alert(alert_a)

    # Create Bank A case linking alert_a
    case_a = case_svc.create_case(
        title="Test Investigation Case A",
        priority=CasePriority.P2_HIGH,
        alert_ids=[alert_a.id],
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case_a.id}/eligible-entities", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    matched = [e for e in data if e["entity_id"] == ent_a.id]
    assert len(matched) == 1
    assert matched[0]["bank_id"] == "bank_a"
    assert matched[0]["source_alert_id"] == alert_a.id
    assert matched[0]["entity_type"] == "customer"


def test_get_case_eligible_entities_cross_tenant_rejected(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank B investigator cannot retrieve eligible entities for a Bank A case."""
    case_svc = CaseManagementService()
    case_a = case_svc.create_case(
        title="Confidential Bank A Case",
        priority=CasePriority.P1_CRITICAL,
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )

    headers = {
        "X-Tenant-ID": "bank_b",
        "Authorization": f"Bearer {auth_tokens['bank_b']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case_a.id}/eligible-entities", headers=headers)
    assert resp.status_code == 403
    assert "is not authorized to access resources belonging to" in resp.json()["detail"]


def test_get_case_eligible_entities_not_found(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Querying a non-existent case returns 404 Not Found."""
    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get("/api/v1/cases/non_existent_case_xyz/eligible-entities", headers=headers)
    assert resp.status_code == 404
    assert "Case not found" in resp.json()["detail"]


def test_get_case_intelligence_evidence_anonymous_masking(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Verified intelligence evidence under ANONYMOUS_FLAG strictly masks counterpart identity."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    batch_store = DurableCrossBankBatchStore.get_instance()

    case = case_svc.create_case(
        title="Bank A Multi-Counterparty Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"

    # Record active batch in batch store
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="consortium_alpha",
        query_entity_id="ent_cust_alice",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}, "bank_c": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": "sig_b_123", "bank_c": "sig_c_456"},
        receipt_ids={"bank_b": "rcp_b_123", "bank_c": "rcp_c_456"},
        overall_outcome="COMPLETED",
        case_id=case.id,
        ttl_seconds=3600,
    )

    # Register evidence with ANONYMOUS_FLAG
    payload = {
        "signal_id": "sig_b_123",
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {
            "match_confirmed": True,
            "match_category": "EXACT_MATCH",
            "confidence": 0.98,
            "risk_level": "HIGH",
            "typology_indicators": ["MULE_BURST"],
        },
        "provenance": {
            "signal_id": "sig_b_123",
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ANONYMOUS_FLAG",
            "target_bank_id": None,  # Anonymous
            "opaque_provenance_ref": "prov_opaque_999",
        },
        "analyst_notes": "Cryptographic match confirmed across consortium peer.",
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Consortium Intelligence Evidence (CONSORTIUM_ALPHA)",
        file_path=f"storage/evidence/case_{case.id}_sig_b.json",
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={
            "signal_id": "sig_b_123",
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ANONYMOUS_FLAG",
            "batch_query_id": query_id,
            "is_authoritative": True,
            "operational_status": "OPERATIONAL",
        },
    )

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["case_id"] == case.id
    assert ev_item["batch_query_id"] == query_id
    assert ev_item["disclosure_mode"] == "ANONYMOUS_FLAG"
    # STRICT PRIVACY INVARIANT: Target bank MUST be None
    assert ev_item["target_bank_id"] is None
    assert ev_item["match_confirmed"] is True
    assert ev_item["match_category"] == "EXACT_MATCH"
    assert ev_item["confidence"] == 0.98
    assert ev_item["risk_level"] == "HIGH"
    assert "MULE_BURST" in ev_item["typology_indicators"]
    assert ev_item["retention_status"] == "ACTIVE"
    assert ev_item["batch_expires_at"] is not None
    assert ev_item["verification_state"] == "AUTHENTIC_VERIFIED"


def test_get_case_intelligence_evidence_attributed_disclosure(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Verified intelligence evidence under ATTRIBUTED_INSTITUTIONS reveals authorized counterpart identity."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Bank A Attributed Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    payload = {
        "signal_id": "sig_b_attr_789",
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {
            "match_confirmed": True,
            "match_category": "PROBABILISTIC_FUZZY",
            "confidence": 0.85,
            "risk_level": "MEDIUM",
            "typology_indicators": ["STRUCTURING"],
        },
        "provenance": {
            "signal_id": "sig_b_attr_789",
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
        },
        "analyst_notes": "Attributed match with partner bank_b.",
        "batch_query_id": "batch_attr_789",
    }
    content_str = json.dumps(payload, sort_keys=True)

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Consortium Intelligence Evidence (CONSORTIUM_ALPHA)",
        file_path=f"storage/evidence/case_{case.id}_sig_attr.json",
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={
            "signal_id": "sig_b_attr_789",
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "batch_query_id": "batch_attr_789",
        },
    )

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["disclosure_mode"] == "ATTRIBUTED_INSTITUTIONS"
    assert ev_item["target_bank_id"] == "bank_b"
    assert ev_item["match_confirmed"] is True
    assert ev_item["match_category"] == "PROBABILISTIC_FUZZY"
    # Batch was not recorded in batch store, so retention status reflects RETENTION_EXPIRED
    assert ev_item["retention_status"] == "RETENTION_EXPIRED"
    assert ev_item["batch_expires_at"] is None


def test_get_case_intelligence_evidence_cross_tenant_rejected(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank B investigator cannot retrieve intelligence evidence for a Bank A case."""
    case_svc = CaseManagementService()
    case = case_svc.create_case(
        title="Bank A Private Intelligence Case",
        priority=CasePriority.P1_CRITICAL,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    headers = {
        "X-Tenant-ID": "bank_b",
        "Authorization": f"Bearer {auth_tokens['bank_b']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 403
    assert "is not authorized to access resources belonging to" in resp.json()["detail"]
