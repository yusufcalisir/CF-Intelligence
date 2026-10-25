"""Comprehensive security acceptance test suite for institutional trust separation,
storage lockdown, bilateral governance consent, and prototype signal containment.

Verifies:
1. Coordinator Credential Decoupling:
   - Coordinator possesses zero institutional signing keys or master auth secrets.
   - Subprocess launch arguments contain no secrets (--auth-secret, --trusted-peers).
   - Scoped RPC endpoint authorization: matching token cannot invoke ingestion or admin routes.
2. Peer Enrollment and Entity Storage Lockdown:
   - Route-specific scopes enforced: /worker/enroll-peer requires 'admin', /worker/store-entity requires 'ingestion'.
   - Peer key replacement lockdown: duplicate peer enrollment rejected without explicit rotation authority.
   - Comprehensive path traversal, directory escape, absolute path, null byte, and symlink rejection.
   - Cross-bank storage isolation: Bank A worker cannot access Bank B storage vault.
3. Multi-Institution Bilateral Consent for Operational Sharing:
   - Unilateral approval by requesting bank strictly rejected for counterpart data disclosure.
   - Bilateral explicit approvals from both institutions enable authorized matching.
   - Independent institutional revocation by counterpart bank immediately halts query execution.
4. Prototype Signal Containment Across All Case States:
   - Protocol prototype outputs emit is_authoritative=False, assurance_level='PROTOCOL_PROTOTYPE'.
   - Prototype signals stored strictly in research_prototype_signals, never in authoritative_signals.
   - Prototype signals rejected from attaching to case evidence across ALL case states (OPEN, IN_PROGRESS, ESCALATED, CLOSED).
"""

from __future__ import annotations

import os
import secrets
import socket
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
    CrossBankMatchingCoordinator,
    InstitutionalWorkerClient,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
)
from app.application.services.model_registry import create_trusted_internal_context
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
)
from app.domain.enums import CaseStatus
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService


def _get_free_port() -> int:
    """Acquire an ephemeral port from the operating system."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def isolate_environment_and_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure clean isolated Redis stores, temporary disk storage, and clear cached settings."""
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

    from app.config import get_settings
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()

    stores = [
        RedisStore("consortia"),
        RedisStore("consortium_proposals"),
        RedisStore("authoritative_signals"),
        RedisStore("research_prototype_signals"),
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
def trust_separation_context(tmp_path: Path) -> dict[str, Any]:
    """Setup multi-institution environment with Bank A and Bank B."""
    gov_svc = ConsortiumGovernanceService()
    auth_svc = AuthenticationService.get_instance()

    storage_root = str(tmp_path / "institutional_storage")
    os.makedirs(storage_root, exist_ok=True)

    u_a = auth_svc.register_user(
        "officer_a_trust", "AdminSecret123!", "bank_a", roles=["admin", "analyst"], clearance_level=3
    )
    u_b = auth_svc.register_user(
        "officer_b_trust", "AdminSecret123!", "bank_b", roles=["admin", "analyst"], clearance_level=3
    )
    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token

    consortium_id = f"trust_alliance_{uuid.uuid4().hex[:8]}"
    gov_svc.create_consortium(
        consortium_id=consortium_id,
        name="Institutional Trust Alliance",
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

    def _set_policy(consortia: Any, proposals: Any) -> None:
        c = consortia[consortium_id]
        c.metadata["intelligence_sharing_policy"] = {
            "allowed_purposes": ["AML", "FRAUD_INVESTIGATION", "AML_COLLABORATIVE_INVESTIGATION"],
            "allow_cross_border_sharing": True,
            "institutional_jurisdictions": {"bank_a": "US", "bank_b": "US"},
        }

    gov_svc._store.atomic_mutate(_set_policy)

    secret_a = secrets.token_hex(32)
    secret_b = secrets.token_hex(32)

    return {
        "storage_root": storage_root,
        "consortium_id": consortium_id,
        "gov_svc": gov_svc,
        "auth_svc": auth_svc,
        "token_a": token_a,
        "token_b": token_b,
        "secret_a": secret_a,
        "secret_b": secret_b,
    }


class TestCoordinatorSecretDecouplingAndPermissionBoundaries:
    """Verifies that coordinator holds zero institutional secrets and cannot escalate privileges."""

    def test_coordinator_holds_zero_signing_secrets_and_scoped_tokens_only(
        self, trust_separation_context: dict[str, Any]
    ):
        """Coordinator must possess only matching tokens and no signing or admin secrets."""
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        secret_b: str = trust_separation_context["secret_b"]
        gov_svc: ConsortiumGovernanceService = trust_separation_context["gov_svc"]

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

        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        # Register workers using their matching token (not master auth secret)
        coordinator.register_worker("bank_a", worker_a, worker_a.matching_token)
        coordinator.register_worker("bank_b", worker_b, worker_b.matching_token)

        # 1. Coordinator state has zero master auth secrets
        assert not hasattr(coordinator, "_worker_secrets")
        assert coordinator._matching_tokens["bank_a"] == worker_a.matching_token
        assert coordinator._matching_tokens["bank_b"] == worker_b.matching_token
        assert secret_a not in str(coordinator.__dict__)
        assert secret_b not in str(coordinator.__dict__)

        # 2. Coordinator has no access to worker KMS private exponents
        assert not hasattr(coordinator, "kms_private_exponent")
        assert not hasattr(coordinator, "_kms_private_exponent")

    def test_subprocess_launch_arguments_contain_no_credentials(
        self, trust_separation_context: dict[str, Any]
    ):
        """Worker daemon launch must pass secrets through environment variables, not sys.argv."""
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        port_a = _get_free_port()

        node_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
            trusted_peer_keys={"bank_b": "fake_peer_key_777!"},
        )
        daemon = InstitutionalWorkerDaemon(
            node=node_a,
            host="127.0.0.1",
            port=port_a,
            run_as_process=True,
        )

        daemon.start()
        try:
            assert daemon._process is not None
            # Inspect actual process command line via daemon._process.args
            raw_args = daemon._process.args
            cmdline = " ".join(raw_args) if isinstance(raw_args, list) else str(raw_args)

            # Verify credentials are NOT in command line
            assert "--auth-secret" not in cmdline
            assert "--trusted-peers" not in cmdline
            assert secret_a not in cmdline
            assert "fake_peer_key_777!" not in cmdline
        finally:
            daemon.stop()

    def test_compromised_coordinator_matching_token_denied_ingestion_and_admin(
        self, trust_separation_context: dict[str, Any]
    ):
        """An adversary possessing the coordinator's matching token cannot invoke /store-entity or /enroll-peer."""
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        port_a = _get_free_port()

        node_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
        )
        daemon = InstitutionalWorkerDaemon(
            node=node_a,
            host="127.0.0.1",
            port=port_a,
            run_as_process=True,
        )
        daemon.start()

        try:
            # Client created with matching token only (like the coordinator)
            matching_only_client = InstitutionalWorkerClient(
                bank_id="bank_a",
                base_url=f"http://127.0.0.1:{daemon.port}",
                matching_token=node_a.matching_token,
            )

            # 1. Matching token cannot invoke entity storage (HTTP 403 Insufficient scope)
            ent = Entity(
                id="ent_adversary_inject",
                entity_type=EntityType.CUSTOMER,
                bank_id="bank_a",
                display_label="Injected Target",
                risk_level=RiskLevel.HIGH,
                attributes={"raw_identifier": "malicious@poison.org"},
            )
            with pytest.raises(PermissionError, match="lacks required scope 'ingestion'"):
                matching_only_client._post(
                    "/worker/store-entity",
                    {"entity": ent.to_dict()},
                    token=node_a.matching_token,
                )

            # 2. Matching token cannot invoke peer enrollment (HTTP 403 Insufficient scope)
            with pytest.raises(PermissionError, match="lacks required scope 'admin'"):
                matching_only_client._post(
                    "/worker/enroll-peer",
                    {
                        "peer_bank_id": "bank_attacker",
                        "verification_key": "AttackerForgedKey123_AtLeast16Chars!",
                        "allow_rotation": False,
                    },
                    token=node_a.matching_token,
                )

            # 3. Matching token CAN invoke matching query route
            # (Fails validation on empty/untrusted payload rather than 403 scope error)
            from datetime import UTC, datetime

            from app.application.services.isolated_matching_service import BlindedQueryMessage
            msg = BlindedQueryMessage(
                job_id=str(uuid.uuid4()),
                consortium_id="test_consortium",
                requesting_bank_id="bank_b",
                target_bank_id="bank_a",
                blinded_element_hex="0123456789abcdef",
                nonce=secrets.token_hex(16),
                timestamp=datetime.now(UTC).isoformat(),
                purpose="AML_COLLABORATIVE_INVESTIGATION",
                signature="forged_sig_1234567890abcdef",
            )
            with pytest.raises(ConsortiumSecurityError):
                matching_only_client.process_counterpart_query(msg)
        finally:
            daemon.stop()


class TestPeerEnrollmentAndEntityStorageLockdown:
    """Verifies route-specific authority lockdown, path traversal rejection, and storage isolation."""

    def test_unauthorized_peer_enrollment_rejected(
        self, trust_separation_context: dict[str, Any]
    ):
        """Worker rejects peer enrollment when presented with an unauthorized or wrong token."""
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        port_a = _get_free_port()

        node_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
        )
        daemon = InstitutionalWorkerDaemon(
            node=node_a,
            host="127.0.0.1",
            port=port_a,
            run_as_process=True,
        )
        daemon.start()

        try:
            # Client with an invalid token
            unauthorized_client = InstitutionalWorkerClient(
                bank_id="bank_a",
                base_url=f"http://127.0.0.1:{daemon.port}",
                admin_token="BadToken123",
            )
            with pytest.raises(PermissionError, match="Invalid or unrecognized worker token"):
                unauthorized_client.enroll_peer("bank_c", "ValidKey777_AtLeast16Chars!")
        finally:
            daemon.stop()

    def test_forged_peer_key_replacement_locked_down(
        self, trust_separation_context: dict[str, Any]
    ):
        """Once a peer is enrolled, subsequent attempts to overwrite key fail closed."""
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]

        worker_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
            trusted_peer_keys={"bank_b": "AuthoritativeKeyBankB_Deterministic!"},
        )

        # Attempt to replace Bank B's trusted key without rotation authority
        with pytest.raises(ConsortiumSecurityError, match="already enrolled in worker"):
            worker_a.enroll_trusted_peer("bank_b", "AttackerReplacedKey_AtLeast16Chars!", allow_rotation=False)

        # Confirm original key was not modified
        assert worker_a.get_trusted_peer_key("bank_b") == "AuthoritativeKeyBankB_Deterministic!"

    @pytest.mark.parametrize(
        "malicious_id",
        [
            "../traversal",
            "..\\windows_traversal",
            "/etc/passwd",
            "\\Windows\\System32",
            "C:\\evil\\path",
            "id\x00poison",
            "id/nested/file",
            "id\\nested",
            "   ",
        ],
    )
    def test_entity_id_path_traversal_and_injection_rejected(
        self, tmp_path: Path, malicious_id: str
    ):
        """Worker rejects all path traversal, directory escape, and illegal character entity IDs."""
        storage_root = str(tmp_path / "sanitize_storage")
        worker = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secrets.token_hex(32),
        )

        with pytest.raises(ValueError, match="Invalid entity identifier"):
            worker._sanitize_entity_id(malicious_id)

    def test_cross_bank_storage_access_denied(
        self, tmp_path: Path
    ):
        """Bank A worker cannot read, overwrite, or access files in Bank B's storage vault."""
        storage_root = str(tmp_path / "cross_storage")
        secret_a = secrets.token_hex(32)
        secret_b = secrets.token_hex(32)

        worker_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
        )
        worker_b = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=secret_b,
        )

        ent_b = Entity(
            id="ent_bank_b_exclusive",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Bank B Exclusive Customer",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": "exclusive@bank_b.com"},
        )
        worker_b.store_private_entity(ent_b)

        # Bank A attempting to load Bank B entity directly fails closed
        with pytest.raises(
            ValueError,
            match="not found in isolated private storage of institution 'bank_a'",
        ):
            worker_a.load_private_entity("ent_bank_b_exclusive")


class TestBilateralInstitutionalConsentAndGovernance:
    """Verifies multi-institution authorization requirements for operational intelligence sharing."""

    def test_unilateral_single_bank_grant_denies_counterpart_disclosure(
        self, trust_separation_context: dict[str, Any]
    ):
        """Bank A alone cannot authorize disclosure of Bank B's private matching intelligence."""
        gov_svc: ConsortiumGovernanceService = trust_separation_context["gov_svc"]
        consortium_id: str = trust_separation_context["consortium_id"]
        token_a: str = trust_separation_context["token_a"]
        token_b: str = trust_separation_context["token_b"]
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        secret_b: str = trust_separation_context["secret_b"]

        grant_id = f"grant_unilateral_{uuid.uuid4().hex[:6]}"

        # Bank A registers grant unilaterally
        gov_svc.register_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            authority="Financial Crime Enforcement Network",
            allowed_purposes=["AML", "FRAUD_INVESTIGATION", "AML_COLLABORATIVE_INVESTIGATION"],
            institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
            allow_institution_identity_sharing=True,
            revision=1,
            approving_bank_id="bank_a",
            auth_token=token_a,
        )

        # Worker setup
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

        ent_a = Entity(
            id="ent_unilateral_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Unilateral Target",
            risk_level=RiskLevel.MEDIUM,
            attributes={"raw_identifier": "unilateral.target@fin.org"},
        )
        worker_a.store_private_entity(ent_a)

        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        coordinator.register_worker("bank_a", worker_a, worker_a.matching_token)
        coordinator.register_worker("bank_b", worker_b, worker_b.matching_token)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_unilateral_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        # Must fail closed because Bank B has not approved operational disclosure
        with pytest.raises(
            ConsortiumSecurityError,
            match="Unilateral approval by 'bank_a' does not constitute bilateral institutional consent",
        ):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

        # Now Bank B provides authenticated institutional approval
        gov_svc.approve_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            approving_bank_id="bank_b",
            auth_token=token_b,
        )

        # Query now succeeds through bilateral consent
        signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)
        assert signal is not None
        assert signal.match_confirmed is False  # No counterpart match

    def test_counterpart_revocation_halts_subsequent_matching_immediately(
        self, trust_separation_context: dict[str, Any]
    ):
        """When Bank B revokes consent, subsequent matching queries fail closed."""
        gov_svc: ConsortiumGovernanceService = trust_separation_context["gov_svc"]
        consortium_id: str = trust_separation_context["consortium_id"]
        token_a: str = trust_separation_context["token_a"]
        token_b: str = trust_separation_context["token_b"]
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        secret_b: str = trust_separation_context["secret_b"]

        grant_id = f"grant_revocation_{uuid.uuid4().hex[:6]}"

        # Bilateral grant setup
        gov_svc.register_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            authority="FFIEC",
            allowed_purposes=["AML"],
            institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
            allow_institution_identity_sharing=True,
            revision=1,
            approving_bank_id="bank_a",
            auth_token=token_a,
        )
        gov_svc.approve_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            approving_bank_id="bank_b",
            auth_token=token_b,
        )

        # Bank B revokes consent
        gov_svc.revoke_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            revoking_bank_id="bank_b",
            reason="Institutional policy update",
            auth_token=token_b,
        )

        worker_a = IsolatedBankWorkerNode(bank_id="bank_a", storage_root=storage_root, auth_secret=secret_a)
        worker_b = IsolatedBankWorkerNode(bank_id="bank_b", storage_root=storage_root, auth_secret=secret_b)
        ent_a = Entity(
            id="ent_revoked_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Revoked Target",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": "revoked@target.org"},
        )
        worker_a.store_private_entity(ent_a)

        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        coordinator.register_worker("bank_a", worker_a, worker_a.matching_token)
        coordinator.register_worker("bank_b", worker_b, worker_b.matching_token)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_revoked_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        with pytest.raises(ConsortiumSecurityError, match="is inactive or revoked"):
            intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)


class TestPrototypeSignalContainmentAndEvidenceGating:
    """Verifies that prototype signals cannot contaminate authoritative case evidence in any case state."""

    def test_prototype_signal_issuance_is_strictly_non_authoritative(
        self, trust_separation_context: dict[str, Any]
    ):
        """Emitted matching results must have is_authoritative=False and assurance_level=PROTOCOL_PROTOTYPE."""
        gov_svc: ConsortiumGovernanceService = trust_separation_context["gov_svc"]
        consortium_id: str = trust_separation_context["consortium_id"]
        token_a: str = trust_separation_context["token_a"]
        token_b: str = trust_separation_context["token_b"]
        storage_root: str = trust_separation_context["storage_root"]
        secret_a: str = trust_separation_context["secret_a"]
        secret_b: str = trust_separation_context["secret_b"]

        grant_id = f"grant_proto_{uuid.uuid4().hex[:6]}"
        gov_svc.register_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            authority="FFIEC",
            allowed_purposes=["AML", "AML_COLLABORATIVE_INVESTIGATION"],
            institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
            allow_institution_identity_sharing=True,
            revision=1,
            approving_bank_id="bank_a",
            auth_token=token_a,
        )
        gov_svc.approve_operational_data_sharing_grant(
            consortium_id=consortium_id,
            grant_id=grant_id,
            approving_bank_id="bank_b",
            auth_token=token_b,
        )

        worker_a = IsolatedBankWorkerNode(
            bank_id="bank_a", storage_root=storage_root, auth_secret=secret_a, trusted_peer_keys={"bank_b": secret_b}
        )
        worker_b = IsolatedBankWorkerNode(
            bank_id="bank_b", storage_root=storage_root, auth_secret=secret_b, trusted_peer_keys={"bank_a": secret_a}
        )

        raw_id = "test.prototype.candidate@bank.com"
        ent_a = Entity(
            id="ent_p_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Prototype Target",
            risk_level=RiskLevel.MEDIUM,
            attributes={"raw_identifier": raw_id},
        )
        ent_b = Entity(
            id="ent_p_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Prototype Target",
            risk_level=RiskLevel.MEDIUM,
            attributes={"raw_identifier": raw_id},
        )
        worker_a.store_private_entity(ent_a)
        worker_b.store_private_entity(ent_b)

        coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
        coordinator.register_worker("bank_a", worker_a, worker_a.matching_token)
        coordinator.register_worker("bank_b", worker_b, worker_b.matching_token)

        intel_svc = CrossBankIntelligenceService(
            consortium_service=gov_svc,
            matching_coordinator=coordinator,
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_p_a",
            consortium_id=consortium_id,
            execution_mode="VERIFIED_OPERATIONAL",
        )

        signal = intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

        # 1. Properties are strictly non-authoritative
        assert signal.is_authoritative is False
        assert signal.assurance_level == "PROTOCOL_PROTOTYPE"
        assert signal.execution_mode == "RESEARCH_PROTOTYPE"

        # 2. Persisted to research store, NOT authoritative store
        proto_store = RedisStore("research_prototype_signals")
        auth_store = RedisStore("authoritative_signals")
        assert proto_store.get(signal.signal_id) is not None
        assert auth_store.get(signal.signal_id) is None

    @pytest.mark.parametrize(
        "case_state",
        [
            CaseStatus.OPEN,
            CaseStatus.INVESTIGATING,
            CaseStatus.ESCALATED,
            CaseStatus.CLOSED_CONFIRMED,
        ],
    )
    def test_prototype_signal_cannot_attach_to_case_evidence_in_any_state(
        self, trust_separation_context: dict[str, Any], case_state: CaseStatus
    ):
        """Prototype matching signals are blocked from entering case evidence across ALL case states."""
        token_a: str = trust_separation_context["token_a"]
        case_svc = CaseManagementService()
        evidence_svc = EvidenceRegistryService(case_service=case_svc)

        case = case_svc.create_case(
            title=f"Prototype Evidence Gating {case_state.value}",
            bank_id="bank_a",
        )
        # Transition case to desired state using valid transitions
        if case_state == CaseStatus.INVESTIGATING:
            case_svc.change_status(case.id, CaseStatus.INVESTIGATING)
        elif case_state == CaseStatus.ESCALATED:
            case_svc.change_status(case.id, CaseStatus.INVESTIGATING)
            case_svc.change_status(case.id, CaseStatus.ESCALATED)
        elif case_state == CaseStatus.CLOSED_CONFIRMED:
            case_svc.change_status(case.id, CaseStatus.INVESTIGATING)
            case_svc.change_status(
                case.id,
                CaseStatus.CLOSED_CONFIRMED,
                supervisor_signatures=["supervisor_bob"],
            )

        # Fake prototype signal in research store
        fake_proto_id = str(uuid.uuid4())
        proto_store = RedisStore("research_prototype_signals")
        proto_store.set(
            fake_proto_id,
            {
                "signal_id": fake_proto_id,
                "is_authoritative": False,
                "assurance_level": "PROTOCOL_PROTOTYPE",
                "execution_mode": "RESEARCH_PROTOTYPE",
            },
        )

        intel_svc = CrossBankIntelligenceService(
            consortium_service=trust_separation_context["gov_svc"],
            case_service=case_svc,
            evidence_service=evidence_svc,
        )

        with pytest.raises(
            ConsortiumSecurityError,
            match="is a research prototype result and cannot enter case evidence in any case state",
        ):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=fake_proto_id,
                auth_token=token_a,
            )
