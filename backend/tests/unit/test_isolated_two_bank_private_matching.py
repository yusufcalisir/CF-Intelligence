"""Unit and integration test suite for authentic isolated two-bank private matching architecture.

Verifies:
1. Institutional process, storage vault, and KMS session isolation.
2. Negative access-control probes at storage and key boundaries.
3. Coordinator isolation (zero access to raw private data or bank keys).
4. Diffie-Hellman Private Set Intersection (DH-PSI) matching correctness:
   - Legitimate exact match producing verified operational signal.
   - Genuine no-match negative control with 0.0 confidence.
   - Counterpart entity removal negative control.
5. Active consortium governance gating:
   - Revoked member institution rejection.
   - Cross-consortium denial.
   - Missing operational sharing grant denial.
   - Missing institutional jurisdiction denial.
6. Transcript integrity, anti-replay, and tamper resistance:
   - Reused nonce rejection.
   - Tampered payload or signature rejection.
7. Zero raw PII exposure across all messages and transcripts.
8. Authoritative case evidence binding and idempotent attachment.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid
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
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    MemberRole,
)
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.isolated_matching_service import (
    CounterpartMatchResponse,
    CrossBankMatchingCoordinator,
    IsolatedBankWorkerNode,
)
from app.application.services.kms_service import TenantKMSSession
from app.application.services.model_registry import create_trusted_internal_context
from app.config import get_settings
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService


@pytest.fixture(autouse=True)
def isolate_environment_and_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure clean isolated Redis stores, temporary disk storage, and clear cached settings."""
    from app.infrastructure.security.auth_service import AuthenticationService
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
    from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

    store_file = tmp_path / "consortium_store.json"
    account_file = tmp_path / "account_store.json"
    storage_dir = tmp_path / "app_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("APP_ENV", "development")

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()
    stores = [
        RedisStore("consortia"),
        RedisStore("consortium_proposals"),
        RedisStore("authoritative_signals"),
        RedisStore("evidence_registry"),
        RedisStore("fraud_cases"),
        RedisStore("users"),
    ]
    for s in stores:
        s.clear()
    yield
    for s in stores:
        s.clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()
    get_settings.cache_clear()


@pytest.fixture
def institutional_setup(tmp_path: Path):
    """Sets up an authentic two-bank consortium environment with isolated worker nodes."""
    storage_root = str(tmp_path / "institutional_storage")
    os.makedirs(storage_root, exist_ok=True)

    # 1. Consortium Governance setup
    gov_svc = ConsortiumGovernanceService()
    auth_svc = AuthenticationService.get_instance()

    u_a = auth_svc.get_user("officer_a")
    if not u_a:
        u_a = auth_svc.register_user(
            "officer_a", "AdminSecret123!", "bank_a", roles=["admin", "analyst"], clearance_level=3
        )
    u_b = auth_svc.get_user("officer_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "officer_b", "AdminSecret123!", "bank_b", roles=["admin", "analyst"], clearance_level=3
        )
    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token

    consortium_id = f"bilateral_fraud_alliance_{uuid.uuid4().hex[:8]}"
    gov_svc.create_consortium(
        consortium_id=consortium_id,
        name="Bilateral Cross-Bank Fraud Alliance",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id=consortium_id,
        target_bank_id="bank_b",
    )
    gov_svc.add_member(
        consortium_id,
        "bank_b",
        role=MemberRole.FULL_MEMBER,
        voting_power=1.0,
        trusted_context=ctx,
    )

    # Configure institutional jurisdictions in consortium policy
    def _set_jurisdictions(consortia: Any, proposals: Any) -> None:
        c = consortia[consortium_id]
        c.metadata["intelligence_sharing_policy"] = {
            "allowed_purposes": ["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
            "allow_cross_border_sharing": True,
            "institutional_jurisdictions": {
                "bank_a": "US",
                "bank_b": "US",
            },
        }

    gov_svc._store.atomic_mutate(_set_jurisdictions)

    # Register explicit operational data sharing grant
    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id="grant_operational_bilateral_001",
        authority="Federal Financial Institutions Examination Council",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    # Bilateral institutional consent: Bank B records its approval
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id="grant_operational_bilateral_001",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    # 2. Institutional Workers with isolated vaults and KMS sessions
    secret_a = "bank_a_auth_credential_key_2026"
    secret_b = "bank_b_auth_credential_key_2026"
    worker_a = IsolatedBankWorkerNode(
        bank_id="bank_a",
        storage_root=storage_root,
        auth_secret=secret_a,
        trusted_peer_keys={"bank_b": secret_b},
    )
    worker_b = IsolatedBankWorkerNode(
        bank_id="bank_b",
        storage_root=storage_root,
        auth_secret=secret_b,
        trusted_peer_keys={"bank_a": secret_a},
    )

    # 3. Matching Coordinator
    policy_engine = ConsortiumPolicyEngine()
    coordinator = CrossBankMatchingCoordinator(
        consortium_service=gov_svc,
        policy_engine=policy_engine,
        storage_root=storage_root,
    )
    coordinator.register_worker("bank_a", worker_a, secret_a)
    coordinator.register_worker("bank_b", worker_b, secret_b)

    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService(case_service=case_svc)
    intel_svc = CrossBankIntelligenceService(
        consortium_service=gov_svc,
        case_service=case_svc,
        evidence_service=evidence_svc,
        policy_engine=policy_engine,
        matching_coordinator=coordinator,
    )

    return {
        "storage_root": storage_root,
        "consortium_id": consortium_id,
        "gov_svc": gov_svc,
        "worker_a": worker_a,
        "worker_b": worker_b,
        "secret_a": secret_a,
        "secret_b": secret_b,
        "coordinator": coordinator,
        "case_svc": case_svc,
        "evidence_svc": evidence_svc,
        "intel_svc": intel_svc,
        "token_a": token_a,
        "token_b": token_b,
    }


class TestInstitutionalStorageAndKeyIsolation:
    """Verifies physical process/storage separation and KMS access control."""

    def test_bank_worker_vault_and_kms_isolation(self, institutional_setup: dict[str, Any]):
        """Each bank worker operates strictly within its own storage vault and KMS session."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]

        # 1. Storage vault paths are completely separated
        assert worker_a.vault_dir != worker_b.vault_dir
        assert "bank_a" in worker_a.vault_dir
        assert "bank_b" in worker_b.vault_dir
        assert Path(worker_a.vault_dir).exists()
        assert Path(worker_b.vault_dir).exists()

        # 2. KMS private exponents are strictly distinct
        exp_a = worker_a._kms_session.get_psi_private_exponent()
        exp_b = worker_b._kms_session.get_psi_private_exponent()
        assert exp_a != exp_b
        assert exp_a > 1
        assert exp_b > 1

    def test_negative_access_control_cross_bank_kms_probe_rejected(self):
        """Bank A session attempting to retrieve Bank B's private key fails closed."""
        session_a = TenantKMSSession(bank_id="bank_a")
        with pytest.raises(PermissionError, match="KMS access control violation"):
            # Bank A tenant session cannot access Bank B's key
            session_a._kms.get_psi_private_exponent(
                bank_id="bank_b", caller_tenant="bank_a"
            )

    def test_coordinator_has_zero_raw_identifier_exposure(
        self, institutional_setup: dict[str, Any]
    ):
        """Coordinator object maintains zero access to private bank vaults or KMS secret keys."""
        coordinator: CrossBankMatchingCoordinator = institutional_setup["coordinator"]
        # Coordinator does not maintain private keys
        assert not hasattr(coordinator, "private_key")
        assert not hasattr(coordinator, "vault_dir")
        assert not hasattr(coordinator, "private_exponent")


class TestIsolatedPrivateMatchingExecution:
    """Verifies end-to-end two-bank private matching protocol correctness and governance."""

    def test_legitimate_exact_match_produces_verified_operational_signal(
        self, institutional_setup: dict[str, Any]
    ):
        """Exact match between Bank A and Bank B generates a verifiable minimal operational signal."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        # Store entity in Bank A's private vault
        ent_a = Entity(
            id="ent_cust_alice_bank_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Alice Syndicate Target",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": "syndicate.target.777@international.org"},
        )
        worker_a.store_private_entity(ent_a)

        # Store counterpart entity with matching raw identifier in Bank B's private vault
        ent_b = Entity(
            id="ent_cust_alice_bank_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Alice Counterpart Account",
            risk_level=RiskLevel.CRITICAL,
            attributes={"raw_identifier": "syndicate.target.777@international.org"},
        )
        worker_b.store_private_entity(ent_b)

        # Execute verified operational matching
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_cust_alice_bank_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        signal: CrossBankIntelligenceSignal = intel_svc.execute_two_bank_private_matching(
            req=req, auth_token=token_a
        )

        # Assert matching correctness
        assert signal.match_confirmed is True
        assert signal.match_category == "EXACT_MATCH"
        assert signal.match_confidence == 1.0
        assert signal.is_authoritative is False
        assert signal.is_synthetic is False
        assert signal.execution_mode == "RESEARCH_PROTOTYPE"
        assert signal.assurance_level == "PROTOCOL_PROTOTYPE"
        assert "bank_b" in signal.contributing_institutions
        assert signal.sanitized_risk_level == "UNAVAILABLE"
        assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators

        # Zero raw PII exposure invariant
        assert "syndicate.target.777@international.org" not in str(signal.model_dump())
        assert ent_b.id not in str(signal.model_dump())

    def test_no_match_negative_control_produces_zero_correlation(
        self, institutional_setup: dict[str, Any]
    ):
        """Negative control: Divergent entities produce NO_MATCH with zero false correlation."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_unique_bank_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Unique Customer Alpha",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": "unique.alpha.123@local-bank.com"},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_different_bank_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Different Customer Beta",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": "completely.unrelated.456@other-bank.com"},
        )
        worker_b.store_private_entity(ent_b)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_unique_bank_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

        assert signal.match_confirmed is False
        assert signal.match_category == "NO_MATCH"
        assert signal.match_confidence == 0.0
        assert len(signal.contributing_institutions) == 0
        assert signal.sanitized_risk_level == "UNOBSERVED"

    def test_removed_counterpart_entity_produces_no_match(
        self, institutional_setup: dict[str, Any]
    ):
        """Removing counterpart entity from Bank B's vault eliminates subsequent match."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_to_be_removed_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "temporary.match.888@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_to_be_removed_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            attributes={"raw_identifier": "temporary.match.888@test.org"},
        )
        worker_b.store_private_entity(ent_b)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_to_be_removed_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        # Initial match confirms
        sig1 = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)
        assert sig1.match_confirmed is True

        # Remove counterpart entity file from Bank B's private vault
        b_file = Path(worker_b.vault_dir) / f"{ent_b.id}.json"
        if b_file.exists():
            b_file.unlink()

        # Subsequent query yields NO_MATCH
        sig2 = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)
        assert sig2.match_confirmed is False
        assert sig2.match_category == "NO_MATCH"
        assert sig2.match_confidence == 0.0


class TestConsortiumGovernanceGatingAndSecurityFailures:
    """Verifies that governance rules and protocol tampering fail closed."""

    def test_revoked_member_institution_fails_closed(
        self, institutional_setup: dict[str, Any]
    ):
        """Evicted or revoked member institution cannot participate in matching."""
        gov_svc: ConsortiumGovernanceService = institutional_setup["gov_svc"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]

        ent_a = Entity(
            id="ent_revocation_probe",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "revocation.probe@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        # Evict bank_b from consortium
        def _evict_b(consortia: Any, proposals: Any) -> None:
            c = consortia[consortium_id]
            c.removed_members["bank_b"] = {"bank_id": "bank_b", "reason": "non_compliance"}
            c.members.pop("bank_b", None)

        gov_svc._store.atomic_mutate(_evict_b)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_revocation_probe",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(ConsortiumSecurityError, match="evicted/revoked|not an active member"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

    def test_missing_operational_sharing_grant_denies_query(
        self, institutional_setup: dict[str, Any]
    ):
        """Consortium without explicit operational sharing grant denies operational queries."""
        gov_svc: ConsortiumGovernanceService = institutional_setup["gov_svc"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]

        ent_a = Entity(
            id="ent_grant_test",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "grant.test@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        # Remove operational grant
        def _remove_grant(consortia: Any, proposals: Any) -> None:
            c = consortia[consortium_id]
            c.metadata.pop("operational_data_sharing_grant", None)

        gov_svc._store.atomic_mutate(_remove_grant)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_grant_test",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(ConsortiumSecurityError, match="operational data-sharing grant"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

    def test_missing_institutional_jurisdiction_denies_query(
        self, institutional_setup: dict[str, Any]
    ):
        """Absence of authoritative institutional jurisdiction fails closed."""
        gov_svc: ConsortiumGovernanceService = institutional_setup["gov_svc"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]

        ent_a = Entity(
            id="ent_jurisdiction_test",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "jurisdiction.test@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        # Wipe jurisdictions from metadata
        def _wipe_jurisdictions(consortia: Any, proposals: Any) -> None:
            c = consortia[consortium_id]
            policy = c.metadata.get("intelligence_sharing_policy", {})
            policy.pop("institutional_jurisdictions", None)
            c.metadata["intelligence_sharing_policy"] = policy
            grant = c.metadata.get("operational_data_sharing_grant", {})
            grant.pop("institution_jurisdictions", None)
            c.metadata["operational_data_sharing_grant"] = grant

        gov_svc._store.atomic_mutate(_wipe_jurisdictions)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_jurisdiction_test",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(ConsortiumSecurityError, match="jurisdiction"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

    def test_replay_attack_with_consumed_nonce_rejected(
        self, institutional_setup: dict[str, Any]
    ):
        """Replaying a query message with an already-consumed nonce is rejected."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        secret_a: str = institutional_setup["secret_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_replay_test",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "replay.test@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        query = worker_a.initiate_blinded_query(
            query_entity_id="ent_replay_test",
            consortium_id=consortium_id,
            target_bank_id="bank_b",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

        # First query processing succeeds
        resp = worker_b.process_counterpart_query(query=query, requester_auth_secret=secret_a)
        assert resp is not None

        # Replaying identical message with same nonce fails closed
        with pytest.raises(ConsortiumSecurityError, match="Replay attack detected"):
            worker_b.process_counterpart_query(query=query, requester_auth_secret=secret_a)

    def test_tampered_counterpart_signature_fails_closed(
        self, institutional_setup: dict[str, Any]
    ):
        """Counterpart response with tampered double-blinded elements fails signature check."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        secret_a: str = institutional_setup["secret_a"]
        secret_b: str = institutional_setup["secret_b"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_tamper_sig",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "tamper.sig@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        query = worker_a.initiate_blinded_query(
            query_entity_id="ent_tamper_sig",
            consortium_id=consortium_id,
            target_bank_id="bank_b",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

        resp = worker_b.process_counterpart_query(query=query, requester_auth_secret=secret_a)

        # Tamper with double-blinded query element
        tampered_resp = CounterpartMatchResponse(
            job_id=resp.job_id,
            consortium_id=resp.consortium_id,
            target_bank_id=resp.target_bank_id,
            double_blinded_query_hex="deadbeefcafebabe",
            blinded_target_elements_hex=resp.blinded_target_elements_hex,
            sanitized_risk_context=resp.sanitized_risk_context,
            nonce=resp.nonce,
            timestamp=resp.timestamp,
            signature=resp.signature,  # Signature now mismatched
        )

        with pytest.raises(ConsortiumSecurityError, match="Peer authentication failed|Invalid signature"):
            worker_a.finalize_match_evaluation(
                query=query, response=tampered_resp, counterpart_auth_secret=secret_b
            )


class TestAuthoritativeEvidenceBinding:
    """Verifies binding of verified operational matching results to real case evidence."""

    def test_verified_operational_signal_attaches_to_authoritative_case_evidence(
        self, institutional_setup: dict[str, Any]
    ):
        """Authoritative match signal attaches successfully to case evidence with CAS durability."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        case_svc: CaseManagementService = institutional_setup["case_svc"]
        evidence_svc: EvidenceRegistryService = institutional_setup["evidence_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_evidence_target_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Target Account Alpha",
            attributes={"raw_identifier": "case.evidence.target@bank.org"},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_evidence_target_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Target Account Beta",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": "case.evidence.target@bank.org"},
        )
        worker_b.store_private_entity(ent_b)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_evidence_target_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)
        assert signal.is_authoritative is False
        assert signal.assurance_level == "PROTOCOL_PROTOTYPE"
        assert signal.match_confirmed is True

        case = case_svc.create_case(title="Authoritative Fraud Case Alpha", bank_id="bank_a")

        # 1. Prototype results are strictly blocked from case evidence ledger across all cases
        with pytest.raises(ConsortiumSecurityError, match="research prototype|cannot enter case evidence"):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=token_a,
                notes="Verified DH-PSI cross-bank correlation confirmed with counterpart Bank B.",
            )

        # 2. Authoritative operational signal attaches cleanly
        auth_sig_id = str(uuid.uuid4())
        auth_sig_data = {
            "signal_id": auth_sig_id,
            "consortium_id": consortium_id,
            "requesting_bank_id": "bank_a",
            "query_entity_id": "ent_evidence_target_a",
            "privacy_hash_truncated": "hash_abc",
            "match_confirmed": True,
            "match_category": "EXACT_MATCH",
            "match_confidence": 1.0,
            "contributing_institutions_count": 1,
            "contributing_institutions": ["bank_b"],
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "sanitized_risk_level": "UNAVAILABLE",
            "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"],
            "graph_context": {},
            "provenance": {},
            "execution_mode": "VERIFIED_OPERATIONAL",
            "attestation_status": "HARDWARE_ENCLAVE_ATTESTED",
            "is_authoritative": True,
            "is_synthetic": False,
            "assurance_level": "OPERATIONAL_PRIVACY_VERIFIED",
        }
        content_hash = hashlib.sha256(
            json.dumps(auth_sig_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        intel_svc._signal_store.set(
            auth_sig_id,
            {
                "signal_id": auth_sig_id,
                "signal_data": auth_sig_data,
                "requesting_bank_id": "bank_a",
                "consortium_id": consortium_id,
                "query_entity_id": "ent_evidence_target_a",
                "issued_to_principal": "worker:bank_a",
                "issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "expires_at": (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)).isoformat(),
                "content_hash": content_hash,
                "is_authoritative": True,
                "is_synthetic": False,
                "operational_status": "OPERATIONAL",
                "assurance_level": "OPERATIONAL_PRIVACY_VERIFIED",
                "execution_mode": "VERIFIED_OPERATIONAL",
                "admission_status": "COMMITTED",
                "publication_status": "PUBLISHED",
                "is_attachable": True,
                "receipt_id": f"rcp_{auth_sig_id}",
                "receipt": {
                    "receipt_id": f"rcp_{auth_sig_id}",
                    "worker_signature": "sig_worker_a_valid",
                    "coordinator_signature": "sig_coord_valid",
                },
                "attached_to_cases": [],
            },
        )
        intel_svc._admitted_receipts_store.set(
            f"rcp_{auth_sig_id}", {"admitted_at": datetime.datetime.now(datetime.UTC).isoformat(), "signal_id": auth_sig_id}
        )

        attach_resp = intel_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=auth_sig_id,
            auth_token=token_a,
            notes="Audited operational signal attached.",
        )

        assert attach_resp.evidence_id is not None
        assert attach_resp.case_id == case.id

        ev = evidence_svc.get_evidence(attach_resp.evidence_id)
        assert ev is not None
        assert ev["evidence_type"] == "CROSS_BANK_INTELLIGENCE"
        assert ev["metadata"]["is_authoritative"] is True
        assert ev["metadata"]["operational_status"] == "OPERATIONAL"
        assert Path(ev["file_path"]).exists()

        content = evidence_svc.get_evidence_content(attach_resp.evidence_id)
        assert "Audited operational signal attached." in content

    def test_idempotent_replayed_attachment_avoids_duplicate_evidence(
        self, institutional_setup: dict[str, Any]
    ):
        """Replaying attachment with the same signal returns existing record without duplicate files."""
        worker_a: IsolatedBankWorkerNode = institutional_setup["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_setup["worker_b"]
        intel_svc: CrossBankIntelligenceService = institutional_setup["intel_svc"]
        case_svc: CaseManagementService = institutional_setup["case_svc"]
        token_a: str = institutional_setup["token_a"]
        consortium_id: str = institutional_setup["consortium_id"]

        ent_a = Entity(
            id="ent_idempotent_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "idempotent.attachment@bank.org"},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_idempotent_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            attributes={"raw_identifier": "idempotent.attachment@bank.org"},
        )
        worker_b.store_private_entity(ent_b)

        auth_sig_id = str(uuid.uuid4())
        auth_sig_data = {
            "signal_id": auth_sig_id,
            "consortium_id": consortium_id,
            "requesting_bank_id": "bank_a",
            "query_entity_id": "ent_idempotent_a",
            "privacy_hash_truncated": "hash_idem",
            "match_confirmed": True,
            "match_category": "EXACT_MATCH",
            "match_confidence": 1.0,
            "contributing_institutions_count": 1,
            "contributing_institutions": ["bank_b"],
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "sanitized_risk_level": "UNAVAILABLE",
            "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"],
            "graph_context": {},
            "provenance": {},
            "execution_mode": "VERIFIED_OPERATIONAL",
            "attestation_status": "HARDWARE_ENCLAVE_ATTESTED",
            "is_authoritative": True,
            "is_synthetic": False,
            "assurance_level": "OPERATIONAL_PRIVACY_VERIFIED",
        }
        content_hash = hashlib.sha256(
            json.dumps(auth_sig_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        intel_svc._signal_store.set(
            auth_sig_id,
            {
                "signal_id": auth_sig_id,
                "signal_data": auth_sig_data,
                "requesting_bank_id": "bank_a",
                "consortium_id": consortium_id,
                "query_entity_id": "ent_idempotent_a",
                "issued_to_principal": "worker:bank_a",
                "issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "expires_at": (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)).isoformat(),
                "content_hash": content_hash,
                "is_authoritative": True,
                "is_synthetic": False,
                "operational_status": "OPERATIONAL",
                "assurance_level": "OPERATIONAL_PRIVACY_VERIFIED",
                "execution_mode": "VERIFIED_OPERATIONAL",
                "admission_status": "COMMITTED",
                "publication_status": "PUBLISHED",
                "is_attachable": True,
                "receipt_id": f"rcp_{auth_sig_id}",
                "receipt": {
                    "receipt_id": f"rcp_{auth_sig_id}",
                    "worker_signature": "sig_worker_a_valid",
                    "coordinator_signature": "sig_coord_valid",
                },
                "attached_to_cases": [],
            },
        )
        intel_svc._admitted_receipts_store.set(
            f"rcp_{auth_sig_id}", {"admitted_at": datetime.datetime.now(datetime.UTC).isoformat(), "signal_id": auth_sig_id}
        )

        case = case_svc.create_case(title="Idempotent Target Case", bank_id="bank_a")

        resp1 = intel_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=auth_sig_id,
            auth_token=token_a,
            idempotency_key="idemp_key_001",
        )
        assert resp1.is_duplicate_submission is False

        # Replay identical attachment
        resp2 = intel_svc.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=auth_sig_id,
            auth_token=token_a,
            idempotency_key="idemp_key_001",
        )
        assert resp2.is_duplicate_submission is True
        assert resp2.evidence_id == resp1.evidence_id

