"""Unit test suite for Authorized Cross-Bank Intelligence Workflow.

Verifies:
1. Mandatory institutional authentication & permitted role enforcement.
2. Active consortium membership gating and revocation/eviction fail-closed boundary.
3. Cross-consortium isolation and counterpart validation.
4. Institutional non-spoofing and token tenant binding.
5. Entity ownership verification & anti-probing enumeration resistance.
6. Real consortium data-sharing policy evaluation (purpose, attribution, and anonymization).
7. Authoritative server-issued signal registry & anti-forgery attachment enforcement.
8. Idempotent evidence attachment semantics without duplicate timeline records.
9. CAS concurrency failure rollback (preventing orphaned evidence records).
10. Terminal case immutability and tenant case ownership.
11. Operational privacy mode fail-closed truth (hardware enclave and P2P PSI requirements).
"""

from __future__ import annotations

import datetime
from collections.abc import Generator
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
)
from app.application.services.case_service import (
    CaseManagementService,
    EvidenceRegistryService,
    InvalidCaseTransitionError,
    TerminalCaseImmutableError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    MemberRole,
)
from app.domain.enums import CasePriority, CaseStatus, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "consortium_unit_store.json"
    account_file = tmp_path / "account_unit_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("intel_analyst_a")
    if not u_a:
        u_a = auth_svc.register_user(
            "intel_analyst_a",
            "AdminPass123!",
            "bank_a",
            roles=["admin", "analyst"],
            clearance_level=3,
        )
    else:
        u_a.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("intel_analyst_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "intel_analyst_b",
            "AdminPass123!",
            "bank_b",
            roles=["admin", "analyst"],
            clearance_level=3,
        )
    else:
        u_b.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_b)

    u_unauth = auth_svc.get_user("unauth_visitor")
    if not u_unauth:
        u_unauth = auth_svc.register_user(
            "unauth_visitor", "AdminPass123!", "bank_a", roles=["visitor_role"], clearance_level=1
        )
    else:
        u_unauth.roles = ["visitor_role"]
        auth_svc.identity_store.save_user(u_unauth)

    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "unauthorized_role": auth_svc.create_token_bundle(u_unauth).access_token,
    }


@pytest.fixture
def consortium_fixture(auth_tokens: dict[str, str]) -> tuple[ConsortiumGovernanceService, str]:
    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="nordic_aml_ring",
        name="Nordic AML Intelligence Alliance",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["bank_a"],
    )

    from app.application.services.model_registry import create_trusted_internal_context

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id="nordic_aml_ring",
        target_bank_id="bank_b",
    )
    gov_svc.add_member(
        "nordic_aml_ring",
        "bank_b",
        role=MemberRole.FULL_MEMBER,
        voting_power=1.0,
        trusted_context=ctx,
    )
    return gov_svc, "nordic_aml_ring"


@pytest.fixture
def intel_service(
    consortium_fixture: tuple[ConsortiumGovernanceService, str],
) -> CrossBankIntelligenceService:
    gov_svc, _ = consortium_fixture
    entity_svc = EntityResolutionService()
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    return CrossBankIntelligenceService(
        consortium_service=gov_svc,
        entity_service=entity_svc,
        case_service=case_svc,
        evidence_service=evidence_svc,
    )


class TestCrossBankIntelligenceWorkflow:
    def test_authorized_exact_match_signal_with_policy_anonymization(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify authorized matching between active members enforces default counterpart anonymization."""
        entity_svc = intel_service.entity_service

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="suspect.merchant@domain.org",
            bank_id="bank_a",
            attributes={"business_type": "high_risk_crypto"},
        )
        entity_svc.update_risk_level(ent_a.id, RiskLevel.HIGH)

        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="suspect.merchant@domain.org",
            bank_id="bank_b",
            attributes={"business_type": "high_risk_crypto"},
        )
        entity_svc.update_risk_level(ent_b.id, RiskLevel.CRITICAL)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
            target_bank_id="bank_b",
            execution_mode="SIMULATED_RESEARCH",
        )

        signal = intel_service.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert signal.match_confirmed is True
        assert signal.match_category == "EXACT_MATCH"
        assert signal.match_confidence == 1.0
        assert signal.contributing_institutions_count == 1
        # Default policy scrubs counterpart bank names unless explicitly authorized
        assert signal.disclosure_mode == "ANONYMIZED_SUMMARY"
        assert signal.contributing_institutions == []
        assert signal.sanitized_risk_level in ("CRITICAL", "HIGH")
        assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators

        # Invariant: Counterpart raw PII must never be present
        assert "suspect.merchant@domain.org" not in signal.privacy_hash_truncated
        assert "suspect.merchant" not in str(signal.model_dump())

    def test_counterpart_attribution_when_authorized_by_policy(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify counterpart bank name is disclosed when consortium policy allows identity sharing."""
        gov_svc = intel_service.consortium_service

        def _enable_identity_sharing(consortia: Any, proposals: Any) -> None:
            c = consortia["nordic_aml_ring"]
            if not hasattr(c, "metadata") or c.metadata is None:
                c.metadata = {}
            c.metadata["allow_institution_identity_sharing"] = True

        gov_svc._store.atomic_mutate(_enable_identity_sharing)

        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="shared.entity@domain.org",
            bank_id="bank_a",
        )
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="shared.entity@domain.org",
            bank_id="bank_b",
        )
        entity_svc.update_risk_level(ent_b.id, RiskLevel.HIGH)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
            target_bank_id="bank_b",
            execution_mode="SIMULATED_RESEARCH",
        )

        signal = intel_service.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert signal.match_confirmed is True
        assert signal.disclosure_mode == "ATTRIBUTED_INSTITUTIONS"
        assert "bank_b" in signal.contributing_institutions

    def test_unauthenticated_query_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
    ) -> None:
        """Verify intelligence query without authentication fails closed."""
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="some_id",
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(PermissionError, match="Authentication required"):
            intel_service.query_cross_bank_intelligence(req=req, auth_token=None)

    def test_unauthorized_role_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify token lacking permitted intelligence role is rejected."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="test.role@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(PermissionError, match="lacks permitted intelligence query role"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["unauthorized_role"],
            )

    def test_unowned_entity_query_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify querying an entity owned by another institution is rejected as cross-tenant violation."""
        entity_svc = intel_service.entity_service
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="foreign.entity@bank-b.com",
            bank_id="bank_b",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_b.id,  # Bank A requesting Bank B's private entity
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(PermissionError, match="Cross-tenant access violation"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_nonexistent_local_entity_query_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify querying an unanchored non-existent entity is rejected to prevent enumeration."""
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="fabricated_nonexistent_ent_999",
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(ValueError, match="not found in institution 'bank_a' database"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_mismatched_privacy_id_probe_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify passing an arbitrary privacy_id different from the local entity hash is rejected."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="customer.valid@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            privacy_id="forged_target_hash_probe_0123456789abcdef",
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(ValueError, match="does not match the authoritative hash"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_unapproved_intelligence_purpose_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify query with unapproved purpose is rejected under deny-by-default sharing policy."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="purpose.check@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
            purpose="COMMERCIAL_CREDIT_MARKETING",  # Unapproved purpose!
        )

        with pytest.raises(
            ConsortiumSecurityError, match="Consortium data-sharing policy rejected"
        ):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_revoked_member_access_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify revoked or evicted institution is strictly rejected."""
        gov_svc = intel_service.consortium_service

        def _evict(consortia: Any, proposals: Any) -> None:
            c_mut = consortia["nordic_aml_ring"]
            c_mut.removed_members["bank_b"] = {"bank_id": "bank_b", "reason": "evicted"}
            c_mut.members.pop("bank_b", None)

        gov_svc._store.atomic_mutate(_evict)

        entity_svc = intel_service.entity_service
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="evicted.cust@bank-b.com",
            bank_id="bank_b",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_b",
            query_entity_id=ent_b.id,
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(ConsortiumSecurityError, match="revoked or evicted"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_b"],
            )

    def test_non_spoofing_institutional_impersonation_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify token belonging to bank_b cannot query as bank_a."""
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="dummy_ent",
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(PermissionError, match="Institutional impersonation rejected"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_b"],  # Bank B token posing as Bank A
            )

    def test_operational_mode_fails_closed_without_hardware_enclave(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify requesting VERIFIED_OPERATIONAL fails closed in software environment without SGX."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="op.check@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(RuntimeError, match="Operational privacy attestation unavailable"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_attach_signal_to_case_evidence_ledger_authoritative(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify attaching a server-issued authoritative signal to an open case."""
        case_svc = intel_service.case_service
        entity_svc = intel_service.entity_service

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="target.case.evidence@ring.com",
            bank_id="bank_a",
        )
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="target.case.evidence@ring.com",
            bank_id="bank_b",
        )
        entity_svc.update_risk_level(ent_b.id, RiskLevel.HIGH)

        # 1. Authoritative query produces server-registered signal
        query_req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
            target_bank_id="bank_b",
            execution_mode="SIMULATED_RESEARCH",
        )
        signal = intel_service.query_cross_bank_intelligence(
            req=query_req,
            auth_token=auth_tokens["bank_a"],
        )
        assert signal.signal_id is not None

        # 2. Create open investigation case for Bank A
        case = case_svc.create_case(
            title="Structuring and Cross-Bank Layering Review",
            bank_id="bank_a",
            assigned_to="analyst_alice",
            priority=CasePriority.P2_HIGH,
        )
        initial_version = case.version

        # 3. Attach using server-issued signal_id
        attach_resp = intel_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
            notes="Confirmed correlation with Bank Beta entity.",
        )

        assert attach_resp.evidence_type == "CROSS_BANK_INTELLIGENCE"
        assert attach_resp.case_id == case.id
        assert attach_resp.signal_id == signal.signal_id
        assert len(attach_resp.content_hash) == 64
        assert attach_resp.case_version == initial_version + 1
        assert attach_resp.timeline_event_recorded is True
        assert attach_resp.is_duplicate_submission is False

        # 4. Check evidence was appended to case
        updated_case = case_svc.get_case(case.id)
        assert updated_case is not None
        assert attach_resp.evidence_id in updated_case.evidence_ids
        timeline_events = [e for e in updated_case.timeline if e.event_type == "evidence_added"]
        assert len(timeline_events) >= 1

    def test_forged_unregistered_signal_attachment_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify client-fabricated or unregistered signal_id is rejected."""
        case_svc = intel_service.case_service
        case = case_svc.create_case(
            title="Anti-Forgery Test Case",
            bank_id="bank_a",
        )

        with pytest.raises(
            ValueError, match="Authoritative signal 'forged-signal-id-1234' not found"
        ):
            intel_service.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id="forged-signal-id-1234",
                auth_token=auth_tokens["bank_a"],
            )

    def test_expired_signal_attachment_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify expired authoritative signals cannot be attached to evidence ledgers."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="expiry.test@bank-a.com",
            bank_id="bank_a",
        )
        query_req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
        )
        signal = intel_service.query_cross_bank_intelligence(
            req=query_req,
            auth_token=auth_tokens["bank_a"],
        )

        # Mutate authoritative record to simulate expiration
        record = intel_service._signal_store.get(signal.signal_id)
        assert record is not None
        past = (datetime.datetime.now(UTC) - datetime.timedelta(hours=2)).isoformat()
        record["expires_at"] = past
        intel_service._signal_store.set(signal.signal_id, record)

        case = intel_service.case_service.create_case(title="Expiry Case", bank_id="bank_a")

        with pytest.raises(ValueError, match="has expired"):
            intel_service.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )

    def test_cross_tenant_signal_attachment_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify signal issued to Bank B cannot be attached to Bank A's case."""
        entity_svc = intel_service.entity_service
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="bankb.cust@domain.com",
            bank_id="bank_b",
        )
        query_req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_b",
            query_entity_id=ent_b.id,
            consortium_id="nordic_aml_ring",
        )
        signal_b = intel_service.query_cross_bank_intelligence(
            req=query_req,
            auth_token=auth_tokens["bank_b"],
        )

        case_a = intel_service.case_service.create_case(title="Bank A Case", bank_id="bank_a")

        with pytest.raises(PermissionError, match="Cross-tenant signal violation"):
            intel_service.attach_signal_to_case_evidence(
                case_id=case_a.id,
                signal_id=signal_b.signal_id,
                auth_token=auth_tokens["bank_a"],
            )

    def test_idempotent_duplicate_signal_attachment(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify replaying attachment of the same signal is idempotent and does not create duplicate events."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="idempotent.flow@domain.com",
            bank_id="bank_a",
        )
        query_req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
        )
        signal = intel_service.query_cross_bank_intelligence(
            req=query_req,
            auth_token=auth_tokens["bank_a"],
        )

        case = intel_service.case_service.create_case(title="Idempotency Test", bank_id="bank_a")

        # 1. First attachment
        resp1 = intel_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
        )
        assert resp1.timeline_event_recorded is True
        assert resp1.is_duplicate_submission is False

        # 2. Second attachment of the same signal
        resp2 = intel_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
        )
        assert resp2.evidence_id == resp1.evidence_id
        assert resp2.is_duplicate_submission is True
        assert resp2.timeline_event_recorded is False

        # Verify only 1 evidence_added event exists
        updated_case = intel_service.case_service.get_case(case.id)
        assert updated_case is not None
        events = [e for e in updated_case.timeline if e.event_type == "evidence_added"]
        assert len(events) == 1

    def test_cas_concurrency_failure_rolls_back_evidence_store(
        self,
        intel_service: CrossBankIntelligenceService,
    ) -> None:
        """Verify EvidenceRegistryService rolls back evidence store record if case CAS update fails."""
        case_svc = intel_service.case_service
        evidence_svc = intel_service.evidence_service

        case = case_svc.create_case(title="CAS Rollback Test", bank_id="bank_a")
        initial_evidence_count = len(evidence_svc.get_case_evidence(case.id))

        # Mock update_conditional to simulate concurrent modification failure (returns False)
        with patch.object(
            evidence_svc._case_service._cases, "update_conditional", return_value=False
        ):
            with pytest.raises(InvalidCaseTransitionError, match="Precondition failed"):
                evidence_svc.register_evidence(
                    case_id=case.id,
                    evidence_type="CROSS_BANK_INTELLIGENCE",
                    title="Simulated Conflict Evidence",
                    file_path=f"storage/evidence/conflict_test_{case.id[:8]}.json",
                    content="{'test': 'conflict'}",
                    uploaded_by="analyst_alice",
                )

        # Assert no orphaned evidence record was left in the evidence store!
        remaining_evidence = evidence_svc.get_case_evidence(case.id)
        assert len(remaining_evidence) == initial_evidence_count

    def test_terminal_case_attachment_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify finalized terminal cases cannot accept new intelligence evidence."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="terminal.test@domain.com",
            bank_id="bank_a",
        )
        query_req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
        )
        signal = intel_service.query_cross_bank_intelligence(
            req=query_req,
            auth_token=auth_tokens["bank_a"],
        )

        from app.application.services.case_service import _case_to_dict

        case_svc = intel_service.case_service
        case = case_svc.create_case(title="Terminal Case", bank_id="bank_a")
        retrieved_case = case_svc.get_case(case.id)
        assert retrieved_case is not None
        retrieved_case.status = CaseStatus.CLOSED_CONFIRMED
        case_svc._cases.set(case.id, _case_to_dict(retrieved_case))

        with pytest.raises(TerminalCaseImmutableError, match="finalized in terminal state"):
            intel_service.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )
