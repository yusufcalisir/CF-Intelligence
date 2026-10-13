"""GATE-01 through GATE-10: Authenticated Governance & Deployment Operations Test Suite.

Verifies that model lifecycle and deployment mutations require genuine Bearer authentication,
authoritative active account verification, and live RBAC role checks from DurableAccountStore.
Guarantees fail-closed behavior before any mutation occurs and confirms persisted state integrity.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
import torch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.application.schemas.model_registry import ModelPromoteRequest
from app.application.services.model_registry import ModelRegistry
from app.application.services.zero_downtime_deployer import ZeroDowntimeDeploymentManager
from app.domain.deployment_state import DeploymentStage
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import AuthenticationService
from app.main import app
from app.presentation.routers.model_registry import (
    promote_model_by_simulation,
    promote_model_version,
)


@pytest.fixture
def auth_gateway_env():
    """Isolated environment fixture providing DurableAccountStore, AuthenticationService,

    ModelRegistry, ZeroDowntimeDeploymentManager, and seeded test principals.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = os.path.join(tmp_dir, "accounts.json")
        reg_dir = os.path.join(tmp_dir, "models_reg")
        os.makedirs(reg_dir, exist_ok=True)

        signing_secret = "secure_gateway_test_secret_key_at_least_32_bytes_long"
        authority_secret = "authoritative_signing_master_secret_key_64_bytes_test"

        auth_svc = AuthenticationService(
            signing_secret=signing_secret,
            store_path=store_path,
            authority_secret=authority_secret,
            app_env="development",
            access_token_expire_minutes=15,
            refresh_token_expire_days=7,
        )
        store = auth_svc.identity_store

        test_registry = ModelRegistry(storage_dir=reg_dir)
        test_deployer = ZeroDowntimeDeploymentManager(current_version="v2.0.0")

        # Seed authoritative test users
        users = {
            "ml_eng": auth_svc.register_user("operator_ml", "Pass123!", "bank_a", ["ml_engineer"], clearance_level=2),
            "ml_lead": auth_svc.register_user("operator_lead", "Pass123!", "bank_a", ["ml_lead"], clearance_level=3),
            "compliance": auth_svc.register_user("officer_comp", "Pass123!", "bank_a", ["compliance_officer"], clearance_level=3),
            "devops": auth_svc.register_user("operator_devops", "Pass123!", "bank_a", ["devops"], clearance_level=2),
            "admin": auth_svc.register_user("operator_admin", "Pass123!", "bank_a", ["admin"], clearance_level=3),
            "analyst": auth_svc.register_user("unprivileged_analyst", "Pass123!", "bank_a", ["analyst"], clearance_level=1),
            "auditor": auth_svc.register_user("unprivileged_auditor", "Pass123!", "bank_a", ["auditor"], clearance_level=1),
            "revoked": auth_svc.register_user("operator_revoked", "Pass123!", "bank_a", ["ml_engineer"], clearance_level=2),
            "disabled": auth_svc.register_user("operator_disabled", "Pass123!", "bank_a", ["ml_engineer"], clearance_level=2),
            "demoted": auth_svc.register_user("operator_demoted", "Pass123!", "bank_a", ["ml_engineer"], clearance_level=2),
        }

        # Issue tokens
        tokens = {name: auth_svc.create_token_bundle(u).access_token for name, u in users.items()}

        # Deactivate disabled user
        auth_svc.deactivate_user("operator_disabled")

        # Explicitly revoke revoked user's token
        auth_svc.revoke_token(tokens["revoked"])

        # Demote demoted user in DurableAccountStore post-token issuance
        demoted_rec = store.get_user("operator_demoted")
        assert demoted_rec is not None
        demoted_rec.roles = ["analyst"]
        store.save_user(demoted_rec)

        # Seed models in registry
        sim_id = "sim_gate_test"
        v1 = test_registry.save_version(
            simulation_id=sim_id,
            state_dict={"weight": torch.tensor([1.0, 2.0])},
            metrics={"auc_roc": 0.85, "auc": 0.85, "f1_score": 0.80},
            is_promoted=False,
        )
        v2 = test_registry.save_version(
            simulation_id=sim_id,
            state_dict={"weight": torch.tensor([3.0, 4.0])},
            metrics={"auc_roc": 0.90, "auc": 0.90, "f1_score": 0.85},
            is_promoted=False,
        )

        # Patch application singletons
        with (
            patch.object(AuthenticationService, "get_instance", return_value=auth_svc),
            patch("app.presentation.routers.model_registry.registry", test_registry),
            patch("app.presentation.routers.model_registry.deployer", test_deployer),
        ):
            client = TestClient(app)
            yield {
                "client": client,
                "store": store,
                "auth_svc": auth_svc,
                "registry": test_registry,
                "deployer": test_deployer,
                "tokens": tokens,
                "users": users,
                "sim_id": sim_id,
                "v1": v1["version"],
                "v2": v2["version"],
            }


# ── GATE-01: Anonymous Champion Promotion ──────────────────────────────────


def test_gate_01_anonymous_champion_promotion_rejected(auth_gateway_env):
    """GATE-01: Anonymous champion promotion is rejected (401) and manifest remains unmutated."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]

    resp = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "champion", "enforce_sr11_7": False},
    )
    assert resp.status_code == 401
    assert "Missing Authorization header" in resp.json()["detail"]

    # Verify persisted state unchanged
    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v1)
    assert entry["status"] != "champion"
    assert entry["is_active"] is False


# ── GATE-02: Anonymous Challenger Promotion ────────────────────────────────


def test_gate_02_anonymous_challenger_promotion_rejected(auth_gateway_env):
    """GATE-02: Anonymous challenger promotion is rejected (401) and manifest remains unmutated."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]

    resp = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
    )
    assert resp.status_code == 401
    assert "Missing Authorization header" in resp.json()["detail"]

    # Verify persisted state unchanged
    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v1)
    assert entry["status"] != "challenger"
    assert entry["is_active"] is False


# ── GATE-03: Anonymous Rollback ─────────────────────────────────────────────


def test_gate_03_anonymous_rollback_rejected(auth_gateway_env):
    """GATE-03: Anonymous rollback is rejected (401) and existing champion remains unchanged."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]
    v2 = auth_gateway_env["v2"]

    # First set v1 as champion legitimately
    token_ml = auth_gateway_env["tokens"]["ml_eng"]
    resp_init = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "champion", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {token_ml}"},
    )
    assert resp_init.status_code == 200
    active_before = reg.get_active_version(sim_id)
    assert active_before is not None
    assert active_before["version"] == v1

    # Anonymous attempt to rollback to v2
    resp_rb = client.post(f"/api/v1/registry/{sim_id}/rollback/{v2}")
    assert resp_rb.status_code == 401
    assert "Missing Authorization header" in resp_rb.json()["detail"]

    # Persisted active champion must be completely unchanged
    active_after = reg.get_active_version(sim_id)
    assert active_after is not None
    assert active_after["version"] == v1
    assert active_after["generation"] == active_before["generation"]


# ── GATE-04: Anonymous Deployment Operations ────────────────────────────────


def test_gate_04_anonymous_deployment_operations_rejected(auth_gateway_env):
    """GATE-04: Anonymous deployment initiate, drain, rolling-update, finalize, and abort rejected."""
    client = auth_gateway_env["client"]
    deployer = auth_gateway_env["deployer"]

    # 1. Initiate
    resp1 = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v3.0.0", "compatibility_window_hours": 24, "initial_connections": 100},
    )
    assert resp1.status_code == 401
    assert len(deployer.list_sessions()) == 0

    # Create session legitimately with devops token to test subsequent anonymous mutations
    token_devops = auth_gateway_env["tokens"]["devops"]
    resp_init = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v3.0.0", "compatibility_window_hours": 24, "initial_connections": 100},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_init.status_code == 200
    session_id = resp_init.json()["session_id"]
    sess_before = deployer.get_session(session_id)
    assert sess_before is not None
    assert sess_before.active_connections_count == 100

    # 2. Anonymous Drain
    resp2 = client.post(f"/api/v1/registry/deployment/{session_id}/drain", json={"batch_size": 50})
    assert resp2.status_code == 401
    assert deployer.get_session(session_id).active_connections_count == 100

    # 3. Anonymous Rolling Update
    resp3 = client.post(f"/api/v1/registry/deployment/{session_id}/rolling-update", json={"instance_ids": ["node-1"]})
    assert resp3.status_code == 401
    assert len(deployer.get_session(session_id).updated_instances) == 0

    # 4. Anonymous Finalize
    resp4 = client.post(f"/api/v1/registry/deployment/{session_id}/finalize")
    assert resp4.status_code == 401
    assert deployer.current_version == "v2.0.0"

    # 5. Anonymous Abort
    resp5 = client.post(f"/api/v1/registry/deployment/{session_id}/abort", json={"reason": "Attacker"})
    assert resp5.status_code == 401
    assert deployer.get_session(session_id).stage == DeploymentStage.DRAINING_CONNECTIONS


# ── GATE-05: Authenticated But Insufficiently Privileged Account ───────────


def test_gate_05_insufficiently_privileged_account_rejected(auth_gateway_env):
    """GATE-05: Authenticated but unauthorized accounts (analyst, auditor) rejected (403) before mutation."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    deployer = auth_gateway_env["deployer"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]

    analyst_token = auth_gateway_env["tokens"]["analyst"]
    auditor_token = auth_gateway_env["tokens"]["auditor"]

    headers_analyst = {"Authorization": f"Bearer {analyst_token}"}
    headers_auditor = {"Authorization": f"Bearer {auditor_token}"}

    # 1. Analyst cannot promote champion or challenger
    resp1 = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers=headers_analyst,
    )
    assert resp1.status_code == 403
    assert "not authorized to execute model promotion" in resp1.json()["detail"]

    # 2. Auditor cannot rollback model
    resp2 = client.post(f"/api/v1/registry/{sim_id}/rollback/{v1}", headers=headers_auditor)
    assert resp2.status_code == 403
    assert "not authorized to execute model rollback" in resp2.json()["detail"]

    # 3. Analyst cannot initiate deployment
    resp3 = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v3.0.0"},
        headers=headers_analyst,
    )
    assert resp3.status_code == 403
    assert "not authorized to execute deployment initiation" in resp3.json()["detail"]
    assert len(deployer.list_sessions()) == 0

    # Verify manifest entry remains untouched
    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v1)
    assert entry["status"] != "challenger"
    assert entry["is_active"] is False


# ── GATE-06: Revoked, Disabled, or Demoted Operator Credentials ─────────────


def test_gate_06_revoked_disabled_demoted_operator_rejected(auth_gateway_env):
    """GATE-06: Revoked (401), disabled (401), or demoted (403) operator credentials rejected before mutation."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]

    revoked_token = auth_gateway_env["tokens"]["revoked"]
    disabled_token = auth_gateway_env["tokens"]["disabled"]
    demoted_token = auth_gateway_env["tokens"]["demoted"]

    # 1. Revoked operator token rejected
    resp_rev = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {revoked_token}"},
    )
    assert resp_rev.status_code == 401
    assert "revoked" in resp_rev.json()["detail"].lower()

    # 2. Disabled account operator rejected
    resp_dis = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {disabled_token}"},
    )
    assert resp_dis.status_code == 401
    assert "deactivated" in resp_dis.json()["detail"].lower()

    # 3. Demoted operator (stale JWT claim defeated by live DurableAccountStore lookup)
    resp_dem = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {demoted_token}"},
    )
    assert resp_dem.status_code == 403
    assert "not authorized to execute model promotion" in resp_dem.json()["detail"]

    # Persisted manifest completely untouched
    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v1)
    assert entry["status"] != "challenger"
    assert entry["is_active"] is False


# ── GATE-07: Forged Request Body Fields Cannot Authorize ────────────────────


def test_gate_07_forged_request_fields_cannot_authorize(auth_gateway_env):
    """GATE-07: Caller-controlled body fields ('user', 'role', 'actor', 'approved_by') cannot establish authority."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]
    analyst_token = auth_gateway_env["tokens"]["analyst"]

    forged_payload = {
        "target_status": "challenger",
        "enforce_sr11_7": False,
        "user": "operator_admin",
        "role": "admin",
        "actor": "operator_admin",
        "approved_by": "operator_admin",
    }

    # 1. Anonymous caller with forged admin fields
    resp_anon = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json=forged_payload,
    )
    assert resp_anon.status_code == 401

    # 2. Analyst caller with forged admin fields in request body
    resp_auth = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json=forged_payload,
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert resp_auth.status_code == 403

    # Manifest remains unmutated
    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v1)
    assert entry["status"] != "challenger"


# ── GATE-08: Registered Route Aliases Enforce Equivalent Authorization ─────


def test_gate_08_all_route_aliases_enforce_equivalent_authorization(auth_gateway_env):
    """GATE-08: All four registered route prefixes enforce identical authentication and authorization."""
    client = auth_gateway_env["client"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]
    token_ml = auth_gateway_env["tokens"]["ml_eng"]

    prefixes = [
        "/api/v1/registry",
        "/v1/registry",
        "/api/v1/models",
        "/v1/models",
    ]

    for p in prefixes:
        # Anonymous rejection
        resp_anon = client.post(
            f"{p}/{sim_id}/versions/{v1}/promote",
            json={"target_status": "challenger", "enforce_sr11_7": False},
        )
        assert resp_anon.status_code == 401, f"Prefix {p} failed to reject anonymous caller"

        resp_anon_rb = client.post(f"{p}/{sim_id}/rollback/{v1}")
        assert resp_anon_rb.status_code == 401, f"Prefix {p} rollback failed to reject anonymous caller"

        resp_anon_dep = client.post(f"{p}/deployment/initiate", json={"target_version": "v3.0.0"})
        assert resp_anon_dep.status_code == 401, f"Prefix {p} deployment failed to reject anonymous caller"

    # Authorized operator succeeds identically across aliases
    resp_auth = client.post(
        f"/api/v1/models/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {token_ml}"},
    )
    assert resp_auth.status_code == 200


# ── GATE-09: Genuinely Authorized Operators Succeed ─────────────────────────


def test_gate_09_authorized_operator_workflows_succeed(auth_gateway_env):
    """GATE-09: Authorized operators can perform promotion, rollback, and deployment lifecycles."""
    client = auth_gateway_env["client"]
    reg = auth_gateway_env["registry"]
    deployer = auth_gateway_env["deployer"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]
    v2 = auth_gateway_env["v2"]

    token_ml = auth_gateway_env["tokens"]["ml_eng"]
    token_comp = auth_gateway_env["tokens"]["compliance"]
    token_devops = auth_gateway_env["tokens"]["devops"]

    # 1. ML Engineer promotes v1 to challenger
    resp_chall = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        json={"target_status": "challenger", "enforce_sr11_7": False},
        headers={"Authorization": f"Bearer {token_ml}"},
    )
    assert resp_chall.status_code == 200
    assert resp_chall.json()["status"] == "challenger"
    v1_meta = reg.get_version_metadata(sim_id, v1)
    assert v1_meta is not None
    assert v1_meta["status"] == "challenger"
    assert v1_meta.get("promoted_by") == "operator_ml"

    # 2. Compliance officer rolls back v2 as active champion
    resp_rb = client.post(
        f"/api/v1/registry/{sim_id}/rollback/{v2}",
        headers={"Authorization": f"Bearer {token_comp}"},
    )
    assert resp_rb.status_code == 200
    active = reg.get_active_version(sim_id)
    assert active is not None
    assert active["version"] == v2
    assert active.get("rolled_back_by") == "officer_comp"

    # 3. DevOps executes full deployment cycle
    resp_init = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v2.5.0", "compatibility_window_hours": 12, "initial_connections": 100},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_init.status_code == 200
    session_id = resp_init.json()["session_id"]

    resp_drain = client.post(
        f"/api/v1/registry/deployment/{session_id}/drain",
        json={"batch_size": 100},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_drain.status_code == 200
    assert resp_drain.json()["active_connections_count"] == 0

    resp_roll = client.post(
        f"/api/v1/registry/deployment/{session_id}/rolling-update",
        json={"instance_ids": ["worker-1", "worker-2"]},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_roll.status_code == 200
    assert resp_roll.json()["stage"] == DeploymentStage.DUAL_VERSION_ACTIVE.value

    resp_fin = client.post(
        f"/api/v1/registry/deployment/{session_id}/finalize",
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_fin.status_code == 200
    assert deployer.current_version == "v2.5.0"

    # 4. DevOps abort workflow
    resp_init2 = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v3.0.0"},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    s2 = resp_init2.json()["session_id"]
    resp_abort = client.post(
        f"/api/v1/registry/deployment/{s2}/abort",
        json={"reason": "Canary metric degradation"},
        headers={"Authorization": f"Bearer {token_devops}"},
    )
    assert resp_abort.status_code == 200
    assert resp_abort.json()["stage"] == DeploymentStage.ABORTED.value


# ── GATE-10: Delegation and Direct Service Actor Authorization ─────────────


@pytest.mark.asyncio
async def test_gate_10_delegation_and_direct_service_security(auth_gateway_env):
    """GATE-10: Router delegation, direct handler calls, and direct service actor checks cannot bypass authorization."""
    reg = auth_gateway_env["registry"]
    sim_id = auth_gateway_env["sim_id"]
    v1 = auth_gateway_env["v1"]
    token_ml = auth_gateway_env["tokens"]["ml_eng"]

    # 1. Direct handler invocation without Authorization header fails closed
    payload = ModelPromoteRequest(target_status="challenger", enforce_sr11_7=False)
    with pytest.raises(HTTPException) as exc_info:
        await promote_model_version(simulation_id=sim_id, version=v1, payload=payload, authorization=None)
    assert exc_info.value.status_code == 401

    # 2. Router delegation (/promote -> promote_model_version) without auth fails closed
    with pytest.raises(HTTPException) as exc_deleg:
        await promote_model_by_simulation(simulation_id=sim_id, payload=payload, version=v1, authorization=None)
    assert exc_deleg.value.status_code == 401

    # 3. Direct service call to promote_version with unauthorized actor fails closed
    with pytest.raises(ModelIntegrityError, match="lacks required role authority"):
        reg.promote_version(sim_id, v1, target_status="challenger", actor="unprivileged_analyst")

    # 4. Direct service call to rollback with unauthorized actor fails closed
    with pytest.raises(ModelIntegrityError, match="lacks required role authority"):
        reg.rollback(sim_id, v1, actor="unprivileged_analyst")

    # 5. Direct service call with non-existent actor fails closed
    with pytest.raises(ModelIntegrityError, match="is not an active authoritative user"):
        reg.promote_version(sim_id, v1, target_status="challenger", actor="non_existent_user")

    # 6. Direct service call with valid token succeeds
    res = reg.promote_version(sim_id, v1, target_status="challenger", auth_token=token_ml)
    assert res["status"] == "challenger"
    assert res.get("promoted_by") == "operator_ml"
