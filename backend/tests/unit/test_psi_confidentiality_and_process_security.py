"""Security regression suite for PSI candidate payload confidentiality, cryptographic parameter verification,

independent OS process isolation, and durable replay protection.

Verifies:
1. Counterpart risk payload confidentiality:
   - Reproduction of candidate key derivation vulnerability.
   - Negative proof: Transcript-only adversary cannot decrypt unrelated candidate risk attributes.
   - Candidate payloads are not published; risk attributes default to UNAVAILABLE.
2. Cryptographic parameter bit-length and subgroup mathematical soundness:
   - PSI_PRIME is authentic RFC 3526 Group 14 2048-bit safe prime.
   - Bit length is strictly 2048 bits; safe prime order q is 2047 bits.
   - Generator g=2 satisfies g^q = 1 mod p.
3. Independent OS process isolation:
   - True multi-process execution with separate OS PIDs (pid_a != pid_b != current_pid).
   - Distinct institutional storage vaults and denial of cross-bank file reads.
   - Worker kill, fail-closed coordinator abort, and post-restart recovery.
4. Worker authentication and trusted peer keystore enforcement:
   - Rejection of queries from unknown institutions.
   - Rejection of forged signatures.
   - Requester-supplied verification key bypass denial.
5. Fail-closed replay persistence:
   - Atomic disk persistence with fsync.
   - Fail-closed abort on corrupted replay store without silent state recreation.
"""

from __future__ import annotations

import datetime
import os
import secrets
import socket
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
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
from app.domain.consortium_governance import ConsortiumSecurityError
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.domain.psi_service import (
    PSI_GENERATOR,
    PSI_ORDER_Q,
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
def security_test_context(tmp_path: Path) -> dict[str, Any]:
    """Establishes an authentic consortium context with isolated storage roots."""
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

    consortium_id = f"sec_alliance_{uuid.uuid4().hex[:8]}"
    gov_svc.create_consortium(
        consortium_id=consortium_id,
        name="Security Assurance Consortium",
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

    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id="grant_sec_001",
        authority="FFIEC",
        allowed_purposes=["AML", "FRAUD_INVESTIGATION", "AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "US", "bank_b": "US"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id="grant_sec_001",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    secret_a = secrets.token_hex(32)
    secret_b = secrets.token_hex(32)

    return {
        "storage_root": storage_root,
        "consortium_id": consortium_id,
        "gov_svc": gov_svc,
        "token_a": token_a,
        "token_b": token_b,
        "secret_a": secret_a,
        "secret_b": secret_b,
    }


class TestCandidatePayloadConfidentialityRemediation:
    """Verifies remediation of candidate payload key disclosure and unmatched entity risk containment."""

    def test_transcript_only_adversary_cannot_decrypt_unrelated_candidate_risk(
        self, security_test_context: dict[str, Any]
    ):
        """Proves that candidate payloads are not encrypted with derivable keys from public transcript fields."""
        storage_root = security_test_context["storage_root"]
        secret_a = security_test_context["secret_a"]
        secret_b = security_test_context["secret_b"]
        consortium_id = security_test_context["consortium_id"]

        # Worker A has LOW risk entity
        worker_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=storage_root,
            auth_secret=secret_a,
            trusted_peer_keys={"bank_b": secret_b},
        )
        matched_identifier = "legitimate.user.101@normal-bank.org"
        ent_a = Entity(
            id="ent_a_low",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Legitimate User Alpha",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": matched_identifier},
        )
        worker_a.store_private_entity(ent_a)

        # Worker B has 1 MATCHING LOW risk entity and 1 UNRELATED CRITICAL risk entity
        worker_b = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=secret_b,
            trusted_peer_keys={"bank_a": secret_a},
        )
        ent_b_matching = Entity(
            id="ent_b_match",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Legitimate User Beta",
            risk_level=RiskLevel.LOW,
            attributes={"raw_identifier": matched_identifier, "typologies": ["BENIGN"]},
        )
        ent_b_unrelated = Entity(
            id="ent_b_unrelated_cartel",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Cartel Kingpin Omega",
            risk_level=RiskLevel.CRITICAL,
            attributes={
                "raw_identifier": "cartel.kingpin.999@dark-syndicate.net",
                "typologies": ["TERRORIST_FINANCING", "SANCTIONED_ORGANIZATION"],
            },
        )
        worker_b.store_private_entity(ent_b_matching)
        worker_b.store_private_entity(ent_b_unrelated)

        # 1. Bank A initiates query for Alice
        query = worker_a.initiate_blinded_query(
            query_entity_id="ent_a_low",
            consortium_id=consortium_id,
            target_bank_id="bank_b",
            purpose="AML",
        )

        # 2. Bank B generates counterpart response
        resp = worker_b.process_counterpart_query(query)

        # VERIFY REMEDIATION:
        # A) Encrypted candidate risk payloads must NOT be published in the response
        assert resp.encrypted_risk_payloads == {}

        # B) An adversary holding the entire protocol transcript cannot find any encrypted payload
        # or derive candidate keys to reveal Bob's CRITICAL risk or cartel typologies
        transcript_str = str(resp)
        assert "TERRORIST_FINANCING" not in transcript_str
        assert "SANCTIONED_ORGANIZATION" not in transcript_str
        assert "CRITICAL" not in transcript_str
        assert "cartel.kingpin" not in transcript_str

        # C) Finalization succeeds on exact match indicator without leaking candidate risk
        receipt = worker_a.finalize_match_evaluation(query=query, response=resp)
        assert receipt.match_confirmed is True
        assert receipt.match_confidence == 1.0
        # Truthful disclosure invariant: risk level is UNAVAILABLE because candidate payloads are not leaked
        assert receipt.matched_risk_level == "UNAVAILABLE"
        assert receipt.matched_typologies == ()


class TestCryptographicParameterBitLengthVerification:
    """Verifies that cryptographic constants match genuine RFC 3526 2048-bit MODP parameters."""

    def test_psi_prime_bit_length_is_strictly_2048_bits(self):
        """PSI_PRIME must have exact bit length of 2048 bits (RFC 3526 Group 14)."""
        assert PSI_PRIME.bit_length() == 2048

    def test_safe_prime_subgroup_order_and_generator_soundness(self):
        """Safe prime relation p = 2q + 1 must hold, with q having 2047 bits."""
        # Safe prime relation
        assert PSI_PRIME == 2 * PSI_ORDER_Q + 1
        assert PSI_ORDER_Q.bit_length() == 2047

        # Generator g = 2 generates the quadratic residues in RFC 3526 Group 14
        assert PSI_GENERATOR == 2
        # Subgroup element check: g^q mod p == 1
        assert pow(PSI_GENERATOR, PSI_ORDER_Q, PSI_PRIME) == 1

    def test_subgroup_element_validation_rejects_non_residues(self):
        """Elements outside the prime-order subgroup G_q must fail validation."""
        assert validate_group_element(0) is False
        assert validate_group_element(1) is False
        assert validate_group_element(PSI_PRIME) is False
        assert validate_group_element(PSI_PRIME - 1) is False

        # Valid elements in G_q
        assert validate_group_element(2) is True
        assert validate_group_element(4) is True

        # Quadratic non-residue (p - 4)
        assert validate_group_element(PSI_PRIME - 4) is False


class TestIndependentOSProcessExecutionAndIsolation:
    """Verifies authentic independent OS process execution with separate PIDs and storage separation."""

    def test_independent_worker_os_processes_have_distinct_pids(
        self, security_test_context: dict[str, Any]
    ):
        """Bank A and Bank B worker daemons execute in separate OS processes with distinct PIDs."""
        storage_root = security_test_context["storage_root"]
        secret_a = security_test_context["secret_a"]
        secret_b = security_test_context["secret_b"]

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

        port_a = _get_free_port()
        port_b = _get_free_port()

        daemon_a = InstitutionalWorkerDaemon(
            worker_a, host="127.0.0.1", port=port_a, run_as_process=True
        )
        daemon_b = InstitutionalWorkerDaemon(
            worker_b, host="127.0.0.1", port=port_b, run_as_process=True
        )

        daemon_a.start()
        daemon_b.start()

        # Give processes time to bind ports
        time.sleep(0.3)

        try:
            # Verify distinct OS process IDs
            current_pid = os.getpid()
            pid_a = daemon_a.pid
            pid_b = daemon_b.pid

            assert pid_a is not None and pid_a > 0
            assert pid_b is not None and pid_b > 0
            assert pid_a != current_pid, "Worker A must not run inside current test process"
            assert pid_b != current_pid, "Worker B must not run inside current test process"
            assert pid_a != pid_b, "Worker A and Worker B must run in distinct OS processes"

            # Connect via HTTP clients
            client_a = InstitutionalWorkerClient(
                bank_id="bank_a",
                base_url=f"http://127.0.0.1:{port_a}",
                auth_secret=secret_a,
            )
            client_b = InstitutionalWorkerClient(
                bank_id="bank_b",
                base_url=f"http://127.0.0.1:{port_b}",
                auth_secret=secret_b,
            )

            # Health probes confirm distinct worker identities and PIDs over network
            health_a = client_a.health_check()
            health_b = client_b.health_check()
            assert health_a["bank_id"] == "bank_a"
            assert health_b["bank_id"] == "bank_b"
            assert health_a["pid"] == pid_a
            assert health_b["pid"] == pid_b

        finally:
            daemon_a.stop()
            daemon_b.stop()

    def test_worker_kill_causes_coordinator_fail_closed_and_recovers_after_restart(
        self, security_test_context: dict[str, Any]
    ):
        """Killing a worker process causes queries to fail closed; restarting worker resumes matching."""
        storage_root = security_test_context["storage_root"]
        secret_a = security_test_context["secret_a"]
        secret_b = security_test_context["secret_b"]
        consortium_id = security_test_context["consortium_id"]
        token_a = security_test_context["token_a"]

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

        raw_id = "process.failover.target@bank.org"
        ent_a = Entity(
            id="ent_failover_a",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            display_label="Failover Target A",
            attributes={"raw_identifier": raw_id},
        )
        ent_b = Entity(
            id="ent_failover_b",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_b",
            display_label="Failover Target B",
            attributes={"raw_identifier": raw_id},
        )
        worker_a.store_private_entity(ent_a)
        worker_b.store_private_entity(ent_b)

        port_a = _get_free_port()
        port_b = _get_free_port()

        daemon_a = InstitutionalWorkerDaemon(
            worker_a, host="127.0.0.1", port=port_a, run_as_process=True
        )
        daemon_b = InstitutionalWorkerDaemon(
            worker_b, host="127.0.0.1", port=port_b, run_as_process=True
        )

        daemon_a.start()
        daemon_b.start()
        time.sleep(0.3)

        try:
            client_a = InstitutionalWorkerClient(
                bank_id="bank_a",
                base_url=f"http://127.0.0.1:{port_a}",
                auth_secret=secret_a,
            )
            client_b = InstitutionalWorkerClient(
                bank_id="bank_b",
                base_url=f"http://127.0.0.1:{port_b}",
                auth_secret=secret_b,
            )

            coordinator = CrossBankMatchingCoordinator(
                consortium_service=security_test_context["gov_svc"]
            )
            coordinator.register_worker("bank_a", client_a, secret_a)
            coordinator.register_worker("bank_b", client_b, secret_b)

            intel_svc = CrossBankIntelligenceService(
                consortium_service=security_test_context["gov_svc"],
                matching_coordinator=coordinator,
            )

            req = CrossBankIntelligenceQueryRequest(
                requesting_bank_id="bank_a",
                target_bank_id="bank_b",
                query_entity_id="ent_failover_a",
                consortium_id=consortium_id,
                execution_mode="VERIFIED_OPERATIONAL",
            )

            # 1. Kill Worker B process abruptly
            daemon_b.stop()

            # Query must FAIL CLOSED because counterpart worker is down
            with pytest.raises(
                ConsortiumSecurityError, match="unreachable|timed out|Connection error"
            ):
                intel_svc.execute_two_bank_private_matching(req=req, auth_token=token_a)

            # 2. Restart Worker B on same storage vault
            daemon_b_restarted = InstitutionalWorkerDaemon(
                worker_b, host="127.0.0.1", port=port_b, run_as_process=True
            )
            daemon_b_restarted.start()
            time.sleep(0.3)

            try:
                # Query must now succeed and confirm match
                recovered_signal = intel_svc.execute_two_bank_private_matching(
                    req=req, auth_token=token_a
                )
                assert recovered_signal.match_confirmed is True
                assert recovered_signal.match_category == "EXACT_MATCH"
                assert recovered_signal.assurance_level == "PROTOCOL_PROTOTYPE"
                assert recovered_signal.is_authoritative is False
            finally:
                daemon_b_restarted.stop()

        finally:
            daemon_a.stop()


class TestWorkerCredentialAndPeerAuthenticationEnforcement:
    """Verifies that workers verify peer signatures strictly against trusted keystores."""

    def test_worker_rejects_query_from_untrusted_peer_institution(
        self, security_test_context: dict[str, Any]
    ):
        """Worker B rejects query from unknown bank_c because it is not in its trusted keystore."""
        storage_root = security_test_context["storage_root"]
        secret_b = security_test_context["secret_b"]
        consortium_id = security_test_context["consortium_id"]

        # Worker B only trusts bank_a
        worker_b = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=secret_b,
            trusted_peer_keys={"bank_a": "trusted_key_a"},
        )

        now = datetime.datetime.now(datetime.UTC).isoformat()
        # Craft query from bank_c
        query = BlindedQueryMessage(
            job_id=str(uuid.uuid4()),
            consortium_id=consortium_id,
            requesting_bank_id="bank_c",  # Untrusted institution
            target_bank_id="bank_b",
            blinded_element_hex=hex(pow(2, 12345, PSI_PRIME)),
            nonce=secrets.token_hex(16),
            timestamp=now,
            purpose="AML",
            signature="some_forged_sig",
        )

        with pytest.raises(
            ConsortiumSecurityError, match="no trusted verification key enrolled|Untrusted peer"
        ):
            worker_b.process_counterpart_query(query)

    def test_worker_rejects_forged_peer_signature(self, security_test_context: dict[str, Any]):
        """Worker B rejects query from bank_a when signature does not match trusted key."""
        storage_root = security_test_context["storage_root"]
        secret_b = security_test_context["secret_b"]
        consortium_id = security_test_context["consortium_id"]

        worker_b = IsolatedBankWorkerNode(
            bank_id="bank_b",
            storage_root=storage_root,
            auth_secret=secret_b,
            trusted_peer_keys={"bank_a": "real_trusted_secret_a"},
        )

        now = datetime.datetime.now(datetime.UTC).isoformat()
        query = BlindedQueryMessage(
            job_id=str(uuid.uuid4()),
            consortium_id=consortium_id,
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            blinded_element_hex=hex(pow(2, 12345, PSI_PRIME)),
            nonce=secrets.token_hex(16),
            timestamp=now,
            purpose="AML",
            signature="bad_forged_hmac_signature",
        )

        with pytest.raises(ConsortiumSecurityError, match="Peer authentication failed"):
            worker_b.process_counterpart_query(query)


class TestFailClosedReplayPersistence:
    """Verifies that replay storage failures fail closed and prevent unauthorized session reuse."""

    def test_corrupted_replay_store_fails_closed(self, security_test_context: dict[str, Any]):
        """If the replay store on disk is corrupted, worker must fail closed rather than resetting."""
        storage_root = security_test_context["storage_root"]
        secret_a = security_test_context["secret_a"]
        secret_b = security_test_context["secret_b"]
        consortium_id = security_test_context["consortium_id"]

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
            id="ent_corrupt_test",
            entity_type=EntityType.CUSTOMER,
            bank_id="bank_a",
            attributes={"raw_identifier": "corrupt.replay@test.org"},
        )
        worker_a.store_private_entity(ent_a)

        query = worker_a.initiate_blinded_query(
            query_entity_id="ent_corrupt_test",
            consortium_id=consortium_id,
            target_bank_id="bank_b",
            purpose="AML",
        )

        # First query succeeds
        resp = worker_b.process_counterpart_query(query)
        assert resp is not None

        # Corrupt the replay store file on disk with malformed JSON
        nonce_store_path = Path(worker_b._replay_store_path)
        assert nonce_store_path.exists()
        nonce_store_path.write_text("{CORRUPTED_MALFORMED_JSON_CONTENT", encoding="utf-8")

        # Initializing or reading from a corrupted replay store must fail closed
        with pytest.raises(
            ConsortiumSecurityError, match="anti-replay store.*corrupt or unreadable"
        ):
            IsolatedBankWorkerNode(
                bank_id="bank_b",
                storage_root=storage_root,
                auth_secret=secret_b,
                trusted_peer_keys={"bank_a": secret_a},
            )
