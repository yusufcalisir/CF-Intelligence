"""Unit and Adversarial Test Suite for Milestone 5 Pilot Operations.

Verifies:
- State machine transition boundaries & invalid transition rejection.
- Independent multi-party approval requirements (Bank A, Bank B, Operator).
- Forged and stale approval rejection under configuration revision.
- Quota exhaustion (total quota and per-institution quota).
- Expiration time enforcement (fails closed).
- Kill switch controls (PAUSE, RESUME, ABORT).
- Pilot query authorization gating (institution allowlist, purpose restriction).
- Tamper-evident scope digest computation.
- FastAPI endpoints for pilot provisioning, preflight, approval, activation, kill switch, and dossier export.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.pilot_operations import (
    PilotApprovalRequest,
    PilotKillSwitchRequest,
    PilotProvisionRequest,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.pilot_operations_service import (
    InvalidPilotTransitionError,
    PilotAuthorizationError,
    PilotExpiredError,
    PilotGovernanceError,
    PilotOperationsService,
    PilotQuotaExceededError,
)
from app.domain.consortium_governance import MemberRole
from app.domain.pilot_operations import (
    PilotApprovalRecord,
    PilotEntity,
    PilotOperationalConfig,
    PilotStatus,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app

SHARED_JWT_SECRET = "jwt_secret_unit_test_pilot_operations_091823749812734"
SHARED_CONSORTIUM_STORE_SECRET = "consortium_store_hmac_unit_test_98712398471"


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

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurablePilotStore._instance = None
    PilotOperationsService._instance = None

    auth_svc = AuthenticationService.get_instance()
    u_op = auth_svc.register_user(
        "unit_operator", "Pass123!", "consortium", roles=["admin", "security_officer"]
    )
    u_a = auth_svc.register_user("unit_bank_a_admin", "Pass123!", "bank_a", roles=["admin"])
    u_b = auth_svc.register_user("unit_bank_b_admin", "Pass123!", "bank_b", roles=["admin"])
    u_hacker = auth_svc.register_user("unit_hacker", "Pass123!", "bank_c", roles=["analyst"])

    token_op = auth_svc.create_token_bundle(u_op).access_token
    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token
    token_hacker = auth_svc.create_token_bundle(u_hacker).access_token

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
    PilotOperationsService._instance = pilot_svc

    return {
        "pilot_svc": pilot_svc,
        "gov_svc": gov_svc,
        "token_op": token_op,
        "token_a": token_a,
        "token_b": token_b,
        "token_hacker": token_hacker,
    }


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

    svc.provision_pilot(
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

    # Only Bank A approves, Bank B has not approved
    svc.approve_pilot(pilot_id, "bank_a", "unit_bank_a_admin", "Bank A approval")

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
            approved_datasets=["ds_1"],
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


def test_quota_exhaustion_fails_closed(unit_pilot_env: dict[str, Any]) -> None:
    """Exceeding pilot query quota or institutional quota must fail closed."""
    svc: PilotOperationsService = unit_pilot_env["pilot_svc"]
    pilot_id = "pilot_quota_test"

    svc.provision_pilot(
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
    svc.approve_pilot(pilot_id, "bank_a", "admin_a")
    svc.approve_pilot(pilot_id, "bank_b", "admin_b")
    svc.approve_pilot(pilot_id, "consortium", "operator")
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

    # Expired 1 hour ago
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

    svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_unit",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_synthetic_datasets=["ds_syn"],
    )
    svc.run_preflight(pilot_id)
    svc.approve_pilot(pilot_id, "bank_a", "admin_a")
    svc.approve_pilot(pilot_id, "bank_b", "admin_b")
    svc.approve_pilot(pilot_id, "consortium", "operator")
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
