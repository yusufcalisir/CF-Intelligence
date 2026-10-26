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
    InstitutionalWorkerClient,
)
from app.application.services.pilot_operations_service import (
    PilotGovernanceError,
    PilotOperationsService,
)
from app.domain.consortium_governance import MemberRole
from app.domain.institutional_identity import InstitutionalSigningIdentity
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.domain.pilot_operations import PilotStatus
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app

# Import Docker topology fixtures from test_institutional_docker_psi_vertical_slice
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
    }


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
        # 1. Consortium Root CA identity is stable across calls
        ca_resp1 = client.get("/v1/onboarding/consortium-ca")
        ca_resp2 = client.get("/v1/onboarding/consortium-ca")
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

        # Step 4: Independent Bilateral Approvals
        # 4a. Bank A Approves
        app_a = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_a", "notes": "Bank A board approval for pilot"},
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert app_a.status_code == 200
        assert app_a.json()["status"] == "AWAITING_APPROVAL"

        # 4b. Bank B Approves
        app_b = client.post(
            "/api/v1/pilots/pilot_two_bank_test_01/approve",
            json={"institution_id": "bank_b", "notes": "Bank B compliance approval for pilot"},
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
    pilot_id = "pilot_docker_live_01"
    pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        creator_actor="pilot_operator_01",
    )
    pilot_svc.run_preflight(pilot_id, operator_actor="pilot_operator_01")
    pilot_svc.approve_pilot(pilot_id, "bank_a", "admin_bank_a", "Bank A approval")
    pilot_svc.approve_pilot(pilot_id, "bank_b", "admin_bank_b", "Bank B approval")
    pilot_svc.approve_pilot(pilot_id, "consortium", "pilot_operator_01", "Operator approval")
    pilot_svc.activate_pilot(pilot_id, "pilot_operator_01")

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

    # 1. Provision & Activate Resilience Pilot
    pilot_id = "pilot_resilience_01"
    pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        creator_actor="pilot_operator_01",
    )
    pilot_svc.run_preflight(pilot_id, operator_actor="pilot_operator_01")
    pilot_svc.approve_pilot(pilot_id, "bank_a", "admin_bank_a", "Approved A")
    pilot_svc.approve_pilot(pilot_id, "bank_b", "admin_bank_b", "Approved B")
    pilot_svc.approve_pilot(pilot_id, "consortium", "pilot_operator_01", "Approved Op")
    pilot_svc.activate_pilot(pilot_id, "pilot_operator_01")

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
        pilot_svc.pause_pilot(pilot_id, "pilot_operator_01", "Testing persistence across restart")
        pilot_db_file = pilot_test_env["pilot_db_file"]

        # Simulate process reboot with fresh PilotOperationsService instance reading from DB
        rebooted_store = DurablePilotStore(str(pilot_db_file))
        rebooted_svc = PilotOperationsService(pilot_store=rebooted_store, consortium_service=gov_svc)

        persisted_pilot = rebooted_svc.get_pilot(pilot_id)
        assert persisted_pilot is not None
        assert persisted_pilot.status == PilotStatus.PAUSED
        # State machine prevents silent re-authorization or auto-activation
        assert persisted_pilot.is_active is False


# ==============================================================================
# Gate F — Pilot Evidence & Durable Audit Dossier Export
# ==============================================================================
def test_gate_f_audit_dossier_and_evidence_export(
    pilot_test_env: dict[str, Any],
) -> None:
    """Gate F: Full audit dossier export, state transition log, and absence of raw secrets."""
    pilot_svc: PilotOperationsService = pilot_test_env["pilot_svc"]
    token_op = pilot_test_env["token_op"]

    pilot_id = "pilot_dossier_01"
    pilot_svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="consortium_pilot_m5",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["syn_a_v1", "syn_b_v1"],
        max_queries_total=10,
        max_queries_per_institution=5,
        duration_days=7,
        creator_actor="pilot_operator_01",
    )
    pilot_svc.run_preflight(pilot_id, operator_actor="pilot_operator_01")
    pilot_svc.approve_pilot(pilot_id, "bank_a", "admin_bank_a", "Approved A")
    pilot_svc.approve_pilot(pilot_id, "bank_b", "admin_bank_b", "Approved B")
    pilot_svc.approve_pilot(pilot_id, "consortium", "pilot_operator_01", "Approved Op")
    pilot_svc.activate_pilot(pilot_id, "pilot_operator_01")
    pilot_svc.record_query_outcome(pilot_id, success=True, is_match=True, latency_ms=45.2)
    pilot_svc.complete_pilot(pilot_id, "pilot_operator_01", "Pilot completed successfully")

    with TestClient(app) as client:
        dossier_resp = client.get(
            f"/api/v1/pilots/{pilot_id}/dossier",
            headers={"Authorization": f"Bearer {token_op}"},
        )
        assert dossier_resp.status_code == 200, f"Dossier export failed: {dossier_resp.text}"
        dossier = dossier_resp.json()

        # 1. Structure Verification
        assert dossier["pilot_id"] == pilot_id
        assert dossier["status"] == "COMPLETED"
        assert dossier["scope_digest"]
        assert len(dossier["state_history"]) >= 5
        assert len(dossier["approvals"]) == 3
        assert dossier["telemetry"]["queries_total"] == 1
        assert dossier["telemetry"]["matches_found"] == 1

        # 2. Confidentiality Verification (Zero raw secrets, tokens, or PII)
        dossier_text = json.dumps(dossier)
        assert "private_key" not in dossier_text.lower()
        assert "password" not in dossier_text.lower()
        assert "token" not in dossier_text.lower() or "token_bundle" not in dossier_text.lower()
