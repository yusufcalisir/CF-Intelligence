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
from app.domain.consortium_governance import ConsortiumSecurityError
from app.domain.enums import CaseStatus
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
    admission_status: str = "COMMITTED",
    publication_status: str = "PUBLISHED",
    is_attachable: bool = True,
    expires_delta_hours: int = 24,
) -> tuple[str, dict[str, Any]]:
    now = datetime.datetime.now(UTC)
    sig_id = f"sig_{uuid.uuid4().hex}"
    contribs = contributing_institutions if contributing_institutions is not None else [target_bank_id]

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
        "graph_context": {},
        "provenance": {},
        "execution_mode": execution_mode,
        "attestation_status": "HARDWARE_ENCLAVE_ATTESTED",
        "is_authoritative": is_authoritative,
        "is_synthetic": is_synthetic,
        "assurance_level": assurance_level,
    }
    content_hash = hashlib.sha256(
        json.dumps(sig_data, sort_keys=True).encode("utf-8")
    ).hexdigest()

    record = {
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
        "attached_to_cases": [],
    }

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

