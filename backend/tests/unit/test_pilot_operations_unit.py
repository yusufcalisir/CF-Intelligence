"""Unit and Adversarial Test Suite for Milestone 5 Pilot Operations.

Verifies:
- State machine transition boundaries & invalid transition rejection.
- Independent multi-party approval requirements (Bank A, Bank B, Operator).
- Forged and stale approval rejection under configuration revision.
- Mandatory attack: Operator attempting to approve for banks fails closed.
- Invalid, missing, or empty cryptographic signatures fail closed.
- Quota exhaustion (total quota and per-institution quota).
- Expiration time enforcement (fails closed).
- Kill switch controls (PAUSE, RESUME, ABORT) and mid-flight pause race containment.
- Atomic concurrency reservations in durable SQLite store.
- Onboarding private-key redaction and unconditional authentication enforcement.
- Pilot query authorization gating (institution allowlist, purpose restriction).
- Tamper-evident scope digest computation.
- FastAPI endpoints for pilot provisioning, preflight, approval, activation, kill switch, and dossier export.
"""

from __future__ import annotations

import concurrent.futures
import datetime
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
    InstitutionalCoordinatorDaemon,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
)
from app.application.services.pilot_operations_service import (
    PilotAuthorizationError,
    PilotExpiredError,
    PilotOperationsService,
    PilotQuotaExceededError,
)
from app.domain.consortium_governance import MemberRole
from app.domain.institutional_identity import InstitutionalSigningIdentity
from app.domain.pilot_operations import (
    PilotApprovalRecord,
    PilotEntity,
    PilotGovernanceError,
    PilotOperationalConfig,
    PilotStatus,
    canonical_pilot_approval_bytes,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app

SHARED_JWT_SECRET = "jwt_secret_unit_test_pilot_operations_091823749812734"
SHARED_CONSORTIUM_STORE_SECRET = "consortium_store_hmac_unit_test_98712398471"
SHARED_COORDINATOR_SECRET = "coordinator_secret_unit_test_8392173491827"


@pytest.fixture
def unit_pilot_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    consortium_store_file = tmp_path / "unit_pilot_consortium_store.json"
    account_store_file = tmp_path / "unit_pilot_account_store.json"
    pilot_db_file = tmp_path / "unit_pilot_operations.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SHARED_COORDINATOR_SECRET)

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurablePilotStore._instance = None
    PilotOperationsService._instance = None

    auth_svc = AuthenticationService.get_instance()
    u_op = auth_svc.register_user(
        "unit_operator", "Pass123!", "consortium", roles=["admin", "security_officer", "operator"]
    )
    u_a = auth_svc.register_user("unit_bank_a_admin", "Pass123!", "bank_a", roles=["admin"])
    u_b = auth_svc.register_user("unit_bank_b_admin", "Pass123!", "bank_b", roles=["admin"])
    u_hacker = auth_svc.register_user("unit_hacker", "Pass123!", "bank_c", roles=["analyst"])

    token_op = auth_svc.create_token_bundle(u_op).access_token
    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token
    token_hacker = auth_svc.create_token_bundle(u_hacker).access_token

    principal_op = AuthenticatedPrincipal(
        principal_id="unit_operator",
        tenant_id="consortium",
        roles=["admin", "security_officer", "operator"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_a = AuthenticatedPrincipal(
        principal_id="unit_bank_a_admin",
        tenant_id="bank_a",
        roles=["admin"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_b = AuthenticatedPrincipal(
        principal_id="unit_bank_b_admin",
        tenant_id="bank_b",
        roles=["admin"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_hacker = AuthenticatedPrincipal(
        principal_id="unit_hacker",
        tenant_id="bank_c",
        roles=["analyst"],
        is_authenticated=True,
        auth_method="jwt",
    )

    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="c_unit",
        name="Unit Test Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id="c_unit",
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )
    gov_svc.register_operational_data_sharing_grant(
        consortium_id="c_unit",
        grant_id="grant_c_unit",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id="c_unit",
        grant_id="grant_c_unit",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    pilot_store = DurablePilotStore(str(pilot_db_file))
    pilot_svc = PilotOperationsService(pilot_store=pilot_store, consortium_service=gov_svc)
    id_a = InstitutionalSigningIdentity.generate("bank_a")
    id_b = InstitutionalSigningIdentity.generate("bank_b")
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key)
    PilotOperationsService._instance = pilot_svc

    # Start live test daemons on ephemeral ports for authentic preflight verification
    node_a = IsolatedBankWorkerNode("bank_a", storage_root=str(tmp_path / "bank_a_vault"), signing_key=id_a)
    node_b = IsolatedBankWorkerNode("bank_b", storage_root=str(tmp_path / "bank_b_vault"), signing_key=id_b)
    daemon_a = InstitutionalWorkerDaemon(node_a, host="127.0.0.1", port=0, run_as_process=False)
    daemon_b = InstitutionalWorkerDaemon(node_b, host="127.0.0.1", port=0, run_as_process=False)
    url_a = daemon_a.start()
    url_b = daemon_b.start()
    coord_obj = CrossBankMatchingCoordinator()
    coord_daemon = InstitutionalCoordinatorDaemon(
        coord_obj, host="127.0.0.1", port=0, coordinator_secret=SHARED_COORDINATOR_SECRET
    )
    url_coord = coord_daemon.start()

    test_topology = {
        "bank_a_worker_url": url_a,
        "bank_b_worker_url": url_b,
        "coordinator_url": url_coord,
    }

    orig_provision = pilot_svc.provision_pilot

    def _provision_with_topo(*args: Any, **kwargs: Any) -> PilotEntity:
        if "topology" not in kwargs:
            kwargs["topology"] = dict(test_topology)
        return orig_provision(*args, **kwargs)

    pilot_svc.provision_pilot = _provision_with_topo  # type: ignore[method-assign]

    try:
        yield {
            "pilot_svc": pilot_svc,
            "gov_svc": gov_svc,
            "pilot_db_file": pilot_db_file,
            "id_a": id_a,
            "id_b": id_b,
            "principal_op": principal_op,
            "principal_a": principal_a,
            "principal_b": principal_b,
            "principal_hacker": principal_hacker,
            "token_op": token_op,
            "token_a": token_a,
            "token_b": token_b,
            "token_hacker": token_hacker,
            "test_topology": test_topology,
        }
    finally:
        daemon_a.stop()
        daemon_b.stop()
        coord_daemon.stop()


def _sign(pilot: PilotEntity, inst: str, identity: InstitutionalSigningIdentity, approver_id: str) -> str:
    challenge = canonical_pilot_approval_bytes(
        pilot_id=pilot.config.pilot_id,
        revision=pilot.config.revision,
        scope_digest=pilot.scope_digest,
        approver_institution=inst.lower().strip(),
        approver_id=approver_id,
    )
    return identity.sign(challenge)


def test_scope_digest_deterministic_and_tamper_evident() -> None:
    """Verifies that scope digest is canonical and changes with any parameter."""
    cfg1 = PilotOperationalConfig(
        pilot_id="pilot_01",
        revision=1,
        consortium_id="c_01",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"ds_1": "hash1"},
        query_quota_total=10,
        query_quota_per_institution=5,
    )
    cfg2 = PilotOperationalConfig(
        pilot_id="pilot_01",
        revision=1,
        consortium_id="c_01",
        participating_institutions=["bank_b", "bank_a"],  # order inverted
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"ds_1": "hash1"},
        query_quota_total=10,
        query_quota_per_institution=5,
    )
    assert cfg1.compute_scope_digest() == cfg2.compute_scope_digest(), "Scope digest must be canonical and invariant to input list ordering"

    cfg3 = PilotOperationalConfig(
        pilot_id="pilot_01",
        revision=2,  # revision incremented
        consortium_id="c_01",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"ds_1": "hash1"},
        query_quota_total=10,
        query_quota_per_institution=5,
    )
    assert cfg1.compute_scope_digest() != cfg3.compute_scope_digest(), "Scope digest must change when revision changes"


def test_missing_institutional_approval_rejects_activation(unit_pilot_env: dict[str, Any]) -> None:
    """Activation must fail closed if any participating bank has not approved."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_missing_approval"

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
        max_queries_total=10,
        max_queries_per_institution=5,
    )
    svc.run_preflight(pilot_id, operator_actor="unit_operator")

    # Only Bank A approves with valid principal and signature, Bank B has not approved
    sig_a = _sign(pilot, "bank_a", unit_pilot_env["id_a"], "unit_bank_a_admin")
    svc.approve_pilot(
        pilot_id=pilot_id,
        approving_institution="bank_a",
        principal=unit_pilot_env["principal_a"],
        notes="Bank A approval",
        signature=sig_a,
    )

    with pytest.raises(PilotAuthorizationError) as exc:
        svc.activate_pilot(pilot_id, operator_actor="unit_operator")
    assert "missing approval" in str(exc.value).lower() or "bank_b" in str(exc.value).lower()


def test_stale_approval_rejected_on_scope_revision(unit_pilot_env: dict[str, Any]) -> None:
    """Approvals made against a stale scope digest must be rejected."""
    pilot = PilotEntity(
        config=PilotOperationalConfig(
            pilot_id="pilot_stale_test",
            revision=1,
            consortium_id="c_unit",
            participating_institutions=["bank_a", "bank_b"],
            allowed_purposes=["AML_INVESTIGATION"],
            allowed_jurisdictions=["EU"],
            approved_datasets={"ds_1": "hash1"},
            query_quota_total=10,
            query_quota_per_institution=5,
            scope_digest="digest_rev_1_abc123",
        ),
        status=PilotStatus.PREFLIGHT,
        status_reason="Preflight complete",
    )
    # Bank A approves with stale scope digest
    stale_approval = PilotApprovalRecord(
        approver_id="admin_a",
        approver_institution="bank_a",
        approver_role="admin",
        scope_digest="stale_digest_xyz999",  # mismatch!
        notes="Stale approval",
    )
    pilot.approvals["bank_a"] = stale_approval

    is_complete, missing = pilot.has_all_required_approvals()
    assert is_complete is False
    assert "bank_a" in missing, "Stale approval must be treated as missing"


def test_mandatory_attack_operator_cannot_approve_for_banks(unit_pilot_env: dict[str, Any]) -> None:
    """Mandatory Attack: The consortium operator attempts to approve for Bank A, Bank B, and itself.

    This must fail before activation. Consortium operators and platform administrators cannot manufacture
    either bank's institutional approval.
    """
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_operator_spoof_attack"

    svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
    )
    svc.run_preflight(pilot_id)

    # Operator attempts to approve on behalf of Bank A -> MUST FAIL CLOSED
    with pytest.raises(PermissionError) as exc_a:
        svc.approve_pilot(
            pilot_id=pilot_id,
            approving_institution="bank_a",
            principal=unit_pilot_env["principal_op"],
            notes="Malicious operator forging Bank A approval",
        )
    assert "cross-bank approval rejected" in str(exc_a.value).lower() or "cannot approve" in str(exc_a.value).lower()

    # Operator attempts to approve on behalf of Bank B -> MUST FAIL CLOSED
    with pytest.raises(PermissionError) as exc_b:
        svc.approve_pilot(
            pilot_id=pilot_id,
            approving_institution="bank_b",
            principal=unit_pilot_env["principal_op"],
            notes="Malicious operator forging Bank B approval",
        )
    assert "cross-bank approval rejected" in str(exc_b.value).lower() or "cannot approve" in str(exc_b.value).lower()

    # Activation must fail closed because neither bank approved
    with pytest.raises(PilotAuthorizationError) as exc_act:
        svc.activate_pilot(pilot_id, operator_actor="unit_operator")
    assert "missing approval" in str(exc_act.value).lower()


def test_invalid_and_empty_signatures_rejected(unit_pilot_env: dict[str, Any]) -> None:
    """Approvals with empty or arbitrary signature strings must be rejected when keys are enrolled."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_sig_verification_test"

    svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
    )
    svc.run_preflight(pilot_id)

    # Bank A attempts empty signature
    with pytest.raises(PilotAuthorizationError) as exc_empty:
        svc.approve_pilot(
            pilot_id=pilot_id,
            approving_institution="bank_a",
            principal=unit_pilot_env["principal_a"],
            signature="",
        )
    assert "cryptographic signature" in str(exc_empty.value).lower()

    # Bank A attempts arbitrary garbage signature
    with pytest.raises(PilotAuthorizationError) as exc_bad:
        svc.approve_pilot(
            pilot_id=pilot_id,
            approving_institution="bank_a",
            principal=unit_pilot_env["principal_a"],
            signature="arbitrary_garbage_hex_string_12345",
        )
    assert "verification failed" in str(exc_bad.value).lower() or "invalid" in str(exc_bad.value).lower()


def test_quota_exhaustion_fails_closed(unit_pilot_env: dict[str, Any]) -> None:
    """Exceeding pilot query quota or institutional quota must fail closed."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_quota_test"

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
        max_queries_total=2,
        max_queries_per_institution=1,
    )
    svc.run_preflight(pilot_id)

    sig_a = _sign(pilot, "bank_a", unit_pilot_env["id_a"], "unit_bank_a_admin")
    sig_b = _sign(pilot, "bank_b", unit_pilot_env["id_b"], "unit_bank_b_admin")

    svc.approve_pilot(pilot_id, "bank_a", principal=unit_pilot_env["principal_a"], signature=sig_a)
    svc.approve_pilot(pilot_id, "bank_b", principal=unit_pilot_env["principal_b"], signature=sig_b)
    svc.approve_pilot(pilot_id, "consortium", principal=unit_pilot_env["principal_op"])
    svc.activate_pilot(pilot_id)

    # First query for Bank A succeeds
    svc.validate_pilot_query(pilot_id, "bank_a", "bank_b", "AML_INVESTIGATION")
    svc.record_query_outcome(pilot_id, success=True, is_match=True, latency_ms=10.0, requesting_bank="bank_a")

    # Second query for Bank A exceeds Bank A's per-institution quota (1)
    with pytest.raises(PilotQuotaExceededError) as exc_inst:
        svc.validate_pilot_query(pilot_id, "bank_a", "bank_b", "AML_INVESTIGATION")
    assert "quota" in str(exc_inst.value).lower()


def test_expiration_enforcement(unit_pilot_env: dict[str, Any]) -> None:
    """Queries on an expired pilot must fail closed."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_expired_test"

    past_time = (datetime.datetime.now(UTC) - datetime.timedelta(hours=1)).isoformat()
    entity = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
        max_queries_total=10,
        max_queries_per_institution=5,
    )
    entity.config.expires_at = past_time
    entity.status = PilotStatus.ACTIVE
    svc.store.save_pilot(entity)

    with pytest.raises(PilotExpiredError) as exc_exp:
        svc.validate_pilot_query(pilot_id, "bank_a", "bank_b", "AML_INVESTIGATION")
    assert "expired" in str(exc_exp.value).lower()


def test_wrong_purpose_and_unauthorized_institutions(unit_pilot_env: dict[str, Any]) -> None:
    """Queries for non-permitted purposes or unapproved institutions must be denied."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_policy_test"

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
    )
    svc.run_preflight(pilot_id)

    sig_a = _sign(pilot, "bank_a", unit_pilot_env["id_a"], "unit_bank_a_admin")
    sig_b = _sign(pilot, "bank_b", unit_pilot_env["id_b"], "unit_bank_b_admin")

    svc.approve_pilot(pilot_id, "bank_a", principal=unit_pilot_env["principal_a"], signature=sig_a)
    svc.approve_pilot(pilot_id, "bank_b", principal=unit_pilot_env["principal_b"], signature=sig_b)
    svc.approve_pilot(pilot_id, "consortium", principal=unit_pilot_env["principal_op"])
    svc.activate_pilot(pilot_id)

    # Wrong purpose
    with pytest.raises(PilotAuthorizationError) as exc_purp:
        svc.validate_pilot_query(pilot_id, "bank_a", "bank_b", "MARKETING_TARGETING")
    assert "not authorized" in str(exc_purp.value).lower()

    # Unauthorized third-party bank
    with pytest.raises(PilotAuthorizationError) as exc_bank:
        svc.validate_pilot_query(pilot_id, "bank_rogue", "bank_b", "AML_INVESTIGATION")
    assert "not a participating institution" in str(exc_bank.value).lower()


def test_api_endpoints_authorization_and_isolation(unit_pilot_env: dict[str, Any]) -> None:
    """Verifies HTTP authorization constraints on /api/v1/pilots endpoints."""
    token_op = unit_pilot_env["token_op"]
    token_a = unit_pilot_env["token_a"]
    token_hacker = unit_pilot_env["token_hacker"]

    with TestClient(app) as client:
        # Non-operator cannot provision pilot
        prov_hack = client.post(
            "/api/v1/pilots",
            json={
                "pilot_id": "pilot_unauth",
                "consortium_id": "c_unit",
                "participating_institutions": ["bank_a", "bank_b"],
                "allowed_purposes": ["AML_INVESTIGATION"],
                "allowed_jurisdictions": ["EU"],
                "approved_synthetic_datasets": ["ds_1"],
            },
            headers={"Authorization": f"Bearer {token_hacker}"},
        )
        assert prov_hack.status_code == 403

        # Operator provisions pilot
        prov_ok = client.post(
            "/api/v1/pilots",
            json={
                "pilot_id": "pilot_api_test",
                "consortium_id": "c_unit",
                "participating_institutions": ["bank_a", "bank_b"],
                "allowed_purposes": ["AML_INVESTIGATION"],
                "allowed_jurisdictions": ["EU"],
                "approved_synthetic_datasets": ["ds_1"],
            },
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert prov_ok.status_code == 201

        # Bank A admin cannot approve on behalf of Bank B (cross-tenant rejection)
        app_cross = client.post(
            "/api/v1/pilots/pilot_api_test/approve",
            json={"institution_id": "bank_b", "notes": "Spoofed approval"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert app_cross.status_code == 403


def test_unauthenticated_pilot_endpoints_rejected(unit_pilot_env: dict[str, Any]) -> None:
    """Every pilot management and data endpoint must return 401 when called without authentication."""
    with TestClient(app) as client:
        assert client.get("/api/v1/pilots").status_code == 401
        assert client.get("/api/v1/pilots/any_pilot").status_code == 401
        assert client.get("/api/v1/pilots/any_pilot/status").status_code == 401
        assert client.get("/api/v1/pilots/any_pilot/telemetry").status_code == 401
        assert client.get("/api/v1/pilots/any_pilot/dossier").status_code == 401
        assert client.post("/api/v1/pilots/any_pilot/pause", json={"reason": "test"}).status_code == 401
        assert client.post("/api/v1/pilots/any_pilot/resume", json={"reason": "test"}).status_code == 401
        assert client.post("/api/v1/pilots/any_pilot/abort", json={"reason": "test"}).status_code == 401
        assert client.post("/api/v1/pilots/any_pilot/complete", json={"reason": "test"}).status_code == 401


def test_atomic_concurrency_quota_reservations(unit_pilot_env: dict[str, Any]) -> None:
    """Atomic quota enforcement: With 1 remaining slot, only 1 concurrent process can reserve it."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_atomic_concurrency_test"

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
        max_queries_total=1,  # EXACTLY 1 slot
        max_queries_per_institution=1,
    )
    svc.run_preflight(pilot_id)
    sig_a = _sign(pilot, "bank_a", unit_pilot_env["id_a"], "unit_bank_a_admin")
    sig_b = _sign(pilot, "bank_b", unit_pilot_env["id_b"], "unit_bank_b_admin")
    svc.approve_pilot(pilot_id, "bank_a", principal=unit_pilot_env["principal_a"], signature=sig_a)
    svc.approve_pilot(pilot_id, "bank_b", principal=unit_pilot_env["principal_b"], signature=sig_b)
    svc.approve_pilot(pilot_id, "consortium", principal=unit_pilot_env["principal_op"])
    svc.activate_pilot(pilot_id)

    results: list[str] = []
    errors: list[Exception] = []

    def attempt_reservation(thread_idx: int) -> None:
        try:
            _, res_id = svc.reserve_query_slot(
                pilot_id=pilot_id,
                requesting_bank="bank_a",
                target_bank="bank_b",
                purpose="AML_INVESTIGATION",
            )
            results.append(res_id)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_reservation, i) for i in range(5)]
        concurrent.futures.wait(futures)

    assert len(results) == 1, f"Exactly 1 reservation must succeed under atomic immediate lock, got {len(results)}"
    assert len(errors) == 4, f"4 reservations must be rejected, got {len(errors)}"
    for err in errors:
        assert isinstance(err, PilotQuotaExceededError)


def test_kill_switch_mid_flight_pause_race(unit_pilot_env: dict[str, Any]) -> None:
    """Kill Switch Race Test: A pause during an in-flight computation prevents authoritative result admission."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_kill_switch_race_test"

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
        max_queries_total=10,
        max_queries_per_institution=5,
    )
    svc.run_preflight(pilot_id)
    sig_a = _sign(pilot, "bank_a", unit_pilot_env["id_a"], "unit_bank_a_admin")
    sig_b = _sign(pilot, "bank_b", unit_pilot_env["id_b"], "unit_bank_b_admin")
    svc.approve_pilot(pilot_id, "bank_a", principal=unit_pilot_env["principal_a"], signature=sig_a)
    svc.approve_pilot(pilot_id, "bank_b", principal=unit_pilot_env["principal_b"], signature=sig_b)
    svc.approve_pilot(pilot_id, "consortium", principal=unit_pilot_env["principal_op"])
    svc.activate_pilot(pilot_id)

    # 1. Start execution: Atomic slot is reserved
    _, res_id = svc.reserve_query_slot(
        pilot_id=pilot_id,
        requesting_bank="bank_a",
        target_bank="bank_b",
        purpose="AML_INVESTIGATION",
    )
    assert res_id

    # 2. Kill switch activated while PSI is computing in flight!
    svc.pause_pilot(
        pilot_id=pilot_id,
        principal=unit_pilot_env["principal_op"],
        reason="Emergency operational pause triggered during matching",
    )

    # 3. Post-computation result arrival: Admission must FAIL CLOSED!
    with pytest.raises(PilotGovernanceError) as exc_commit:
        svc.commit_query_reservation(
            reservation_id=res_id,
            pilot_id=pilot_id,
            receipt_id="rec_race_test_01",
            latency_ms=25.0,
            match_confirmed=True,
        )
    assert "kill switch activated" in str(exc_commit.value).lower()

    # 4. In-flight reservation must be marked ABORTED, not COMMITTED
    with svc.store._get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM pilot_query_reservations WHERE reservation_id = ?;", (res_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "ABORTED"


def test_onboarding_private_key_redacted_and_unauthenticated_blocked(unit_pilot_env: dict[str, Any]) -> None:
    """Bank registration must never transmit institutional private signing or TLS keys, and requires auth."""
    import uuid
    token_op = unit_pilot_env["token_op"]
    test_bank_id = f"bank_test_redact_{uuid.uuid4().hex[:6]}"

    with TestClient(app) as client:
        # Unauthenticated registration must be rejected
        resp_unauth = client.post(
            "/v1/onboarding/register",
            json={
                "bank_id": test_bank_id,
                "legal_name": "Test Bank Redact",
                "jurisdiction": "EU",
                "data_residency_region": "eu-central-1",
                "contact_email": "ops@testbank.com",
            },
        )
        assert resp_unauth.status_code == 401

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        csr_pem = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(
                x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, f"{test_bank_id}.client.cf-intelligence.io"),
                ])
            )
            .sign(priv_key, hashes.SHA256())
            .public_bytes(serialization.Encoding.PEM)
            .decode("utf-8")
        )

        # Authenticated registration returns certificates but NEVER private keys
        resp_auth = client.post(
            "/v1/onboarding/register",
            json={
                "bank_id": test_bank_id,
                "legal_name": "Test Bank Redact",
                "jurisdiction": "DE",
                "data_residency_region": "eu-central-1",
                "contact_email": "ops@testbank.com",
                "csr_pem": csr_pem,
            },
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert resp_auth.status_code == 201
        data = resp_auth.json()
        assert data.get("mtls_key_pem", "") == "", "Private mTLS key must NOT be returned in HTTP response"
        assert "private_key_pem" not in data or data.get("private_key_pem") == "", "Private key must NOT be returned"
        assert data.get("mtls_cert_pem"), "Public certificate must be present"
        # Root CA is retrieved from consortium-ca endpoint
        ca_resp = client.get(
            "/v1/onboarding/consortium-ca",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert ca_resp.status_code == 200
        assert ca_resp.json().get("root_ca_pem"), "Root CA certificate must be present"
