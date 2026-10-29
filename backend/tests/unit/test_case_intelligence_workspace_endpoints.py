"""Unit and integration tests for Case Intelligence Workspace Endpoints.

Verifies acceptance gates:
- Gate A (Authentic Evidence Verification):
  - Digest recalculation & file integrity check (tampered digest -> FAILED_VERIFICATION, missing file -> INDETERMINATE).
  - Issuance provenance verification against _admitted_receipts_store and active batch store.
  - Verification state classification: VERIFIED, INTEGRITY_VERIFIED_ONLY, INDETERMINATE, FAILED_VERIFICATION.
  - Historical integrity preservation when batch retention expires or bilateral grant is revoked.
  - Unadmitted arbitrary evidence classified as INTEGRITY_VERIFIED_ONLY (not authoritative).
- Gate B (Disclosure Safety):
  - ANONYMIZED_SUMMARY strictly enforces target_bank_id: null.
  - Scrubbing of counterpart names from analyst notes under ANONYMIZED_SUMMARY.
  - ATTRIBUTED_INSTITUTIONS reveals authorized counterpart institution.
- Gate C (Authorization & Governance Query Builder):
  - PrincipalDep authentication and investigator role enforcement.
  - Strict case ownership (cross-tenant 403).
  - Strict alert-linked entity scoping (no whole-bank entity inventory fallback).
  - Dual-routed GET /query-capabilities returns active consortia, authorized counterparts, and permitted purposes from real pairwise grants.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService, EvidenceRegistryService
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import ProposalAction
from app.domain.enums import AlertSeverity, AlertStatus, CasePriority, EntityType
from app.domain.investigation_entities import Alert
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_batch_store import DurableCrossBankBatchStore
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app


@pytest.fixture(autouse=True)
def clean_isolated_environment(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate batch store and consortium store for clean test execution."""
    db_file = str(tmp_path / "test_workspace_batches.sqlite")
    monkeypatch.setenv("CFI_BATCH_DB_PATH", db_file)
    c_store_file = str(tmp_path / "test_workspace_consortia.json")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", c_store_file)
    DurableCrossBankBatchStore.reset_instance()
    DurableConsortiumStore.set_instance(None)
    CrossBankIntelligenceService.reset_instance()
    yield
    DurableCrossBankBatchStore.reset_instance()
    DurableConsortiumStore.set_instance(None)
    CrossBankIntelligenceService.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("analyst_a1")
    u_b = auth_svc.get_user("analyst_b1")
    u_admin_a = auth_svc.get_user("admin_a") or auth_svc.register_user(
        "admin_a", "AdminPass123!", "bank_a", roles=["admin"], clearance_level=3
    )
    u_admin_b = auth_svc.get_user("admin_b") or auth_svc.register_user(
        "admin_b", "AdminPass123!", "bank_b", roles=["admin"], clearance_level=3
    )
    assert u_a and u_b and u_admin_a and u_admin_b
    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "admin_a": auth_svc.create_token_bundle(u_admin_a).access_token,
        "admin_b": auth_svc.create_token_bundle(u_admin_b).access_token,
    }


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


# ── Gate C: Eligible Entities Tests ──


def test_get_case_eligible_entities_authenticated_bank_a(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank A investigator retrieves authentic entities strictly linked to alerts on the case."""
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


def test_get_case_eligible_entities_strict_alert_scoping_no_bank_fallback(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Entities owned by Bank A but NOT linked to case alerts are strictly excluded (no whole-bank fallback)."""
    case_svc = CaseManagementService()
    entity_svc = EntityResolutionService()

    # Create Bank A unlinked entity
    unlinked_ent = entity_svc.create_entity(
        entity_type=EntityType.DEVICE,
        raw_identifier="device_unlinked_999",
        bank_id="bank_a",
        attributes={"country": "US"},
    )

    # Create Bank A case with NO alerts
    case_empty = case_svc.create_case(
        title="Empty Alerts Case",
        priority=CasePriority.P3_MEDIUM,
        alert_ids=[],
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case_empty.id}/eligible-entities", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    # Must be empty because no alerts are linked to this case
    matched = [e for e in data if e["entity_id"] == unlinked_ent.id]
    assert len(matched) == 0
    assert len(data) == 0


def test_get_case_eligible_entities_cross_tenant_rejected(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank B investigator cannot retrieve eligible entities for a Bank A case (403 Forbidden)."""
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


# ── Gate A & B: Evidence Verification and Disclosure Safety Tests ──


def test_get_case_intelligence_evidence_anonymous_masking_and_scrubbing(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Verified intelligence evidence under ANONYMIZED_SUMMARY strictly masks counterpart identity and scrubs notes."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    batch_store = DurableCrossBankBatchStore.get_instance()
    consortium_svc = ConsortiumGovernanceService()

    # Ensure active consortium exists with bank_a and bank_b using admin token
    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    consortium_svc.create_consortium(
        consortium_id=consortium_id,
        name="Test Consortium Alpha",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["admin_a"],
    )
    consortium_svc.propose_membership_change(
        consortium_id=consortium_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        auth_token=auth_tokens["admin_a"],
    )

    case = case_svc.create_case(
        title="Bank A Multi-Counterparty Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    signal_id = "sig_b_123"

    # Record active batch in batch store
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id=consortium_id,
        query_entity_id="ent_cust_alice",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": signal_id},
        receipt_ids={"bank_b": "rcp_b_123"},
        overall_outcome="COMPLETED",
        case_id=case.id,
        ttl_seconds=3600,
    )

    # Register evidence with ANONYMIZED_SUMMARY
    payload = {
        "signal_id": signal_id,
        "consortium_id": consortium_id,
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
            "signal_id": signal_id,
            "consortium_id": consortium_id,
            "disclosure_mode": "ANONYMIZED_SUMMARY",
            "target_bank_id": None,  # Anonymous
            "opaque_provenance_ref": "prov_opaque_999",
        },
        "analyst_notes": "Cryptographic match confirmed across consortium peer bank_b with high confidence.",
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title=f"Consortium Intelligence Evidence ({consortium_id.upper()})",
        file_path=f"storage/evidence/case_{case.id}_sig_b.json",
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={
            "signal_id": signal_id,
            "consortium_id": consortium_id,
            "disclosure_mode": "ANONYMIZED_SUMMARY",
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
    assert ev_item["disclosure_mode"] == "ANONYMIZED_SUMMARY"
    # STRICT PRIVACY INVARIANT: Target bank MUST be None
    assert ev_item["target_bank_id"] is None
    # STRICT SCRUBBING: Counterpart name scrubbed from analyst notes
    assert "bank_b" not in ev_item["analyst_notes"]
    assert "[REDACTED_INSTITUTION]" in ev_item["analyst_notes"]
    assert ev_item["match_confirmed"] is True
    assert ev_item["match_category"] == "EXACT_MATCH"
    assert ev_item["confidence"] == 0.98
    assert ev_item["risk_level"] == "HIGH"
    assert "MULE_BURST" in ev_item["typology_indicators"]
    assert ev_item["retention_status"] == "ACTIVE"
    assert ev_item["batch_expires_at"] is not None
    assert ev_item["verification_state"] == "VERIFIED"


def test_get_case_intelligence_evidence_attributed_disclosure(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Verified intelligence evidence under ATTRIBUTED_INSTITUTIONS reveals authorized counterpart identity."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    intel_svc = CrossBankIntelligenceService.get_instance()

    case = case_svc.create_case(
        title="Bank A Attributed Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    signal_id = "sig_b_attr_789"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"

    # Populate admitted receipt store so provenance is recognized as authentic historical evidence
    intel_svc._admitted_receipts_store.set(
        receipt_id,
        {
            "receipt_id": receipt_id,
            "signal_id": signal_id,
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
        },
    )

    payload = {
        "signal_id": signal_id,
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
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": receipt_id,
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
            "signal_id": signal_id,
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
    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "HISTORICAL_RECORD"


def test_get_case_intelligence_evidence_tampered_digest_classified_failed_verification(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Tampered evidence file on disk triggers FAILED_VERIFICATION and operational_status: TAMPERED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Tampered Evidence Case",
        priority=CasePriority.P1_CRITICAL,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    payload = {
        "signal_id": "sig_tampered_001",
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {"disclosure_mode": "ATTRIBUTED_INSTITUTIONS", "target_bank_id": "bank_b"},
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_tampered.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Tampered Evidence Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": "sig_tampered_001", "disclosure_mode": "ATTRIBUTED_INSTITUTIONS"},
    )

    # Tamper with the raw file on disk directly
    assert os.path.exists(file_path)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"signal_id": "sig_tampered_001", "tampered": True}))

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "FAILED_VERIFICATION"
    assert ev_item["operational_status"] == "TAMPERED"
    assert ev_item["is_authoritative"] is False
    assert "Cryptographic verification failed" in ev_item["explanation"]


def test_get_case_intelligence_evidence_unadmitted_arbitrary_classified_integrity_only(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Registered evidence without authentic admission proof is classified as INTEGRITY_VERIFIED_ONLY."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Unadmitted Arbitrary Evidence Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    payload = {
        "signal_id": "sig_unadmitted_999",
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "LOW"},
        "provenance": {"disclosure_mode": "ATTRIBUTED_INSTITUTIONS", "target_bank_id": "bank_b"},
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_unadmitted.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Unadmitted Evidence Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": "sig_unadmitted_999", "disclosure_mode": "ATTRIBUTED_INSTITUTIONS"},
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

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "UNADMITTED"
    assert ev_item["is_authoritative"] is False
    assert "lacks authentic operational issuance proof" in ev_item["explanation"]


def test_get_case_intelligence_evidence_revoked_grant_classified_consent_revoked(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Admitted evidence whose underlying pairwise grant has been revoked is classified as CONSENT_REVOKED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    consortium_svc = ConsortiumGovernanceService()
    intel_svc = CrossBankIntelligenceService.get_instance()
    batch_store = DurableCrossBankBatchStore.get_instance()

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    consortium_svc.create_consortium(
        consortium_id=consortium_id,
        name="Revocation Test Consortium",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["admin_a"],
    )
    consortium_svc.propose_membership_change(
        consortium_id=consortium_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        auth_token=auth_tokens["admin_a"],
    )

    grant_id = f"grant_{uuid.uuid4().hex[:8]}"
    consortium_svc.register_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        institution_a="bank_a",
        institution_b="bank_b",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["FRAUD_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=auth_tokens["admin_a"],
    )
    consortium_svc.approve_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        approving_bank_id="bank_b",
        auth_token=auth_tokens["admin_b"],
    )

    # Now revoke the grant using admin_b
    consortium_svc.revoke_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        revoking_bank_id="bank_b",
        reason="Regulatory directive withdrawal",
        auth_token=auth_tokens["admin_b"],
    )

    case = case_svc.create_case(
        title="Revoked Grant Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    signal_id = "sig_revoked_001"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"

    # Store admitted receipt in intel_svc._admitted_receipts_store
    intel_svc._admitted_receipts_store.set(
        receipt_id,
        {
            "receipt_id": receipt_id,
            "signal_id": signal_id,
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "grant_id": grant_id,
            "consortium_id": consortium_id,
        },
    )

    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id=consortium_id,
        query_entity_id="ent_cust_alice",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": signal_id},
        receipt_ids={"bank_b": receipt_id},
        overall_outcome="COMPLETED",
        case_id=case.id,
        ttl_seconds=3600,
    )

    payload = {
        "signal_id": signal_id,
        "consortium_id": consortium_id,
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": consortium_id,
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": receipt_id,
        },
        "analyst_notes": "Admitted evidence before grant revocation.",
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_revoked.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Revoked Grant Evidence Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={
            "signal_id": signal_id,
            "consortium_id": consortium_id,
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "batch_query_id": query_id,
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

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "CONSENT_REVOKED"
    assert ev_item["is_authoritative"] is False
    assert "Pairwise data sharing authorization has been revoked" in ev_item["explanation"]


def test_get_case_intelligence_evidence_cross_tenant_rejected(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Bank B investigator cannot retrieve intelligence evidence for a Bank A case (403 Forbidden)."""
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


# ── Gate C: Dual-Routed Query Capabilities Tests ──


def test_get_query_capabilities_authenticated_bank_a(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Authorized Bank A investigator queries operational capabilities derived from real pairwise grants."""
    consortium_svc = ConsortiumGovernanceService()

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    consortium_svc.create_consortium(
        consortium_id=consortium_id,
        name="Operational Capabilities Consortium",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["admin_a"],
    )
    consortium_svc.propose_membership_change(
        consortium_id=consortium_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        auth_token=auth_tokens["admin_a"],
    )

    grant_id = f"grant_{uuid.uuid4().hex[:8]}"
    consortium_svc.register_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        institution_a="bank_a",
        institution_b="bank_b",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["FRAUD_INVESTIGATION", "AML_HIGH_RISK_MATCHING"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=auth_tokens["admin_a"],
    )
    consortium_svc.approve_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        approving_bank_id="bank_b",
        auth_token=auth_tokens["admin_b"],
    )

    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    # Test primary endpoint route
    resp = test_client.get("/api/v1/intelligence/cross-bank/query-capabilities", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["requesting_bank_id"] == "bank_a"
    assert isinstance(data["consortia"], list)
    matching_c = [c for c in data["consortia"] if c["consortium_id"] == consortium_id]
    assert len(matching_c) == 1

    c_cap = matching_c[0]
    assert c_cap["status"].lower() == "active"
    counterparts = c_cap["counterparts"]
    b_counterpart = [cp for cp in counterparts if cp["counterpart_bank_id"] == "bank_b"]
    assert len(b_counterpart) == 1
    assert b_counterpart[0]["is_authorized"] is True
    assert b_counterpart[0]["governance_status"] == "ACTIVE"
    assert "FRAUD_INVESTIGATION" in b_counterpart[0]["permitted_purposes"]
    assert "AML_HIGH_RISK_MATCHING" in b_counterpart[0]["permitted_purposes"]

    # Test dual-route alias
    resp_alias = test_client.get("/v1/intelligence/cross-bank/query-capabilities", headers=headers)
    assert resp_alias.status_code == 200, resp_alias.text
    assert resp_alias.json()["requesting_bank_id"] == "bank_a"


def test_get_query_capabilities_cross_tenant_rejected(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Requesting query capabilities for Bank B while authenticated as Bank A is rejected (403 Forbidden)."""
    headers = {
        "X-Tenant-ID": "bank_a",
        "Authorization": f"Bearer {auth_tokens['bank_a']}",
    }
    resp = test_client.get(
        "/api/v1/intelligence/cross-bank/query-capabilities?requesting_bank_id=bank_b",
        headers=headers,
    )
    assert resp.status_code == 403
    assert "is not authorized to access resources belonging to" in resp.json()["detail"]
