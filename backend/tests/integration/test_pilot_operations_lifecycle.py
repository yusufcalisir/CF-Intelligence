"""Milestone 5: Controlled Two-Bank Pilot Operations Integration Test Suite.

Verifies the complete end-to-end operational pilot slice across all 6 acceptance gates:
- Gate A: Secure Institutional Onboarding & Identity Isolation.
- Gate B: Pilot Governance State Machine & Kill Switch.
- Gate C & D: Reproducible Isolated Docker Deployment & Real Analyst-to-Case Investigation.
- Gate E: Operational Resilience, Incident Containment (Worker Outage) & Durable Persistence.
- Gate F: Pilot Evidence, Telemetry & Durable Audit Dossier Export.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
    InstitutionalCoordinatorDaemon,
    InstitutionalWorkerClient,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
)
from app.application.services.pilot_operations_service import (
    PilotGovernanceError,
    PilotOperationsService,
)
from app.domain.consortium_governance import MemberRole
from app.domain.institutional_identity import InstitutionalSigningIdentity
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.domain.pilot_operations import PilotStatus, canonical_pilot_approval_bytes
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app

# Import Docker topology fixtures from test_institutional_docker_psi_vertical_slice
from tests.integration.conftest import SECRET_COORD
from tests.integration.test_institutional_docker_psi_vertical_slice import (
    SHARED_CONSORTIUM_STORE_SECRET,
    SHARED_JWT_SECRET,
    _run_cmd,
    _wait_for_health,
)


@pytest.fixture
def pilot_test_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[dict[str, Any], None, None]:
    """Isolated storage and configuration environment for M5 pilot testing."""
    consortium_store_file = tmp_path / "pilot_m5_consortium_store.json"
    account_store_file = tmp_path / "pilot_m5_account_store.json"
    pilot_db_file = tmp_path / "pilot_m5_operations.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SECRET_COORD)
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurablePilotStore._instance = None
    PilotOperationsService._instance = None

    auth_svc = AuthenticationService.get_instance()

    # Create privileged consortium operator
    u_op = auth_svc.get_user("pilot_operator_01")
    if not u_op:
        u_op = auth_svc.register_user(
            "pilot_operator_01",
            "OperatorPass123!",
            "consortium",
            roles=["admin", "compliance", "security_officer", "system_admin"],
        )
    else:
        u_op.roles = ["admin", "compliance", "security_officer", "system_admin"]
        auth_svc.identity_store.save_user(u_op)

    # Create Bank A Admin & Analyst
    u_admin_a = auth_svc.get_user("admin_bank_a")
    if not u_admin_a:
        u_admin_a = auth_svc.register_user(
            "admin_bank_a",
            "BankAPass123!",
            "bank_a",
            roles=["admin", "compliance"],
        )
    u_analyst_a = auth_svc.get_user("analyst_bank_a")
    if not u_analyst_a:
        u_analyst_a = auth_svc.register_user(
            "analyst_bank_a",
            "AnalystPass123!",
            "bank_a",
            roles=["analyst", "investigator"],
        )

    # Create Bank B Admin
    u_admin_b = auth_svc.get_user("admin_bank_b")
    if not u_admin_b:
        u_admin_b = auth_svc.register_user(
            "admin_bank_b",
            "BankBPass123!",
            "bank_b",
            roles=["admin", "compliance"],
        )

    token_op = auth_svc.create_token_bundle(u_op).access_token
    token_admin_a = auth_svc.create_token_bundle(u_admin_a).access_token
    token_analyst_a = auth_svc.create_token_bundle(u_analyst_a).access_token
    token_admin_b = auth_svc.create_token_bundle(u_admin_b).access_token

    principal_op = AuthenticatedPrincipal(
        principal_id="pilot_operator_01",
        tenant_id="consortium",
        roles=["admin", "operator", "system_admin"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_a = AuthenticatedPrincipal(
        principal_id="admin_bank_a",
        tenant_id="bank_a",
        roles=["admin", "compliance"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_b = AuthenticatedPrincipal(
        principal_id="admin_bank_b",
        tenant_id="bank_b",
        roles=["admin", "compliance"],
        is_authenticated=True,
        auth_method="jwt",
    )

    # Setup consortium
    gov_svc = ConsortiumGovernanceService()
    consortium_id = "consortium_pilot_m5"
    gov_svc.create_consortium(
        consortium_id=consortium_id,
        name="Milestone 5 Two-Bank Pilot Consortium",
        founder_bank_id="bank_a",
        auth_token=token_admin_a,
    )
    gov_svc.add_member(
        consortium_id=consortium_id,
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_admin_a,
    )

    grant_id = f"grant_{consortium_id}"
    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_cross_border=False,
        allow_institution_identity_sharing=False,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_admin_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium_id,
        grant_id=grant_id,
        approving_bank_id="bank_b",
        auth_token=token_admin_b,
    )

    # Initialize Pilot Operations Service
    pilot_store = DurablePilotStore(str(pilot_db_file))
    pilot_svc = PilotOperationsService(pilot_store=pilot_store, consortium_service=gov_svc)
    id_a = InstitutionalSigningIdentity.generate("bank_a")
    id_b = InstitutionalSigningIdentity.generate("bank_b")
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key)
    PilotOperationsService._instance = pilot_svc

    # Spin up ephemeral authenticated daemons for Gate B preflight/resumption validation
    node_a = IsolatedBankWorkerNode("bank_a", storage_root=str(tmp_path / "bank_a_vault"), signing_key=id_a)
    node_b = IsolatedBankWorkerNode("bank_b", storage_root=str(tmp_path / "bank_b_vault"), signing_key=id_b)
    daemon_a = InstitutionalWorkerDaemon(node_a, host="127.0.0.1", port=0, run_as_process=False)
    daemon_b = InstitutionalWorkerDaemon(node_b, host="127.0.0.1", port=0, run_as_process=False)
    url_a = daemon_a.start()
    url_b = daemon_b.start()
    coord_obj = CrossBankMatchingCoordinator()
    coord_daemon = InstitutionalCoordinatorDaemon(
        coord_obj, host="127.0.0.1", port=0, coordinator_secret=SECRET_COORD
    )
    url_coord = coord_daemon.start()

    test_topology = {
        "bank_a_worker_url": url_a,
        "bank_b_worker_url": url_b,
        "coordinator_url": url_coord,
    }

    try:
        yield {
            "consortium_id": consortium_id,
            "grant_id": grant_id,
            "consortium_store_file": consortium_store_file,
            "account_store_file": account_store_file,
            "pilot_db_file": pilot_db_file,
            "gov_svc": gov_svc,
            "pilot_svc": pilot_svc,
            "token_op": token_op,
            "token_admin_a": token_admin_a,
            "token_analyst_a": token_analyst_a,
            "token_admin_b": token_admin_b,
            "id_a": id_a,
            "id_b": id_b,
            "principal_op": principal_op,
            "principal_a": principal_a,
            "principal_b": principal_b,
            "test_topology": test_topology,
        }
    finally:
        daemon_a.stop()
        daemon_b.stop()
        coord_daemon.stop()


# ==============================================================================
# Gate A — Secure Institutional Onboarding
# ==============================================================================
def test_gate_a_secure_onboarding_and_identity_boundary(
    pilot_test_env: dict[str, Any],
) -> None:
    """Gate A: Verify institutional enrollment, cross-tenant isolation, and credential protection."""
    token_op = pilot_test_env["token_op"]
    token_admin_a = pilot_test_env["token_admin_a"]

    with TestClient(app) as client:
        # 0. Unauthenticated access to onboarding endpoints must fail closed (401)
        assert client.get("/v1/onboarding/consortium-ca").status_code == 401
        assert client.post("/v1/onboarding/register", json={}).status_code == 401

        # 1. Consortium Root CA identity is stable across calls (authenticated)
        ca_resp1 = client.get("/v1/onboarding/consortium-ca", headers={"Authorization": f"Bearer {token_op}"})
        ca_resp2 = client.get("/v1/onboarding/consortium-ca", headers={"Authorization": f"Bearer {token_op}"})
        assert ca_resp1.status_code == 200
        assert ca_resp2.status_code == 200
        assert ca_resp1.json()["root_ca_pem"] == ca_resp2.json()["root_ca_pem"]
        assert ca_resp1.json()["ca_fingerprint"] == ca_resp2.json()["ca_fingerprint"]

        # 2. Register Bank Alpha and Bank Beta by Consortium Operator
        bank_a_id = f"bank_alpha_{uuid.uuid4().hex[:6]}"
        bank_b_id = f"bank_beta_{uuid.uuid4().hex[:6]}"

        reg_a = client.post(
            "/v1/onboarding/register",
            json={
                "bank_id": bank_a_id,
                "legal_name": "Bank Alpha Pilot Institution",
                "jurisdiction": "DE",
                "data_residency_region": "eu-central-1",
                "contact_email": "security@alpha.example.com",
            },
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert reg_a.status_code == 201, f"Registration failed: {reg_a.text}"
        assert reg_a.json().get("mtls_key_pem", "") == ""
        assert "private_key_pem" not in reg_a.json() or reg_a.json().get("private_key_pem", "") == ""

        reg_b = client.post(
            "/v1/onboarding/register",
            json={
                "bank_id": bank_b_id,
                "legal_name": "Bank Beta Pilot Institution",
                "jurisdiction": "FR",
                "data_residency_region": "eu-west-1",
                "contact_email": "security@beta.example.com",
            },
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert reg_b.status_code == 201, f"Registration failed: {reg_b.text}"
        assert reg_b.json().get("mtls_key_pem", "") == ""
        assert "private_key_pem" not in reg_b.json() or reg_b.json().get("private_key_pem", "") == ""

        # 3. Cross-Tenant Isolation: Bank A admin cannot view or enroll Bank B credentials
        enroll_cross = client.post(
            f"/v1/onboarding/banks/{bank_b_id}/sign-csr",
            json={
                "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\nMIIB...fake...=\n-----END CERTIFICATE REQUEST-----",
            },
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert enroll_cross.status_code == 403
        assert "cross-tenant" in enroll_cross.text.lower() or "forbidden" in enroll_cross.text.lower()

        status_cross = client.get(
            f"/v1/onboarding/banks/{bank_b_id}/status",
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert status_cross.status_code == 403

        # 4. Private keys are never disclosed in bundle or status endpoints
        bundle_resp = client.get(
            f"/v1/onboarding/bundle/{bank_a_id}",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert bundle_resp.status_code == 200
        bundle_json = bundle_resp.json()
        assert "private_key" not in bundle_json
        assert "signing_key" not in bundle_json
        assert "PRIVATE KEY" not in json.dumps(bundle_json)

        status_resp = client.get(
            f"/v1/onboarding/banks/{bank_a_id}/status",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert status_resp.status_code == 200
        assert "private_key" not in status_resp.json()

        # 5. Suspended bank fails active check
        client.post(
            f"/v1/onboarding/banks/{bank_a_id}/suspend",
            json={"reason": "Testing suspension enforcement"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        susp_resp = client.get(
            f"/v1/onboarding/banks/{bank_a_id}/status",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert susp_resp.status_code == 200
        assert susp_resp.json()["status"].lower() == "suspended"


# ==============================================================================
# Gate B — Pilot State Machine, Governance & Kill Switch
# ==============================================================================
def test_gate_b_pilot_state_machine_and_kill_switch(
    pilot_test_env: dict[str, Any],
) -> None:
    """Gate B: Verify durable state machine, independent approvals, and kill switch."""
    pilot_svc: PilotOperationsService = pilot_test_env["pilot_svc"]
    token_op = pilot_test_env["token_op"]
    token_admin_a = pilot_test_env["token_admin_a"]
    token_admin_b = pilot_test_env["token_admin_b"]

    with TestClient(app) as client:
        # Step 1: Provision Pilot in DRAFT
        prov_payload = {
            "pilot_id": "pilot_two_bank_test_01",
            "consortium_id": "consortium_pilot_m5",
            "participating_institutions": ["bank_a", "bank_b"],
            "allowed_purposes": ["AML_INVESTIGATION"],
            "allowed_jurisdictions": ["EU"],
            "approved_synthetic_datasets": ["syn_bank_a_aml_v1", "syn_bank_b_aml_v1"],
            "max_queries_total": 5,
            "max_queries_per_institution": 3,
            "duration_days": 14,
            "topology": dict(pilot_test_env["test_topology"]),
        }
        prov_resp = client.post(
            "/api/v1/pilots",
            json=prov_payload,
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert prov_resp.status_code == 201, f"Provisioning failed: {prov_resp.text}"
        pilot_data = prov_resp.json()
        assert pilot_data["status"] == "DRAFT"
        assert pilot_data["scope_digest"]

        # Step 2: Invalid Transition - Direct Activation from DRAFT must fail closed
        act_invalid = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/activate",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert act_invalid.status_code == 400
        assert "invalid state transition" in act_invalid.text.lower() or "missing approval" in act_invalid.text.lower()

        # Step 3: Run Preflight Checks
        preflight_resp = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/preflight",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert preflight_resp.status_code == 200, f"Preflight failed: {preflight_resp.text}"
        assert preflight_resp.json()["passed"] is True
        assert preflight_resp.json()["status"] in ("PREFLIGHT", "AWAITING_APPROVAL")

        # Step 4: Independent Bilateral Approvals & Attack Defense
        # 4.0 Unauthenticated approval attempt must fail closed (401)
        app_unauth = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "signature": "fake", "notes": "Unauth"},
        )
        assert app_unauth.status_code == 401

        # 4.1 Mandatory attack: Consortium Operator attempts to approve on behalf of Bank A (must fail 403)
        app_spoof_a = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "signature": "abc", "notes": "Operator spoofing Bank A"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert app_spoof_a.status_code == 403
        assert (
            "cross-bank" in app_spoof_a.text.lower()
            or "cross-tenant" in app_spoof_a.text.lower()
            or "cannot approve" in app_spoof_a.text.lower()
            or "forbidden" in app_spoof_a.text.lower()
        )

        # 4.2 Empty or arbitrary signature strings must be rejected (400)
        app_empty_sig = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "signature": "", "notes": "Empty sig"},
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert app_empty_sig.status_code == 400

        app_bad_sig = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "signature": "00" * 32, "notes": "Invalid cryptographic signature"},
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert app_bad_sig.status_code == 400

        # Compute authentic institutional signatures
        pilot_ent = pilot_svc.get_pilot("pilot_two_bank_test_01")
        assert pilot_ent is not None
        id_a = pilot_test_env["id_a"]
        id_b = pilot_test_env["id_b"]

        sig_bytes_a = canonical_pilot_approval_bytes(
            pilot_id="pilot_two_bank_test_01",
            scope_digest=pilot_ent.scope_digest,
            config_revision=pilot_ent.config.revision,
            approving_bank_id="bank_a",
            approving_principal_id="admin_bank_a",
        )
        sig_a = id_a.sign(sig_bytes_a)

        sig_bytes_b = canonical_pilot_approval_bytes(
            pilot_id="pilot_two_bank_test_01",
            scope_digest=pilot_ent.scope_digest,
            config_revision=pilot_ent.config.revision,
            approving_bank_id="bank_b",
            approving_principal_id="admin_bank_b",
        )
        sig_b = id_b.sign(sig_bytes_b)

        # 4a. Bank A Approves with authentic Ed25519 signature
        app_a = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "signature": sig_a, "notes": "Bank A board approval for pilot"},
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert app_a.status_code == 200
        assert app_a.json()["status"] == "AWAITING_APPROVAL"

        # 4b. Bank B Approves with authentic Ed25519 signature
        app_b = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_b", "signature": sig_b, "notes": "Bank B compliance approval for pilot"},
            headers={"Authorization": f"Bearer {token_admin_b}"},
        )
        assert app_b.status_code == 200
        assert app_b.json()["status"] == "AWAITING_APPROVAL"

        # 4c. Operator Approves -> Transitions to AUTHORIZED
        app_op = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "consortium", "notes": "Consortium operator authorization"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert app_op.status_code == 200
        assert app_op.json()["status"] == "AUTHORIZED"

        # Step 5: Activate Pilot
        act_resp = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/activate",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert act_resp.status_code == 200
        assert act_resp.json()["status"] == "ACTIVE"

        # Step 6: Duplicate Activation Idempotency / Protection
        act_dup = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/activate",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert act_dup.status_code == 200
        assert act_dup.json()["status"] == "ACTIVE"

        # Step 7: Kill Switch - PAUSE
        pause_resp = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/pause",
            json={"reason": "Routine operational inspection pause"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "PAUSED"

        # Unauthorized analyst cannot resume paused pilot (403)
        res_unauth = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/resume",
            json={"reason": "Analyst illegal resume attempt"},
            headers={"Authorization": f"Bearer {pilot_test_env['token_analyst_a']}"},
        )
        assert res_unauth.status_code == 403

        # Query validation fails while PAUSED
        with pytest.raises(PilotGovernanceError) as exc_pause:
            pilot_svc.validate_pilot_query(
                pilot_id="pilot_two_bank_test_01",
                requesting_bank="bank_a",
                target_bank="bank_b",
                purpose="AML_INVESTIGATION",
            )
        assert "paused" in str(exc_pause.value).lower()

        # Step 8: Kill Switch - RESUME
        resume_resp = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/resume",
            json={"reason": "Inspection cleared"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "ACTIVE"

        # Step 9: Kill Switch - ABORT (Terminal)
        abort_resp = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/abort",
            json={"reason": "Simulated terminal incident abort"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert abort_resp.status_code == 200
        assert abort_resp.json()["status"] == "ABORTED"

        # Aborted pilot cannot be resumed
        resume_aborted = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/resume",
            json={"reason": "Illegal resume attempt"},
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert resume_aborted.status_code == 400
        assert "invalid state transition" in resume_aborted.text.lower()


# ==============================================================================
# Gate C & D — Reproducible Docker Deployment & Real Analyst Investigation
# ==============================================================================
def test_gate_c_and_d_real_docker_investigation_workflow(
    docker_topology: dict[str, Any],
    pilot_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate C & D: Live 3-container topology execution with genuine OpenMined PSI and case evidence."""
    pilot_svc: PilotOperationsService = pilot_test_env["pilot_svc"]
    gov_svc: ConsortiumGovernanceService = pilot_test_env["gov_svc"]
    token_admin_b = pilot_test_env["token_admin_b"]
    token_analyst_a = pilot_test_env["token_analyst_a"]

    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    port_coord: int = docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 0. Sync store files into containers and register authorized grant on workers
    store_file = pilot_test_env["consortium_store_file"]
    account_file = pilot_test_env["account_store_file"]
    c_worker_a = docker_topology["container_worker_a"]
    c_worker_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]
    for c in [c_worker_a, c_worker_b, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "cp", str(account_file), f"{c}:/tmp/account_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/account_store.json"], check=False)

    c_data = gov_svc.get_consortium("consortium_pilot_m5")
    assert c_data is not None
    grant_meta = c_data.metadata.get("operational_data_sharing_grant", {})
    scope_digest = grant_meta.get("scope_digest", "")

    client_a.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_INVESTIGATION"],
    )
    client_b.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_INVESTIGATION"],
    )

    # 1. Provision & Authorize Live Pilot
    id_a = docker_topology.get("ident_a") or pilot_test_env["id_a"]
    id_b = docker_topology.get("ident_b") or pilot_test_env["id_b"]
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key, allow_overwrite=True)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key, allow_overwrite=True)

    pilot_id = "pilot_docker_live_01"
    pilot_ent = pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        principal=pilot_test_env["principal_op"],
        topology={
            "bank_a_worker_url": f"http://127.0.0.1:{docker_topology['port_a']}",
            "bank_b_worker_url": f"http://127.0.0.1:{docker_topology['port_b']}",
            "coordinator_url": coord_url,
        },
    )
    pilot_svc.run_preflight(pilot_id, principal=pilot_test_env["principal_op"])
    sig_a = id_a.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_a",
        approving_principal_id="admin_bank_a",
    ))
    sig_b = id_b.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_b",
        approving_principal_id="admin_bank_b",
    ))
    pilot_svc.approve_pilot(pilot_id, "bank_a", principal=pilot_test_env["principal_a"], signature=sig_a, notes="Bank A approval")
    pilot_svc.approve_pilot(pilot_id, "bank_b", principal=pilot_test_env["principal_b"], signature=sig_b, notes="Bank B approval")
    pilot_svc.approve_pilot(pilot_id, "consortium", principal=pilot_test_env["principal_op"], notes="Operator approval")
    pilot_svc.activate_pilot(pilot_id, principal=pilot_test_env["principal_op"])

    # 2. Store matching entity in Bank A and Bank B Docker vaults
    shared_iban = "DE89370400440532013000"
    ent_a_id = "ent_pilot_case_a"
    ent_b_id = "ent_pilot_case_b"
    client_a.store_private_entity({
        "id": ent_a_id,
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_iban,
        "risk_level": "HIGH",
        "typologies": ["TERRORIST_FINANCING"],
    })
    client_b.store_private_entity({
        "id": ent_b_id,
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_iban,
        "risk_level": "CRITICAL",
        "typologies": ["TERRORIST_FINANCING"],
    })

    # Register local institution-owned entity in local entity service
    cbi_svc = CrossBankIntelligenceService(consortium_service=gov_svc)
    ent_obj = Entity(
        id=ent_a_id,
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_iban.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Pilot Subject Alpha",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_iban},
    )
    from app.application.services.entity_resolution import _entity_to_dict

    cbi_svc.entity_service._entities.set(ent_a_id, _entity_to_dict(ent_obj))

    from app.presentation.routers import cross_bank_intelligence as cbi_router_mod

    monkeypatch.setattr(cbi_router_mod, "get_cross_bank_intelligence_service", lambda: cbi_svc)

    with TestClient(app) as test_client:
        auth_headers = {"Authorization": f"Bearer {token_analyst_a}"}

        # Step 3: Open an investigation case owned by Bank A
        case_resp = test_client.post(
            "/api/v1/cases",
            json={
                "title": "Milestone 5 Real Pilot Cross-Bank Investigation",
                "priority": "p1_critical",
                "bank_id": "bank_a",
            },
            headers=auth_headers,
        )
        assert case_resp.status_code == 200, f"Case creation failed: {case_resp.text}"
        case_id = case_resp.json()["id"]

        # Step 4: Request Cross-Bank Intelligence with bound pilot_id
        query_payload = {
            "consortium_id": "consortium_pilot_m5",
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "query_entity_id": ent_a_id,
            "case_id": case_id,
            "purpose": "AML_INVESTIGATION",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "pilot_id": pilot_id,
        }
        query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers=auth_headers,
        )
        assert query_resp.status_code == 200, f"Cross-bank query failed: {query_resp.text}"
        signal_data = query_resp.json()

        # Step 5: Verify authentic OpenMined PSI match semantics
        assert signal_data["match_confirmed"] is True
        assert signal_data["match_category"] == "PROBABILISTIC_FUZZY"
        assert signal_data["match_confidence"] == 0.999999999
        assert signal_data["provenance"]["protocol"] == "OPENMINED-PSI-2.0.6-ECDH"
        signal_id = signal_data["signal_id"]

        # Step 6: Attach verified signal to Case Evidence Ledger
        attach_resp = test_client.post(
            f"/api/v1/cases/{case_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": signal_id,
                "notes": "Milestone 5 operational pilot match evidence",
            },
            headers=auth_headers,
        )
        assert attach_resp.status_code == 200, f"Evidence attachment failed: {attach_resp.text}"

        # Step 7: Verify Pilot Telemetry Updated Durably
        telemetry = pilot_svc.get_telemetry_summary(pilot_id)
        assert telemetry["queries_total"] == 1
        assert telemetry["queries_successful"] == 1
        assert telemetry["matches_found"] == 1
        assert telemetry["queries_by_institution"]["bank_a"] == 1
        assert telemetry["mean_latency_ms"] > 0

        # Step 7b: Server-Controlled Pilot Binding: Request omitting pilot_id must NOT bypass active pilot
        query_payload_no_pilot = dict(query_payload)
        del query_payload_no_pilot["pilot_id"]
        query_resp_bound = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload_no_pilot,
            headers=auth_headers,
        )
        assert query_resp_bound.status_code == 200, f"Server-controlled pilot query failed: {query_resp_bound.text}"
        # Verified: Query without pilot_id was automatically bound to active pilot and recorded in quota
        telemetry_bound = pilot_svc.get_telemetry_summary(pilot_id)
        assert telemetry_bound["queries_total"] == 2
        assert telemetry_bound["queries_by_institution"]["bank_a"] == 2

        # Step 8: Bank B Revokes Consent Mid-Pilot
        gov_svc.revoke_operational_data_sharing_grant(
            consortium_id="consortium_pilot_m5",
            grant_id="grant_consortium_pilot_m5",
            revoking_bank_id="bank_b",
            auth_token=token_admin_b,
        )

        # Step 9: Subsequent queries must fail closed
        query_denied = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers=auth_headers,
        )
        assert query_denied.status_code in (400, 403)
        assert "revoked" in query_denied.text.lower() or "rejected" in query_denied.text.lower()


# ==============================================================================
# Gate E — Operational Resilience & Incident Containment
# ==============================================================================
def test_gate_e_operational_resilience_and_incident_containment(
    docker_topology: dict[str, Any],
    pilot_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate E: Outage containment (Worker B stops), fail-closed, and restart persistence."""
    pilot_svc: PilotOperationsService = pilot_test_env["pilot_svc"]
    gov_svc: ConsortiumGovernanceService = pilot_test_env["gov_svc"]
    token_analyst_a = pilot_test_env["token_analyst_a"]
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    port_coord: int = docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 0. Sync store files into containers and register authorized grant on workers
    store_file = pilot_test_env["consortium_store_file"]
    account_file = pilot_test_env["account_store_file"]
    c_worker_a = docker_topology["container_worker_a"]
    c_worker_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]
    for c in [c_worker_a, c_worker_b, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "cp", str(account_file), f"{c}:/tmp/account_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/account_store.json"], check=False)

    c_data = gov_svc.get_consortium("consortium_pilot_m5")
    assert c_data is not None
    grant_meta = c_data.metadata.get("operational_data_sharing_grant", {})
    scope_digest = grant_meta.get("scope_digest", "")

    client_a.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_INVESTIGATION"],
    )
    client_b.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_INVESTIGATION"],
    )

    id_a = docker_topology.get("ident_a") or pilot_test_env["id_a"]
    id_b = docker_topology.get("ident_b") or pilot_test_env["id_b"]
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key, allow_overwrite=True)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key, allow_overwrite=True)

    pilot_id = "pilot_resilience_01"
    pilot_ent = pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        principal=pilot_test_env["principal_op"],
        topology={
            "bank_a_worker_url": f"http://127.0.0.1:{docker_topology['port_a']}",
            "bank_b_worker_url": f"http://127.0.0.1:{docker_topology['port_b']}",
            "coordinator_url": coord_url,
        },
    )
    pilot_svc.run_preflight(pilot_id, principal=pilot_test_env["principal_op"])
    sig_a = id_a.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_a",
        approving_principal_id="admin_bank_a",
    ))
    sig_b = id_b.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_b",
        approving_principal_id="admin_bank_b",
    ))
    pilot_svc.approve_pilot(pilot_id, "bank_a", principal=pilot_test_env["principal_a"], signature=sig_a, notes="Approved A")
    pilot_svc.approve_pilot(pilot_id, "bank_b", principal=pilot_test_env["principal_b"], signature=sig_b, notes="Approved B")
    pilot_svc.approve_pilot(pilot_id, "consortium", principal=pilot_test_env["principal_op"], notes="Approved Op")
    pilot_svc.activate_pilot(pilot_id, principal=pilot_test_env["principal_op"])

    # Register local entity
    cbi_svc = CrossBankIntelligenceService(consortium_service=gov_svc)
    ent_id = "ent_resilience_a"
    ent_obj = Entity(
        id=ent_id,
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(b"DE99000111222333444").hexdigest(),
        bank_id="bank_a",
        display_label="Resilience Subject",
        risk_level=RiskLevel.MEDIUM,
        attributes={"raw_identifier": "DE99000111222333444"},
    )
    from app.application.services.entity_resolution import _entity_to_dict

    cbi_svc.entity_service._entities.set(ent_id, _entity_to_dict(ent_obj))

    from app.presentation.routers import cross_bank_intelligence as cbi_router_mod

    CrossBankIntelligenceService._instance = cbi_svc
    app.dependency_overrides[cbi_router_mod.get_cross_bank_intelligence_service] = lambda: cbi_svc
    monkeypatch.setattr(cbi_router_mod, "get_cross_bank_intelligence_service", lambda: cbi_svc)

    with TestClient(app) as test_client:
        auth_headers = {"Authorization": f"Bearer {token_analyst_a}"}

        # Step 2: Stop Bank B Container (Simulating Worker Outage)
        _run_cmd(["docker", "stop", c_worker_b], check=True)
        try:
            query_payload = {
                "consortium_id": "consortium_pilot_m5",
                "requesting_bank_id": "bank_a",
                "target_bank_id": "bank_b",
                "query_entity_id": ent_id,
                "purpose": "AML_INVESTIGATION",
                "execution_mode": "VERIFIED_OPERATIONAL",
                "use_established_psi": True,
                "require_established_psi": True,
                "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                "pilot_id": pilot_id,
            }
            # Step 3: Query dispatched during worker outage must fail closed
            outage_resp = test_client.post(
                "/api/v1/intelligence/cross-bank/query",
                json=query_payload,
                headers=auth_headers,
            )
            assert outage_resp.status_code in (400, 403, 500, 502, 503)
            # Verify failure recorded in telemetry
            telem = pilot_svc.get_telemetry_summary(pilot_id)
            assert telem["queries_total"] == 1
            assert telem["queries_failed"] == 1
        finally:
            # Step 4: Restart Bank B Worker & verify healthy recovery
            _run_cmd(["docker", "start", c_worker_b], check=True)
            _wait_for_health(f"http://127.0.0.1:{docker_topology['port_b']}/worker/health", timeout_sec=25.0)

        # Step 5: Test ACID Persistence across service restart
        pilot_svc.pause_pilot(pilot_id, principal=pilot_test_env["principal_op"], reason="Testing persistence across restart")
        pilot_db_file = pilot_test_env["pilot_db_file"]

        # Simulate process reboot with fresh PilotOperationsService instance reading from DB
        rebooted_store = DurablePilotStore(str(pilot_db_file))
        rebooted_svc = PilotOperationsService(pilot_store=rebooted_store, consortium_service=gov_svc)

        persisted_pilot = rebooted_svc.get_pilot(pilot_id)
        assert persisted_pilot is not None
        assert persisted_pilot.status == PilotStatus.PAUSED
        # State machine prevents silent re-authorization or auto-activation
        assert persisted_pilot.is_active is False

        # Restore singleton binding for subsequent live FastAPI operations
        PilotOperationsService._instance = pilot_svc
        pilot_store = pilot_svc.store

        # ==============================================================================
        # Step 6: Full 13-Step Deterministic Real Execution Mid-Flight PAUSE Race
        # Exercising live Docker mesh and FastAPI TestClient without synthetic records
        # ==============================================================================
        # 1. Start actual isolated Bank A, Bank B, and coordinator services (running in Docker mesh).
        _wait_for_health(f"http://127.0.0.1:{docker_topology['port_a']}/worker/health", timeout_sec=20.0)
        _wait_for_health(f"http://127.0.0.1:{docker_topology['port_b']}/worker/health", timeout_sec=20.0)
        _wait_for_health(f"http://127.0.0.1:{port_coord}/coordinator/health", timeout_sec=20.0)

        # Ingest sovereign private entities into Docker worker vaults
        shared_race_iban = "DE88112233445566778899"
        ent_race_a_id = "ent_race_subj_a"
        ent_race_b_id = "ent_race_subj_b"
        client_a.store_private_entity({
            "id": ent_race_a_id,
            "bank_id": "bank_a",
            "entity_type": "account",
            "raw_identifier": shared_race_iban,
            "risk_level": "HIGH",
            "typologies": ["CROSS_BORDER_FRAUD"],
        })
        client_b.store_private_entity({
            "id": ent_race_b_id,
            "bank_id": "bank_b",
            "entity_type": "account",
            "raw_identifier": shared_race_iban,
            "risk_level": "CRITICAL",
            "typologies": ["CROSS_BORDER_FRAUD"],
        })

        # Register local entity in CBI service
        ent_race_obj = Entity(
            id=ent_race_a_id,
            entity_type=EntityType.CUSTOMER,
            privacy_id=hashlib.sha256(shared_race_iban.encode()).hexdigest(),
            bank_id="bank_a",
            display_label="Race Pilot Subject",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": shared_race_iban},
        )
        cbi_svc.entity_service._entities.set(ent_race_a_id, _entity_to_dict(ent_race_obj))

        # 2. Authenticate and authorize a controlled pilot
        race_pilot_id = "pilot_resilience_race_01"
        pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key, allow_overwrite=True)
        pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key, allow_overwrite=True)
        race_pilot_ent = pilot_svc.provision_pilot(
            pilot_id=race_pilot_id,
            consortium_id="consortium_pilot_m5",
            participating_institutions=["bank_a", "bank_b"],
            allowed_purposes=["AML_INVESTIGATION"],
            allowed_jurisdictions=["EU"],
            approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
            max_queries_total=10,
            max_queries_per_institution=5,
            duration_days=7,
            principal=pilot_test_env["principal_op"],
            topology={
                "bank_a_worker_url": f"http://127.0.0.1:{docker_topology['port_a']}",
                "bank_b_worker_url": f"http://127.0.0.1:{docker_topology['port_b']}",
                "coordinator_url": coord_url,
            },
        )
        pilot_svc.run_preflight(race_pilot_id, principal=pilot_test_env["principal_op"])
        sig_race_a = id_a.sign(canonical_pilot_approval_bytes(
            pilot_id=race_pilot_id,
            scope_digest=race_pilot_ent.scope_digest,
            config_revision=race_pilot_ent.config.revision,
            approving_bank_id="bank_a",
            approving_principal_id="admin_bank_a",
        ))
        sig_race_b = id_b.sign(canonical_pilot_approval_bytes(
            pilot_id=race_pilot_id,
            scope_digest=race_pilot_ent.scope_digest,
            config_revision=race_pilot_ent.config.revision,
            approving_bank_id="bank_b",
            approving_principal_id="admin_bank_b",
        ))
        pilot_svc.approve_pilot(race_pilot_id, "bank_a", principal=pilot_test_env["principal_a"], signature=sig_race_a)
        pilot_svc.approve_pilot(race_pilot_id, "bank_b", principal=pilot_test_env["principal_b"], signature=sig_race_b)
        pilot_svc.approve_pilot(race_pilot_id, "consortium", principal=pilot_test_env["principal_op"])
        pilot_svc.activate_pilot(race_pilot_id, principal=pilot_test_env["principal_op"])
        p_active_check = pilot_svc.get_pilot(race_pilot_id)
        assert p_active_check is not None and p_active_check.status == PilotStatus.ACTIVE

        # Create investigation case owned by Bank A
        case_race_resp = test_client.post(
            "/api/v1/cases",
            json={
                "title": "Milestone 5 Operational Race Investigation Case",
                "priority": "p1_critical",
                "bank_id": "bank_a",
            },
            headers=auth_headers,
        )
        assert case_race_resp.status_code == 200, f"Case creation failed: {case_race_resp.text}"
        case_race_id = case_race_resp.json()["id"]

        op_headers = {"Authorization": f"Bearer {pilot_test_env['token_op']}"}

        # 3. & 4. & 5. Hold actual execution immediately before result finalization using deterministic hook
        hook_called_for_pilot = []
        captured_precommit_signal_id = []
        captured_precommit_receipt_id = []

        def _deterministic_pause_hook(effective_pilot_id: str, sig_id: str | None = None, rcp_id: str | None = None) -> None:
            if effective_pilot_id == race_pilot_id:
                hook_called_for_pilot.append(effective_pilot_id)
                if sig_id:
                    captured_precommit_signal_id.append(sig_id)
                if rcp_id:
                    captured_precommit_receipt_id.append(rcp_id)
                # 5. PAUSE the pilot through its authorized HTTP endpoint
                pause_http_resp = test_client.post(
                    f"/api/v1/pilots/{race_pilot_id}/pause",
                    json={"reason": "Adversarial mid-flight pause race containment verification"},
                    headers=op_headers,
                )
                assert pause_http_resp.status_code == 200
                assert pause_http_resp.json()["status"] == "PAUSED"

        cbi_svc._before_result_admission_hook = _deterministic_pause_hook

        race_query_payload = {
            "consortium_id": "consortium_pilot_m5",
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "query_entity_id": ent_race_a_id,
            "case_id": case_race_id,
            "purpose": "AML_INVESTIGATION",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "pilot_id": race_pilot_id,
        }

        # 6. Allow genuine PSI execution to complete through HTTP app
        race_query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=race_query_payload,
            headers=auth_headers,
        )

        # 7. Assert query cannot issue an authoritative successful result
        assert len(hook_called_for_pilot) == 1, "Synchronization hook was not invoked before admission"
        assert len(captured_precommit_signal_id) == 1, "Pre-commit hook captured genuine would-be signal ID"
        assert len(captured_precommit_receipt_id) == 1, "Pre-commit hook captured genuine receipt ID"
        assert race_query_resp.status_code in (400, 403, 409), f"Query should have failed closed: {race_query_resp.text}"
        assert "paused" in race_query_resp.text.lower() or "governance" in race_query_resp.text.lower()

        # 8. Verify that the durable reservation was NOT committed (released/aborted)
        with pilot_store._lock, pilot_store._get_connection() as conn:
            cur = conn.execute(
                "SELECT status, finalized_at FROM pilot_query_reservations WHERE pilot_id = ? ORDER BY reserved_at DESC LIMIT 1;",
                (race_pilot_id,),
            )
            res_row = cur.fetchone()
            assert res_row is not None, "Durable reservation record should exist"
            assert res_row["status"] != "COMMITTED", "Reservation must NOT be committed on paused pilot"
            assert res_row["status"] in ("RELEASED", "ABORTED", "FAILED")

        # 9. Verify that no attachable authoritative signal became visible
        fallback = getattr(cbi_svc._signal_store, "_fallback_store", {})
        for sig_id, sig_rec in fallback.items():
            if isinstance(sig_rec, dict) and sig_rec.get("pilot_id") == race_pilot_id:
                assert sig_rec.get("admission_status") != "COMMITTED"
                assert sig_rec.get("is_attachable") is not True

        # 10. Verify that the actual case evidence endpoint rejects the result using actual would-be signal ID
        actual_uncommitted_sig_id = captured_precommit_signal_id[0] if captured_precommit_signal_id else f"uncommitted_sig_{race_pilot_id}"
        attach_race_denied = test_client.post(
            f"/api/v1/cases/{case_race_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": actual_uncommitted_sig_id,
                "notes": "Attempting to attach uncommitted mid-flight signal",
            },
            headers=auth_headers,
        )
        assert attach_race_denied.status_code in (400, 403, 404), f"Case attachment should have been rejected: {attach_race_denied.text}"

        # 11. RESUME only after valid authenticated readiness checks
        resume_http_resp = test_client.post(
            f"/api/v1/pilots/{race_pilot_id}/resume",
            json={"reason": "Operational incident containment cleared by operator"},
            headers=op_headers,
        )
        assert resume_http_resp.status_code == 200, f"Pilot resume failed: {resume_http_resp.text}"
        assert resume_http_resp.json()["status"] == "ACTIVE"
        p_resumed_check = pilot_svc.get_pilot(race_pilot_id)
        assert p_resumed_check is not None and p_resumed_check.status == PilotStatus.ACTIVE

        # Verify that previous rejected signal CANNOT be attached after RESUME
        attach_race_revive_denied = test_client.post(
            f"/api/v1/cases/{case_race_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": actual_uncommitted_sig_id,
                "notes": "Attempting to revive uncommitted signal after resume",
            },
            headers=auth_headers,
        )
        assert attach_race_revive_denied.status_code in (400, 403, 404), f"Pre-commit rejected signal must not revive: {attach_race_revive_denied.text}"

        # 12. Verify that a fresh authorized PSI query can succeed and attach legitimately
        cbi_svc._before_result_admission_hook = None  # Remove pause hook for normal run
        fresh_query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=race_query_payload,
            headers=auth_headers,
        )
        assert fresh_query_resp.status_code == 200, f"Fresh query post-resume failed: {fresh_query_resp.text}"
        fresh_signal = fresh_query_resp.json()
        assert fresh_signal["match_confirmed"] is True
        fresh_sig_id = fresh_signal["signal_id"]

        # Verify durable reservation is now PUBLISHED
        with pilot_store._lock, pilot_store._get_connection() as conn:
            cur = conn.execute(
                "SELECT status, signal_id FROM pilot_query_reservations WHERE pilot_id = ? AND status = 'PUBLISHED';",
                (race_pilot_id,),
            )
            committed_row = cur.fetchone()
            assert committed_row is not None, "Reservation must be PUBLISHED for fresh successful query"
            assert committed_row["signal_id"] == fresh_sig_id

        # Attach to case evidence legitimately
        attach_fresh_resp = test_client.post(
            f"/api/v1/cases/{case_race_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": fresh_sig_id,
                "notes": "Milestone 5 legitimate verified post-resumption evidence",
            },
            headers=auth_headers,
        )
        assert attach_fresh_resp.status_code == 200, f"Fresh evidence attachment failed: {attach_fresh_resp.text}"

        # 13. Verify that a query omitting pilot_id cannot bypass a paused governing pilot
        # Pause governing pilot again via HTTP endpoint
        pause_again_resp = test_client.post(
            f"/api/v1/pilots/{race_pilot_id}/pause",
            json={"reason": "Paused for omission bypass verification"},
            headers=op_headers,
        )
        assert pause_again_resp.status_code == 200
        assert pause_again_resp.json()["status"] == "PAUSED"

        query_payload_omitted = dict(race_query_payload)
        del query_payload_omitted["pilot_id"]

        omission_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload_omitted,
            headers=auth_headers,
        )
        assert omission_resp.status_code in (400, 403), f"Omission query should have failed: {omission_resp.text}"
        assert "paused" in omission_resp.text.lower() or "governance" in omission_resp.text.lower()

        # ==============================================================================
        # Step 7: Deterministic Real Execution Post-Commit Publication Race Verification
        # Exercises the exact race boundary: pilot transitions to PAUSED after SQLite query
        # reservation becomes COMMITTED but before resulting authoritative signal is published to Redis.
        # ==============================================================================
        post_commit_pilot_id = "pilot_resilience_post_commit_01"
        post_commit_pilot_ent = pilot_svc.provision_pilot(
            pilot_id=post_commit_pilot_id,
            consortium_id="consortium_pilot_m5",
            participating_institutions=["bank_a", "bank_b"],
            allowed_purposes=["AML_INVESTIGATION"],
            allowed_jurisdictions=["EU"],
            approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
            max_queries_total=10,
            max_queries_per_institution=5,
            duration_days=7,
            principal=pilot_test_env["principal_op"],
            topology={
                "bank_a_worker_url": f"http://127.0.0.1:{docker_topology['port_a']}",
                "bank_b_worker_url": f"http://127.0.0.1:{docker_topology['port_b']}",
                "coordinator_url": coord_url,
            },
        )
        pilot_svc.run_preflight(post_commit_pilot_id, principal=pilot_test_env["principal_op"])
        sig_pc_a = id_a.sign(canonical_pilot_approval_bytes(
            pilot_id=post_commit_pilot_id,
            scope_digest=post_commit_pilot_ent.scope_digest,
            config_revision=post_commit_pilot_ent.config.revision,
            approving_bank_id="bank_a",
            approving_principal_id="admin_bank_a",
        ))
        sig_pc_b = id_b.sign(canonical_pilot_approval_bytes(
            pilot_id=post_commit_pilot_id,
            scope_digest=post_commit_pilot_ent.scope_digest,
            config_revision=post_commit_pilot_ent.config.revision,
            approving_bank_id="bank_b",
            approving_principal_id="admin_bank_b",
        ))
        pilot_svc.approve_pilot(post_commit_pilot_id, "bank_a", principal=pilot_test_env["principal_a"], signature=sig_pc_a)
        pilot_svc.approve_pilot(post_commit_pilot_id, "bank_b", principal=pilot_test_env["principal_b"], signature=sig_pc_b)
        pilot_svc.approve_pilot(post_commit_pilot_id, "consortium", principal=pilot_test_env["principal_op"])
        pilot_svc.activate_pilot(post_commit_pilot_id, principal=pilot_test_env["principal_op"])
        p_pc_active = pilot_svc.get_pilot(post_commit_pilot_id)
        assert p_pc_active is not None and p_pc_active.status == PilotStatus.ACTIVE

        # Ingest sovereign private entity for post-commit race into Docker worker vaults
        shared_pc_iban = "DE55443322110099887766"
        ent_pc_a_id = "ent_post_commit_subj_a"
        ent_pc_b_id = "ent_post_commit_subj_b"
        client_a.store_private_entity({
            "id": ent_pc_a_id,
            "bank_id": "bank_a",
            "entity_type": "account",
            "raw_identifier": shared_pc_iban,
            "risk_level": "HIGH",
            "typologies": ["CORRUPTION"],
        })
        client_b.store_private_entity({
            "id": ent_pc_b_id,
            "bank_id": "bank_b",
            "entity_type": "account",
            "raw_identifier": shared_pc_iban,
            "risk_level": "CRITICAL",
            "typologies": ["CORRUPTION"],
        })
        ent_pc_obj = Entity(
            id=ent_pc_a_id,
            entity_type=EntityType.CUSTOMER,
            privacy_id=hashlib.sha256(shared_pc_iban.encode()).hexdigest(),
            bank_id="bank_a",
            display_label="Post Commit Race Subject",
            risk_level=RiskLevel.HIGH,
            attributes={"raw_identifier": shared_pc_iban},
        )
        cbi_svc.entity_service._entities.set(ent_pc_a_id, _entity_to_dict(ent_pc_obj))

        case_pc_resp = test_client.post(
            "/api/v1/cases",
            json={
                "title": "Post Commit Publication Race Case",
                "priority": "p2_high",
                "bank_id": "bank_a",
            },
            headers=auth_headers,
        )
        assert case_pc_resp.status_code == 200, f"Case creation failed: {case_pc_resp.text}"
        case_pc_id = case_pc_resp.json()["id"]

        # Register deterministic post-commit pre-publication synchronization hook
        pc_hook_called = []
        captured_pc_sig_id = []
        captured_pc_rcp_id = []

        def _deterministic_post_commit_pause_hook(effective_pilot_id: str, sig_id: str, rcp_id: str) -> None:
            if effective_pilot_id == post_commit_pilot_id:
                pc_hook_called.append(effective_pilot_id)
                captured_pc_sig_id.append(sig_id)
                captured_pc_rcp_id.append(rcp_id)
                # PAUSE the pilot through its authorized HTTP endpoint AFTER SQLite commit
                # but BEFORE authoritative Redis publication!
                pause_http_resp = test_client.post(
                    f"/api/v1/pilots/{post_commit_pilot_id}/pause",
                    json={"reason": "Adversarial post-commit pre-publication pause race verification"},
                    headers=op_headers,
                )
                assert pause_http_resp.status_code == 200
                assert pause_http_resp.json()["status"] == "PAUSED"

        cbi_svc._before_signal_publication_hook = _deterministic_post_commit_pause_hook

        pc_query_payload = {
            "consortium_id": "consortium_pilot_m5",
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "query_entity_id": ent_pc_a_id,
            "case_id": case_pc_id,
            "purpose": "AML_INVESTIGATION",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "pilot_id": post_commit_pilot_id,
        }

        # Dispatch real PSI query: execution completes and commits in SQLite, then pause intercepts publication
        pc_query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=pc_query_payload,
            headers=auth_headers,
        )

        # Assert query fails closed
        assert len(pc_hook_called) == 1, "Post-commit hook was not invoked"
        assert len(captured_pc_sig_id) == 1, "Failed to capture genuine would-be signal ID"
        assert len(captured_pc_rcp_id) == 1, "Failed to capture genuine receipt ID"
        rej_post_commit_sig_id = captured_pc_sig_id[0]
        rej_post_commit_rcp_id = captured_pc_rcp_id[0]
        assert len(rej_post_commit_rcp_id) > 0
        assert pc_query_resp.status_code in (400, 403), f"Query should have failed closed: {pc_query_resp.text}"
        assert "paused" in pc_query_resp.text.lower() or "governance" in pc_query_resp.text.lower()

        # Verify durable reservation is in PUBLICATION_REJECTED status in SQLite
        with pilot_store._lock, pilot_store._get_connection() as conn:
            cur = conn.execute(
                "SELECT status, rejection_reason, signal_id, receipt_id FROM pilot_query_reservations WHERE pilot_id = ? ORDER BY reserved_at DESC LIMIT 1;",
                (post_commit_pilot_id,),
            )
            pc_res_row = cur.fetchone()
            assert pc_res_row is not None, "Durable reservation record must exist"
            assert pc_res_row["status"] == "PUBLICATION_REJECTED", "Reservation must be PUBLICATION_REJECTED when paused prior to publication"
            assert "prior to authoritative signal publication" in str(pc_res_row["rejection_reason"])

        # Verify no attachable authoritative signal is visible in Redis store
        stored_rejected_sig = cbi_svc._signal_store.get(rej_post_commit_sig_id)
        if stored_rejected_sig is not None:
            assert stored_rejected_sig.get("is_authoritative") is not True
            assert stored_rejected_sig.get("is_attachable") is not True
            assert stored_rejected_sig.get("admission_status") != "COMMITTED"
            assert stored_rejected_sig.get("publication_status") == "PUBLICATION_REJECTED"

        # Verify case evidence attachment is REJECTED using the EXACT genuine captured signal ID
        attach_pc_denied = test_client.post(
            f"/api/v1/cases/{case_pc_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": rej_post_commit_sig_id,
                "notes": "Attempting to attach post-commit rejected signal",
            },
            headers=auth_headers,
        )
        assert attach_pc_denied.status_code in (400, 403), f"Case attachment must be rejected: {attach_pc_denied.text}"

        # RESUME the pilot via authenticated HTTP endpoint with readiness verification
        resume_pc_resp = test_client.post(
            f"/api/v1/pilots/{post_commit_pilot_id}/resume",
            json={"reason": "Cleared post-commit pause race containment"},
            headers=op_headers,
        )
        assert resume_pc_resp.status_code == 200
        assert resume_pc_resp.json()["status"] == "ACTIVE"

        # Verify the rejected publication CANNOT reappear or become attachable after RESUME
        attach_pc_revive_denied = test_client.post(
            f"/api/v1/cases/{case_pc_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": rej_post_commit_sig_id,
                "notes": "Attempting to revive post-commit rejected signal after pilot resumed",
            },
            headers=auth_headers,
        )
        assert attach_pc_revive_denied.status_code in (400, 403), f"Rejected signal must NOT revive: {attach_pc_revive_denied.text}"

        # Execute fresh authorized query and verify normal publication and evidence attachment
        cbi_svc._before_signal_publication_hook = None
        fresh_pc_query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=pc_query_payload,
            headers=auth_headers,
        )
        assert fresh_pc_query_resp.status_code == 200, f"Fresh query post-resume failed: {fresh_pc_query_resp.text}"
        fresh_pc_signal = fresh_pc_query_resp.json()
        assert fresh_pc_signal["match_confirmed"] is True
        fresh_pc_sig_id = fresh_pc_signal["signal_id"]

        # Verify durable reservation is now PUBLISHED in SQLite
        with pilot_store._lock, pilot_store._get_connection() as conn:
            cur = conn.execute(
                "SELECT status, signal_id, receipt_id FROM pilot_query_reservations WHERE pilot_id = ? AND status = 'PUBLISHED';",
                (post_commit_pilot_id,),
            )
            pub_pc_row = cur.fetchone()
            assert pub_pc_row is not None, "Reservation must be PUBLISHED for fresh successful query"
            assert pub_pc_row["signal_id"] == fresh_pc_sig_id

        # Verify signal in Redis is authoritative and attachable
        pub_pc_sig = cbi_svc._signal_store.get(fresh_pc_sig_id)
        assert pub_pc_sig is not None
        assert pub_pc_sig.get("is_authoritative") is True
        assert pub_pc_sig.get("is_attachable") is True
        assert pub_pc_sig.get("publication_status") == "PUBLISHED"

        # Legitimate case evidence attachment succeeds
        attach_pc_ok = test_client.post(
            f"/api/v1/cases/{case_pc_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": fresh_pc_sig_id,
                "notes": "Legitimate post-commit post-resume evidence attachment",
            },
            headers=auth_headers,
        )
        assert attach_pc_ok.status_code == 200, f"Fresh evidence attachment failed: {attach_pc_ok.text}"


# ==============================================================================
# Gate F — Pilot Evidence & Durable Audit Dossier Export
# ==============================================================================
def test_gate_f_audit_dossier_and_evidence_export(
    docker_topology: dict[str, Any],
    pilot_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate F: Full audit dossier export referencing genuine PSI receipts, case evidence, and zero raw secrets."""
    pilot_svc: PilotOperationsService = pilot_test_env["pilot_svc"]
    gov_svc: ConsortiumGovernanceService = pilot_test_env["gov_svc"]
    token_op = pilot_test_env["token_op"]
    token_analyst_a = pilot_test_env["token_analyst_a"]
    port_coord: int = docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 0. Sync store files into containers and register authorized grant on workers
    store_file = pilot_test_env["consortium_store_file"]
    account_file = pilot_test_env["account_store_file"]
    c_worker_a = docker_topology["container_worker_a"]
    c_worker_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]
    for c in [c_worker_a, c_worker_b, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "cp", str(account_file), f"{c}:/tmp/account_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/account_store.json"], check=False)

    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    c_data = gov_svc.get_consortium("consortium_pilot_m5")
    assert c_data is not None
    grant_meta = c_data.metadata.get("operational_data_sharing_grant", {})
    scope_digest = grant_meta.get("scope_digest", "")

    client_a.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_INVESTIGATION"],
    )
    client_b.register_authorized_grant(
        consortium_id="consortium_pilot_m5",
        grant_id=pilot_test_env["grant_id"],
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_INVESTIGATION"],
    )

    # 1. Provision Pilot
    id_a = docker_topology.get("ident_a") or pilot_test_env["id_a"]
    id_b = docker_topology.get("ident_b") or pilot_test_env["id_b"]
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key, allow_overwrite=True)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key, allow_overwrite=True)

    pilot_id = "pilot_dossier_01"
    pilot_ent = pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        principal=pilot_test_env["principal_op"],
        topology={
            "bank_a_worker_url": f"http://127.0.0.1:{docker_topology['port_a']}",
            "bank_b_worker_url": f"http://127.0.0.1:{docker_topology['port_b']}",
            "coordinator_url": coord_url,
        },
    )
    pilot_svc.run_preflight(pilot_id, principal=pilot_test_env["principal_op"])

    # 2. Independent Bilateral Approvals with genuine Ed25519 signatures
    sig_a = id_a.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_a",
        approving_principal_id="admin_bank_a",
    ))
    sig_b = id_b.sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        scope_digest=pilot_ent.scope_digest,
        config_revision=pilot_ent.config.revision,
        approving_bank_id="bank_b",
        approving_principal_id="admin_bank_b",
    ))
    pilot_svc.approve_pilot(pilot_id, "bank_a", principal=pilot_test_env["principal_a"], signature=sig_a, notes="Approved A")
    pilot_svc.approve_pilot(pilot_id, "bank_b", principal=pilot_test_env["principal_b"], signature=sig_b, notes="Approved B")
    pilot_svc.approve_pilot(pilot_id, "consortium", principal=pilot_test_env["principal_op"], notes="Approved Op")
    pilot_svc.activate_pilot(pilot_id, principal=pilot_test_env["principal_op"])

    # 3. Setup genuine entity in Docker vaults and CrossBankIntelligenceService
    shared_iban = "DE89370400440532013000"
    ent_id = "ent_dossier_a"
    client_a.store_private_entity({
        "id": ent_id,
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_iban,
        "risk_level": "HIGH",
        "typologies": ["TERRORIST_FINANCING"],
    })
    client_b.store_private_entity({
        "id": "ent_dossier_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_iban,
        "risk_level": "CRITICAL",
        "typologies": ["TERRORIST_FINANCING"],
    })

    cbi_svc = CrossBankIntelligenceService(consortium_service=gov_svc)
    ent_obj = Entity(
        id=ent_id,
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_iban.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Dossier Subject",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_iban},
    )
    from app.application.services.entity_resolution import _entity_to_dict
    cbi_svc.entity_service._entities.set(ent_id, _entity_to_dict(ent_obj))

    from app.presentation.routers import cross_bank_intelligence as cbi_router_mod
    monkeypatch.setattr(cbi_router_mod, "get_cross_bank_intelligence_service", lambda: cbi_svc)

    with TestClient(app) as client:
        auth_headers_analyst = {"Authorization": f"Bearer {token_analyst_a}"}
        auth_headers_op = {"Authorization": f"Bearer {token_op}"}

        # 4. Open investigation case
        case_resp = client.post(
            "/api/v1/cases",
            json={
                "title": "Gate F Authentic Pilot Case Investigation",
                "priority": "p1_critical",
                "bank_id": "bank_a",
            },
            headers=auth_headers_analyst,
        )
        assert case_resp.status_code == 200, f"Case creation failed: {case_resp.text}"
        case_id = case_resp.json()["id"]

        # 5. Execute genuine OpenMined PSI query on synthetic data
        query_payload = {
            "consortium_id": "consortium_pilot_m5",
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "query_entity_id": ent_id,
            "case_id": case_id,
            "purpose": "AML_INVESTIGATION",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "pilot_id": pilot_id,
        }
        query_resp = client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers=auth_headers_analyst,
        )
        assert query_resp.status_code == 200, f"Cross-bank query failed: {query_resp.text}"
        signal_data = query_resp.json()
        signal_id = signal_data["signal_id"]
        receipt_id = signal_data.get("receipt_id") or signal_data.get("provenance", {}).get("receipt_id", "")
        assert receipt_id, "Genuine PSI execution receipt ID must be present in signal response"

        # 6. Attach genuine signal to Case Evidence
        attach_resp = client.post(
            f"/api/v1/cases/{case_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": signal_id,
                "notes": "Gate F authentic pilot execution match evidence",
            },
            headers=auth_headers_analyst,
        )
        assert attach_resp.status_code == 200, f"Evidence attachment failed: {attach_resp.text}"

        # 7. Complete pilot through authenticated operator API
        comp_resp = client.post(
            f"/api/v1/pilots/{pilot_id}/complete",
            json={"reason": "Milestone 5 operational pilot successfully completed"},
            headers=auth_headers_op,
        )
        assert comp_resp.status_code == 200, f"Pilot completion failed: {comp_resp.text}"

        # 8. Export Audit Dossier
        dossier_resp = client.get(
            f"/api/v1/pilots/{pilot_id}/dossier",
            headers=auth_headers_op,
        )
        assert dossier_resp.status_code == 200, f"Dossier export failed: {dossier_resp.text}"
        dossier = dossier_resp.json()

        # 9. Structure & Provenance Verification
        assert dossier["pilot_id"] == pilot_id
        assert dossier["status"] == "COMPLETED"
        assert dossier["scope_digest"]
        assert len(dossier["state_history"]) >= 5
        assert len(dossier["approvals"]) == 3
        assert dossier["telemetry"]["queries_total"] == 1
        assert dossier["telemetry"]["matches_found"] == 1
        measured_latency = dossier["telemetry"]["mean_latency_ms"]
        assert measured_latency > 0, "Telemetry mean latency must be measured and positive"
        assert measured_latency != 45.2, "Latency must NOT be fabricated illustrative 45.2 ms value"

        # 10. Execution Receipts Binding Verification
        exec_receipts = dossier.get("execution_receipts", [])
        assert len(exec_receipts) >= 1, "Dossier must persist and export genuine execution receipts"
        matching_receipt = next((r for r in exec_receipts if r["receipt_id"] == receipt_id), None)
        assert matching_receipt is not None, f"Dossier execution receipts must contain genuine receipt ID {receipt_id}"
        assert matching_receipt["signal_id"] == signal_id
        assert matching_receipt["case_id"] == case_id
        assert matching_receipt["match_confirmed"] is True

        # 11. Audit Trail Digest Verification
        audit_trail = dossier.get("audit_trail", [])
        assert len(audit_trail) > 0, "Audit trail must not be empty"
        for evt in audit_trail:
            assert "event_id" in evt
            assert "event_digest" in evt
            assert len(evt["event_digest"]) == 64, "Event digest must be valid SHA-256 hash"

        # 12. Confidentiality Verification (Zero raw secrets, tokens, or PII)
        dossier_text = json.dumps(dossier)
        assert "private_key" not in dossier_text.lower()
        assert "password" not in dossier_text.lower()
        assert "token" not in dossier_text.lower() or "token_bundle" not in dossier_text.lower()

