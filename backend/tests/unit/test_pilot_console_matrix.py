"""Console and Regression Matrix Tests for M6.6 Governed Consortium Pilot Operations Console.

Verifies:
1. Anonymous access rejection (401 Unauthorized for listing and pilot details).
2. Cross-tenant isolation (403 Forbidden for reading, approving, pausing, and telemetry).
3. Non-operator lifecycle transition rejection (403 Forbidden).
4. Participating institution emergency kill-switch authority (200 OK) vs non-operator resume rejection (403 Forbidden).
5. Role-restricted audit and compliance dossier export (403 Forbidden for non-auditors, 200 OK for auditor/operator).
6. Truthful telemetry latency metrics (null / has_latency_observations: False when 0 observations exist).
7. Accurate permitted_actions and missing_approvals derivation across pilot lifecycle.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.pilot_operations_service import PilotOperationsService
from app.domain.consortium_governance import MemberRole
from app.domain.institutional_identity import InstitutionalSigningIdentity
from app.domain.pilot_operations import (
    canonical_pilot_approval_bytes,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app

SHARED_JWT_SECRET = "jwt_secret_pilot_console_matrix_918237198273"
SHARED_CONSORTIUM_SECRET = "consortium_secret_pilot_console_matrix_81923"


@pytest.fixture
def console_test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, Any], None, None]:
    consortium_store_file = tmp_path / "console_consortium_store.json"
    account_store_file = tmp_path / "console_account_store.json"
    pilot_db_file = tmp_path / "console_pilot_operations.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_SECRET)
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
    # Users
    u_op = auth_svc.register_user(
        "console_operator", "Pass123!", "consortium", roles=["admin", "operator", "consortium_operator"]
    )
    u_bank_a_admin = auth_svc.register_user(
        "console_bank_a_admin", "Pass123!", "bank_a", roles=["admin", "security_officer"]
    )
    u_bank_a_auditor = auth_svc.register_user(
        "console_bank_a_auditor", "Pass123!", "bank_a", roles=["auditor", "compliance_officer"]
    )
    u_bank_a_analyst = auth_svc.register_user(
        "console_bank_a_analyst", "Pass123!", "bank_a", roles=["analyst", "investigator"]
    )
    u_bank_b_admin = auth_svc.register_user(
        "console_bank_b_admin", "Pass123!", "bank_b", roles=["admin"]
    )
    u_outsider = auth_svc.register_user(
        "console_outsider", "Pass123!", "bank_c", roles=["admin", "operator"]
    )

    token_op = auth_svc.create_token_bundle(u_op).access_token
    token_bank_a_admin = auth_svc.create_token_bundle(u_bank_a_admin).access_token
    token_bank_a_auditor = auth_svc.create_token_bundle(u_bank_a_auditor).access_token
    token_bank_a_analyst = auth_svc.create_token_bundle(u_bank_a_analyst).access_token
    token_bank_b_admin = auth_svc.create_token_bundle(u_bank_b_admin).access_token
    token_outsider = auth_svc.create_token_bundle(u_outsider).access_token

    # Setup consortium
    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="consortium-main",
        name="Main Consortium",
        founder_bank_id="bank_a",
        auth_token=token_bank_a_admin,
    )
    gov_svc.add_member(
        consortium_id="consortium-main",
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_bank_a_admin,
    )
    gov_svc.add_member(
        consortium_id="consortium-main",
        bank_id="bank_c",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_bank_a_admin,
    )
    gov_svc.register_operational_data_sharing_grant(
        consortium_id="consortium-main",
        grant_id="grant_c_main",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["US", "EU"],
        auth_token=token_bank_a_admin,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id="consortium-main",
        grant_id="grant_c_main",
        approving_bank_id="bank_b",
        auth_token=token_bank_b_admin,
    )

    pilot_store = DurablePilotStore(str(pilot_db_file))
    pilot_svc = PilotOperationsService(pilot_store=pilot_store, consortium_service=gov_svc)
    id_a = InstitutionalSigningIdentity.generate("bank_a")
    id_b = InstitutionalSigningIdentity.generate("bank_b")
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key)
    monkeypatch.setattr(pilot_svc, "_probe_node_health", lambda url, node_type, **kw: (True, "HEALTHY_MOCK"))
    PilotOperationsService._instance = pilot_svc

    client = TestClient(app)

    yield {
        "client": client,
        "pilot_svc": pilot_svc,
        "id_a": id_a,
        "id_b": id_b,
        "tokens": {
            "operator": token_op,
            "bank_a_admin": token_bank_a_admin,
            "bank_a_auditor": token_bank_a_auditor,
            "bank_a_analyst": token_bank_a_analyst,
            "bank_b_admin": token_bank_b_admin,
            "outsider": token_outsider,
        },
    }

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurablePilotStore._instance = None
    PilotOperationsService._instance = None


def test_anonymous_discovery_rejected(console_test_env: dict[str, Any]) -> None:
    """Anonymous discovery must return 401 Unauthorized."""
    client: TestClient = console_test_env["client"]

    # Listing pilots
    resp = client.get("/api/v1/pilots")
    assert resp.status_code == 401

    # Inspecting pilot detail
    resp = client.get("/api/v1/pilots/pilot-test-anon")
    assert resp.status_code == 401


def test_cross_tenant_isolation(console_test_env: dict[str, Any]) -> None:
    """Non-participating tenants must receive 403 Forbidden on read, action, or telemetry."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]

    # Provision a pilot for bank_a and bank_b by operator
    prov_payload = {
        "pilot_id": "pilot-iso-001",
        "consortium_id": "consortium-main",
        "participating_institutions": ["bank_a", "bank_b"],
        "allowed_purposes": ["AML_INVESTIGATION"],
        "duration_days": 14,
        "query_quota_total": 50,
        "query_quota_per_institution": 25,
    }
    prov_resp = client.post(
        "/api/v1/pilots",
        json=prov_payload,
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert prov_resp.status_code in (200, 201), prov_resp.text

    # bank_c (outsider) attempts to read pilot
    read_resp = client.get(
        "/api/v1/pilots/pilot-iso-001",
        headers={"Authorization": f"Bearer {tokens['outsider']}"},
    )
    assert read_resp.status_code == 403

    # bank_c attempts to approve
    app_resp = client.post(
        "/api/v1/pilots/pilot-iso-001/approve",
        json={"approving_institution": "bank_c", "notes": "unauthorized"},
        headers={"Authorization": f"Bearer {tokens['outsider']}"},
    )
    assert app_resp.status_code == 403

    # bank_c attempts to pause
    pause_resp = client.post(
        "/api/v1/pilots/pilot-iso-001/pause",
        json={"reason": "unauthorized attempt"},
        headers={"Authorization": f"Bearer {tokens['outsider']}"},
    )
    assert pause_resp.status_code == 403

    # bank_c attempts to read telemetry
    tel_resp = client.get(
        "/api/v1/pilots/pilot-iso-001/telemetry",
        headers={"Authorization": f"Bearer {tokens['outsider']}"},
    )
    assert tel_resp.status_code == 403


def test_dossier_role_authorization(console_test_env: dict[str, Any]) -> None:
    """Dossier export must require compliance, auditor, operator, or admin role."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]

    # Provision pilot
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": "pilot-dossier-001",
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    # bank_a analyst (lacking auditor/compliance role) -> 403
    resp_analyst = client.get(
        "/api/v1/pilots/pilot-dossier-001/dossier",
        headers={"Authorization": f"Bearer {tokens['bank_a_analyst']}"},
    )
    assert resp_analyst.status_code == 403

    # bank_a auditor -> 200
    resp_auditor = client.get(
        "/api/v1/pilots/pilot-dossier-001/dossier",
        headers={"Authorization": f"Bearer {tokens['bank_a_auditor']}"},
    )
    assert resp_auditor.status_code == 200
    dossier = resp_auditor.json()
    assert dossier["pilot_id"] == "pilot-dossier-001"
    assert "approvals" in dossier

    # operator -> 200
    resp_op = client.get(
        "/api/v1/pilots/pilot-dossier-001/dossier",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert resp_op.status_code == 200


def test_kill_switch_emergency_authority_and_operator_resume(console_test_env: dict[str, Any]) -> None:
    """Participating institutions can pause; resuming requires operator authority."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]
    id_a = console_test_env["id_a"]
    id_b = console_test_env["id_b"]

    pilot_id = "pilot-kill-001"
    prov_resp = client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
            "allowed_purposes": ["AML_INVESTIGATION"],
            "allowed_jurisdictions": ["EU"],
            "operational_grant_id": "grant_c_main",
            "topology": {
                "bank_a_worker_url": "http://127.0.0.1:8001",
                "bank_b_worker_url": "http://127.0.0.1:8002",
                "coordinator_url": "http://127.0.0.1:8000",
            },
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert prov_resp.status_code in (200, 201), prov_resp.text

    # Preflight
    pf_resp = client.post(
        f"/api/v1/pilots/{pilot_id}/preflight",
        json={},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert pf_resp.status_code == 200, pf_resp.text
    assert pf_resp.json()["status"] == "AWAITING_APPROVAL"

    # Approve all three parties
    status_resp = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    scope_digest = status_resp.json()["scope_digest"]

    # Bank A signature
    sig_a = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_a", approver_id="console_bank_a_admin"))
    resp_a = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_a},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_a.status_code == 200, resp_a.text

    # Bank B signature
    sig_b = id_b.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_b", approver_id="console_bank_b_admin"))
    resp_b = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_b", "signature": sig_b},
        headers={"Authorization": f"Bearer {tokens['bank_b_admin']}"},
    )
    assert resp_b.status_code == 200, resp_b.text

    # Operator approval
    resp_op = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "OPERATOR", "notes": "Approved by Consortium Operator"},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert resp_op.status_code == 200, resp_op.text
    assert resp_op.json()["status"] == "AUTHORIZED"

    # Activate
    act_resp = client.post(
        f"/api/v1/pilots/{pilot_id}/activate",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert act_resp.status_code == 200, act_resp.text
    assert act_resp.json()["status"] == "ACTIVE"

    # Bank A triggers emergency pause
    pause_resp = client.post(
        f"/api/v1/pilots/{pilot_id}/pause",
        json={"reason": "Suspicious anomaly detected by Bank A fraud desk"},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "PAUSED"

    # Bank A attempts to resume -> 403 Forbidden (Resume requires operator authority)
    resume_fail = client.post(
        f"/api/v1/pilots/{pilot_id}/resume",
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resume_fail.status_code == 403

    # Operator resumes -> 200 OK
    resume_ok = client.post(
        f"/api/v1/pilots/{pilot_id}/resume",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert resume_ok.status_code == 200
    assert resume_ok.json()["status"] == "ACTIVE"


def test_permitted_actions_and_missing_approvals_derivation(console_test_env: dict[str, Any]) -> None:
    """Response must contain truthful permitted_actions and missing_approvals."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]
    id_a = console_test_env["id_a"]

    pilot_id = "pilot-actions-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    # Initial DRAFT status for Operator
    op_status = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    assert op_status["status"] == "DRAFT"
    assert "preflight" in op_status["permitted_actions"]
    assert "approve" in op_status["permitted_actions"]
    assert "activate" not in op_status["permitted_actions"]
    assert set(op_status["missing_approvals"]) == {"bank_a", "bank_b", "OPERATOR"}

    # Bank A approves
    scope_digest = op_status["scope_digest"]
    sig_a = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_a", approver_id="console_bank_a_admin"))
    app_resp = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_a},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert app_resp.status_code == 200, app_resp.text
    app_a = app_resp.json()
    assert "bank_a" not in app_a["missing_approvals"]
    assert set(app_a["missing_approvals"]) == {"bank_b", "OPERATOR"}


def test_truthful_telemetry_no_observations(console_test_env: dict[str, Any]) -> None:
    """When zero queries have executed, latency metrics must be null and has_latency_observations False."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]

    pilot_id = "pilot-tel-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
            "query_quota_total": 100,
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    tel_resp = client.get(
        f"/api/v1/pilots/{pilot_id}/telemetry",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert tel_resp.status_code == 200
    tel = tel_resp.json()
    assert tel["queries_executed"] == 0
    assert tel["query_quota_total"] == 100
    assert tel["quota_remaining"] == 100
    # Gate E verification: No fake 0.0 ms!
    assert tel["mean_latency_ms"] is None
    assert tel["p95_latency_ms"] is None
    assert tel["has_latency_observations"] is False
    assert tel["latency_sample_count"] == 0


def test_bank_admin_operator_impersonation_rejected(console_test_env: dict[str, Any]) -> None:
    """Gate A: Bank A or Bank B administrator cannot impersonate independent consortium operator."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]

    pilot_id = "pilot-op-impersonate-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    # 1. Bank A admin attempts approving_institution="OPERATOR"
    resp_a = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "OPERATOR", "signature": ""},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_a.status_code == 403, resp_a.text
    assert "participating bank" in resp_a.text.lower() or "operator" in resp_a.text.lower()

    # 2. Bank B admin attempts approving_institution="OPERATOR"
    resp_b = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "OPERATOR", "signature": ""},
        headers={"Authorization": f"Bearer {tokens['bank_b_admin']}"},
    )
    assert resp_b.status_code == 403, resp_b.text


def test_cross_bank_and_unauthorized_role_approvals_rejected(console_test_env: dict[str, Any]) -> None:
    """Gate A & B: Cross-bank manufacturing and analyst approval submission are strictly rejected."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]
    id_a = console_test_env["id_a"]

    pilot_id = "pilot-cross-role-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    status_resp = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    ).json()
    # Bank A admin sees ONLY bank_a in approvable_institutions, NEVER bank_b or OPERATOR
    assert status_resp["approvable_institutions"] == ["bank_a"]
    assert "approve" in status_resp["permitted_actions"]
    assert status_resp["caller_approver_id"] == "console_bank_a_admin"
    assert status_resp["canonical_signing_challenge"] is not None

    # Bank A analyst cannot approve
    analyst_status = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['bank_a_analyst']}"},
    ).json()
    assert analyst_status["approvable_institutions"] == []
    assert "approve" not in analyst_status["permitted_actions"]

    # Bank A attempts to approve Bank B
    scope_digest = status_resp["scope_digest"]
    sig_a = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_b", approver_id="console_bank_a_admin"))
    resp_cross = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_b", "signature": sig_a},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_cross.status_code == 403, resp_cross.text
    assert "cannot approve pilot on behalf of" in resp_cross.text.lower() or "cross-bank" in resp_cross.text.lower()

    # Bank A analyst attempts to approve Bank A
    resp_analyst = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_a},
        headers={"Authorization": f"Bearer {tokens['bank_a_analyst']}"},
    )
    assert resp_analyst.status_code == 403, resp_analyst.text
    assert "lacks required institutional approval role" in resp_analyst.text.lower()


def test_cryptographic_signature_enforcement_and_tamper_rejection(console_test_env: dict[str, Any]) -> None:
    """Gate A: Enrolled keys require genuine signatures; unsigned or tampered submissions fail closed."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]
    id_a = console_test_env["id_a"]
    id_b = console_test_env["id_b"]

    pilot_id = "pilot-crypto-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    status_resp = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    ).json()
    scope_digest = status_resp["scope_digest"]

    # 1. Unsigned submission when verification key is enrolled -> 400 Bad Request
    resp_unsigned = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": ""},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_unsigned.status_code == 400
    assert "missing cryptographic signature" in resp_unsigned.text.lower()

    # 2. Tampered signature (signed with Bank B's private key for Bank A) -> 400 Bad Request
    sig_wrong_key = id_b.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_a", approver_id="console_bank_a_admin"))
    resp_bad_sig = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_wrong_key},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_bad_sig.status_code == 400
    assert "signature verification failed" in resp_bad_sig.text.lower()

    # 3. Altered scope digest signature -> 400 Bad Request
    sig_altered_scope = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, "0" * 64, "bank_a", approver_id="console_bank_a_admin"))
    resp_bad_scope = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_altered_scope},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_bad_scope.status_code == 400
    assert "signature verification failed" in resp_bad_scope.text.lower()

    # 4. Valid signature -> 200 OK
    sig_valid = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_a", approver_id="console_bank_a_admin"))
    resp_valid = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_valid},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert resp_valid.status_code == 200, resp_valid.text


def test_operator_and_institutional_identity_separation(console_test_env: dict[str, Any]) -> None:
    """Gate A: Same principal cannot serve as both institutional approver and independent operator."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]
    id_a = console_test_env["id_a"]
    id_b = console_test_env["id_b"]

    pilot_id = "pilot-separation-001"
    client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": pilot_id,
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
            "allowed_purposes": ["AML_INVESTIGATION"],
            "allowed_jurisdictions": ["EU"],
            "operational_grant_id": "grant_c_main",
            "topology": {
                "bank_a_worker_url": "http://127.0.0.1:8001",
                "bank_b_worker_url": "http://127.0.0.1:8002",
                "coordinator_url": "http://127.0.0.1:8000",
            },
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )

    # Preflight moves pilot to AWAITING_APPROVAL
    pf_resp = client.post(
        f"/api/v1/pilots/{pilot_id}/preflight",
        json={},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert pf_resp.status_code == 200, pf_resp.text
    assert pf_resp.json()["status"] == "AWAITING_APPROVAL"

    status_resp = client.get(
        f"/api/v1/pilots/{pilot_id}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    scope_digest = status_resp["scope_digest"]

    # Bank A approves
    sig_a = id_a.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_a", approver_id="console_bank_a_admin"))
    client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_a", "signature": sig_a},
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )

    # Bank B approves
    sig_b = id_b.sign(canonical_pilot_approval_bytes(pilot_id, 1, scope_digest, "bank_b", approver_id="console_bank_b_admin"))
    client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "bank_b", "signature": sig_b},
        headers={"Authorization": f"Bearer {tokens['bank_b_admin']}"},
    )

    # Operator approves with independent operator principal -> 200 OK
    resp_op = client.post(
        f"/api/v1/pilots/{pilot_id}/approve",
        json={"approving_institution": "OPERATOR", "notes": "Independent operator authorization"},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert resp_op.status_code == 200, resp_op.text
    final_status = resp_op.json()
    assert final_status["is_fully_approved"] is True
    assert final_status["status"] == "AUTHORIZED"
    # Ensure OPERATOR approval is recorded under uppercase 'OPERATOR' key
    assert "OPERATOR" in final_status["approvals"]
    assert final_status["approvals"]["OPERATOR"]["approver_institution"] == "OPERATOR"


def test_pilot_provisioning_authority_and_truthful_topology(console_test_env: dict[str, Any]) -> None:
    """Gate C: Provisioning requires operator authority; empty topology truthfully reports unverified in preflight."""
    client: TestClient = console_test_env["client"]
    tokens = console_test_env["tokens"]

    # 1. Bank A admin attempts to provision -> 403 Forbidden
    prov_fail = client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": "pilot-bank-prov-001",
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
        },
        headers={"Authorization": f"Bearer {tokens['bank_a_admin']}"},
    )
    assert prov_fail.status_code == 403, prov_fail.text
    assert "participating bank" in prov_fail.text.lower() or "operator" in prov_fail.text.lower()

    # 2. Operator provisions with empty topology -> 201 Created in DRAFT status
    prov_ok = client.post(
        "/api/v1/pilots",
        json={
            "pilot_id": "pilot-draft-topo-001",
            "consortium_id": "consortium-main",
            "participating_institutions": ["bank_a", "bank_b"],
            "allowed_purposes": ["AML_INVESTIGATION"],
            # No topology provided!
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert prov_ok.status_code in (200, 201), prov_ok.text
    draft = prov_ok.json()
    assert draft["status"] == "DRAFT"

    # 3. Running preflight on this draft without topology fails preflight check
    # Temporarily remove mocked probe to test topology resolution
    svc = console_test_env["pilot_svc"]
    entity = svc.store.get_pilot("pilot-draft-topo-001")
    assert entity is not None
    bank_a_url, bank_b_url, coord_url, missing = svc._resolve_topology_endpoints(entity)
    assert len(missing) == 3
    assert "bank_a worker" in missing
    assert "bank_b worker" in missing
    assert "coordinator" in missing
