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

import hashlib
import json
import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceSignal,
    DisclosureMode,
    MatchCategory,
    SanitizedRiskLevel,
)
from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService, EvidenceRegistryService
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumStatus,
    ProposalAction,
)
from app.domain.enums import AlertSeverity, AlertStatus, CasePriority, EntityType
from app.domain.institutional_identity import InstitutionalVerificationRegistry
from app.domain.investigation_entities import Alert
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_batch_store import DurableCrossBankBatchStore
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app
from tests.unit.test_operational_result_admission_contract import (
    SECRET_A,
    SECRET_COORD,
    TEST_SIGNING_IDENTITY_A,
    TEST_SIGNING_IDENTITY_B,
    _build_authentic_test_signal,
    compute_operational_grant_scope_digest,
)


@pytest.fixture(autouse=True)
def clean_isolated_environment(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate batch store, consortium store, and cryptographic identities for clean test execution."""
    db_file = str(tmp_path / "test_workspace_batches.sqlite")
    monkeypatch.setenv("CFI_BATCH_DB_PATH", db_file)
    c_store_file = str(tmp_path / "test_workspace_consortia.json")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", c_store_file)
    monkeypatch.setenv("CFI_INSTITUTION_PUBLIC_KEY_BANK_A", TEST_SIGNING_IDENTITY_A.public_key_hex)
    monkeypatch.setenv("CFI_INSTITUTION_PUBLIC_KEY_BANK_B", TEST_SIGNING_IDENTITY_B.public_key_hex)
    monkeypatch.setenv("CFI_PEER_KEY_BANK_B", TEST_SIGNING_IDENTITY_B.public_key_hex)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SECRET_COORD)
    monkeypatch.setenv("WORKER_AUTH_SECRET", SECRET_A)
    monkeypatch.setenv("COORDINATOR_AUTH_SECRET", SECRET_COORD)
    DurableCrossBankBatchStore.reset_instance()
    DurableConsortiumStore.set_instance(None)
    CrossBankIntelligenceService.reset_instance()
    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_a", TEST_SIGNING_IDENTITY_A.public_key)
    reg.register_public_key("bank_b", TEST_SIGNING_IDENTITY_B.public_key)
    CrossBankIntelligenceService.get_instance().institutional_registry = reg
    yield
    DurableCrossBankBatchStore.reset_instance()
    DurableConsortiumStore.set_instance(None)
    CrossBankIntelligenceService.reset_instance()


def _attach_authentic_test_evidence(
    case_id: str,
    consortium_id: str = "consortium_alpha",
    requesting_bank_id: str = "bank_a",
    target_bank_id: str = "bank_b",
    query_entity_id: str = "ent_cust_alice",
    disclosure_mode: DisclosureMode = "ATTRIBUTED_INSTITUTIONS",
    match_confirmed: bool = True,
    match_category: MatchCategory = "EXACT_MATCH",
    confidence: float = 0.98,
    sanitized_risk_level: SanitizedRiskLevel | None = "HIGH",
    sanitized_typologies: tuple[str, ...] | None = ("BURST_ACTIVITY_OBSERVED",),
    analyst_notes: str | None = None,
    batch_query_id: str | None = None,
    idempotency_key: str | None = None,
    record_in_batch_store: bool = True,
    attach_to_case: bool = True,
) -> tuple[CrossBankIntelligenceSignal, Any, dict[str, Any]]:
    """Genuinely admit and attach authentic cross-bank operational evidence."""
    os.environ["CFI_COORDINATOR_SECRET"] = SECRET_COORD

    intel_svc = CrossBankIntelligenceService.get_instance()
    consortium_svc = ConsortiumGovernanceService()
    batch_store = DurableCrossBankBatchStore.get_instance()

    reg = intel_svc.institutional_registry or InstitutionalVerificationRegistry()
    if requesting_bank_id not in reg._enrolled_keys:
        reg.register_public_key(requesting_bank_id, TEST_SIGNING_IDENTITY_A.public_key)
    if target_bank_id not in reg._enrolled_keys:
        reg.register_public_key(target_bank_id, TEST_SIGNING_IDENTITY_B.public_key)
    intel_svc.institutional_registry = reg
    consortium_svc.institutional_registry = reg

    grant_id = f"grant_{consortium_id}"
    scope_digest = compute_operational_grant_scope_digest(
        grant_id=grant_id,
        revision=1,
        participating_banks=[requesting_bank_id, target_bank_id],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={requesting_bank_id: "EU", target_bank_id: "EU"},
        allowed_jurisdictions=["EU", "US"],
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
    )

    grant_dict = {
        "grant_id": grant_id,
        "authority": "BILATERAL_INSTITUTIONAL_CONSENT",
        "revision": 1,
        "scope_digest": scope_digest,
        "is_active": True,
        "status": "APPROVED",
        "participating_banks": [requesting_bank_id, target_bank_id],
        "institution_a": requesting_bank_id,
        "institution_b": target_bank_id,
        "allowed_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
        "institution_jurisdictions": {requesting_bank_id: "EU", target_bank_id: "EU"},
        "allowed_jurisdictions": ["EU", "US"],
        "allow_cross_border": False,
        "allow_institution_identity_sharing": True,
        "approvals": {
            requesting_bank_id: {"signature": "sig_a", "scope_digest": scope_digest, "revision": 1, "grant_revision": 1},
            target_bank_id: {"signature": "sig_b", "scope_digest": scope_digest, "revision": 1, "grant_revision": 1},
        },
    }

    existing_consortium = consortium_svc.get_consortium(consortium_id)
    if not existing_consortium:
        consortium = Consortium(
            consortium_id=consortium_id,
            name=f"Consortium {consortium_id}",
            status=ConsortiumStatus.ACTIVE,
            members={
                requesting_bank_id: ConsortiumMember(requesting_bank_id),
                target_bank_id: ConsortiumMember(target_bank_id),
            },
            metadata={
                "operational_data_sharing_grant": grant_dict,
                "pairwise_operational_grants": {grant_id: grant_dict},
            },
        )
        consortium_svc.store.save_consortium(consortium)
    else:
        existing_consortium.metadata.setdefault("pairwise_operational_grants", {})[grant_id] = grant_dict
        existing_consortium.metadata["operational_data_sharing_grant"] = grant_dict
        consortium_svc.store.save_consortium(existing_consortium)

    sig, req, tkt, rcp, cp = _build_authentic_test_signal(
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
        match_confirmed=match_confirmed,
        match_category=match_category,
        match_confidence=confidence,
        disclosure_mode=disclosure_mode,
        sanitized_risk_level=sanitized_risk_level,
        sanitized_typologies=sanitized_typologies,
    )
    if disclosure_mode == "ANONYMIZED_SUMMARY":
        sig = sig.model_copy(update={"contributing_institutions_count": 1 if match_confirmed else 0})

    admitted = intel_svc._admit_and_verify_operational_signal(
        signal=sig,
        expected_req=req,
        audited_actor="analyst_a1",
    )
    admitted["admission_status"] = "COMMITTED"
    admitted["publication_status"] = "PUBLISHED"
    admitted["is_attachable"] = True
    intel_svc._signal_store.set(sig.signal_id, admitted)

    if batch_query_id and record_in_batch_store:
        batch_store.record_batch(
            query_id=batch_query_id,
            requesting_bank_id=requesting_bank_id,
            consortium_id=consortium_id,
            query_entity_id=query_entity_id,
            principal_id="analyst_a1",
            principal_tenant_id=requesting_bank_id,
            target_bank_ids=[target_bank_id],
            execution_outcomes={target_bank_id: {"status": "SUCCESS"}},
            successful_signal_ids={target_bank_id: sig.signal_id},
            receipt_ids={target_bank_id: rcp.receipt_id},
            overall_outcome="COMPLETED",
            case_id=case_id,
            ttl_seconds=3600,
        )

    princ = AuthenticatedPrincipal(
        principal_id="analyst_a1",
        tenant_id=requesting_bank_id,
        roles=["analyst"],
        is_authenticated=True,
    )

    attach_res: Any = None
    if attach_to_case:
        attach_res = intel_svc.attach_signal_to_case_evidence(
            case_id=case_id,
            signal_id=sig.signal_id,
            principal=princ,
            notes=analyst_notes,
            batch_query_id=batch_query_id,
            idempotency_key=idempotency_key,
        )

    return sig, attach_res, admitted


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

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    case = case_svc.create_case(
        title="Bank A Multi-Counterparty Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_alice",
    )
    query_id = f"batch_{uuid.uuid4().hex[:12]}"

    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ANONYMIZED_SUMMARY",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.98,
        sanitized_risk_level="HIGH",
        sanitized_typologies=("BURST_ACTIVITY_OBSERVED",),
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    # Directly inject unscrubbed free-text notes into the registered evidence payload
    # to verify read-side deterministic scrubbing of counterpart references
    ev_id = attach_res.evidence_id
    assert isinstance(ev_id, str)
    raw_content = evidence_svc.get_evidence_content(ev_id)
    ev_payload = json.loads(raw_content)
    ev_payload["analyst_notes"] = "Cryptographic match confirmed across consortium peer bank_b with high confidence."
    updated_content = json.dumps(ev_payload, sort_keys=True, indent=2)
    updated_hash = hashlib.sha256(updated_content.encode("utf-8")).hexdigest()

    item = evidence_svc._evidence_store.get(ev_id)
    assert item is not None
    item["content_hash"] = updated_hash
    with open(item["file_path"], "w", encoding="utf-8") as f:
        f.write(updated_content)
    evidence_svc._evidence_store.set(ev_id, item)

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
    assert "BURST_ACTIVITY_OBSERVED" in ev_item["typology_indicators"]
    assert ev_item["retention_status"] == "ACTIVE"
    assert ev_item["batch_expires_at"] is not None
    assert ev_item["verification_state"] == "VERIFIED"


def test_get_case_intelligence_evidence_attributed_disclosure(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Verified intelligence evidence under ATTRIBUTED_INSTITUTIONS reveals authorized counterpart identity."""
    case_svc = CaseManagementService()
    case = case_svc.create_case(
        title="Bank A Attributed Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="PROBABILISTIC_FUZZY",
        confidence=0.85,
        sanitized_risk_level="MEDIUM",
        sanitized_typologies=("STRUCTURING",),
        analyst_notes="Attributed match with partner bank_b.",
        batch_query_id=query_id,
        record_in_batch_store=False,  # Unrecorded in batch store -> RETENTION_EXPIRED
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
    assert ev_item["verification_state"] == "VERIFIED"
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
    consortium_svc = ConsortiumGovernanceService()

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    case = case_svc.create_case(
        title="Revoked Grant Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        sanitized_risk_level="HIGH",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    # Now revoke the grant using admin_b
    grant_id = f"grant_{consortium_id}"
    consortium_svc.revoke_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        revoking_bank_id="bank_b",
        reason="Regulatory directive withdrawal",
        auth_token=auth_tokens["admin_b"],
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

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["operational_status"] == "CONSENT_REVOKED"
    assert ev_item["is_authoritative"] is True
    assert "Historical operational evidence cryptographically verified" in ev_item["explanation"]
    assert "revoked" in ev_item["explanation"]


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
    assert b_counterpart[0]["execution_readiness"] == "UNVERIFIED"
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


def test_adversarial_intact_evidence_with_active_batch_no_admitted_receipt_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Intact evidence referencing a real active batch, but lacking admitted receipt is NOT VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    batch_store = DurableCrossBankBatchStore.get_instance()

    case = case_svc.create_case(
        title="Active Batch Unadmitted Receipt Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    signal_id = "sig_active_batch_no_rcp_001"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"

    # Batch exists in SQLite with successful signal
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="consortium_alpha",
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
    # NOTE: receipt_id is deliberately NOT set in _admitted_receipts_store

    payload = {
        "signal_id": signal_id,
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": receipt_id,
        },
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_no_admitted_rcp.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Active Batch Unadmitted Evidence",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id, "batch_query_id": query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "UNADMITTED"
    assert ev_item["is_authoritative"] is False
    assert "lacks authentic operational issuance receipt" in ev_item["explanation"]


def test_adversarial_intact_evidence_with_unrelated_admitted_receipt_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Intact evidence referencing an admitted receipt issued for a DIFFERENT signal is NOT VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    intel_svc = CrossBankIntelligenceService.get_instance()
    batch_store = DurableCrossBankBatchStore.get_instance()

    case = case_svc.create_case(
        title="Unrelated Admitted Receipt Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    signal_id = "sig_forged_binding_001"
    unrelated_signal_id = "sig_original_different_002"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"

    # Admitted receipt is legitimately registered, but for a DIFFERENT signal
    intel_svc._admitted_receipts_store.set(
        receipt_id,
        {
            "receipt_id": receipt_id,
            "signal_id": unrelated_signal_id,
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "consortium_id": "consortium_alpha",
            "query_entity_id": "ent_cust_alice",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "content_hash": hashlib.sha256(b"dummy_diff_sig").hexdigest(),
        },
    )

    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="consortium_alpha",
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
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": receipt_id,
        },
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_unrelated_rcp.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Unrelated Receipt Evidence",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id, "batch_query_id": query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "PROVENANCE_MISMATCH"
    assert ev_item["is_authoritative"] is False
    assert "signal binding mismatch" in ev_item["explanation"]


def test_adversarial_matching_receipt_id_with_mismatched_scope_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Matching receipt ID but mismatched requesting bank / entity scope is NOT VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    intel_svc = CrossBankIntelligenceService.get_instance()

    case = case_svc.create_case(
        title="Mismatched Scope Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    signal_id = "sig_scope_mismatch_001"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"

    # Admitted receipt has mismatched requesting bank (bank_c instead of bank_a)
    intel_svc._admitted_receipts_store.set(
        receipt_id,
        {
            "receipt_id": receipt_id,
            "signal_id": signal_id,
            "requesting_bank_id": "bank_c",
            "target_bank_id": "bank_b",
            "consortium_id": "consortium_alpha",
            "query_entity_id": "ent_cust_alice",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "content_hash": hashlib.sha256(b"dummy_scope_sig").hexdigest(),
        },
    )

    payload = {
        "signal_id": signal_id,
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": receipt_id,
        },
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_mismatched_scope.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Mismatched Scope Evidence",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["operational_status"] == "PROVENANCE_MISMATCH"
    assert ev_item["is_authoritative"] is False
    assert "scope mismatch" in ev_item["explanation"]


def test_authentic_operational_admission_with_matching_receipt_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Authentic operational evidence with fully consistent admitted receipt and batch is VERIFIED."""
    case_svc = CaseManagementService()

    case = case_svc.create_case(
        title="Authentic Admission Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.98,
        sanitized_risk_level="CRITICAL",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["operational_status"] == "OPERATIONAL"
    assert ev_item["is_authoritative"] is True
    assert ev_item["retention_status"] == "ACTIVE"
    expected_ev_id = getattr(attach_res, "evidence_id", None)
    assert isinstance(expected_ev_id, str)
    assert ev_item["evidence_id"] == expected_ev_id


def test_expired_batch_with_retained_authentic_issuance_verified_historical(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Expired batch cache (>24h TTL) preserves VERIFIED historical state when issuance proof is retained."""
    case_svc = CaseManagementService()

    case = case_svc.create_case(
        title="Expired Batch Historical Retention Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    expired_query_id = f"batch_expired_{uuid.uuid4().hex[:8]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=False,
        match_category="NO_MATCH",
        confidence=0.0,
        sanitized_risk_level="LOW",
        batch_query_id=expired_query_id,
        record_in_batch_store=False,
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["retention_status"] == "RETENTION_EXPIRED"
    assert ev_item["operational_status"] == "HISTORICAL_RECORD"
    assert ev_item["is_authoritative"] is True


def test_expired_batch_without_retained_issuance_proof_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """Expired batch cache without retained authentic issuance proof cannot receive VERIFIED status."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Expired Batch No Proof Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    expired_query_id = f"batch_purged_{uuid.uuid4().hex[:8]}"
    signal_id = f"sig_unverified_{uuid.uuid4().hex[:8]}"

    # Neither batch record nor admitted receipt exists
    payload = {
        "signal_id": signal_id,
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ANONYMIZED_SUMMARY",
        },
        "batch_query_id": expired_query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_no_proof.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Unverified Evidence Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id, "batch_query_id": expired_query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "INTEGRITY_VERIFIED_ONLY"
    assert ev_item["retention_status"] == "RETENTION_EXPIRED"
    assert ev_item["is_authoritative"] is False


def test_query_capabilities_circuit_breaker_open_readiness_unavailable(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """When circuit breaker is in OPEN state, counterpart execution_readiness is UNAVAILABLE."""
    consortium_svc = ConsortiumGovernanceService()
    intel_svc = CrossBankIntelligenceService.get_instance()

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    consortium_svc.create_consortium(
        consortium_id=consortium_id,
        name="CB Readiness Test Consortium",
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

    # Trip the circuit breaker for (consortium_id, bank_a, bank_b)
    from app.domain.circuit_breaker_policy import CounterpartyCircuitBreakerPolicy
    cb_policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=600.0,
    )
    intel_svc.circuit_breaker_store.record_failure(
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        error_category="WORKER_UNAVAILABLE",
        probe_id=None,
        policy=cb_policy,
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get("/api/v1/intelligence/cross-bank/query-capabilities", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    c_match = [c for c in data["consortia"] if c["consortium_id"] == consortium_id][0]
    b_cp = [cp for cp in c_match["counterparts"] if cp["counterpart_bank_id"] == "bank_b"][0]

    assert b_cp["is_authorized"] is True
    assert b_cp["governance_status"] == "ACTIVE"
    assert b_cp["execution_readiness"] == "UNAVAILABLE"


# ── Mandatory Trust Boundary Adversarial Suite (Tests 1-10) ──


def test_adversarial_1_intact_evidence_with_manually_fabricated_receipt_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """1. Intact evidence file with manually fabricated or unregistered receipt record MUST NOT receive VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Fabricated Receipt Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    fabricated_receipt_id = f"rcp_fabricated_{uuid.uuid4().hex[:8]}"
    signal_id = f"sig_fake_{uuid.uuid4().hex[:8]}"
    query_id = f"batch_{uuid.uuid4().hex[:8]}"

    payload = {
        "signal_id": signal_id,
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": fabricated_receipt_id,
        },
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_fabricated_rcp.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Fabricated Receipt Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id, "batch_query_id": query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] != "VERIFIED"
    assert ev_item["verification_state"] in ("INTEGRITY_VERIFIED_ONLY", "INDETERMINATE", "FAILED_VERIFICATION")
    assert ev_item["is_authoritative"] is False


def test_adversarial_2_admitted_signal_with_altered_match_results_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """2. Genuine admitted signal and receipt paired with modified match results in persisted payload is not VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Altered Match Results Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.98,
        sanitized_risk_level="CRITICAL",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    ev_id = getattr(attach_res, "evidence_id", None)
    assert isinstance(ev_id, str)
    ev_rec = evidence_svc._evidence_store.get(ev_id)
    assert ev_rec is not None
    file_path = ev_rec["file_path"]

    # Read original content, modify match_confirmed from True to False
    with open(file_path, encoding="utf-8") as f:
        payload = json.load(f)
    payload["signal_content"]["match_confirmed"] = False

    tampered_content = json.dumps(payload, sort_keys=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(tampered_content)

    # Update evidence store content_hash so raw disk integrity passes but content diverges from authoritative signal
    new_hash = hashlib.sha256(tampered_content.encode("utf-8")).hexdigest()
    ev_rec["content_hash"] = new_hash
    evidence_svc._evidence_store.set(ev_id, ev_rec)

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] != "VERIFIED"
    assert ev_item["verification_state"] in ("FAILED_VERIFICATION", "INDETERMINATE")
    assert ev_item["is_authoritative"] is False
    assert "diverges" in ev_item["explanation"]


def test_adversarial_3_receipt_digest_diverges_from_signal_content_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """3. Admitted receipt whose issuance digest does not match canonical signal content hash is not VERIFIED."""
    case_svc = CaseManagementService()
    intel_svc = CrossBankIntelligenceService.get_instance()

    case = case_svc.create_case(
        title="Divergent Issuance Digest Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.98,
        sanitized_risk_level="CRITICAL",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    rcp_id = admitted["receipt_id"]
    rcp_rec = intel_svc._admitted_receipts_store.get(rcp_id)
    assert rcp_rec is not None
    # Alter content_hash in admitted receipt store to a bogus 64-char hash
    rcp_rec["content_hash"] = "e" * 64
    intel_svc._admitted_receipts_store.set(rcp_id, rcp_rec)

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] != "VERIFIED"
    assert ev_item["verification_state"] in ("FAILED_VERIFICATION", "INDETERMINATE")
    assert ev_item["is_authoritative"] is False
    assert "diverges" in ev_item["explanation"] or "does not match" in ev_item["explanation"]


def test_adversarial_4_generic_registry_bypass_not_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """4. Evidence registered directly via generic registry bypassing cross-bank attachment is not VERIFIED."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Generic Registry Bypass Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    consortium_id = "consortium_alpha"
    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, _, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.95,
        sanitized_risk_level="HIGH",
        batch_query_id=query_id,
        record_in_batch_store=True,
        attach_to_case=False,
    )

    # Bypass attach_signal_to_case_evidence: Register directly through generic evidence registry
    payload = {
        "signal_id": sig.signal_id,
        "consortium_id": consortium_id,
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {
            "match_confirmed": sig.match_confirmed,
            "match_category": sig.match_category,
            "confidence": sig.match_confidence,
            "risk_level": sig.sanitized_risk_level,
        },
        "provenance": {
            "signal_id": sig.signal_id,
            "consortium_id": consortium_id,
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "target_bank_id": "bank_b",
            "receipt_id": admitted["receipt_id"],
        },
        "batch_query_id": query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_bypass.json"
    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Direct Generic Registration Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": sig.signal_id, "batch_query_id": query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] != "VERIFIED"
    assert ev_item["verification_state"] in ("INTEGRITY_VERIFIED_ONLY", "INDETERMINATE")
    assert ev_item["is_authoritative"] is False
    assert "unauthorized generic registry bypass" in ev_item["explanation"] or "attachment proof" in ev_item["explanation"]


def test_adversarial_5_authentic_admitted_signal_attached_through_m65_pipeline_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """5. Authentic admitted signal attached through accepted M6.5 pipeline receives VERIFIED status."""
    case_svc = CaseManagementService()

    case = case_svc.create_case(
        title="M65 Pipeline Verified Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.98,
        sanitized_risk_level="CRITICAL",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["operational_status"] == "OPERATIONAL"
    assert ev_item["is_authoritative"] is True
    assert ev_item["retention_status"] == "ACTIVE"
    expected_ev_id = getattr(attach_res, "evidence_id", None)
    assert isinstance(expected_ev_id, str)
    assert ev_item["evidence_id"] == expected_ev_id


def test_adversarial_6_authentic_historical_evidence_after_batch_expiry_verified(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """6. Authentic historical evidence record after batch expiration receives VERIFIED with retained durable proof."""
    case_svc = CaseManagementService()

    case = case_svc.create_case(
        title="Historical Evidence Retention Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    expired_query_id = f"batch_expired_{uuid.uuid4().hex[:8]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=False,
        match_category="NO_MATCH",
        confidence=0.0,
        sanitized_risk_level="LOW",
        batch_query_id=expired_query_id,
        record_in_batch_store=False,  # Simulating intermediate batch cache expiration/purging
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["retention_status"] == "RETENTION_EXPIRED"
    assert ev_item["operational_status"] == "HISTORICAL_RECORD"
    assert ev_item["is_authoritative"] is True


def test_adversarial_7_unavailable_historical_issuance_proof_classified_integrity_only(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """7. Evidence with expired batch and unavailable issuance proof must receive INTEGRITY_VERIFIED_ONLY or INDETERMINATE."""
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()

    case = case_svc.create_case(
        title="Unavailable Issuance Proof Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    expired_query_id = f"batch_purged_{uuid.uuid4().hex[:8]}"
    signal_id = f"sig_unverified_{uuid.uuid4().hex[:8]}"

    payload = {
        "signal_id": signal_id,
        "consortium_id": "consortium_alpha",
        "requesting_bank_id": "bank_a",
        "query_entity_id": "ent_cust_alice",
        "signal_content": {"match_confirmed": True, "risk_level": "HIGH"},
        "provenance": {
            "signal_id": signal_id,
            "consortium_id": "consortium_alpha",
            "disclosure_mode": "ANONYMIZED_SUMMARY",
        },
        "batch_query_id": expired_query_id,
    }
    content_str = json.dumps(payload, sort_keys=True)
    file_path = f"storage/evidence/case_{case.id}_no_proof_adv.json"

    evidence_svc.register_evidence(
        case_id=case.id,
        evidence_type="CROSS_BANK_INTELLIGENCE",
        title="Unverified Evidence Record",
        file_path=file_path,
        content=content_str,
        uploaded_by="analyst_a1",
        metadata={"signal_id": signal_id, "batch_query_id": expired_query_id},
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] != "VERIFIED"
    assert ev_item["verification_state"] in ("INTEGRITY_VERIFIED_ONLY", "INDETERMINATE")
    assert ev_item["retention_status"] == "RETENTION_EXPIRED"
    assert ev_item["is_authoritative"] is False
    assert "lacks authentic operational issuance" in ev_item["explanation"]


def test_adversarial_8_current_grant_revocation_preserves_historical_authenticity(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """8. Pairwise grant revocation preserves historical evidence authenticity while updating operational status."""
    case_svc = CaseManagementService()
    consortium_svc = ConsortiumGovernanceService()

    consortium_id = f"c_{uuid.uuid4().hex[:8]}"
    case = case_svc.create_case(
        title="Revoked Grant Historical Authenticity Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        sanitized_risk_level="HIGH",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    # Revoke pairwise operational grant
    grant_id = f"grant_{consortium_id}"
    consortium_svc.revoke_pairwise_operational_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        revoking_bank_id="bank_b",
        reason="Regulatory withdrawal",
        auth_token=auth_tokens["admin_b"],
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["operational_status"] == "CONSENT_REVOKED"
    assert ev_item["is_authoritative"] is True
    assert "Historical operational evidence cryptographically verified" in ev_item["explanation"]
    assert "revoked" in ev_item["explanation"]


def test_adversarial_9_anonymized_evidence_no_counterpart_disclosure(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """9. Anonymized evidence record strictly conceals counterpart bank identity in verification projection."""
    case_svc = CaseManagementService()

    case = case_svc.create_case(
        title="Anonymized Masking Audit Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    sig, attach_res, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ANONYMIZED_SUMMARY",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        confidence=0.91,
        sanitized_risk_level="HIGH",
        batch_query_id=query_id,
        record_in_batch_store=True,
    )

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    ev_item = data[0]

    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["disclosure_mode"] == "ANONYMIZED_SUMMARY"
    assert ev_item["target_bank_id"] is None
    # Verify counterpart identity bank_b never appears in projected representation
    item_str = json.dumps(ev_item).lower()
    assert "bank_b" not in item_str


def test_adversarial_10_same_case_idempotent_replay_unchanged_evidence(
    test_client: TestClient, auth_tokens: dict[str, str]
) -> None:
    """10. Idempotent re-attachment of same signal to same case preserves original evidence without duplicate."""
    case_svc = CaseManagementService()
    intel_svc = CrossBankIntelligenceService.get_instance()

    case = case_svc.create_case(
        title="Idempotent Replay Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
        assigned_to="analyst_a1",
    )

    query_id = f"batch_{uuid.uuid4().hex[:12]}"
    idem_key = f"idem_{uuid.uuid4().hex[:16]}"
    sig, attach_res1, admitted = _attach_authentic_test_evidence(
        case_id=case.id,
        consortium_id="consortium_alpha",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_cust_alice",
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        match_confirmed=True,
        sanitized_risk_level="HIGH",
        analyst_notes="Consistent replay note",
        batch_query_id=query_id,
        record_in_batch_store=True,
        idempotency_key=idem_key,
    )

    # Replay attachment with same idempotency key and signal
    princ = AuthenticatedPrincipal(
        principal_id="analyst_a1",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )
    attach_res2 = intel_svc.attach_signal_to_case_evidence(
        case_id=case.id,
        signal_id=sig.signal_id,
        principal=princ,
        notes="Consistent replay note",
        batch_query_id=query_id,
        idempotency_key=idem_key,
    )

    is_dup = getattr(attach_res2, "is_duplicate_submission", False)
    ev_id2 = getattr(attach_res2, "evidence_id", None)
    ev_id1 = getattr(attach_res1, "evidence_id", None)
    assert isinstance(ev_id2, str)
    assert isinstance(ev_id1, str)
    assert is_dup is True
    assert ev_id2 == ev_id1

    headers = {"X-Tenant-ID": "bank_a", "Authorization": f"Bearer {auth_tokens['bank_a']}"}
    resp = test_client.get(f"/api/v1/cases/{case.id}/intelligence-evidence", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1  # No duplicate evidence records
    ev_item = data[0]

    assert ev_item["evidence_id"] == ev_id1
    assert ev_item["verification_state"] == "VERIFIED"
    assert ev_item["operational_status"] == "OPERATIONAL"
    assert ev_item["is_authoritative"] is True
