"""Unit test suite for Authoritative Cross-Bank Case Evidence Admission and Tenant Isolation (M6.5 Task 1).

Verifies:
1. Genuinely authenticated institutional principal and permitted evidence-management role required.
2. Caller-supplied actor or bank parameters cannot fabricate principals or bypass authentication.
3. Authoritative, pre-existing case belonging to caller's institution required (no temporary case creation).
4. Strict tenant isolation between caller institution and case-owning institution.
5. Acceptance restricted to verified, authoritative, cryptographically admitted, non-synthetic operational signals.
6. Rejection of unfinalized, revoked, rejected, prototype, simulated, and synthetic signals.
7. Preservation of provenance and counterparty identity respecting consortium disclosure modes (ATTRIBUTED vs ANONYMIZED).
8. Durable idempotent deduplication and optimistic concurrency (CAS) conflict recovery.
9. Complete, attributable audit trail for both accepted and rejected evidence attachment attempts.
10. Containment of partial multi-counterparty outcomes (no evidence manufactured for failed/blocked counterparties).
"""

from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import json
import uuid
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from app.application.schemas.cross_bank_intelligence import CrossBankEvidenceAttachResponse
from app.application.services.case_service import (
    CaseManagementService,
    CaseNotFoundError,
    EvidenceRegistryService,
    InvalidCaseTransitionError,
    TerminalCaseImmutableError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.pilot_operations_service import PilotOperationsService
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CrossBankIdempotencyConflictError,
)
from app.domain.enums import CaseStatus
from app.domain.pilot_operations import PilotEntity, PilotOperationalConfig, PilotStatus
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

SHARED_JWT_SECRET = "super-secret-jwt-key-for-test-suites-minimum-32-chars-long"


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_file = tmp_path / "consortium_admission_store.json"
    account_file = tmp_path / "account_admission_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")
    monkeypatch.setattr(
        "app.infrastructure.security.auth_service.hash_password",
        lambda p, rounds=12: "$2b$04$testseedmockhashforevidenceadmissiononly000000000000",
    )

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    CrossBankIntelligenceService._instance = None
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    CrossBankIntelligenceService._instance = None


@pytest.fixture
def admission_env() -> dict[str, Any]:
    gov_svc = ConsortiumGovernanceService()
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService(case_service=case_svc)
    entity_svc = EntityResolutionService()
    auth_svc = AuthenticationService.get_instance()

    # Use seeded users
    u_admin = auth_svc.get_user("admin")
    u_a = auth_svc.get_user("analyst_a1")
    u_b = auth_svc.get_user("analyst_b1")
    u_auditor = auth_svc.get_user("auditor")
    assert u_admin and u_a and u_b and u_auditor

    tokens = {
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "unprivileged": auth_svc.create_token_bundle(u_auditor).access_token,
    }

    principals = {
        "bank_a": AuthenticatedPrincipal(
            principal_id="analyst_a1",
            tenant_id="bank_a",
            roles=["analyst", "investigator"],
            is_authenticated=True,
        ),
        "bank_b": AuthenticatedPrincipal(
            principal_id="analyst_b1",
            tenant_id="bank_b",
            roles=["analyst", "investigator"],
            is_authenticated=True,
        ),
        "privileged_bank_b": AuthenticatedPrincipal(
            principal_id="admin_b",
            tenant_id="bank_b",
            roles=["super_admin", "cross_bank_investigator", "compliance_auditor"],
            is_authenticated=True,
        ),
        "unprivileged": AuthenticatedPrincipal(
            principal_id="auditor",
            tenant_id="bank_a",
            roles=["auditor"],
            is_authenticated=True,
        ),
        "no_tenant": AuthenticatedPrincipal(
            principal_id="orphan_user",
            tenant_id=None,
            roles=["analyst"],
            is_authenticated=True,
        ),
        "unauthenticated": AuthenticatedPrincipal(
            principal_id="anonymous",
            tenant_id="bank_a",
            roles=["analyst"],
            is_authenticated=False,
        ),
    }

    # Setup consortium using admin token
    gov_svc.create_consortium(
        consortium_id="c_admission_test",
        name="Case Evidence Admission Consortium",
        founder_bank_id="bank_a",
        auth_token=tokens["admin"],
    )
    gov_svc.add_member(
        consortium_id="c_admission_test",
        bank_id="bank_b",
        auth_token=tokens["admin"],
    )

    cbi_svc = CrossBankIntelligenceService(
        consortium_service=gov_svc,
        case_service=case_svc,
        evidence_service=evidence_svc,
        entity_service=entity_svc,
    )

    return {
        "gov_svc": gov_svc,
        "case_svc": case_svc,
        "evidence_svc": evidence_svc,
        "entity_svc": entity_svc,
        "cbi_svc": cbi_svc,
        "tokens": tokens,
        "principals": principals,
        "consortium_id": "c_admission_test",
    }


def _create_verified_operational_signal_record(
    cbi_svc: CrossBankIntelligenceService,
    consortium_id: str = "c_admission_test",
    requesting_bank_id: str = "bank_a",
    target_bank_id: str = "bank_b",
    query_entity_id: str = "entity_alpha",
    disclosure_mode: str = "ATTRIBUTED_INSTITUTIONS",
    contributing_institutions: list[str] | None = None,
    assurance_level: str = "REVIEWED_ESTABLISHED_PSI_VERIFIED",
    execution_mode: str = "VERIFIED_OPERATIONAL",
    is_authoritative: bool = True,
    is_synthetic: bool = False,
    admission_status: str | None = "COMMITTED",
    publication_status: str | None = "PUBLISHED",
    is_attachable: bool | None = True,
    expires_delta_hours: int = 24,
    register_in_admitted_receipts: bool = True,
    receipt_id: str | None = None,
    ticket_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    now = datetime.datetime.now(UTC)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = receipt_id or f"rcp_{uuid.uuid4().hex}"
    tkt_id = ticket_id or f"tkt_{uuid.uuid4().hex}"
    contribs = contributing_institutions if contributing_institutions is not None else [target_bank_id]

    receipt_obj = {
        "receipt_id": rcp_id,
        "worker_signature": "sig_worker_a_valid_ecdsap256",
        "coordinator_signature": "sig_coord_valid_ecdsap256",
        "match_confirmed": len(contribs) > 0,
        "disclosure_mode": disclosure_mode,
        "contributing_institutions": contribs if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else [],
    }
    ticket_obj = {
        "ticket_id": tkt_id,
        "grant_id": "grant_test",
        "grant_revision": 1,
        "grant_scope_digest": "digest_test",
    }

    sig_data = {
        "signal_id": sig_id,
        "consortium_id": consortium_id,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else None,
        "query_entity_id": query_entity_id,
        "privacy_hash_truncated": "hash_sample_truncated",
        "match_confirmed": len(contribs) > 0,
        "match_category": "EXACT_MATCH" if contribs else "NO_MATCH",
        "match_confidence": 1.0 if contribs else 0.0,
        "contributing_institutions_count": len(contribs),
        "contributing_institutions": contribs if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else [],
        "disclosure_mode": disclosure_mode,
        "sanitized_risk_level": "HIGH",
        "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"],
        "graph_context": {
            "has_incident_cross_bank_correlations": len(contribs) > 0,
            "sanitized_degree_band": "BILATERAL" if contribs else "ISOLATED",
        },
        "provenance": {
            "receipt_id": rcp_id,
            "ticket_id": tkt_id,
            "target_bank_id": target_bank_id if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else None,
            "execution_mode": execution_mode,
            "assurance_level": assurance_level,
            "receipt": receipt_obj,
            "ticket": ticket_obj,
        },
        "execution_mode": execution_mode,
        "attestation_status": "HARDWARE_ENCLAVE_ATTESTED",
        "is_authoritative": is_authoritative,
        "is_synthetic": is_synthetic,
        "assurance_level": assurance_level,
    }
    content_hash = hashlib.sha256(
        json.dumps(sig_data, sort_keys=True).encode("utf-8")
    ).hexdigest()

    record: dict[str, Any] = {
        "signal_id": sig_id,
        "signal_data": sig_data,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id,
        "consortium_id": consortium_id,
        "query_entity_id": query_entity_id,
        "issued_to_principal": f"worker:{requesting_bank_id}",
        "issued_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(hours=expires_delta_hours)).isoformat(),
        "content_hash": content_hash,
        "is_authoritative": is_authoritative,
        "is_synthetic": is_synthetic,
        "operational_status": "OPERATIONAL" if is_authoritative and not is_synthetic else "NON_OPERATIONAL_SYNTHETIC",
        "admission_status": admission_status,
        "publication_status": publication_status,
        "is_attachable": is_attachable,
        "assurance_level": assurance_level,
        "execution_mode": execution_mode,
        "disclosure_mode": disclosure_mode,
        "receipt_id": rcp_id,
        "ticket_id": tkt_id,
        "receipt": receipt_obj,
        "ticket": ticket_obj,
        "attached_to_cases": [],
    }

    if (
        register_in_admitted_receipts
        and is_authoritative
        and not is_synthetic
        and execution_mode == "VERIFIED_OPERATIONAL"
        and admission_status == "COMMITTED"
        and publication_status == "PUBLISHED"
        and is_attachable is True
    ):
        cbi_svc._admitted_receipt_ids.add(rcp_id)
        cbi_svc._admitted_receipts_store.set(
            rcp_id,
            {
                "admitted_at": now.isoformat(),
                "signal_id": sig_id,
                "receipt_id": rcp_id,
                "content_hash": content_hash,
                "requesting_bank_id": requesting_bank_id,
                "target_bank_id": target_bank_id,
                "consortium_id": consortium_id,
                "query_entity_id": query_entity_id,
                "execution_mode": execution_mode,
                "assurance_level": assurance_level,
            },
        )

    cbi_svc._signal_store.set(sig_id, record)
    return sig_id, record


class TestAuthoritativeEvidenceAdmission:
    def test_authenticated_analyst_can_attach_verified_operational_signal_to_owned_case(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Verified operational signal attaches cleanly to an open owned case by authenticated principal."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Verified Fraud Ring Investigation", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal,
            notes="Attaching verified operational correlation with Bank B.",
        )

        assert isinstance(resp, CrossBankEvidenceAttachResponse)
        assert resp.case_id == case.id
        assert resp.signal_id == sig_id
        assert resp.is_duplicate_submission is False
        assert resp.timeline_event_recorded is True
        assert len(resp.content_hash) == 64

        # Evidence was registered in case
        updated_case = case_svc.get_case(case.id)
        assert updated_case is not None
        assert resp.evidence_id in updated_case.evidence_ids

    def test_missing_authentication_rejected_even_if_actor_or_bank_supplied(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Caller cannot supply actor, caller_bank_id, or case_bank to bypass mandatory authentication."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]

        case = case_svc.create_case(title="Authentication Gate Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # Supplying actor/bank without principal or token MUST raise PermissionError
        with pytest.raises(PermissionError, match="Authentication required"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                actor="malicious_spoofed_analyst",
                caller_bank_id="bank_a",
                case_bank="bank_a",
            )

    def test_unauthenticated_anonymous_principal_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Unauthenticated principal is rejected fail-closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal = admission_env["principals"]["unauthenticated"]

        case = case_svc.create_case(title="Anonymous Gate Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(PermissionError, match="Unauthenticated principal rejected"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal,
            )

    def test_principal_without_tenant_association_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Principal without tenant_id cannot attach evidence."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal = admission_env["principals"]["no_tenant"]

        case = case_svc.create_case(title="Tenant Check Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(PermissionError, match="Institutional tenant_id is required"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal,
            )

    def test_principal_with_unpermitted_role_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Principal lacking an approved intelligence role cannot attach evidence."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal = admission_env["principals"]["unprivileged"]

        case = case_svc.create_case(title="Role Check Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(PermissionError, match="lacks permitted role"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal,
            )

    def test_nonexistent_case_rejected_even_if_case_bank_supplied(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Caller cannot manufacture a temporary Case by supplying case_bank for a non-existent case_id."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        principal = admission_env["principals"]["bank_a"]
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(CaseNotFoundError, match="not found"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id="completely_nonexistent_case_999",
                signal_id=sig_id,
                principal=principal,
                case_bank="bank_a",
            )

    def test_cross_tenant_evidence_attachment_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Bank B analyst cannot attach evidence to Bank A's case."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_b = admission_env["principals"]["bank_b"]

        case_a = case_svc.create_case(title="Bank A Exclusive Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(PermissionError, match="Tenant isolation violation"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case_a.id,
                signal_id=sig_id,
                principal=principal_b,
            )

    def test_privileged_bank_b_principal_cannot_attach_to_bank_a_case(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Privileged Bank B principal (super_admin / cross_bank_investigator) cannot attach evidence to Bank A's case."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        privileged_b = admission_env["principals"]["privileged_bank_b"]
        audit_chain = ImmutableAuditChain.get_instance()

        case_a = case_svc.create_case(title="Bank A Highly Sensitive Case", bank_id="bank_a")
        sig_id_a, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(PermissionError, match="Tenant isolation violation.*Authoritative case evidence writes require caller-bank ownership"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case_a.id,
                signal_id=sig_id_a,
                principal=privileged_b,
            )

        # Zero evidence mutation
        assert len(evidence_svc.get_case_evidence(case_a.id)) == 0
        updated_case = case_svc.get_case(case_a.id)
        assert updated_case is not None
        assert len(updated_case.evidence_ids) == 0

        # Attributable rejection audit event recorded
        last_event = audit_chain.get_snapshot()[-1]
        assert last_event.event_type == "CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHMENT_REJECTED"
        assert "bank_b" in last_event.actor_id

    def test_cross_tenant_signal_binding_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Signal issued to Bank B cannot be attached to Bank A's case even if Bank A requests it."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case_a = case_svc.create_case(title="Bank A Case", bank_id="bank_a")
        sig_id_b, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_b")

        with pytest.raises(PermissionError, match="Cross-tenant signal violation"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case_a.id,
                signal_id=sig_id_b,
                principal=principal_a,
            )

    def test_simulated_research_signal_rejected_from_authoritative_ledger(
        self, admission_env: dict[str, Any]
    ) -> None:
        """SIMULATED_RESEARCH signal cannot enter authoritative case evidence ledger."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Investigation Dossier", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            execution_mode="SIMULATED_RESEARCH",
            is_authoritative=False,
            is_synthetic=True,
        )

        with pytest.raises(ConsortiumSecurityError, match="Simulated research signals cannot be attached"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_protocol_prototype_signal_rejected_from_authoritative_ledger(
        self, admission_env: dict[str, Any]
    ) -> None:
        """PROTOCOL_PROTOTYPE signal cannot enter authoritative case evidence ledger."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Prototype Rejection Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            assurance_level="PROTOCOL_PROTOTYPE",
            execution_mode="RESEARCH_PROTOTYPE",
        )

        with pytest.raises(ConsortiumSecurityError, match="Protocol prototype matching results cannot enter case evidence"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_uncommitted_admission_status_signal_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Signal with admission_status != COMMITTED is rejected."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Uncommitted Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            admission_status="PENDING_REVIEW",
        )

        with pytest.raises(ConsortiumSecurityError, match="admission status is 'PENDING_REVIEW', not COMMITTED"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_unfinalized_publication_status_signal_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Signal with publication_status != PUBLISHED is rejected."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Unpublished Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            publication_status="PUBLICATION_PREPARED",
        )

        with pytest.raises(ConsortiumSecurityError, match="publication status is 'PUBLICATION_PREPARED', not PUBLISHED"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_tampered_content_hash_signal_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Signal whose stored content hash does not match canonical digest is rejected."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Tamper Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # Tamper payload
        rec["signal_data"]["match_category"] = "MALICIOUSLY_FORGED"
        cbi_svc._signal_store.set(sig_id, rec)

        with pytest.raises(ConsortiumSecurityError, match="canonical content digest mismatch"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_expired_signal_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Expired operational signal cannot be attached."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Expired Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            expires_delta_hours=-2,
        )

        with pytest.raises(ValueError, match="has expired"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_terminal_closed_case_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Attachment into finalized/closed terminal case fails with TerminalCaseImmutableError."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Terminal Closed Case", bank_id="bank_a")
        from app.application.services.case_service import _case_to_dict

        case.status = CaseStatus.CLOSED_CONFIRMED
        case_svc._cases.set(case.id, _case_to_dict(case))

        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        with pytest.raises(TerminalCaseImmutableError, match="is finalized in terminal state"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_idempotent_duplicate_attachment_returns_identical_record(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Attaching same signal twice is idempotent and does not produce duplicate events or records."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Idempotency Test Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        resp1 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            idempotency_key="idemp_key_101",
        )
        assert resp1.is_duplicate_submission is False
        assert resp1.timeline_event_recorded is True

        resp2 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            idempotency_key="idemp_key_101",
        )
        assert resp2.is_duplicate_submission is True
        assert resp2.timeline_event_recorded is False
        assert resp2.evidence_id == resp1.evidence_id

        updated_case = case_svc.get_case(case.id)
        assert updated_case is not None
        assert updated_case.evidence_ids.count(resp1.evidence_id) == 1

    def test_cas_concurrency_conflict_recovery_returns_idempotent_result(
        self, admission_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CAS conflict occurs during register_evidence, it recovers idempotently if evidence exists."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="CAS Conflict Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # First attach succeeds
        resp1 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
        )

        # Mock register_evidence to raise InvalidCaseTransitionError
        def mock_register(*args: Any, **kwargs: Any):
            raise InvalidCaseTransitionError("Precondition failed: concurrent modification")

        monkeypatch.setattr(evidence_svc, "register_evidence", mock_register)

        # Subsequent call catches InvalidCaseTransitionError, detects existing evidence, returns duplicate
        resp2 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
        )
        assert resp2.is_duplicate_submission is True
        assert resp2.evidence_id == resp1.evidence_id

    def test_provenance_and_disclosure_mode_contract(
        self, admission_env: dict[str, Any]
    ) -> None:
        """ATTRIBUTED mode retains target counterpart; ANONYMIZED mode strips attributed institutions."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case_attr = case_svc.create_case(title="Attributed Case", bank_id="bank_a")
        sig_attr, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        )
        resp_attr = cbi_svc.attach_signal_to_case_evidence(
            case_id=case_attr.id,
            signal_id=sig_attr,
            principal=principal_a,
        )
        ev_attr = evidence_svc.get_evidence(resp_attr.evidence_id)
        assert ev_attr is not None
        assert ev_attr["metadata"]["disclosure_mode"] == "ATTRIBUTED_INSTITUTIONS"

        case_anon = case_svc.create_case(title="Anonymized Case", bank_id="bank_a")
        sig_anon, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ANONYMOUS_SUMMARY",
        )
        resp_anon = cbi_svc.attach_signal_to_case_evidence(
            case_id=case_anon.id,
            signal_id=sig_anon,
            principal=principal_a,
        )
        ev_anon = evidence_svc.get_evidence(resp_anon.evidence_id)
        assert ev_anon is not None
        assert ev_anon["metadata"]["disclosure_mode"] == "ANONYMOUS_SUMMARY"

    def test_audit_chain_records_events_for_both_accepted_and_rejected_attempts(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Every accepted and rejected attempt records an attributable event in ImmutableAuditChain."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]
        audit_chain = ImmutableAuditChain.get_instance()
        init_len = len(audit_chain.get_snapshot())

        case = case_svc.create_case(title="Audit Verification Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # 1. Accepted attachment
        cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
        )
        events_after_accept = audit_chain.get_snapshot()
        assert len(events_after_accept) > init_len
        accept_event = events_after_accept[-1]
        assert accept_event.event_type == "CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHED"
        assert accept_event.actor_id == "user:analyst_a1@bank_a"
        assert accept_event.details["case_id"] == case.id

        # 2. Rejected attachment (e.g. simulated signal)
        sim_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            execution_mode="SIMULATED_RESEARCH",
            is_authoritative=False,
            is_synthetic=True,
        )
        with pytest.raises(ConsortiumSecurityError):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sim_id,
                principal=principal_a,
            )

        events_after_reject = audit_chain.get_snapshot()
        reject_event = events_after_reject[-1]
        assert reject_event.event_type == "CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHMENT_REJECTED"
        assert reject_event.actor_id == "user:analyst_a1@bank_a"
        assert reject_event.details["error_type"] == "ConsortiumSecurityError"
        assert "Simulated research signals cannot be attached" in reject_event.details["rejection_reason"]

    def test_partial_multi_counterparty_cannot_manufacture_evidence_for_failed_counterparty(
        self, admission_env: dict[str, Any]
    ) -> None:
        """In a multi-target query where Bank B succeeds and Bank C fails, evidence cannot be attached for Bank C."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Multi-Counterparty Partition Case", bank_id="bank_a")

        # Bank B operational signal exists
        sig_id_b, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
        )
        # Bank C failed / blocked (no published operational signal exists in _signal_store)
        missing_sig_c = f"sig_failed_bank_c_{uuid.uuid4().hex}"

        # Attaching Bank B succeeds
        resp_b = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id_b,
            principal=principal_a,
        )
        assert resp_b.signal_id == sig_id_b

        # Attempting to attach non-existent / failed signal for Bank C fails closed
        with pytest.raises(ValueError, match="Authoritative signal .* not found"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=missing_sig_c,
                principal=principal_a,
            )

    def test_forged_simulated_signal_with_recomputed_hash_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Store record produced by flipping simulated signal flags and recomputing hash is rejected."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Forgery Defense Case", bank_id="bank_a")

        # 1. Create a simulated research signal
        sig_id, sim_record = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            execution_mode="SIMULATED_RESEARCH",
            is_authoritative=False,
            is_synthetic=True,
            register_in_admitted_receipts=False,
        )

        # 2. Forger flips flags in server store and recomputes SHA-256
        sim_record["is_authoritative"] = True
        sim_record["is_synthetic"] = False
        sim_record["execution_mode"] = "VERIFIED_OPERATIONAL"
        sim_record["admission_status"] = "COMMITTED"
        sim_record["publication_status"] = "PUBLISHED"
        sim_record["is_attachable"] = True
        sim_record["signal_data"]["execution_mode"] = "VERIFIED_OPERATIONAL"
        sim_record["signal_data"]["is_authoritative"] = True
        sim_record["signal_data"]["is_synthetic"] = False
        sim_record["content_hash"] = hashlib.sha256(
            json.dumps(sim_record["signal_data"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        # Note: receipt is NOT registered in _admitted_receipts_store and no published pilot reservation exists
        cbi_svc._signal_store.set(sig_id, sim_record)

        # 3. Attachment must fail closed
        with pytest.raises(ConsortiumSecurityError, match="Authoritative issuance verification failed.*Defensible evidence requires verified operational execution"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_missing_admission_or_publication_states_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Absent, null, or incomplete admission/publication states fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Incomplete State Case", bank_id="bank_a")

        # 1. Missing admission_status (None)
        sig_id_no_adm, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            admission_status=None,
        )
        with pytest.raises(ConsortiumSecurityError, match="admission status is 'None', not COMMITTED"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_no_adm,
                principal=principal_a,
            )

        # 2. Missing publication_status (None)
        sig_id_no_pub, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            publication_status=None,
        )
        with pytest.raises(ConsortiumSecurityError, match="publication status is 'None', not PUBLISHED"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_no_pub,
                principal=principal_a,
            )

        # 3. Missing is_attachable (False or None)
        sig_id_no_att, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            is_attachable=False,
        )
        with pytest.raises(ConsortiumSecurityError, match="marked non-authoritative or unattachable"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_no_att,
                principal=principal_a,
            )

    def test_contradictory_signal_data_flags_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Contradictory flags between record and signal_data fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Contradiction Defense Case", bank_id="bank_a")
        sig_id, record = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # Record claims authoritative, but signal_data claims non-authoritative
        record["is_authoritative"] = True
        record["signal_data"]["is_authoritative"] = False
        record["content_hash"] = hashlib.sha256(
            json.dumps(record["signal_data"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        cbi_svc._signal_store.set(sig_id, record)

        with pytest.raises(ConsortiumSecurityError, match="signal_data lacks affirmative is_authoritative=True"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_idempotency_conflict_same_key_different_signal(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction C: Reusing idempotency key for a differing signal raises CrossBankIdempotencyConflictError."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Idempotency Conflict Case", bank_id="bank_a")
        sig_id_1, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")
        sig_id_2, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # 1. Attach signal 1 with idempotency key
        resp1 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id_1,
            principal=principal_a,
            idempotency_key="exclusive_idemp_key_001",
        )
        assert resp1.signal_id == sig_id_1
        assert resp1.is_duplicate_submission is False

        # 2. Reusing same idempotency key for signal 2 MUST raise CrossBankIdempotencyConflictError
        with pytest.raises(CrossBankIdempotencyConflictError, match="Idempotency conflict for key 'exclusive_idemp_key_001'"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_2,
                principal=principal_a,
                idempotency_key="exclusive_idemp_key_001",
            )

        # Verify evidence ledger count remains 1 and evidence_id from resp1 is not falsified for signal 2
        ev_list = evidence_svc.get_case_evidence(case.id)
        assert len(ev_list) == 1
        assert ev_list[0]["metadata"]["signal_id"] == sig_id_1

    def test_cas_concurrency_conflict_recovery_with_different_signal_raises_idempotency_conflict(
        self, admission_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Correction C: CAS recovery path detects idempotency key mismatch with differing signal and raises conflict."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="CAS Conflict Idempotency Case", bank_id="bank_a")
        sig_id_1, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")
        sig_id_2, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # Mock register_evidence to simulate a race where another process committed a differing signal under the same key
        orig_register = evidence_svc.register_evidence

        def mock_register(*args: Any, **kwargs: Any):
            orig_register(
                case_id=case.id,
                evidence_type="CROSS_BANK_INTELLIGENCE",
                title="Concurrent attachment",
                file_path=f"storage/evidence/case_{case.id}_concurrent_{uuid.uuid4().hex[:8]}.json",
                content="{}",
                uploaded_by=principal_a.principal_id,
                metadata={"idempotency_key": "cas_idemp_key_002", "signal_id": sig_id_1},
            )
            raise InvalidCaseTransitionError("Precondition failed: concurrent modification")

        monkeypatch.setattr(evidence_svc, "register_evidence", mock_register)

        # Attempt to attach signal 2 under the same key; CAS recovery must catch conflict
        with pytest.raises(CrossBankIdempotencyConflictError, match="during concurrent submission on case"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_2,
                principal=principal_a,
                idempotency_key="cas_idemp_key_002",
            )

    def test_disclosure_policy_anonymized_mode_persisted_evidence_zero_counterpart_leak(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction D: Anonymized disclosure mode recursively excludes prohibited counterpart identities from persisted evidence."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Anonymized Projection Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ANONYMOUS_SUMMARY",
        )

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
        )

        ev = evidence_svc.get_evidence(resp.evidence_id)
        assert ev is not None
        assert Path(ev["file_path"]).exists()

        persisted_content = json.loads(Path(ev["file_path"]).read_text(encoding="utf-8"))

        # Recursive check that "bank_b" is nowhere in the persisted JSON structure
        def assert_no_forbidden_counterpart(data: Any, forbidden: str) -> None:
            if isinstance(data, str):
                assert forbidden.lower() not in data.lower(), (
                    f"Forbidden counterpart identity '{forbidden}' leaked in persisted string: '{data}'"
                )
            elif isinstance(data, dict):
                for k, v in data.items():
                    assert forbidden.lower() not in k.lower(), (
                        f"Forbidden counterpart identity '{forbidden}' leaked in dictionary key: '{k}'"
                    )
                    assert_no_forbidden_counterpart(v, forbidden)
            elif isinstance(data, list):
                for item in data:
                    assert_no_forbidden_counterpart(item, forbidden)

        assert_no_forbidden_counterpart(persisted_content, "bank_b")

        # Verify safe projected properties
        sig_content = persisted_content["signal_content"]
        assert sig_content["target_bank_id"] is None
        assert sig_content["contributing_institutions"] == []
        assert sig_content["match_confirmed"] is True
        assert sig_content["match_category"] == "EXACT_MATCH"
        assert sig_content["sanitized_risk_level"] == "HIGH"
        assert sig_content["assurance_level"] == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
        assert persisted_content["provenance"]["target_bank_id"] is None
        assert persisted_content["provenance"]["contributing_institutions"] == []

    def test_disclosure_policy_attributed_mode_persisted_evidence_allows_authorized_counterpart(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction D: Attributed disclosure mode permits authorized counterpart identity in persisted evidence."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Attributed Projection Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        )

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
        )

        ev = evidence_svc.get_evidence(resp.evidence_id)
        assert ev is not None
        persisted_content = json.loads(Path(ev["file_path"]).read_text(encoding="utf-8"))

        sig_content = persisted_content["signal_content"]
        assert sig_content["target_bank_id"] == "bank_b"
        assert sig_content["contributing_institutions"] == ["bank_b"]
        assert persisted_content["provenance"]["target_bank_id"] == "bank_b"
        assert persisted_content["provenance"]["contributing_institutions"] == ["bank_b"]

    def test_adversarial_stolen_receipt_attachment_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Storing receipt R from authentic signal A onto signal B fails closed even if R is in memory."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Stolen Receipt Defense Case", bank_id="bank_a")

        # 1. Authentic signal A admitted with receipt R
        sig_id_a, rec_a = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
        )
        receipt_r = rec_a["receipt_id"]
        assert receipt_r in cbi_svc._admitted_receipt_ids

        # 2. Fabricate signal B pointing to receipt R with affirmative flags
        sig_id_b = f"sig_stolen_{uuid.uuid4().hex}"
        sig_data_b = dict(rec_a["signal_data"])
        sig_data_b["signal_id"] = sig_id_b
        sig_data_b["provenance"] = dict(rec_a["signal_data"]["provenance"])
        sig_data_b["provenance"]["receipt_id"] = receipt_r
        content_hash_b = hashlib.sha256(
            json.dumps(sig_data_b, sort_keys=True).encode("utf-8")
        ).hexdigest()

        rec_b = dict(rec_a)
        rec_b["signal_id"] = sig_id_b
        rec_b["signal_data"] = sig_data_b
        rec_b["content_hash"] = content_hash_b
        rec_b["receipt_id"] = receipt_r
        cbi_svc._signal_store.set(sig_id_b, rec_b)

        # 3. Attachment of B must fail closed: receipt R is bound to signal A, not B
        with pytest.raises(ConsortiumSecurityError, match="Cryptographic admission binding mismatch"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_b,
                principal=principal_a,
            )

    def test_adversarial_tampered_signal_content_with_recomputed_hash_rejected_by_trusted_issuance_digest(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Modifying authentic signal content and recomputing hash in store fails against trusted admission digest."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Mutated Content Defense Case", bank_id="bank_a")

        # 1. Authentic signal A admitted with receipt R
        sig_id_a, rec_a = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
        )

        # 2. Mutate signal data content and recompute hash in signal store
        rec_a["signal_data"]["match_category"] = "HIGH_CONFIDENCE_MATCH"
        rec_a["signal_data"]["sanitized_risk_level"] = "CRITICAL"
        recomputed_hash = hashlib.sha256(
            json.dumps(rec_a["signal_data"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        rec_a["content_hash"] = recomputed_hash
        cbi_svc._signal_store.set(sig_id_a, rec_a)

        # 3. Attachment must fail closed because recomputed_hash != trusted_content_hash in admitted_receipts_store
        with pytest.raises(ConsortiumSecurityError, match="diverges from cryptographically admitted issuance digest"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id_a,
                principal=principal_a,
            )

    def test_idempotency_same_key_different_notes_raises_conflict(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Same idempotency key with materially altered notes raises CrossBankIdempotencyConflictError."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Idempotency Material Conflict Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # 1. Attach with initial note
        resp1 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            idempotency_key="idemp_key_notes_test",
            notes="Initial investigation finding: High risk correlation.",
        )
        assert resp1.is_duplicate_submission is False

        # 2. Replay with identical note succeeds idempotently
        resp2 = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            idempotency_key="idemp_key_notes_test",
            notes="Initial investigation finding: High risk correlation.",
        )
        assert resp2.is_duplicate_submission is True
        assert resp2.evidence_id == resp1.evidence_id

        # 3. Reuse same key with altered note raises typed conflict
        with pytest.raises(CrossBankIdempotencyConflictError, match="altered material attachment payload"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
                idempotency_key="idemp_key_notes_test",
                notes="Altered conflicting note: Cleared of suspicion.",
            )

    def test_cas_concurrency_conflict_recovery_with_matching_fingerprint_returns_duplicate(
        self, admission_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Correction B: CAS recovery path detects matching fingerprint under same key and returns duplicate."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="CAS Concurrency Match Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        orig_register = evidence_svc.register_evidence
        matching_fp = CrossBankIntelligenceService._compute_evidence_attachment_fingerprint(
            case_id=case.id,
            signal_id=sig_id,
            notes="Identical Concurrent Note",
        )

        def mock_register(*args: Any, **kwargs: Any):
            orig_register(
                case_id=case.id,
                evidence_type="CROSS_BANK_INTELLIGENCE",
                title="Concurrent attachment",
                file_path=f"storage/evidence/case_{case.id}_match_{uuid.uuid4().hex[:8]}.json",
                content="{}",
                uploaded_by=principal_a.principal_id,
                metadata={
                    "idempotency_key": "cas_match_key_001",
                    "signal_id": sig_id,
                    "attachment_fingerprint": matching_fp,
                },
            )
            raise InvalidCaseTransitionError("Precondition failed: concurrent modification")

        monkeypatch.setattr(evidence_svc, "register_evidence", mock_register)

        # Attempt to attach with matching note; CAS recovery detects duplicate and succeeds
        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            idempotency_key="cas_match_key_001",
            notes="Identical Concurrent Note",
        )
        assert resp.is_duplicate_submission is True
        assert resp.signal_id == sig_id

    def test_cas_concurrency_conflict_recovery_with_differing_notes_raises_idempotency_conflict(
        self, admission_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Correction B: CAS recovery path detects altered notes under same key and raises conflict."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="CAS Concurrency Notes Conflict Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        orig_register = evidence_svc.register_evidence
        committed_fp = CrossBankIntelligenceService._compute_evidence_attachment_fingerprint(
            case_id=case.id,
            signal_id=sig_id,
            notes="Committed Note by Worker 1",
        )

        def mock_register(*args: Any, **kwargs: Any):
            orig_register(
                case_id=case.id,
                evidence_type="CROSS_BANK_INTELLIGENCE",
                title="Concurrent attachment",
                file_path=f"storage/evidence/case_{case.id}_concurrent_{uuid.uuid4().hex[:8]}.json",
                content="{}",
                uploaded_by=principal_a.principal_id,
                metadata={
                    "idempotency_key": "cas_notes_key_001",
                    "signal_id": sig_id,
                    "attachment_fingerprint": committed_fp,
                },
            )
            raise InvalidCaseTransitionError("Precondition failed: concurrent modification")

        monkeypatch.setattr(evidence_svc, "register_evidence", mock_register)

        # Attempt to attach under same key with different notes; CAS recovery must catch fingerprint conflict
        with pytest.raises(CrossBankIdempotencyConflictError, match="altered material attachment payload"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
                idempotency_key="cas_notes_key_001",
                notes="Conflicting Note by Worker 2",
            )

    def test_real_concurrent_evidence_attachments_with_independent_service_instances(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Independent service instances executing concurrent attachments on shared storage."""
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        gov_svc: ConsortiumGovernanceService = admission_env["gov_svc"]
        entity_svc: EntityResolutionService = admission_env["entity_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        inst_1 = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            case_service=case_svc,
            evidence_service=evidence_svc,
            entity_service=entity_svc,
        )
        inst_2 = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            case_service=case_svc,
            evidence_service=evidence_svc,
            entity_service=entity_svc,
        )

        case = case_svc.create_case(title="Real Concurrency Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(inst_1, requesting_bank_id="bank_a")

        # Part 1: Concurrent identical attachment
        results: list[CrossBankEvidenceAttachResponse] = []
        errors: list[Exception] = []

        def worker_call(service: CrossBankIntelligenceService, key: str, note: str) -> None:
            try:
                resp = service.attach_signal_to_case_evidence(
                    case_id=case.id,
                    signal_id=sig_id,
                    principal=principal_a,
                    idempotency_key=key,
                    notes=note,
                )
                results.append(resp)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_call, inst_1, "real_concurrent_key_001", "Identical note")
            fut2 = executor.submit(worker_call, inst_2, "real_concurrent_key_001", "Identical note")
            concurrent.futures.wait([fut1, fut2])

        assert len(errors) == 0
        assert len(results) == 2
        assert results[0].evidence_id == results[1].evidence_id
        assert results[0].signal_id == sig_id
        assert results[1].signal_id == sig_id

        case_evidence = evidence_svc.get_case_evidence(case.id)
        assert len(case_evidence) == 1
        assert case_evidence[0]["id"] == results[0].evidence_id

        # Part 2: Concurrent conflicting attachment with same key but differing notes
        results_conflict: list[CrossBankEvidenceAttachResponse] = []
        errors_conflict: list[Exception] = []

        case_2 = case_svc.create_case(title="Real Concurrency Conflict Case", bank_id="bank_a")
        sig_id_2, _ = _create_verified_operational_signal_record(inst_1, requesting_bank_id="bank_a")

        def worker_call_conflict(service: CrossBankIntelligenceService, key: str, note: str) -> None:
            try:
                resp = service.attach_signal_to_case_evidence(
                    case_id=case_2.id,
                    signal_id=sig_id_2,
                    principal=principal_a,
                    idempotency_key=key,
                    notes=note,
                )
                results_conflict.append(resp)
            except Exception as exc:
                errors_conflict.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_call_conflict, inst_1, "conflict_concurrent_key", "Note from Thread 1")
            fut2 = executor.submit(worker_call_conflict, inst_2, "conflict_concurrent_key", "Note from Thread 2")
            concurrent.futures.wait([fut1, fut2])

        assert len(results_conflict) == 1
        assert len(errors_conflict) == 1
        assert isinstance(errors_conflict[0], CrossBankIdempotencyConflictError)

    def test_anonymized_evidence_projection_zero_counterpart_leak_with_injected_identity_text(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction C: Deliberately injected counterpart text in typologies or provenance is completely excluded in anonymized mode."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        gov_svc: ConsortiumGovernanceService = admission_env["gov_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        def _setup_active_grant(consortia: Any, proposals: Any) -> None:
            c = consortia["c_admission_test"]
            if not hasattr(c, "metadata") or c.metadata is None:
                c.metadata = {}
            c.metadata["operational_data_sharing_grant"] = {
                "grant_id": "grant_bilateral_bank_a_bank_b",
                "is_active": True,
                "status": "ACTIVE",
                "revision": 1,
                "approvals": {"bank_a": True, "bank_b": True},
                "revoked_by_institutions": [],
            }

        gov_svc._store.atomic_mutate(_setup_active_grant)

        case = case_svc.create_case(title="Adversarial Anonymized Projection Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ANONYMOUS_SUMMARY",
        )

        # Inject counterpart-identifying strings into typology indicators and provenance references
        rec["signal_data"]["sanitized_typology_indicators"] = [
            "CROSS_BANK_ENTITY_OBSERVED",
            "INJECTED_FLAG_TARGET_BANK_B",
            "SUSPICIOUS_NODE_BANK_B",
            "UNRECOGNIZED_UNSAFE_TYPOLOGY",
        ]
        rec["signal_data"]["provenance"]["ticket_id"] = "ticket_issued_to_bank_b"
        rec["signal_data"]["provenance"]["grant_id"] = "grant_bilateral_bank_a_bank_b"
        rec["signal_data"]["provenance"]["grant_revision"] = 1
        rec["signal_data"]["provenance"]["receipt_id"] = "rcp_exec_bank_b"
        rec["ticket_id"] = "ticket_issued_to_bank_b"
        rec["grant_id"] = "grant_bilateral_bank_a_bank_b"
        rec["grant_revision"] = 1
        rec["receipt_id"] = "rcp_exec_bank_b"
        rec["content_hash"] = hashlib.sha256(
            json.dumps(rec["signal_data"], sort_keys=True).encode("utf-8")
        ).hexdigest()

        cbi_svc._admitted_receipts_store.set(
            "rcp_exec_bank_b",
            {
                "admitted_at": datetime.datetime.now(UTC).isoformat(),
                "signal_id": sig_id,
                "receipt_id": "rcp_exec_bank_b",
                "content_hash": rec["content_hash"],
                "requesting_bank_id": "bank_a",
                "target_bank_id": "bank_b",
                "consortium_id": "c_admission_test",
                "query_entity_id": rec.get("query_entity_id"),
                "execution_mode": "VERIFIED_OPERATIONAL",
                "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
            },
        )
        cbi_svc._signal_store.set(sig_id, rec)

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            notes="Analyst reviewing zero-counterpart leak projection.",
        )

        ev = evidence_svc.get_evidence(resp.evidence_id)
        assert ev is not None
        persisted_content = json.loads(Path(ev["file_path"]).read_text(encoding="utf-8"))

        def assert_no_forbidden_counterpart(data: Any, forbidden: str) -> None:
            if isinstance(data, str):
                assert forbidden.lower() not in data.lower(), (
                    f"Forbidden counterpart identity '{forbidden}' leaked in persisted string: '{data}'"
                )
            elif isinstance(data, dict):
                for k, v in data.items():
                    assert forbidden.lower() not in k.lower(), (
                        f"Forbidden counterpart identity '{forbidden}' leaked in dictionary key: '{k}'"
                    )
                    assert_no_forbidden_counterpart(v, forbidden)
            elif isinstance(data, list):
                for item in data:
                    assert_no_forbidden_counterpart(item, forbidden)

        assert_no_forbidden_counterpart(persisted_content, "bank_b")

        sig_content = persisted_content["signal_content"]
        assert sig_content["sanitized_typology_indicators"] == ["CROSS_BANK_ENTITY_OBSERVED"]
        assert sig_content["target_bank_id"] is None
        assert sig_content["contributing_institutions"] == []

    def test_adversarial_missing_digest_in_admission_record_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Admission record lacking a content digest must fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Missing Digest Test Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        # Corrupt admission record to have missing content_hash
        rcp_id = rec["receipt_id"]
        adm_entry = cbi_svc._admitted_receipts_store.get(rcp_id)
        assert adm_entry is not None
        adm_entry["content_hash"] = None
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_entry)

        with pytest.raises(ConsortiumSecurityError, match="lacks a valid, independently established issuance digest"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_adversarial_malformed_digest_in_admission_record_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Malformed or truncated digest in admission registry must fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Malformed Digest Test Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        rcp_id = rec["receipt_id"]
        adm_entry = cbi_svc._admitted_receipts_store.get(rcp_id)
        assert adm_entry is not None
        adm_entry["content_hash"] = "short_hash_123"
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_entry)

        with pytest.raises(ConsortiumSecurityError, match="lacks a valid, independently established issuance digest"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_adversarial_mismatched_or_missing_scope_bindings_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Missing or mismatched consortium, requesting bank, or counterpart scope must fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Scope Verification Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")
        rcp_id = rec["receipt_id"]
        base_adm = dict(cbi_svc._admitted_receipts_store.get(rcp_id))

        # 1. Missing consortium scope
        adm_no_consortium = dict(base_adm)
        adm_no_consortium["consortium_id"] = None
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_no_consortium)
        with pytest.raises(ConsortiumSecurityError, match="consortium scope 'None' does not match"):
            cbi_svc.attach_signal_to_case_evidence(case_id=case.id, signal_id=sig_id, principal=principal_a)

        # 2. Mismatched consortium scope
        adm_wrong_consortium = dict(base_adm)
        adm_wrong_consortium["consortium_id"] = "other_consortium"
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_wrong_consortium)
        with pytest.raises(ConsortiumSecurityError, match="consortium scope 'other_consortium' does not match"):
            cbi_svc.attach_signal_to_case_evidence(case_id=case.id, signal_id=sig_id, principal=principal_a)

        # 3. Missing requesting bank scope
        adm_no_requesting = dict(base_adm)
        adm_no_requesting["requesting_bank_id"] = None
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_no_requesting)
        with pytest.raises(ConsortiumSecurityError, match="requesting bank scope 'None' does not match"):
            cbi_svc.attach_signal_to_case_evidence(case_id=case.id, signal_id=sig_id, principal=principal_a)

        # 4. Mismatched counterpart (target bank) scope
        adm_wrong_target = dict(base_adm)
        adm_wrong_target["target_bank_id"] = "bank_c"
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_wrong_target)
        with pytest.raises(ConsortiumSecurityError, match="counterpart scope 'bank_c' does not match"):
            cbi_svc.attach_signal_to_case_evidence(case_id=case.id, signal_id=sig_id, principal=principal_a)

        # 5. Missing counterpart scope
        adm_no_target = dict(base_adm)
        adm_no_target["target_bank_id"] = None
        cbi_svc._admitted_receipts_store.set(rcp_id, adm_no_target)
        with pytest.raises(ConsortiumSecurityError, match="counterpart scope 'None' does not match"):
            cbi_svc.attach_signal_to_case_evidence(case_id=case.id, signal_id=sig_id, principal=principal_a)

    def test_adversarial_historic_pilot_record_lacking_immutable_digest_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Historic pilot reservation lacking independently stored content_hash must fail closed."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]
        pilot_svc = PilotOperationsService.get_instance()

        case = case_svc.create_case(title="Historic Pilot Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        res_id = f"res_historic_{uuid.uuid4().hex[:8]}"
        p_id = "pilot_historic_test"

        pilot_svc.store.save_pilot(
            PilotEntity(
                config=PilotOperationalConfig(
                    pilot_id=p_id,
                    consortium_id="c_admission_test",
                    participating_institutions=["bank_a", "bank_b"],
                ),
                status=PilotStatus.ACTIVE,
            )
        )
        with pilot_svc.store._lock, pilot_svc.store._get_connection() as conn:
            conn.execute(
                """INSERT INTO pilot_query_reservations (
                    reservation_id, pilot_id, requesting_bank_id, target_bank_id, purpose,
                    status, reserved_at, finalized_at, published_at, receipt_id, signal_id, content_hash
                ) VALUES (?, ?, ?, ?, ?, 'PUBLISHED', ?, ?, ?, ?, ?, NULL);""",
                (
                    res_id, p_id, "bank_a", "bank_b", "AML_INVESTIGATION",
                    datetime.datetime.now(UTC).isoformat(),
                    datetime.datetime.now(UTC).isoformat(),
                    datetime.datetime.now(UTC).isoformat(),
                    rec["receipt_id"], sig_id,
                )
            )
            conn.commit()

        rec["reservation_id"] = res_id
        rec["pilot_id"] = p_id
        cbi_svc._signal_store.set(sig_id, rec)

        cbi_svc._admitted_receipts_store.delete(rec["receipt_id"])

        with pytest.raises(ConsortiumSecurityError, match="lacks a valid, independently established issuance digest"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_adversarial_pilot_record_with_tampered_content_and_recomputed_hash_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction A: Pilot record with modified content and recomputed mutable hash must be rejected against durable pilot digest."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        principal_a = admission_env["principals"]["bank_a"]
        pilot_svc = PilotOperationsService.get_instance()

        case = case_svc.create_case(title="Pilot Tampering Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(cbi_svc, requesting_bank_id="bank_a")

        original_hash = rec["content_hash"]
        res_id = f"res_tamper_{uuid.uuid4().hex[:8]}"
        p_id = "pilot_tamper_test"

        pilot_svc.store.save_pilot(
            PilotEntity(
                config=PilotOperationalConfig(
                    pilot_id=p_id,
                    consortium_id="c_admission_test",
                    participating_institutions=["bank_a", "bank_b"],
                ),
                status=PilotStatus.ACTIVE,
            )
        )
        with pilot_svc.store._lock, pilot_svc.store._get_connection() as conn:
            conn.execute(
                """INSERT INTO pilot_query_reservations (
                    reservation_id, pilot_id, requesting_bank_id, target_bank_id, purpose,
                    status, reserved_at, finalized_at, published_at, receipt_id, signal_id, content_hash
                ) VALUES (?, ?, ?, ?, ?, 'PUBLISHED', ?, ?, ?, ?, ?, ?);""",
                (
                    res_id, p_id, "bank_a", "bank_b", "AML_INVESTIGATION",
                    datetime.datetime.now(UTC).isoformat(),
                    datetime.datetime.now(UTC).isoformat(),
                    datetime.datetime.now(UTC).isoformat(),
                    rec["receipt_id"], sig_id, original_hash,
                )
            )
            conn.commit()

        rec["reservation_id"] = res_id
        rec["pilot_id"] = p_id

        # Adversary tampers with signal_data and recomputes the mutable content_hash
        rec["signal_data"]["match_confidence"] = 0.1234
        recomputed = hashlib.sha256(json.dumps(rec["signal_data"], sort_keys=True).encode("utf-8")).hexdigest()
        rec["content_hash"] = recomputed
        cbi_svc._signal_store.set(sig_id, rec)

        cbi_svc._admitted_receipts_store.delete(rec["receipt_id"])

        with pytest.raises(ConsortiumSecurityError, match="diverges from cryptographically admitted issuance digest"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
            )

    def test_anonymized_evidence_projection_zero_identifying_tokens_with_predictable_ids(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction B: Predictable institution-bearing IDs do not expose raw names or recoverable tokens in anonymized mode."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Predictable IDs Test Case", bank_id="bank_a")
        sig_id, rec = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ANONYMOUS_SUMMARY",
            receipt_id="rcp_predictable_bank_b_target_999",
            ticket_id="ticket_predictable_bank_b_target_999",
        )

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            notes="Analyst verifying no predictable tokens persist in anonymous summary",
        )

        ev = evidence_svc.get_evidence(resp.evidence_id)
        assert ev is not None
        persisted_raw = Path(ev["file_path"]).read_text(encoding="utf-8")
        persisted_json = json.loads(persisted_raw)

        # 1. No raw "bank_b" in persisted content
        assert "bank_b" not in persisted_raw.lower()

        # 2. No recoverable unsalted hashes of predictable tokens
        pred_tokens = [
            "rcp_predictable_bank_b_target_999",
            "ticket_predictable_bank_b_target_999",
            "grant_test",
        ]
        for tok in pred_tokens:
            full_h = hashlib.sha256(tok.encode("utf-8")).hexdigest()
            assert full_h not in persisted_raw
            assert full_h[:16] not in persisted_raw

        # 3. Provenance exposes only independent opaque reference
        prov = persisted_json["provenance"]
        assert prov["target_bank_id"] is None
        assert prov["receipt_id"] is None
        assert prov["ticket_id"] is None
        assert prov["grant_id"] is None
        assert prov.get("opaque_provenance_ref") is not None
        assert prov["opaque_provenance_ref"].startswith("prov_ref_")

    def test_negative_anonymous_notes_with_counterparty_reference_rejected(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction C: Negative test - notes referencing counterparty are rejected under ANONYMOUS_SUMMARY."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Negative Notes Test Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ANONYMOUS_SUMMARY",
        )

        with pytest.raises(ConsortiumSecurityError, match="violat.*anonymity.*policy|counterpart.*identifier"):
            cbi_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=sig_id,
                principal=principal_a,
                notes="Investigation confirmed by bank_b",
            )

        # Verify no evidence was registered on the case
        case_evs = evidence_svc.get_case_evidence(case.id)
        assert len(case_evs) == 0

    def test_attributed_mode_allows_ordinary_analyst_notes_with_counterparty(
        self, admission_env: dict[str, Any]
    ) -> None:
        """Correction C: Attributed disclosure mode preserves ordinary analyst notes when permitted by governance."""
        cbi_svc: CrossBankIntelligenceService = admission_env["cbi_svc"]
        case_svc: CaseManagementService = admission_env["case_svc"]
        evidence_svc: EvidenceRegistryService = admission_env["evidence_svc"]
        principal_a = admission_env["principals"]["bank_a"]

        case = case_svc.create_case(title="Attributed Notes Test Case", bank_id="bank_a")
        sig_id, _ = _create_verified_operational_signal_record(
            cbi_svc,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        )

        resp = cbi_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sig_id,
            principal=principal_a,
            notes="Investigation confirmed by bank_b",
        )

        assert resp.evidence_id is not None
        ev = evidence_svc.get_evidence(resp.evidence_id)
        assert ev is not None
        persisted = json.loads(Path(ev["file_path"]).read_text(encoding="utf-8"))
        assert persisted["analyst_notes"] == "Investigation confirmed by bank_b"
        assert persisted["provenance"]["target_bank_id"] == "bank_b"

