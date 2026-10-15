"""Production-grade FastAPI Request Tests: MODULE 01-B.5.

Workstream: 01-B.5 — REST Sign-Off Authentication Token Propagation
Verifies:
- API-SIGN-01: Valid authenticated ML engineer successfully signs off.
- API-SIGN-02: Valid authenticated compliance officer successfully signs off.
- API-SIGN-03: Missing Authorization header -> rejected (HTTP 401).
- API-SIGN-04: Invalid or expired token -> rejected (HTTP 401).
- API-SIGN-05: Authenticated principal differs from payload.user -> rejected (HTTP 403).
- API-SIGN-06: Unauthorized role claim -> rejected (HTTP 403 / HTTP 400).
- API-SIGN-07: Successful dual sign-offs result in genuine verifiable approval evidence and champion promotion.
- API-SIGN-08: Two approvals from the same person cannot authorize champion promotion (Four-Eyes violation).
- API-SIGN-09: Invalid authentication cannot create or modify sign-off records (zero state mutation).
- API-SIGN-10: Existing receipt verification, issuer revocation, challenger admission, and champion governance remain functional.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import (
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.main import app
from app.presentation.routers.model_registry import registry

client = TestClient(app)
TEST_SIGNING_SECRET = "test-signing-secret-key-32-chars-long!"


@pytest.fixture
def isolated_production_env():
    """Configure clean isolated storage, trust store, and production environment."""
    settings = get_settings()
    orig_env = settings.app_env
    orig_secret = settings.payload_signing_secret
    orig_jwt = settings.oidc_jwt_signing_secret
    orig_ident_sec = settings.identity_store_secret
    orig_ident_path = settings.identity_store_path

    temp_dir = tempfile.mkdtemp()
    store_file = os.path.join(temp_dir, "isolated_identity_store.json")

    settings.app_env = "production"
    settings.payload_signing_secret = TEST_SIGNING_SECRET
    settings.oidc_jwt_signing_secret = "test-prod-jwt-signing-secret-32-chars-long!"
    settings.identity_store_secret = "test-prod-identity-authority-secret-32-chars-long!"
    settings.identity_store_path = store_file

    audit_ledger_file = os.path.join(temp_dir, "isolated_audit_ledger.json")
    nonce_store_file = os.path.join(temp_dir, "isolated_nonces.json")
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_ledger_file
    os.environ["CFI_INTERNAL_NONCE_STORE_PATH"] = nonce_store_file
    from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

    reset_default_nonce_store()
    ImmutableAuditChain.reset_instance()

    old_storage = registry.storage_dir
    old_root = registry.registry_root
    old_verifier = getattr(registry, "_verifier", None)

    registry.storage_dir = temp_dir
    registry.registry_root = os.path.join(temp_dir, "registry")
    os.makedirs(registry.registry_root, exist_ok=True)

    # Set up cryptographic trust store and receipt issuer
    trust_file = os.path.join(temp_dir, "trust_store.json")
    admin_token = "admin-secret-test-token-12345"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_key_01"
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry._verifier = verifier

    # Seed approver users in AuthenticationService
    AuthenticationService.reset_instance()
    auth_svc = AuthenticationService.get_instance()
    u_mle = auth_svc.register_user(
        "mle_lead_officer", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2
    )
    u_comp = auth_svc.register_user(
        "compliance_chief", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3
    )
    u_analyst = auth_svc.register_user("analyst_viewer", "Pass123!Secure", "bank_a", roles=["analyst"])
    u_dual = auth_svc.register_user("dual_approver_user", "Pass123!Secure", "bank_a", roles=["ml_engineer", "compliance"])

    tok_mle = auth_svc.create_token_bundle(u_mle).access_token
    tok_comp = auth_svc.create_token_bundle(u_comp).access_token
    tok_analyst = auth_svc.create_token_bundle(u_analyst).access_token
    tok_dual = auth_svc.create_token_bundle(u_dual).access_token

    context = {
        "temp_dir": temp_dir,
        "trust_store": trust_store,
        "verifier": verifier,
        "priv_key": priv_key,
        "kid": kid,
        "admin_token": admin_token,
        "auth_svc": auth_svc,
        "u_mle": u_mle,
        "u_comp": u_comp,
        "u_analyst": u_analyst,
        "u_dual": u_dual,
        "tok_mle": tok_mle,
        "tok_comp": tok_comp,
        "tok_analyst": tok_analyst,
        "tok_dual": tok_dual,
    }

    yield context

    # Teardown
    AuthenticationService.reset_instance()
    os.environ.pop("CFI_AUDIT_LEDGER_PATH", None)
    os.environ.pop("CFI_INTERNAL_NONCE_STORE_PATH", None)
    reset_default_nonce_store()
    ImmutableAuditChain.reset_instance()
    settings.app_env = orig_env
    settings.payload_signing_secret = orig_secret
    settings.oidc_jwt_signing_secret = orig_jwt
    settings.identity_store_secret = orig_ident_sec
    settings.identity_store_path = orig_ident_path
    registry.storage_dir = old_storage
    registry.registry_root = old_root
    registry._verifier = old_verifier
    shutil.rmtree(temp_dir, ignore_errors=True)


def _seed_valid_candidate(ctx: dict[str, Any], sim_id: str) -> tuple[dict[str, Any], str]:
    """Helper to save an authentic unpromoted candidate with genuine training receipt."""
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
            private_key=ctx["priv_key"],
            key_id=ctx["kid"],
            require_authenticated_job=False,
        )
        evidence = TrainingExecutionEvidence(
            training_run_id=f"run_{sim_id}",
            job_id=f"job_{sim_id}",
            simulation_id=sim_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=3,
            executed_epochs=3,
            sample_count=200,
            observed_metrics={"loss": 0.04, "auc": 0.94},
            artifact_sha256=sha256,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=FEATURE_NAMES,
            owning_bank_id="bank_a",
        )
        receipt = issuer.issue_receipt(
            evidence=evidence,
            model_bytes=model_bytes,
            owning_bank_id="bank_a",
        ).to_dict()

        entry = registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"loss": 0.04, "auc": 0.94, "pr_auc": 0.91, "f1_score": 0.89},
            artifact_bytes=model_bytes,
            training_receipt=receipt,
            is_promoted=False,
            owning_bank_id="bank_a",
        )
        return entry, sha256
    finally:
        settings.app_env = current_env


class TestRestSignOffTokenPropagation:
    """Rigorous evaluation matrix for API-SIGN-01 through API-SIGN-10."""

    def test_api_sign_01_valid_authenticated_ml_engineer(self, isolated_production_env):
        """API-SIGN-01: Valid authenticated ML engineer successfully signs off via REST."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_01"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )

        resp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={
                "role": "ml_engineer",
                "user": ctx["u_mle"].username,
                "signature": sig_ml,
                "fairness_score": 0.95,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert len(data["sign_offs"]) == 1
        so = data["sign_offs"][0]
        assert so["role"] == "ml_engineer"
        assert so["user"] == ctx["u_mle"].username

        # Verify disk manifest state
        manifest = registry._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 1
        stored_so = manifest[0]["sign_offs"][0]
        assert stored_so["is_signature_valid"] is True
        assert stored_so["is_identity_verified"] is True
        assert stored_so["is_role_verified"] is True

    def test_api_sign_02_valid_authenticated_compliance_officer(self, isolated_production_env):
        """API-SIGN-02: Valid authenticated compliance officer successfully signs off via REST."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_02"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_comp = generate_signoff_signature(
            sim_id, version, sha256, "compliance", ctx["u_comp"].username, signing_secret=TEST_SIGNING_SECRET
        )

        resp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_comp']}"},
            json={
                "role": "compliance",
                "user": ctx["u_comp"].username,
                "signature": sig_comp,
                "fairness_score": 0.95,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert len(data["sign_offs"]) == 1
        so = data["sign_offs"][0]
        assert so["role"] == "compliance"
        assert so["user"] == ctx["u_comp"].username

        manifest = registry._load_manifest(sim_id)
        stored_so = manifest[0]["sign_offs"][0]
        assert stored_so["is_signature_valid"] is True
        assert stored_so["is_identity_verified"] is True
        assert stored_so["is_role_verified"] is True

    def test_api_sign_03_missing_authorization_header(self, isolated_production_env):
        """API-SIGN-03: Missing Authorization header -> rejected with HTTP 401."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_03"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )

        resp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            json={
                "role": "ml_engineer",
                "user": ctx["u_mle"].username,
                "signature": sig_ml,
            },
        )
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert "Bearer" in resp.headers["WWW-Authenticate"]
        assert "Missing Authorization header" in resp.json()["detail"]

    def test_api_sign_04_invalid_or_expired_token(self, isolated_production_env):
        """API-SIGN-04: Invalid, malformed, or expired token -> rejected with HTTP 401."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_04"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )

        # 1. Malformed authorization scheme
        resp_scheme = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Basic {ctx['tok_mle']}"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_scheme.status_code == 401
        assert "Invalid authorization header format" in resp_scheme.json()["detail"]

        # 2. Corrupted token string
        resp_corrupt = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": "Bearer not.a.valid.jwt.token"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_corrupt.status_code == 401
        assert "Invalid access token" in resp_corrupt.json()["detail"]

        # 3. Expired token
        past_time = int(time.time()) - 3600
        expired_payload = {
            "sub": ctx["u_mle"].user_id,
            "username": ctx["u_mle"].username,
            "roles": ctx["u_mle"].roles,
            "token_type": "access",
            "iat": past_time - 900,
            "exp": past_time,
        }
        expired_tok = jwt.encode(expired_payload, ctx["auth_svc"].signing_secret, algorithm="HS256")

        resp_exp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {expired_tok}"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_exp.status_code == 401
        assert "expired" in resp_exp.json()["detail"].lower()

        # 4. Revoked token
        revoked_bundle = ctx["auth_svc"].create_token_bundle(ctx["u_mle"])
        ctx["auth_svc"].revoke_token(revoked_bundle.access_token)
        resp_rev = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {revoked_bundle.access_token}"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_rev.status_code == 401
        assert "revoked" in resp_rev.json()["detail"].lower()

    def test_api_sign_05_authenticated_principal_differs_from_payload_user(self, isolated_production_env):
        """API-SIGN-05: Authenticated principal differs from payload.user -> rejected with HTTP 403."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_05"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_spoofed = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", "victim_engineer", signing_secret=TEST_SIGNING_SECRET
        )

        # Authenticated as mle_lead_officer, but body claims victim_engineer
        resp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={
                "role": "ml_engineer",
                "user": "victim_engineer",
                "signature": sig_spoofed,
                "fairness_score": 0.95,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "does not match claimed approver" in detail
        assert ctx["u_mle"].username in detail
        assert "victim_engineer" in detail

    def test_api_sign_06_unauthorized_role_claim_rejected(self, isolated_production_env):
        """API-SIGN-06: Unauthorized role claim -> rejected with HTTP 403 or 400."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_06"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        # 1. ML engineer attempting compliance sign-off
        sig_comp_spoof = generate_signoff_signature(
            sim_id, version, sha256, "compliance", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp_mle_comp = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={
                "role": "compliance",
                "user": ctx["u_mle"].username,
                "signature": sig_comp_spoof,
            },
        )
        assert resp_mle_comp.status_code == 403
        assert "lacks compliance role authority" in resp_mle_comp.json()["detail"]

        # 2. Compliance officer attempting ml_engineer sign-off
        sig_mle_spoof = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_comp"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp_comp_mle = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_comp']}"},
            json={
                "role": "ml_engineer",
                "user": ctx["u_comp"].username,
                "signature": sig_mle_spoof,
            },
        )
        assert resp_comp_mle.status_code == 403
        assert "lacks ml_engineer role authority" in resp_comp_mle.json()["detail"]

        # 3. Analyst attempting either sign-off
        sig_analyst = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_analyst"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp_analyst = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_analyst']}"},
            json={
                "role": "ml_engineer",
                "user": ctx["u_analyst"].username,
                "signature": sig_analyst,
            },
        )
        assert resp_analyst.status_code == 403
        assert "lacks ml_engineer role authority" in resp_analyst.json()["detail"]

        # 4. Unknown/malformed role
        resp_unknown_role = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={
                "role": "super_admin_bypass",
                "user": ctx["u_mle"].username,
                "signature": sig_analyst,
            },
        )
        assert resp_unknown_role.status_code == 400
        assert "Role must be 'compliance' or 'ml_engineer'" in resp_unknown_role.json()["detail"]

    def test_api_sign_07_successful_dual_signoffs_champion_promotion(self, isolated_production_env):
        """API-SIGN-07: Successful dual sign-offs produce verifiable approval evidence and champion promotion."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_07"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        # First sign-off: ML engineer
        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp1 = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={
                "role": "ml_engineer",
                "user": ctx["u_mle"].username,
                "signature": sig_ml,
                "fairness_score": 0.95,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1["sign_offs"]) == 1
        assert data1["status"] != "champion"

        # Second sign-off: Compliance officer
        sig_comp = generate_signoff_signature(
            sim_id, version, sha256, "compliance", ctx["u_comp"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp2 = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_comp']}"},
            json={
                "role": "compliance",
                "user": ctx["u_comp"].username,
                "signature": sig_comp,
                "fairness_score": 0.96,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["sign_offs"]) == 2
        assert data2["status"] == "approved"

        # Explicit promotion to champion
        promoted_resp = registry.promote_version(
            sim_id, version, target_status="champion", auth_token=ctx["tok_mle"]
        )
        assert promoted_resp["status"] == "champion"
        assert promoted_resp["is_active"] is True

        # Verify persisted manifest invariants
        manifest = registry._load_manifest(sim_id)
        promoted = manifest[0]
        assert promoted["status"] == "champion"
        assert promoted["is_active"] is True
        assert promoted.get("is_approved") is True
        assert promoted.get("is_production_eligible") is True
        assert len(promoted["sign_offs"]) == 2
        for s in promoted["sign_offs"]:
            assert s["is_signature_valid"] is True
            assert s["is_identity_verified"] is True
            assert s["is_role_verified"] is True
            assert s["is_independent_human_authorization"] is True

    def test_api_sign_08_two_approvals_same_person_rejected(self, isolated_production_env):
        """API-SIGN-08: Two approvals from the same person cannot authorize champion promotion (Four-Eyes violation)."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_08"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        # Dual-role user signs off as ml_engineer
        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_dual"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp1 = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_dual']}"},
            json={
                "role": "ml_engineer",
                "user": ctx["u_dual"].username,
                "signature": sig_ml,
            },
        )
        assert resp1.status_code == 200

        # Same user tries to sign off as compliance
        sig_comp = generate_signoff_signature(
            sim_id, version, sha256, "compliance", ctx["u_dual"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp2 = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_dual']}"},
            json={
                "role": "compliance",
                "user": ctx["u_dual"].username,
                "signature": sig_comp,
            },
        )
        assert resp2.status_code == 400
        assert "Four-Eyes Principle violation" in resp2.json()["detail"]

        # Verify candidate was NOT promoted to champion
        manifest = registry._load_manifest(sim_id)
        assert len(manifest[0]["sign_offs"]) == 1
        assert manifest[0]["status"] != "champion"
        assert manifest[0].get("is_active") is not True

    def test_api_sign_09_invalid_authentication_cannot_create_or_modify_records(self, isolated_production_env):
        """API-SIGN-09: Invalid authentication cannot create or modify sign-off records."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_09"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        # Initial legitimate signoff
        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )
        resp_legit = client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_legit.status_code == 200

        manifest_baseline = registry._load_manifest(sim_id)
        baseline_signoffs_len = len(manifest_baseline[0]["sign_offs"])
        assert baseline_signoffs_len == 1

        # Attempt 1: Missing auth
        client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            json={"role": "compliance", "user": ctx["u_comp"].username, "signature": "fake_sig_12345"},
        )
        # Attempt 2: Corrupted token
        client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": "Bearer bad.token.value"},
            json={"role": "compliance", "user": ctx["u_comp"].username, "signature": "fake_sig_12345"},
        )
        # Attempt 3: Mismatched principal
        client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_comp']}"},
            json={"role": "compliance", "user": "other_officer", "signature": "fake_sig_12345"},
        )
        # Attempt 4: Unauthorized role claim
        client.post(
            f"/api/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_analyst']}"},
            json={"role": "compliance", "user": ctx["u_analyst"].username, "signature": "fake_sig_12345"},
        )

        # Confirm persisted manifest is 100% pristine and unmodified
        manifest_current = registry._load_manifest(sim_id)
        assert len(manifest_current[0]["sign_offs"]) == baseline_signoffs_len
        assert manifest_current[0]["sign_offs"][0]["user"] == ctx["u_mle"].username
        assert manifest_current[0]["status"] != "champion"

    def test_api_sign_10_governance_receipt_revocation_challenger_parity(self, isolated_production_env):
        """API-SIGN-10: Multi-prefix route parity, model serving resolution, receipt verification, and issuer revocation remain functional."""
        ctx = isolated_production_env
        sim_id = "sim_api_sign_10"
        entry, sha256 = _seed_valid_candidate(ctx, sim_id)
        version = entry["version"]

        sig_ml = generate_signoff_signature(
            sim_id, version, sha256, "ml_engineer", ctx["u_mle"].username, signing_secret=TEST_SIGNING_SECRET
        )
        sig_comp = generate_signoff_signature(
            sim_id, version, sha256, "compliance", ctx["u_comp"].username, signing_secret=TEST_SIGNING_SECRET
        )

        # Test multi-prefix route: submit ML sign-off via /v1/registry
        resp_p1 = client.post(
            f"/v1/registry/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_mle']}"},
            json={"role": "ml_engineer", "user": ctx["u_mle"].username, "signature": sig_ml},
        )
        assert resp_p1.status_code == 200

        # Submit Compliance sign-off via /api/v1/models prefix
        resp_p2 = client.post(
            f"/api/v1/models/{sim_id}/versions/{version}/signoff",
            headers={"Authorization": f"Bearer {ctx['tok_comp']}"},
            json={"role": "compliance", "user": ctx["u_comp"].username, "signature": sig_comp},
        )
        assert resp_p2.status_code == 200
        assert resp_p2.json()["status"] == "approved"

        # Explicit promotion to champion
        registry.promote_version(sim_id, version, target_status="champion", auth_token=ctx["tok_mle"])

        # ModelService resolves active champion
        model_svc = ModelService(get_settings())
        champ_model = model_svc.get_champion(sim_id, registry=registry)
        assert champ_model is not None

        v1_ident = registry.get_active_champion_identity(sim_id)
        assert v1_ident is not None
        assert v1_ident["version"] == version

        champ_meta = registry.get_champion_metadata(sim_id)
        assert champ_meta is not None
        assert champ_meta["status"] == "champion"
        assert champ_meta["is_active"] is True

        # Inactive default: Save v2 without promotion defaults to inactive
        entry_v2, sha_v2 = _seed_valid_candidate(ctx, sim_id)
        assert entry_v2["version"] == 2
        assert entry_v2["status"] == "inactive"
        assert entry_v2["is_active"] is False

        # Promote to challenger
        registry.promote_version(sim_id, 2, target_status="challenger", auth_token=ctx["tok_mle"])
        challenger_meta = registry.get_challenger_metadata(sim_id)
        assert challenger_meta is not None
        assert challenger_meta["status"] == "challenger"
        assert challenger_meta["is_active"] is False

        # Seed candidate v3 before revocation
        entry_v3, sha_v3 = _seed_valid_candidate(ctx, sim_id)
        assert entry_v3["version"] == 3

        # Issuer key revocation: Revoke issuer key
        ctx["trust_store"].revoke_key(ctx["kid"], admin_token=ctx["admin_token"], reason="Key compromised")

        # In production, promoting v3 signed by the revoked issuer key fails admission
        with pytest.raises(Exception, match="revoked"):
            registry.promote_version(
                sim_id, entry_v3["version"], target_status="challenger", auth_token=ctx["tok_mle"]
            )
