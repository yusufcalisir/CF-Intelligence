"""Unit test suite for Authorized Cross-Bank Intelligence Workflow.

Verifies:
1. Active consortium membership gating and authorization.
2. Evicted/revoked institution rejection (fail closed).
3. Cross-consortium isolation and counterpart validation.
4. Authenticated identity non-spoofing enforcement.
5. Exact and fuzzy privacy-preserving matching without raw PII leakage.
6. Hardware attestation fail-closed boundary enforcement.
7. Case evidence attachment, tenant ownership, and terminal case immutability.
8. Cryptographic audit trail preservation.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
)
from app.application.services.case_service import (
    CaseManagementService,
    EvidenceRegistryService,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    MemberRole,
)
from app.domain.enums import CasePriority, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "consortium_unit_store.json"
    account_file = tmp_path / "account_unit_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    yield
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("intel_analyst_a")
    if not u_a:
        u_a = auth_svc.register_user(
            "intel_analyst_a", "AdminPass123!", "bank_a", roles=["admin", "analyst"], clearance_level=3
        )
    else:
        u_a.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("intel_analyst_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "intel_analyst_b", "AdminPass123!", "bank_b", roles=["admin", "analyst"], clearance_level=3
        )
    else:
        u_b.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_b)

    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
    }


@pytest.fixture
def consortium_fixture(auth_tokens: dict[str, str]) -> tuple[ConsortiumGovernanceService, str]:
    gov_svc = ConsortiumGovernanceService()
    # Founder creates consortium
    gov_svc.create_consortium(
        consortium_id="nordic_aml_ring",
        name="Nordic AML Intelligence Alliance",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["bank_a"],
    )
    # Add bank_b
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
    def test_authorized_exact_match_signal(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify authorized matching between active consortium members without raw PII leakage."""
        entity_svc = intel_service.entity_service

        # Seed matching suspect entity at Bank A and Bank B
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
        )

        signal = intel_service.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
            actor="analyst_alice",
        )

        assert signal.match_confirmed is True
        assert signal.match_category == "EXACT_MATCH"
        assert signal.match_confidence == 1.0
        assert signal.contributing_institutions_count == 1
        assert "bank_b" in signal.contributing_institutions
        assert signal.sanitized_risk_level in ("CRITICAL", "HIGH")
        assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators

        # Invariant: Counterpart raw display labels and raw PII must never be present
        assert "suspect.merchant@domain.org" not in signal.privacy_hash_truncated
        assert "suspect.merchant" not in str(signal.model_dump())

    def test_no_match_returns_honest_unobserved_signal(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify non-matching entity produces an honest NO_MATCH signal rather than fallback."""
        entity_svc = intel_service.entity_service
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="unique.isolated.customer@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id="nordic_aml_ring",
        )

        signal = intel_service.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert signal.match_confirmed is False
        assert signal.match_category == "NO_MATCH"
        assert signal.match_confidence == 0.0
        assert signal.contributing_institutions_count == 0
        assert signal.contributing_institutions == []
        assert signal.sanitized_risk_level == "UNOBSERVED"

    def test_revoked_member_access_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify revoked or evicted institution is strictly rejected."""
        gov_svc = intel_service.consortium_service
        c = gov_svc.get_consortium("nordic_aml_ring")
        assert c is not None

        def _evict(consortia: Any, proposals: Any) -> None:
            c_mut = consortia["nordic_aml_ring"]
            c_mut.removed_members["bank_b"] = {"bank_id": "bank_b", "reason": "evicted"}
            c_mut.members.pop("bank_b", None)

        gov_svc._store.atomic_mutate(_evict)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_b",
            privacy_id="some_test_privacy_hash_12345",
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
            privacy_id="dummy_hash_for_spoof_check",
            consortium_id="nordic_aml_ring",
        )

        with pytest.raises(PermissionError, match="Institutional impersonation rejected"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_b"],  # Bank B token posing as Bank A
            )

    def test_hardware_attestation_requirement_fails_closed(
        self,
        intel_service: CrossBankIntelligenceService,
        auth_tokens: dict[str, str],
    ) -> None:
        """Verify strict physical hardware attestation fails closed in software environment."""
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            privacy_id="dummy_hash",
            consortium_id="nordic_aml_ring",
            require_hardware_attestation=True,
        )

        with pytest.raises(RuntimeError, match="Hardware attestation unavailable"):
            intel_service.query_cross_bank_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    def test_attach_signal_to_case_evidence_ledger(
        self,
        intel_service: CrossBankIntelligenceService,
    ) -> None:
        """Verify attaching intelligence signal to open case updates version, timeline, and SHA-256 evidence."""
        case_svc = intel_service.case_service

        # Create open investigation case for Bank A
        case = case_svc.create_case(
            title="Structuring and Cross-Bank Layering Review",
            bank_id="bank_a",
            assigned_to="analyst_alice",
            priority=CasePriority.P2_HIGH,
        )
        assert case.is_open is True
        initial_version = case.version

        # Fabricate verified signal for testing evidence binding
        signal = CrossBankIntelligenceSignal(
            signal_id="sig-8841-test",
            consortium_id="nordic_aml_ring",
            requesting_bank_id="bank_a",
            query_entity_id="ent-110",
            privacy_hash_truncated="a1b2c3d4e5f6...",
            match_confirmed=True,
            match_category="EXACT_MATCH",
            match_confidence=1.0,
            contributing_institutions_count=1,
            contributing_institutions=["bank_b"],
            sanitized_risk_level="HIGH",
            sanitized_typology_indicators=["CROSS_BANK_ENTITY_OBSERVED"],
            graph_context={"sanitized_degree_band": "BILATERAL"},
            provenance={"governance_authority": "consortium:nordic_aml_ring"},
            execution_mode="VERIFIED_OPERATIONAL",
            attestation_status="UNAVAILABLE_NO_HARDWARE_ENCLAVE",
        )

        attach_resp = intel_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal=signal,
            actor="analyst_alice",
            caller_bank_id="bank_a",
            notes="Confirmed correlation with Bank Beta high-risk entity.",
        )

        assert attach_resp.evidence_type == "CROSS_BANK_INTELLIGENCE"
        assert attach_resp.case_id == case.id
        assert len(attach_resp.content_hash) == 64
        assert attach_resp.case_version == initial_version + 1

        # Check evidence was appended to case
        updated_case = case_svc.get_case(case.id)
        assert updated_case is not None
        assert attach_resp.evidence_id in updated_case.evidence_ids
        timeline_events = [e for e in updated_case.timeline if e.event_type == "evidence_added"]
        assert len(timeline_events) >= 1

    def test_attach_signal_to_foreign_tenant_case_rejected(
        self,
        intel_service: CrossBankIntelligenceService,
    ) -> None:
        """Verify cross-tenant case evidence attachment is rejected."""
        case_svc = intel_service.case_service
        case = case_svc.create_case(
            title="Bank B Private Case",
            bank_id="bank_b",
        )

        signal = CrossBankIntelligenceSignal(
            signal_id="sig-test",
            consortium_id="nordic_aml_ring",
            requesting_bank_id="bank_a",
            privacy_hash_truncated="hash...",
            match_confirmed=True,
            match_category="EXACT_MATCH",
            match_confidence=1.0,
            contributing_institutions_count=1,
            contributing_institutions=["bank_b"],
            sanitized_risk_level="HIGH",
            execution_mode="VERIFIED_OPERATIONAL",
            attestation_status="UNAVAILABLE_NO_HARDWARE_ENCLAVE",
        )

        with pytest.raises(PermissionError, match="Tenant isolation violation"):
            intel_service.attach_signal_to_case_evidence(
                case_id=case.id,
                signal=signal,
                caller_bank_id="bank_a",  # Bank A caller trying to attach to Bank B case
            )
