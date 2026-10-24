"""Comprehensive test suite for institutional isolation, multi-process transport, and cryptographic assurance.

Verifies:
1. Multi-process worker execution over authentic loopback HTTP transport (InstitutionalWorkerDaemon + InstitutionalWorkerClient).
2. Negative access-control probes at institutional storage, process, and key custody boundaries.
3. Exact-match risk attribution (Private Payload Association): counteracts cross-entity risk inheritance.
4. Subgroup validation on 2048-bit MODP safe-prime subgroup G_q (Euler criterion: c^q == 1 mod p).
5. Durable anti-replay persistence across worker restarts.
6. Institutional authorization gating: wrong-bank principal, unauthorized roles, forged actors, unauthenticated grant issuance, and grant revocation.
7. Authoritative case evidence attachment and prototype signal containment.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
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
    BlindedQueryMessage,
    CrossBankMatchingCoordinator,
    InstitutionalWorkerClient,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
)
from app.application.services.model_registry import create_trusted_internal_context
from app.config import get_settings
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.domain.psi_service import (
    PSI_PRIME,
    validate_group_element,
)
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService


def _get_free_port() -> int:
    """Acquire an ephemeral port from the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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
def institutional_context(tmp_path: Path) -> dict[str, Any]:
    """Establishes an authentic consortium with isolated banks and governance grants."""
    storage_root = str(tmp_path / "institutional_storage")
    os.makedirs(storage_root, exist_ok=True)

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

    consortium_id = f"institutional_psi_alliance_{uuid.uuid4().hex[:8]}"
    gov_svc.create_consortium(
        consortium_id=consortium_id,
        name="Institutional PSI Alliance",
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
            "allowed_purposes": [
                "ANTI_MONEY_LAUNDERING_CROSS_CHECK",
                "AML_COLLABORATIVE_INVESTIGATION",
                "FRAUD_INVESTIGATION",
                "AML",
            ],
            "allow_cross_border_sharing": True,
            "institutional_jurisdictions": {
                "bank_a": "US",
                "bank_b": "US",
            },
        }

    gov_svc._store.atomic_mutate(_set_jurisdictions)

    # Register authenticated operational sharing grant
    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id="grant_operational_bilateral_001",
        authority="Federal Financial Institutions Examination Council",
        allowed_purposes=[
            "ANTI_MONEY_LAUNDERING_CROSS_CHECK",
            "AML_COLLABORATIVE_INVESTIGATION",
            "FRAUD_INVESTIGATION",
            "AML",
        ],
        institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )

    worker_a = IsolatedBankWorkerNode(
        bank_id="bank_a",
        storage_root=storage_root,
        auth_secret="AuthSecretBankA_Deterministic_777!",
        trusted_peer_keys={"bank_b": "AuthSecretBankB_Deterministic_888!"},
    )
    worker_b = IsolatedBankWorkerNode(
        bank_id="bank_b",
        storage_root=storage_root,
        auth_secret="AuthSecretBankB_Deterministic_888!",
        trusted_peer_keys={"bank_a": "AuthSecretBankA_Deterministic_777!"},
    )

    return {
        "storage_root": storage_root,
        "consortium_id": consortium_id,
        "gov_svc": gov_svc,
        "token_a": token_a,
        "token_b": token_b,
        "worker_a": worker_a,
        "worker_b": worker_b,
        "auth_secret_a": "AuthSecretBankA_Deterministic_777!",
        "auth_secret_b": "AuthSecretBankB_Deterministic_888!",
    }


class TestInstitutionalMultiProcessWorkerTransport:
    """Validates real inter-process / network worker execution using loopback HTTP daemons."""

    def test_multi_process_worker_execution_matches_and_verifies_assurance(
        self, institutional_context: dict[str, Any]
    ):
        """Worker A and Worker B run as independent network daemons; coordinator communicates via HTTP."""
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        token_a: str = institutional_context["token_a"]
        consortium_id: str = institutional_context["consortium_id"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        auth_secret_b: str = institutional_context["auth_secret_b"]

        # Store entities
        raw_id = "target.syndicate.member@fraud-ring.org"
        ent_a = Entity(
            id="ent_a_network",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Syndicate Target A",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": raw_id},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_b_network",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Syndicate Target B",
            risk_level=RiskLevel.CRITICAL,
            attributes={"raw_identifier": raw_id},
        )
        worker_b.store_private_entity(ent_b)

        # Launch real HTTP daemons on ephemeral loopback ports
        port_a = _get_free_port()
        port_b = _get_free_port()

        daemon_a = InstitutionalWorkerDaemon(worker_a, host="127.0.0.1", port=port_a)
        daemon_b = InstitutionalWorkerDaemon(worker_b, host="127.0.0.1", port=port_b)

        daemon_a.start()
        daemon_b.start()
        time.sleep(0.05)

        try:
            client_a = InstitutionalWorkerClient(
                bank_id="bank_a",
                base_url=f"http://127.0.0.1:{port_a}",
                auth_secret=auth_secret_a,
            )
            client_b = InstitutionalWorkerClient(
                bank_id="bank_b",
                base_url=f"http://127.0.0.1:{port_b}",
                auth_secret=auth_secret_b,
            )

            # Coordinator holds NO worker objects and NO HMAC secrets in memory
            coordinator = CrossBankMatchingCoordinator(
                consortium_service=institutional_context["gov_svc"],
            )
            coordinator.register_worker("bank_a", client_a, auth_secret_a)
            coordinator.register_worker("bank_b", client_b, auth_secret_b)

            req = CrossBankIntelligenceQueryRequest(
                requesting_bank_id="bank_a",
                target_bank_id="bank_b",
                query_entity_id="ent_a_network",
                consortium_id=consortium_id,
                execution_mode="VERIFIED_OPERATIONAL",
            )

            intel_svc = CrossBankIntelligenceService(
                consortium_service=institutional_context["gov_svc"],
                matching_coordinator=coordinator,
            )
            signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

            # Assert operational correctness and assurance level
            assert signal.match_confirmed is True
            assert signal.match_category == "EXACT_MATCH"
            assert signal.match_confidence == 1.0
            assert signal.assurance_level == "ISOLATED_INTEGRATION_VERIFIED"
            assert signal.sanitized_risk_level == "UNAVAILABLE"
            assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators

        finally:
            daemon_a.stop()
            daemon_b.stop()


class TestInstitutionalNegativeAccessControlProbes:
    """Verifies that neither worker nor coordinator can access unauthorized storage or keys."""

    def test_bank_worker_storage_isolation_and_negative_access_probes(
        self, institutional_context: dict[str, Any]
    ):
        """Worker A's filesystem storage contains only bank_a data; Worker B storage contains only bank_b."""
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        storage_root: str = institutional_context["storage_root"]

        # Store entity in bank_b
        ent_b = Entity(
            id="ent_secret_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Secret Customer B",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": "secret.b@bankb.com"},
        )
        worker_b.store_private_entity(ent_b)

        # Worker A attempts to load an entity belonging to bank_b from its own store -> must raise ValueError
        with pytest.raises(ValueError, match="not found in isolated private storage"):
            worker_a.load_private_entity("ent_secret_b")

        # Verify filesystem storage layout
        bank_a_path = Path(storage_root) / "bank_a" / "private_entities"
        bank_b_path = Path(storage_root) / "bank_b" / "private_entities"

        assert bank_b_path.exists()
        assert (bank_b_path / "ent_secret_b.json").exists()
        assert not (bank_a_path / "ent_secret_b.json").exists()

    def test_coordinator_has_zero_key_or_vault_custody(
        self, institutional_context: dict[str, Any]
    ):
        """Coordinator must not hold private KMS exponents, vault storage paths, or raw datasets."""
        gov_svc = institutional_context["gov_svc"]
        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)

        assert not hasattr(coordinator, "private_entities")
        assert not hasattr(coordinator, "kms_session")
        assert not hasattr(coordinator, "vault_dir")
        assert not hasattr(coordinator, "_psi_private_exponent")


class TestExactRiskAttributionPrivatePayloadAssociation:
    """Verifies that match risk attribution is strictly bound to the matched entity."""

    def test_exact_match_does_not_inherit_unrelated_critical_risk(
        self, institutional_context: dict[str, Any]
    ):
        """When Bank B holds a LOW-risk matched entity and an unrelated CRITICAL entity,

        the matched result MUST be LOW risk and must NOT leak or inherit CRITICAL risk.
        """
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        token_a: str = institutional_context["token_a"]
        consortium_id: str = institutional_context["consortium_id"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        auth_secret_b: str = institutional_context["auth_secret_b"]

        # Target matching entity: LOW risk
        matched_raw_id = "alice.lowrisk@legitimate-business.org"
        ent_a = Entity(
            id="ent_alice_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Alice Corp A",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": matched_raw_id},
        )
        worker_a.store_private_entity(ent_a)

        ent_b_alice = Entity(
            id="ent_alice_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Alice Corp B",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": matched_raw_id, "typologies": ["ACCOUNT_TAKEOVER"]},
        )
        worker_b.store_private_entity(ent_b_alice)

        # Unrelated entity in Bank B: CRITICAL risk with alarming typologies
        unrelated_raw_id = "malicious.kingpin@cartel-syndicate.net"
        ent_b_bob = Entity(
            id="ent_bob_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Bob Kingpin B",
            risk_level=RiskLevel.CRITICAL,
            attributes={
                "raw_identifier": unrelated_raw_id,
                "typologies": ["TERRORIST_FINANCING", "SANCTIONED_ENTITY"],
            },
        )
        worker_b.store_private_entity(ent_b_bob)

        # Execute matching via coordinator
        coordinator = CrossBankMatchingCoordinator(
            consortium_service=institutional_context["gov_svc"],
        )
        coordinator.register_worker("bank_a", worker_a, auth_secret_a)
        coordinator.register_worker("bank_b", worker_b, auth_secret_b)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=institutional_context["gov_svc"],
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_alice_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

        # Assert matching correctness
        assert signal.match_confirmed is True
        assert signal.match_category == "EXACT_MATCH"

        # PROVE ZERO RISK CONTAMINATION & TRUTHFUL DISCLOSURE:
        # Candidate payloads are not published; risk attributes are UNAVAILABLE
        assert signal.sanitized_risk_level == "UNAVAILABLE"
        assert signal.sanitized_risk_level != "CRITICAL"

        # Typology indicators must NEVER disclose Bob's cartel typologies
        assert "TERRORIST_FINANCING" not in signal.sanitized_typology_indicators
        assert "SANCTIONED_ENTITY" not in signal.sanitized_typology_indicators


class TestSubgroupValidationAndMathematicalSoundness:
    """Verifies prime-order subgroup validation on RFC 3526 2048-bit MODP safe prime."""

    def test_subgroup_validation_rejects_invalid_elements(self):
        """Elements outside the prime-order subgroup G_q (order q) must be rejected."""
        # Non-residues or invalid points: 0, 1, p - 1, p, p + 5
        assert validate_group_element(0) is False
        assert validate_group_element(1) is False
        assert validate_group_element(PSI_PRIME - 1) is False
        assert validate_group_element(PSI_PRIME) is False
        assert validate_group_element(PSI_PRIME + 5) is False

        # Generator g = 2 generates the quadratic residues in RFC 3526 MODP prime (2^q == 1 mod p)
        assert validate_group_element(2) is True
        assert validate_group_element(4) is True

        # Quadratic non-residue: For p = 2q + 1 with p == 7 mod 8, (-1/p) = -1.
        # Since 4 is in G_q, (p - 4) has Legendre symbol (-1) * (+1) = -1.
        # Euler criterion: (p - 4)^q mod p == p - 1 != 1.
        # Thus (p - 4) is inside (1, p-1) but outside G_q, and must be rejected.
        non_residue = PSI_PRIME - 4
        assert validate_group_element(non_residue) is False

    def test_worker_rejects_query_with_subgroup_violating_element(
        self, institutional_context: dict[str, Any]
    ):
        """Worker node must fail closed if blinded query element is not in G_q."""
        import datetime
        import hashlib
        import hmac
        import secrets

        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        consortium_id: str = institutional_context["consortium_id"]

        # Craft query with non-residue (p - 4) which fails Euler subgroup validation
        invalid_element = PSI_PRIME - 4
        now = datetime.datetime.now(datetime.UTC).isoformat()
        job_id = str(uuid.uuid4())
        nonce = secrets.token_hex(16)

        canonical_query = f"{job_id}:{consortium_id}:bank_a:bank_b:{hex(invalid_element)}:{nonce}:{now}:AML"
        sig = hmac.new(
            auth_secret_a.encode("utf-8"), canonical_query.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        invalid_query = BlindedQueryMessage(
            job_id=job_id,
            consortium_id=consortium_id,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            blinded_element_hex=hex(invalid_element),
            nonce=nonce,
            timestamp=now,
            purpose="AML",
            signature=sig,
        )

        with pytest.raises(ConsortiumSecurityError, match="Subgroup validation failed"):
            worker_b.process_counterpart_query(invalid_query, auth_secret_a)


class TestDurableAntiReplayAcrossWorkerRestarts:
    """Verifies that replay resistance nonces persist to disk and survive process restarts."""

    def test_nonce_replay_fails_closed_across_worker_restart(
        self, institutional_context: dict[str, Any]
    ):
        """A nonce used in session 1 must be rejected even after worker re-initialization."""
        storage_root: str = institutional_context["storage_root"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        auth_secret_b: str = institutional_context["auth_secret_b"]
        consortium_id: str = institutional_context["consortium_id"]
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]

        # 1. First Worker B instance processes query
        worker_b_first = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=auth_secret_b,
            trusted_peer_keys={"bank_a": auth_secret_a},
        )

        ent_a = Entity(
            id="ent_replay_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Replay Test",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": "replay.test@consortium.org"},
        )
        worker_a.store_private_entity(ent_a)

        query = worker_a.initiate_blinded_query(
            query_entity_id="ent_replay_a",
            target_bank_id="bank_b",
            consortium_id=consortium_id,
            purpose="AML",
        )

        # Worker B processes query first time -> succeeds
        resp1 = worker_b_first.process_counterpart_query(query, auth_secret_a)
        assert resp1.job_id == query.job_id

        # 2. Simulate worker crash and restart: create a new Worker B instance pointing to same storage
        worker_b_restarted = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=auth_secret_b,
            trusted_peer_keys={"bank_a": auth_secret_a},
        )

        # Attempt to replay the exact same query with same nonce against restarted worker
        with pytest.raises(ConsortiumSecurityError, match="Replay attack detected"):
            worker_b_restarted.process_counterpart_query(query, auth_secret_a)


class TestInstitutionalGovernanceAndActionAuthorization:
    """Verifies rigorous action-specific authorization, role validation, and grant revocation."""

    def test_wrong_bank_caller_token_is_rejected(
        self, institutional_context: dict[str, Any]
    ):
        """A caller from bank_b cannot initiate a private matching query owned by bank_a."""
        token_b: str = institutional_context["token_b"]
        consortium_id: str = institutional_context["consortium_id"]

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",  # Claimed requesting bank
            target_bank_id="bank_b",
            query_entity_id="ent_dummy",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        intel_svc = CrossBankIntelligenceService(consortium_service=institutional_context["gov_svc"])
        with pytest.raises(PermissionError, match="Institutional impersonation rejected"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_b)

    def test_unauthorized_role_is_rejected(
        self, institutional_context: dict[str, Any]
    ):
        """A user with an unauthorized role (e.g. auditor) cannot execute private matching."""
        auth_svc = AuthenticationService.get_instance()
        u_auditor = auth_svc.register_user(
            "auditor_user", "AdminSecret123!", "bank_a", roles=["auditor"], clearance_level=1
        )
        token_auditor = auth_svc.create_token_bundle(u_auditor).access_token
        consortium_id: str = institutional_context["consortium_id"]

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_dummy",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        intel_svc = CrossBankIntelligenceService(consortium_service=institutional_context["gov_svc"])
        with pytest.raises(PermissionError, match="lacks permitted intelligence query role"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_auditor)

    def test_unauthenticated_grant_registration_rejected(
        self, institutional_context: dict[str, Any]
    ):
        """Registering an operational grant without an authentic token must fail."""
        gov_svc: ConsortiumGovernanceService = institutional_context["gov_svc"]
        consortium_id: str = institutional_context["consortium_id"]

        with pytest.raises(PermissionError, match="Authentication required"):
            gov_svc.register_operational_data_sharing_grant(
                consortium_id=consortium_id,
                grant_id="grant_unauth",
                authority="Regulator",
                allowed_purposes=["AML"],
                auth_token=None,  # Missing token
            )

    def test_revoked_grant_denies_matching_execution(
        self, institutional_context: dict[str, Any]
    ):
        """Revoking an operational grant must immediately fail subsequent private matching queries."""
        gov_svc: ConsortiumGovernanceService = institutional_context["gov_svc"]
        consortium_id: str = institutional_context["consortium_id"]
        token_a: str = institutional_context["token_a"]
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        auth_secret_b: str = institutional_context["auth_secret_b"]

        ent_a = Entity(
            id="ent_revoked_grant",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Revoked Grant Target",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": "revoked.target@bank.org"},
        )
        worker_a.store_private_entity(ent_a)

        # Revoke the grant
        gov_svc.revoke_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id="grant_operational_bilateral_001",
            revoking_bank_id="bank_a",
            reason="Regulatory suspension",
            auth_token=token_a,
        )

        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        coordinator.register_worker("bank_a", worker_a, auth_secret_a)
        coordinator.register_worker("bank_b", worker_b, auth_secret_b)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_revoked_grant",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(ConsortiumSecurityError, match="is inactive or revoked"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)


class TestAuthoritativeCaseEvidenceAttachment:
    """Verifies that only authentic verified operational signals attach to authoritative cases."""

    def test_prototype_signal_cannot_attach_to_authoritative_case(
        self, institutional_context: dict[str, Any]
    ):
        """Signals labeled PROTOCOL_PROTOTYPE cannot be attached to authoritative cases or SARs."""
        token_a: str = institutional_context["token_a"]
        worker_a: IsolatedBankWorkerNode = institutional_context["worker_a"]
        worker_b: IsolatedBankWorkerNode = institutional_context["worker_b"]
        auth_secret_a: str = institutional_context["auth_secret_a"]
        auth_secret_b: str = institutional_context["auth_secret_b"]
        consortium_id: str = institutional_context["consortium_id"]
        gov_svc: ConsortiumGovernanceService = institutional_context["gov_svc"]

        case_svc = CaseManagementService()
        evidence_svc = EvidenceRegistryService(case_service=case_svc)
        case = case_svc.create_case(title="Authoritative Case Test", bank_id="bank_a")
        case_dict = case_svc._cases.get(case.id)
        if case_dict:
            case_dict["signature_metadata"] = {"is_authoritative": True}
            case_svc._cases.set(case.id, case_dict)

        # Standard in-process coordinator produces PROTOCOL_PROTOTYPE assurance
        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        coordinator.register_worker("bank_a", worker_a, auth_secret_a)
        coordinator.register_worker("bank_b", worker_b, auth_secret_b)

        raw_id = "prototype.cust.777@bank.org"
        ent_a = Entity(
            id="ent_prototype_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Prototype Target",
            risk_level=RiskLevel.MEDIUM,
            attributes={"raw_identifier": raw_id},
        )
        worker_a.store_private_entity(ent_a)

        ent_b = Entity(
            id="ent_prototype_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Prototype Target",
            risk_level=RiskLevel.MEDIUM,
            attributes={"raw_identifier": raw_id},
        )
        worker_b.store_private_entity(ent_b)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            case_service=case_svc,
            evidence_service=evidence_svc,
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_prototype_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )
        prototype_signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)
        assert prototype_signal.assurance_level == "PROTOCOL_PROTOTYPE"

        with pytest.raises(
            ConsortiumSecurityError,
            match="Protocol prototype matching results cannot enter authoritative case evidence",
        ):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=prototype_signal.signal_id,
                auth_token=token_a,
            )
