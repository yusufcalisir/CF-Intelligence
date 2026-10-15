"""Focused Adversarial and Correctness Tests for WORKSTREAM 01-C.1.

Suite: Authoritative Principal Verification & Durable Account State
Tests:
- IDENT-01: Valid JWT followed by account deactivation -> rejected.
- IDENT-02: Valid JWT followed by account deletion -> rejected.
- IDENT-03: Validly signed JWT naming a nonexistent account -> rejected.
- IDENT-04: Suspended or otherwise explicitly disabled account -> rejected.
- IDENT-05: Active legitimate user with valid JWT -> authentication and sign-off succeed.
- IDENT-06: Worker A deactivates account; independent worker B rejects existing token.
- IDENT-07: Worker A revokes token; independent worker B rejects that token.
- IDENT-08: Process restart preserves inactive account and revocation state.
- IDENT-09: Missing or corrupted production identity authority -> fail closed.
- IDENT-10: Existing receipt, admission, HMAC, and legitimate dual-approval regressions remain correct.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
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
    ModelIntegrityError,
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
from app.infrastructure.security.durable_account_store import (
    AuthenticationSecurityError,
    DurableAccountStore,
)
from app.main import app
from app.presentation.routers.model_registry import registry

client = TestClient(app)
TEST_SIGNING_SECRET = "test-signing-secret-key-32-chars-long!"


def _run_worker_b_verify(store_path: str, token: str, secret: str = TEST_SIGNING_SECRET) -> dict[str, Any]:
    """Spawn an independent Worker B OS process verifying token against shared persistent store."""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    script = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{backend_dir}')\n"
        "from app.infrastructure.security.auth_service import AuthenticationService\n"
        f"svc = AuthenticationService(signing_secret={secret!r}, store_path={store_path!r})\n"
        f"is_valid, claims, msg = svc.verify_access_token({token!r})\n"
        "print(json.dumps({'is_valid': is_valid, 'msg': msg}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=backend_dir,
    )
    assert proc.returncode == 0, f"Worker B crashed with error:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


@pytest.fixture
def identity_test_env():
    """Setup isolated temporary directory, trust store, and durable identity store."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_ident_test_")
    store_path = os.path.join(temp_dir, "test_identity_store.json")
    reg_dir = os.path.join(temp_dir, "registry_storage")
    os.makedirs(reg_dir, exist_ok=True)

    settings = get_settings()
    orig_env = settings.app_env
    orig_secret = settings.payload_signing_secret
    orig_oidc = settings.oidc_jwt_signing_secret
    settings.payload_signing_secret = TEST_SIGNING_SECRET
    settings.oidc_jwt_signing_secret = TEST_SIGNING_SECRET

    old_storage = registry.storage_dir
    old_root = registry.registry_root
    old_verifier = getattr(registry, "_verifier", None)

    registry.storage_dir = reg_dir
    registry.registry_root = reg_dir

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_key_ident"
    admin_token = "admin_secret_token_12345"
    trust_path = os.path.join(temp_dir, "trust_store.json")
    trust_store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry._verifier = verifier

    AuthenticationService.reset_instance()
    auth_svc = AuthenticationService(
        signing_secret=TEST_SIGNING_SECRET,
        store_path=store_path,
    )
    AuthenticationService._instance = auth_svc

    yield {
        "temp_dir": temp_dir,
        "store_path": store_path,
        "reg_dir": reg_dir,
        "auth_svc": auth_svc,
        "priv_key": priv_key,
        "kid": kid,
        "trust_store": trust_store,
        "verifier": verifier,
    }

    AuthenticationService.reset_instance()
    registry.storage_dir = old_storage
    registry.registry_root = old_root
    registry._verifier = old_verifier
    settings.app_env = orig_env
    settings.payload_signing_secret = orig_secret
    settings.oidc_jwt_signing_secret = orig_oidc
    shutil.rmtree(temp_dir, ignore_errors=True)


def _seed_valid_candidate(ctx: dict[str, Any], sim_id: str) -> tuple[dict[str, Any], str]:
    """Helper to save an authentic candidate with verified training receipt."""
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


class TestAuthoritativePrincipalIdentity:
    """Comprehensive adversarial test suite for WORKSTREAM 01-C.1."""

    def test_ident_01_valid_jwt_followed_by_account_deactivation_rejected(self, identity_test_env):
        """IDENT-01: Valid JWT followed by account deactivation -> rejected."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        user = auth_svc.register_user("approver_cp1", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # 1. Valid prior to deactivation
        is_valid, claims, _ = auth_svc.verify_access_token(token)
        assert is_valid is True
        assert claims is not None
        assert claims["username"] == "approver_cp1"

        # 2. Deactivate owner
        deact_ok = auth_svc.deactivate_user("approver_cp1")
        assert deact_ok is True
        assert auth_svc.get_user("approver_cp1") is None

        # 3. verify_access_token must reject
        is_valid_after, _, err_msg = auth_svc.verify_access_token(token)
        assert is_valid_after is False
        assert "deactivated" in err_msg.lower()

        # 4. REST sign-off endpoint must reject with HTTP 401
        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_01")
        sig = generate_signoff_signature(
            "sim_ident_01", 1, sha256, "compliance", "approver_cp1", signing_secret=TEST_SIGNING_SECRET
        )
        resp = client.post(
            "/api/v1/registry/sim_ident_01/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "compliance", "user": "approver_cp1", "signature": sig},
        )
        assert resp.status_code == 401
        assert "deactivated" in resp.json()["detail"].lower()

        # 5. Direct internal ModelRegistry.sign_off must raise ModelIntegrityError
        with pytest.raises(ModelIntegrityError, match="deactivated"):
            registry.sign_off("sim_ident_01", 1, "compliance", user="approver_cp1", signature=sig, auth_token=token)

    def test_ident_02_valid_jwt_followed_by_account_deletion_rejected(self, identity_test_env):
        """IDENT-02: Valid JWT followed by account deletion -> rejected."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        user = auth_svc.register_user("approver_del", "Password2026!", "bank_a", ["ml_engineer"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Delete user permanently
        del_ok = auth_svc.delete_user("approver_del")
        assert del_ok is True

        # verify_access_token must fail closed
        is_valid, _, err_msg = auth_svc.verify_access_token(token)
        assert is_valid is False
        assert "does not exist or has been deleted" in err_msg.lower()

        # REST API must return 401
        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_02")
        sig = generate_signoff_signature(
            "sim_ident_02", 1, sha256, "ml_engineer", "approver_del", signing_secret=TEST_SIGNING_SECRET
        )
        resp = client.post(
            "/api/v1/registry/sim_ident_02/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "ml_engineer", "user": "approver_del", "signature": sig},
        )
        assert resp.status_code == 401
        assert "deleted" in resp.json()["detail"].lower() or "does not exist" in resp.json()["detail"].lower()

    def test_ident_03_validly_signed_jwt_naming_nonexistent_account_rejected(self, identity_test_env):
        """IDENT-03: Validly signed JWT naming a nonexistent account -> rejected."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        now = int(time.time())
        phantom_payload = {
            "sub": "usr_phantom_999",
            "username": "phantom_ghost",
            "bank_id": "bank_a",
            "roles": ["compliance"],
            "token_type": "access",
            "jti": "phantom-jti-001",
            "iat": now,
            "exp": now + 900,
        }
        phantom_token = jwt.encode(phantom_payload, auth_svc.signing_secret, algorithm="HS256")

        is_valid, _, err_msg = auth_svc.verify_access_token(phantom_token)
        assert is_valid is False
        assert "does not exist or has been deleted" in err_msg.lower()

        # REST endpoint must reject with 401
        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_03")
        sig = generate_signoff_signature(
            "sim_ident_03", 1, sha256, "compliance", "phantom_ghost", signing_secret=TEST_SIGNING_SECRET
        )
        resp = client.post(
            "/api/v1/registry/sim_ident_03/versions/1/signoff",
            headers={"Authorization": f"Bearer {phantom_token}"},
            json={"role": "compliance", "user": "phantom_ghost", "signature": sig},
        )
        assert resp.status_code == 401
        assert "does not exist" in resp.json()["detail"].lower()

    def test_ident_04_suspended_account_rejected(self, identity_test_env):
        """IDENT-04: Suspended or otherwise explicitly disabled account -> rejected."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        user = auth_svc.register_user("approver_suspend", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Suspend user
        susp_ok = auth_svc.suspend_user("approver_suspend")
        assert susp_ok is True

        is_valid, _, err_msg = auth_svc.verify_access_token(token)
        assert is_valid is False
        assert "suspended" in err_msg.lower()

        # REST endpoint must reject
        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_04")
        sig = generate_signoff_signature(
            "sim_ident_04", 1, sha256, "compliance", "approver_suspend", signing_secret=TEST_SIGNING_SECRET
        )
        resp = client.post(
            "/api/v1/registry/sim_ident_04/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "compliance", "user": "approver_suspend", "signature": sig},
        )
        assert resp.status_code == 401
        assert "suspended" in resp.json()["detail"].lower()

    def test_ident_05_active_legitimate_user_authentication_and_signoff_succeed(self, identity_test_env):
        """IDENT-05: Active legitimate user with valid JWT -> authentication and sign-off succeed."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        user = auth_svc.register_user("legit_compliance", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Verification succeeds
        is_valid, claims, msg = auth_svc.verify_access_token(token)
        assert is_valid is True
        assert claims is not None
        assert claims["username"] == "legit_compliance"

        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_05")
        sig = generate_signoff_signature(
            "sim_ident_05", 1, sha256, "compliance", "legit_compliance", signing_secret=TEST_SIGNING_SECRET
        )

        resp = client.post(
            "/api/v1/registry/sim_ident_05/versions/1/signoff",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "compliance", "user": "legit_compliance", "signature": sig},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sign_offs"]) == 1
        assert data["sign_offs"][0]["user"] == "legit_compliance"
        assert data["sign_offs"][0]["role"] == "compliance"

        # Stored metadata record in registry
        stored_meta = registry.get_version_metadata("sim_ident_05", 1)
        assert stored_meta is not None
        signoff_rec = stored_meta["sign_offs"][0]
        assert signoff_rec["user"] == "legit_compliance"
        assert signoff_rec["role"] == "compliance"
        assert signoff_rec["is_identity_verified"] is True
        assert signoff_rec["is_role_verified"] is True
        assert signoff_rec["is_signature_valid"] is True

    def test_ident_06_worker_a_deactivates_worker_b_rejects_across_processes(self, identity_test_env):
        """IDENT-06: Worker A deactivates account; independent worker B rejects existing token."""
        store_path = identity_test_env["store_path"]
        auth_svc: AuthenticationService = identity_test_env["auth_svc"]

        # Worker A creates user, issues token, then deactivates account
        user = auth_svc.register_user("cross_proc_user", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Worker A deactivates account in durable store
        deact_ok = auth_svc.deactivate_user("cross_proc_user")
        assert deact_ok is True

        # Independent Worker B (separate OS process) verifies token against durable store
        res = _run_worker_b_verify(store_path, token, TEST_SIGNING_SECRET)
        assert res["is_valid"] is False
        assert "deactivated" in res["msg"].lower()

    def test_ident_07_worker_a_revokes_token_worker_b_rejects_across_processes(self, identity_test_env):
        """IDENT-07: Worker A revokes token; independent worker B rejects that token."""
        store_path = identity_test_env["store_path"]
        auth_svc: AuthenticationService = identity_test_env["auth_svc"]

        user = auth_svc.register_user("revoke_proc_user", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Worker A revokes token
        rev_ok = auth_svc.revoke_token(token)
        assert rev_ok is True

        # Independent Worker B (separate OS process) calls verify_access_token
        res = _run_worker_b_verify(store_path, token, TEST_SIGNING_SECRET)
        assert res["is_valid"] is False
        assert "revoked" in res["msg"].lower()

    def test_ident_08_process_restart_preserves_inactive_account_and_revocations(self, identity_test_env):
        """IDENT-08: Process restart preserves inactive account and revocation state."""
        store_path = identity_test_env["store_path"]
        auth_svc: AuthenticationService = identity_test_env["auth_svc"]

        user = auth_svc.register_user("restart_user", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Deactivate user and revoke token
        auth_svc.deactivate_user("restart_user")
        auth_svc.revoke_token(token)

        # Simulate service restart
        AuthenticationService.reset_instance()
        restarted_svc = AuthenticationService(
            signing_secret=TEST_SIGNING_SECRET,
            store_path=store_path,
        )

        # Default seeding must NOT resurrect deactivated user
        assert restarted_svc.get_user("restart_user") is None
        inactive_rec = restarted_svc.get_user("restart_user", include_inactive=True)
        assert inactive_rec is not None
        assert inactive_rec.is_active is False
        assert inactive_rec.status == "deactivated"

        # Token verification on restarted service must fail closed
        is_valid, _, msg = restarted_svc.verify_access_token(token)
        assert is_valid is False
        assert "revoked" in msg.lower() or "deactivated" in msg.lower()

    def test_ident_09_corrupted_production_identity_authority_fails_closed(self, identity_test_env):
        """IDENT-09: Missing or corrupted production identity authority -> fail closed."""
        temp_dir = identity_test_env["temp_dir"]
        corrupt_path = os.path.join(temp_dir, "corrupt_store.json")

        # Create 0-byte corrupt store file
        with open(corrupt_path, "wb") as f:
            f.write(b"")

        # In production mode, corrupted store strictly raises AuthenticationSecurityError
        with pytest.raises(AuthenticationSecurityError, match="corrupted: file is empty"):
            DurableAccountStore(store_path=corrupt_path)

        # Corrupt JSON
        with open(corrupt_path, "wb") as f:
            f.write(b"{invalid_json_bytes")

        with pytest.raises(AuthenticationSecurityError, match="corrupted: invalid JSON"):
            DurableAccountStore(store_path=corrupt_path)

    def test_ident_10_receipt_admission_hmac_and_dual_approval_regressions(self, identity_test_env):
        """IDENT-10: Existing receipt, admission, HMAC, and legitimate dual-approval regressions remain correct."""
        ctx = identity_test_env
        auth_svc: AuthenticationService = ctx["auth_svc"]
        eng_user = auth_svc.register_user("eng_lead", "Password2026!", "bank_a", ["ml_engineer"])
        cp_user = auth_svc.register_user("cp_officer", "Password2026!", "bank_a", ["compliance"])

        tok_eng = auth_svc.create_token_bundle(eng_user).access_token
        tok_cp = auth_svc.create_token_bundle(cp_user).access_token

        entry, sha256 = _seed_valid_candidate(ctx, "sim_ident_10")

        sig_eng = generate_signoff_signature(
            "sim_ident_10", 1, sha256, "ml_engineer", "eng_lead", signing_secret=TEST_SIGNING_SECRET
        )
        sig_cp = generate_signoff_signature(
            "sim_ident_10", 1, sha256, "compliance", "cp_officer", signing_secret=TEST_SIGNING_SECRET
        )

        # 1. First sign-off (ML engineer) via REST
        resp1 = client.post(
            "/api/v1/registry/sim_ident_10/versions/1/signoff",
            headers={"Authorization": f"Bearer {tok_eng}"},
            json={"role": "ml_engineer", "user": "eng_lead", "signature": sig_eng},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1["sign_offs"]) == 1
        assert data1["status"] != "champion"

        # 2. Second sign-off (Compliance officer) via REST
        resp2 = client.post(
            "/api/v1/registry/sim_ident_10/versions/1/signoff",
            headers={"Authorization": f"Bearer {tok_cp}"},
            json={"role": "compliance", "user": "cp_officer", "signature": sig_cp},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["sign_offs"]) == 2
        assert data2["status"] == "approved"
        assert data2["is_approved"] is True
        assert data2["is_production_eligible"] is True

        # Explicit promotion to champion
        promoted = registry.promote_version("sim_ident_10", 1, target_status="champion", auth_token=tok_eng)
        assert promoted["status"] == "champion"
        assert promoted["is_active"] is True

        # Invariant: Four-Eyes dual approvals verified and champion promoted in metadata
        final_meta = registry.get_version_metadata("sim_ident_10", 1)
        assert final_meta is not None
        assert final_meta["status"] == "champion"
        assert final_meta["is_active"] is True
        assert len(final_meta["sign_offs"]) == 2
        for s in final_meta["sign_offs"]:
            assert s["is_signature_valid"] is True
            assert s["is_identity_verified"] is True
            assert s["is_role_verified"] is True
            assert s["is_independent_human_authorization"] is True
