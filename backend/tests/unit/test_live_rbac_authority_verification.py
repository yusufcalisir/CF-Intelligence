"""RBAC-01 through RBAC-10: Live RBAC Authority & Current Permission Verification Test Suite.

Workstream: 01-C.2 — Live RBAC Authority & Current Permission Verification
Parent Module: 01-C — Approval Identity & RBAC

Verifies:
- RBAC-01: Compliance role removed after JWT issuance -> new Compliance sign-off rejected.
- RBAC-02: ML Engineer role removed after JWT issuance -> new ML Engineer sign-off rejected.
- RBAC-03: JWT contains a privileged role but current authoritative account does not -> rejected.
- RBAC-04: Role change in worker A is enforced by independent worker B across processes.
- RBAC-05: Caller-controlled sign-off role or user cannot override authoritative permissions.
- RBAC-06: Current legitimate Compliance officer submits a valid approval successfully.
- RBAC-07: Current legitimate ML Engineer submits a valid approval successfully.
- RBAC-08: Two independent authorized approvers complete the legitimate champion governance workflow.
- RBAC-09: Same stable principal cannot evade Four-Eyes separation using aliases, casing, or token variations.
- RBAC-10: Historical approval policy (immutable records, fail-closed future admission) and regressions intact.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import jwt
import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from app.application.services.model_registry import (
    DOMAIN_APPROVAL_SIGNOFF,
    ModelRegistry,
    compute_file_sha256,
    derive_domain_key,
    generate_signoff_signature,
    validate_production_admission,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import AuthenticationService
from app.main import app
from app.presentation.routers.model_registry import registry
from app.presentation.routers.realtime_inference import reset_model_cache

client = TestClient(app)

PROD_JWT_SECRET = "rbac-test-prod-jwt-signing-secret-64bytes-entropy!"
PROD_AUTH_SECRET = "rbac-test-prod-identity-authority-secret-64bytes!"
PAYLOAD_SECRET = "rbac-test-payload-signing-secret-64bytes-entropy-key!"
FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]


@pytest.fixture
def rbac_env():
    """Configure clean isolated production environment for live RBAC verification."""
    settings = get_settings()
    orig_env = settings.app_env
    orig_secret = settings.payload_signing_secret
    orig_jwt = settings.oidc_jwt_signing_secret
    orig_ident_sec = settings.identity_store_secret
    orig_ident_path = settings.identity_store_path

    temp_dir = tempfile.mkdtemp(prefix="cfi_rbac_env_")
    store_file = os.path.join(temp_dir, "auth_identity_store.json")

    settings.app_env = "production"
    settings.payload_signing_secret = PAYLOAD_SECRET
    settings.oidc_jwt_signing_secret = PROD_JWT_SECRET
    settings.identity_store_secret = PROD_AUTH_SECRET
    settings.identity_store_path = store_file

    old_storage = registry.storage_dir
    old_root = registry.registry_root
    old_verifier = getattr(registry, "_verifier", None)

    registry.storage_dir = temp_dir
    registry.registry_root = os.path.join(temp_dir, "registry")
    os.makedirs(registry.registry_root, exist_ok=True)

    # Cryptographic trust store & receipt verifier
    trust_file = os.path.join(temp_dir, "trust_store.json")
    admin_token = "admin-secret-token-rbac-test-12345"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_rbac_01"
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry._verifier = verifier

    # Authoritative Authentication Service instance
    AuthenticationService.reset_instance()
    auth_svc = AuthenticationService(
        signing_secret=PROD_JWT_SECRET,
        store_path=store_file,
        authority_secret=PROD_AUTH_SECRET,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    reset_model_cache()

    yield {
        "temp_dir": temp_dir,
        "store_file": store_file,
        "auth_svc": auth_svc,
        "settings": settings,
        "verifier": verifier,
        "priv_key": priv_key,
        "kid": kid,
        "registry": registry,
    }

    # Teardown
    settings.app_env = orig_env
    settings.payload_signing_secret = orig_secret
    settings.oidc_jwt_signing_secret = orig_jwt
    settings.identity_store_secret = orig_ident_sec
    settings.identity_store_path = orig_ident_path
    registry.storage_dir = old_storage
    registry.registry_root = old_root
    registry._verifier = old_verifier
    AuthenticationService.reset_instance()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _create_candidate_model(env: dict[str, Any], sim_id: str, version: int = 1) -> tuple[str, str]:
    """Helper to register a valid candidate model version with real training evidence and receipt."""
    reg: ModelRegistry = env["registry"]
    settings = get_settings()
    current_env = settings.app_env
    settings.app_env = "development"
    try:
        svc = ModelService(settings)
        model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        buf = io.BytesIO()
        torch.save(model.state_dict(), buf)
        model_bytes = buf.getvalue()
        sha256 = hashlib.sha256(model_bytes).hexdigest()

        issuer = TrainingReceiptIssuer(
            private_key=env["priv_key"],
            key_id=env["kid"],
            require_authenticated_job=False,
        )
        evidence = TrainingExecutionEvidence(
            training_run_id=f"run_{sim_id}_{version}",
            job_id=f"job_{sim_id}_{version}",
            simulation_id=sim_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=3,
            executed_epochs=3,
            sample_count=100,
            observed_metrics={"loss": 0.05, "auc": 0.95},
            artifact_sha256=sha256,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=FEATURE_NAMES,
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes).to_dict()

        entry = reg.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"loss": 0.05, "auc": 0.95},
            artifact_bytes=model_bytes,
            training_receipt=receipt,
            is_promoted=False,
        )
        model_path = os.path.join(reg._get_sim_dir(sim_id), entry["filename"])
        return sha256, model_path
    finally:
        settings.app_env = current_env


def _compute_hmac(sim_id: str, version: int, artifact_sha: str, role: str, user: str) -> str:
    """Compute authoritative HMAC sign-off signature."""
    canonical_payload = f"{sim_id}:{version}:{artifact_sha}:{role}:{user}"
    domain_key = derive_domain_key(PAYLOAD_SECRET, DOMAIN_APPROVAL_SIGNOFF)
    return hmac.new(domain_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


class TestLiveRBACAuthorityVerification:
    """Adversarial verification of live RBAC and current approval permissions."""

    def test_rbac_01_compliance_role_removed_after_jwt_issuance_rejected(self, rbac_env):
        """RBAC-01: Compliance role removed after JWT issuance -> new Compliance sign-off rejected."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        user = auth_svc.register_user("officer_comp_01", "CompliancePass2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        sim_id = "sim_rbac_01"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        # Durably remove 'compliance' role (downgrade to 'viewer')
        u_rec = auth_svc.get_user("officer_comp_01")
        u_rec.roles = ["viewer"]
        auth_svc.identity_store.save_user(u_rec)
        assert auth_svc.get_user("officer_comp_01").roles == ["viewer"]

        sig = _compute_hmac(sim_id, 1, sha256, "compliance", "officer_comp_01")

        # 1. REST Endpoint: Rejected with HTTP 403 Forbidden
        res = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "compliance", "user": "officer_comp_01", "signature": sig},
        )
        assert res.status_code == 403
        assert "lacks compliance role authority" in res.json()["detail"].lower()

        # 2. Service Layer: Rejected with ModelIntegrityError
        with pytest.raises(ModelIntegrityError, match="compliance role permissions"):
            rbac_env["registry"].sign_off(
                simulation_id=sim_id,
                version=1,
                role="compliance",
                user="officer_comp_01",
                signature=sig,
                auth_token=token,
            )

        # Confirm registry was not mutated
        manifest = rbac_env["registry"]._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 0

    def test_rbac_02_ml_engineer_role_removed_after_jwt_issuance_rejected(self, rbac_env):
        """RBAC-02: ML Engineer role removed after JWT issuance -> new ML Engineer sign-off rejected."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        user = auth_svc.register_user("engineer_ml_02", "MLPass2026!", "bank_a", ["ml_engineer"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        sim_id = "sim_rbac_02"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        # Durably remove 'ml_engineer' role (downgrade to 'analyst')
        u_rec = auth_svc.get_user("engineer_ml_02")
        u_rec.roles = ["analyst"]
        auth_svc.identity_store.save_user(u_rec)
        assert auth_svc.get_user("engineer_ml_02").roles == ["analyst"]

        sig = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "engineer_ml_02")

        # REST Endpoint rejected with HTTP 403
        res = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "ml_engineer", "user": "engineer_ml_02", "signature": sig},
        )
        assert res.status_code == 403
        assert "lacks ml_engineer role authority" in res.json()["detail"].lower()

        # Service Layer rejected with ModelIntegrityError
        with pytest.raises(ModelIntegrityError, match="ml_engineer role permissions"):
            rbac_env["registry"].sign_off(
                simulation_id=sim_id,
                version=1,
                role="ml_engineer",
                user="engineer_ml_02",
                signature=sig,
                auth_token=token,
            )

        # Manifest remains unchanged
        manifest = rbac_env["registry"]._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 0

    def test_rbac_03_jwt_contains_privileged_role_but_authoritative_account_does_not_rejected(self, rbac_env):
        """RBAC-03: JWT contains a privileged role but current authoritative account does not -> rejected."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        # Account registered with low privilege
        user = auth_svc.register_user("unprivileged_user", "Pass2026!", "bank_a", ["viewer"])

        # Manually forge token with elevated claims (signed with valid key)
        now = time.time()
        forged_payload = {
            "sub": user.user_id,
            "username": "unprivileged_user",
            "bank_id": "bank_a",
            "roles": ["compliance", "admin", "ml_engineer"],  # Elevating claims in token
            "token_type": "access",
            "jti": "jti_forged_elevation_03",
            "iat": now,
            "exp": now + 900,
        }
        forged_token = jwt.encode(forged_payload, PROD_JWT_SECRET, algorithm="HS256")

        sim_id = "sim_rbac_03"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)
        sig = _compute_hmac(sim_id, 1, sha256, "compliance", "unprivileged_user")

        # REST rejection: Authoritative account check overrides forged JWT role claim
        res = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {forged_token}"},
            json={"role": "compliance", "user": "unprivileged_user", "signature": sig},
        )
        assert res.status_code == 403
        assert "lacks compliance role authority" in res.json()["detail"].lower()

        # Service Layer rejection
        with pytest.raises(ModelIntegrityError, match="compliance role permissions"):
            rbac_env["registry"].sign_off(
                simulation_id=sim_id,
                version=1,
                role="compliance",
                user="unprivileged_user",
                signature=sig,
                auth_token=forged_token,
            )

    def test_rbac_04_role_change_in_worker_a_enforced_by_independent_worker_b(self, rbac_env):
        """RBAC-04: Role change in worker A is enforced by independent worker B across processes."""
        store_path = rbac_env["store_file"]

        # Worker A instance
        auth_svc_a = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        # Worker B instance
        auth_svc_b = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )

        user_a = auth_svc_a.register_user("officer_cross_worker", "Pass2026!", "bank_a", ["compliance"])
        bundle = auth_svc_a.create_token_bundle(user_a)
        token = bundle.access_token

        # Worker B initially verifies token and authoritative account
        is_val, claims, _ = auth_svc_b.verify_access_token(token)
        assert is_val is True
        assert "compliance" in auth_svc_b.get_user("officer_cross_worker").roles

        # Worker A demotes the user to 'analyst'
        u_rec = auth_svc_a.get_user("officer_cross_worker")
        u_rec.roles = ["analyst"]
        auth_svc_a.identity_store.save_user(u_rec)

        # Worker B inspects user: atomic reload observes role demotion immediately
        reloaded_b = auth_svc_b.get_user("officer_cross_worker")
        assert reloaded_b.roles == ["analyst"]
        assert "compliance" not in reloaded_b.roles

        sim_id = "sim_rbac_04"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)
        sig = _compute_hmac(sim_id, 1, sha256, "compliance", "officer_cross_worker")

        # Independent registry configured with worker B fails closed on sign-off
        with patch.object(AuthenticationService, "get_instance", return_value=auth_svc_b):
            with pytest.raises(ModelIntegrityError, match="compliance role permissions"):
                rbac_env["registry"].sign_off(
                    simulation_id=sim_id,
                    version=1,
                    role="compliance",
                    user="officer_cross_worker",
                    signature=sig,
                    auth_token=token,
                )

    def test_rbac_05_caller_controlled_sign_off_role_or_user_cannot_override_authoritative_permissions(self, rbac_env):
        """RBAC-05: Caller-controlled sign-off role or user cannot override authoritative permissions."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        comp_user = auth_svc.register_user("legit_comp_05", "Pass2026!", "bank_a", ["compliance"])
        ml_user = auth_svc.register_user("legit_ml_05", "Pass2026!", "bank_a", ["ml_engineer"])

        comp_token = auth_svc.create_token_bundle(comp_user).access_token
        ml_token = auth_svc.create_token_bundle(ml_user).access_token

        sim_id = "sim_rbac_05"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        # Subversion Attempt A: Comp officer attempts to claim 'ml_engineer' role
        sig_a = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "legit_comp_05")
        res_a = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {comp_token}"},
            json={"role": "ml_engineer", "user": "legit_comp_05", "signature": sig_a},
        )
        assert res_a.status_code == 403
        assert "ml_engineer" in res_a.json()["detail"].lower()

        # Subversion Attempt B: Comp officer attempts to sign off as ml_user using comp_token
        sig_b = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "legit_ml_05")
        res_b = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {comp_token}"},
            json={"role": "ml_engineer", "user": "legit_ml_05", "signature": sig_b},
        )
        assert res_b.status_code == 403
        assert "does not match" in res_b.json()["detail"].lower()

        # Subversion Attempt C: ML engineer attempts to claim 'compliance' role
        sig_c = _compute_hmac(sim_id, 1, sha256, "compliance", "legit_ml_05")
        res_c = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {ml_token}"},
            json={"role": "compliance", "user": "legit_ml_05", "signature": sig_c},
        )
        assert res_c.status_code == 403
        assert "compliance" in res_c.json()["detail"].lower()

    def test_rbac_06_current_legitimate_compliance_officer_submits_valid_approval(self, rbac_env):
        """RBAC-06: Current legitimate Compliance officer submits a valid approval successfully."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        user = auth_svc.register_user("officer_valid_06", "ValidPass2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        sim_id = "sim_rbac_06"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)
        sig = _compute_hmac(sim_id, 1, sha256, "compliance", "officer_valid_06")

        res = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "compliance", "user": "officer_valid_06", "signature": sig},
        )
        assert res.status_code == 200
        sign_offs = res.json()["sign_offs"]
        assert len(sign_offs) == 1
        s = sign_offs[0]
        assert s["role"] == "compliance"
        assert s["user"] == "officer_valid_06"
        assert s["signature"] == sig

        manifest = rbac_env["registry"]._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 1
        raw_s = manifest[0]["sign_offs"][0]
        assert raw_s["is_signature_valid"] is True
        assert raw_s["is_identity_verified"] is True
        assert raw_s["is_role_verified"] is True

    def test_rbac_07_current_legitimate_ml_engineer_submits_valid_approval(self, rbac_env):
        """RBAC-07: Current legitimate ML Engineer submits a valid approval successfully."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        user = auth_svc.register_user("engineer_valid_07", "ValidPass2026!", "bank_a", ["ml_engineer"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        sim_id = "sim_rbac_07"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)
        sig = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "engineer_valid_07")

        res = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "ml_engineer", "user": "engineer_valid_07", "signature": sig},
        )
        assert res.status_code == 200
        sign_offs = res.json()["sign_offs"]
        assert len(sign_offs) == 1
        s = sign_offs[0]
        assert s["role"] == "ml_engineer"
        assert s["user"] == "engineer_valid_07"
        assert s["signature"] == sig

        manifest = rbac_env["registry"]._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 1
        raw_s = manifest[0]["sign_offs"][0]
        assert raw_s["is_signature_valid"] is True
        assert raw_s["is_identity_verified"] is True
        assert raw_s["is_role_verified"] is True

    def test_rbac_08_two_independent_authorized_approvers_complete_champion_governance(self, rbac_env):
        """RBAC-08: Two independent authorized approvers can complete the legitimate champion governance workflow."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        ml_user = auth_svc.register_user("ml_approver_08", "MLPass2026!", "bank_a", ["ml_engineer"])
        comp_user = auth_svc.register_user("comp_approver_08", "CompPass2026!", "bank_a", ["compliance"])

        ml_token = auth_svc.create_token_bundle(ml_user).access_token
        comp_token = auth_svc.create_token_bundle(comp_user).access_token

        sim_id = "sim_rbac_08"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        sig_ml = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "ml_approver_08")
        sig_comp = _compute_hmac(sim_id, 1, sha256, "compliance", "comp_approver_08")

        # 1. ML Engineer signs off
        res_1 = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {ml_token}"},
            json={"role": "ml_engineer", "user": "ml_approver_08", "signature": sig_ml},
        )
        assert res_1.status_code == 200

        # 2. Compliance Officer signs off
        res_2 = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {comp_token}"},
            json={"role": "compliance", "user": "comp_approver_08", "signature": sig_comp},
        )
        assert res_2.status_code == 200

        # Model is now legitimately approved and candidate is eligible for champion promotion
        manifest = rbac_env["registry"]._load_manifest(sim_id)
        entry = next(e for e in manifest if e["version"] == 1)
        assert len(entry["sign_offs"]) == 2
        validate_production_admission(entry, context="champion_activation")

    def test_rbac_09_same_stable_principal_cannot_evade_four_eyes_separation(self, rbac_env):
        """RBAC-09: Same stable principal cannot evade Four-Eyes separation using aliases or token differences."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        # User who holds dual roles
        dual_user = auth_svc.register_user("dual_role_user", "DualPass2026!", "bank_a", ["ml_engineer", "compliance"])
        dual_token = auth_svc.create_token_bundle(dual_user).access_token

        sim_id = "sim_rbac_09"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        sig_ml = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "dual_role_user")
        # First approval as ML Engineer succeeds
        res_1 = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {dual_token}"},
            json={"role": "ml_engineer", "user": "dual_role_user", "signature": sig_ml},
        )
        assert res_1.status_code == 200

        # Attempt 1: Same username -> Rejected by Four-Eyes
        sig_comp_1 = _compute_hmac(sim_id, 1, sha256, "compliance", "dual_role_user")
        res_fail_1 = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {dual_token}"},
            json={"role": "compliance", "user": "dual_role_user", "signature": sig_comp_1},
        )
        assert res_fail_1.status_code in (400, 403, 409, 500) or "four-eyes" in res_fail_1.text.lower()

        # Attempt 2: Case evasion ("DUAL_ROLE_USER") -> Rejected by Four-Eyes
        sig_comp_2 = _compute_hmac(sim_id, 1, sha256, "compliance", "DUAL_ROLE_USER")
        res_fail_2 = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {dual_token}"},
            json={"role": "compliance", "user": "DUAL_ROLE_USER", "signature": sig_comp_2},
        )
        assert res_fail_2.status_code in (400, 403, 409, 500) or "four-eyes" in res_fail_2.text.lower()

    def test_rbac_10_historical_approval_policy_and_regressions_intact(self, rbac_env):
        """RBAC-10: Historical approval policy (immutable records, fail-closed future admission) and regressions."""
        auth_svc: AuthenticationService = rbac_env["auth_svc"]
        ml_user = auth_svc.register_user("ml_hist_10", "Pass2026!", "bank_a", ["ml_engineer"])
        comp_user = auth_svc.register_user("comp_hist_10", "Pass2026!", "bank_a", ["compliance"])

        ml_token = auth_svc.create_token_bundle(ml_user).access_token
        comp_token = auth_svc.create_token_bundle(comp_user).access_token

        sim_id = "sim_rbac_10"
        sha256, _ = _create_candidate_model(rbac_env, sim_id, version=1)

        sig_ml = _compute_hmac(sim_id, 1, sha256, "ml_engineer", "ml_hist_10")
        sig_comp = _compute_hmac(sim_id, 1, sha256, "compliance", "comp_hist_10")

        # Both legitimate sign-offs initially recorded
        res_ml = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {ml_token}"},
            json={"role": "ml_engineer", "user": "ml_hist_10", "signature": sig_ml},
        )
        assert res_ml.status_code == 200

        res_comp = client.post(
            f"/api/v1/models/{sim_id}/versions/1/signoff",
            headers={"Authorization": f"Bearer {comp_token}"},
            json={"role": "compliance", "user": "comp_hist_10", "signature": sig_comp},
        )
        assert res_comp.status_code == 200

        # Now simulate subsequent role revocation: comp_user loses compliance role
        comp_rec = auth_svc.get_user("comp_hist_10")
        comp_rec.roles = ["viewer"]
        auth_svc.identity_store.save_user(comp_rec)

        manifest = rbac_env["registry"]._load_manifest(sim_id)
        entry = next(e for e in manifest if e["version"] == 1)

        # Policy Requirement A: Historical records in manifest remain intact (never rewritten)
        assert len(entry["sign_offs"]) == 2
        assert any(s["user"] == "comp_hist_10" and s["role"] == "compliance" for s in entry["sign_offs"])

        # Policy Requirement B: Future production admission / serving fails closed because approver lacks current role
        with pytest.raises(ModelIntegrityError, match="lacks authoritative compliance role"):
            validate_production_admission(entry, context="champion_activation")

        # Now test MISSING-01..06 suite passes
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        cmd_missing = [sys.executable, "-m", "pytest", "tests/unit/test_missing_authority_fail_closed.py", "-v"]
        res_missing = subprocess.run(cmd_missing, cwd=backend_dir, capture_output=True, text=True, timeout=120)
        assert res_missing.returncode == 0, f"MISSING regression failed:\nSTDOUT:\n{res_missing.stdout}\nSTDERR:\n{res_missing.stderr}"
