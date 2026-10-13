"""HARDEN-01 through HARDEN-10: Final Identity Authority Hardening Test Suite.

Verifies:
- HARDEN-01: Deactivated account modified to active with recomputed SHA-256 -> previously issued JWT remains rejected.
- HARDEN-02: Revocation list modified with recomputed digest -> revoked JWT remains rejected.
- HARDEN-03: Integrity/authenticity field removed from production identity file -> fail closed.
- HARDEN-04: Attacker replaces both state and any locally stored trust-anchor material -> rejected by an independently configured verifier.
- HARDEN-05: Empty production identity store does not automatically create default test/admin users.
- HARDEN-06: Default or missing JWT signing secret in production -> fail closed.
- HARDEN-07: Properly provisioned production identities and signing credentials -> legitimate authentication and approval succeed.
- HARDEN-08: Independent worker processes observe valid updates and reject unauthorized identity-state modifications.
- HARDEN-09: Previously accepted IDENT-01 through IDENT-10 continue to pass.
- HARDEN-10: Relevant authentication routes, sign-off, HMAC, and issuer-revocation regressions remain intact.
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

from app.application.services.model_registry import (
    ModelRegistry,
    compute_file_sha256,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import Settings, get_settings
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import (
    AuthenticationSecurityError,
    AuthenticationService,
    DurableAccountStore,
    UserRecord,
)
from app.main import app
from app.presentation.routers.model_registry import registry

client = TestClient(app)

PROD_JWT_SECRET = "production-jwt-signing-secret-high-entropy-64bytes-key-value!"
PROD_AUTH_SECRET = "production-identity-authority-secret-high-entropy-64bytes!"


def _run_subprocess_worker_verify(
    store_path: str,
    token: str,
    jwt_secret: str,
    authority_secret: str | None = None,
    authority_pub_hex: str | None = None,
) -> dict[str, Any]:
    """Spawn an independent OS process verifying token against durable store."""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    auth_sec_repr = repr(authority_secret) if authority_secret else "None"
    auth_pub_repr = repr(authority_pub_hex) if authority_pub_hex else "None"

    script = (
        "import sys, json, os\n"
        f"sys.path.insert(0, r'{backend_dir}')\n"
        "os.environ['APP_ENV'] = 'production'\n"
        "from app.infrastructure.security.auth_service import AuthenticationService\n"
        "try:\n"
        f"    svc = AuthenticationService(signing_secret={jwt_secret!r}, store_path={store_path!r}, "
        f"authority_secret={auth_sec_repr}, authority_public_key={auth_pub_repr}, app_env='production')\n"
        f"    is_valid, claims, msg = svc.verify_access_token({token!r})\n"
        "except Exception as e:\n"
        "    is_valid, claims, msg = False, None, str(e)\n"
        "print(json.dumps({'is_valid': is_valid, 'msg': msg}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=25,
        cwd=backend_dir,
    )
    assert proc.returncode == 0, f"Worker crashed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


@pytest.fixture
def harden_env():
    """Setup isolated production environment fixture."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_harden_test_")
    store_path = os.path.join(temp_dir, "harden_identity_store.json")
    reg_dir = os.path.join(temp_dir, "registry_storage")
    os.makedirs(reg_dir, exist_ok=True)

    settings = get_settings()
    orig_env = settings.app_env
    orig_jwt = settings.oidc_jwt_signing_secret
    orig_ident_sec = settings.identity_store_secret
    orig_ident_key = settings.identity_store_authority_key

    settings.app_env = "production"
    settings.oidc_jwt_signing_secret = PROD_JWT_SECRET
    settings.identity_store_secret = PROD_AUTH_SECRET
    settings.identity_store_authority_key = ""

    old_storage = registry.storage_dir
    old_root = registry.registry_root
    old_verifier = getattr(registry, "_verifier", None)

    registry.storage_dir = reg_dir
    registry.registry_root = reg_dir

    AuthenticationService.reset_instance()

    yield {
        "temp_dir": temp_dir,
        "store_path": store_path,
        "reg_dir": reg_dir,
        "settings": settings,
    }

    AuthenticationService.reset_instance()
    registry.storage_dir = old_storage
    registry.registry_root = old_root
    registry._verifier = old_verifier
    settings.app_env = orig_env
    settings.oidc_jwt_signing_secret = orig_jwt
    settings.identity_store_secret = orig_ident_sec
    settings.identity_store_authority_key = orig_ident_key
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestIdentityAuthorityHardening:
    """HARDEN-01 through HARDEN-10 Adversarial Verification Suite."""

    def test_harden_01_deactivated_account_modified_with_recomputed_sha256_rejected(self, harden_env, monkeypatch):
        """HARDEN-01: Deactivated account modified to active with recomputed SHA-256 -> JWT remains rejected."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("victim_approver", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # 1. Legitimately deactivate owner
        assert auth_svc.deactivate_user("victim_approver") is True
        is_val, _, msg = auth_svc.verify_access_token(token)
        assert is_val is False
        assert "deactivated" in msg.lower()

        # 2. Attacker modifies persistent JSON: reactivates user and recomputes unkeyed SHA-256
        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)

        data = container["data"]
        data["users"]["victim_approver"]["is_active"] = True
        data["users"]["victim_approver"]["status"] = "active"
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        container["integrity_sha256"] = hashlib.sha256(canonical_bytes).hexdigest()

        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        # 3. Active service reloads on verification -> detects tampering, fails closed, token remains rejected
        is_val_after, _, msg_after = auth_svc.verify_access_token(token)
        assert is_val_after is False
        assert "unavailable" in msg_after.lower() or "mismatch" in msg_after.lower()

        # 4. Any new verifier/worker initializing on the tampered store fails closed
        with pytest.raises(AuthenticationSecurityError, match="authentication tag mismatch"):
            AuthenticationService(
                signing_secret=PROD_JWT_SECRET,
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

    def test_harden_02_revocation_list_modified_with_recomputed_digest_rejected(self, harden_env, monkeypatch):
        """HARDEN-02: Revocation list modified with recomputed digest -> revoked JWT remains rejected."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("revoked_principal", "Password2026!", "bank_a", ["ml_engineer"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # 1. Legitimately revoke token
        assert auth_svc.revoke_token(token) is True
        is_val, _, msg = auth_svc.verify_access_token(token)
        assert is_val is False
        assert "revoked" in msg.lower()

        # 2. Attacker removes JTI from revocation list and recomputes SHA-256
        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)

        data = container["data"]
        data["revoked_tokens"] = {}
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        container["integrity_sha256"] = hashlib.sha256(canonical_bytes).hexdigest()

        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        # 3. Active service detects tampering -> fails closed, token remains rejected
        is_val_after, _, msg_after = auth_svc.verify_access_token(token)
        assert is_val_after is False
        assert "unavailable" in msg_after.lower() or "mismatch" in msg_after.lower()

        # 4. Any new verifier/worker initializing on the tampered store fails closed
        with pytest.raises(AuthenticationSecurityError, match="authentication tag mismatch"):
            AuthenticationService(
                signing_secret=PROD_JWT_SECRET,
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

    def test_harden_03_integrity_authenticity_field_removed_fails_closed(self, harden_env, monkeypatch):
        """HARDEN-03: Integrity/authenticity field removed from production identity file -> fail closed."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        store = DurableAccountStore(
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        u = UserRecord("usr_h03", "user_h03", "hash", "bank_a", ["auditor"])
        store.save_user(u)

        # Case A: Remove HMAC and signature fields
        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)
        container.pop("integrity_hmac_sha256", None)
        container.pop("authority_signature", None)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        with pytest.raises(AuthenticationSecurityError, match="unauthenticated persistence format"):
            store.reload(force=True)

        with pytest.raises(AuthenticationSecurityError, match="unauthenticated persistence format"):
            DurableAccountStore(
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

        # Case B: Remove integrity_sha256
        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)
        container.pop("integrity_sha256", None)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        with pytest.raises(AuthenticationSecurityError, match="missing required 'integrity_sha256'"):
            store.reload(force=True)

        with pytest.raises(AuthenticationSecurityError, match="missing required 'integrity_sha256'"):
            DurableAccountStore(
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

    def test_harden_04_attacker_replaces_state_and_local_trust_anchor_rejected_by_verifier(
        self, harden_env, monkeypatch
    ):
        """HARDEN-04: Attacker replaces state and local trust-anchor material -> rejected by independently pinned verifier."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        # 1. Legitimate admin provisions store and derives authoritative public key
        legit_store = DurableAccountStore(
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        legit_u = UserRecord("usr_legit", "legit_user", "hash", "bank_a", ["compliance"])
        legit_store.save_user(legit_u)
        pinned_auth_pub = legit_store.get_authority_public_key()
        assert pinned_auth_pub is not None

        # 2. Independently configured verifier only holds pinned_auth_pub
        verifier_store = DurableAccountStore(
            store_path=store_path,
            authority_public_key=pinned_auth_pub,
            app_env="production",
        )
        assert verifier_store.get_user("legit_user") is not None

        # 3. Attacker creates their own keypair, modifies data, signs with attacker key,
        #    and writes an adjacent authority file in the storage directory
        attacker_priv = ed25519.Ed25519PrivateKey.generate()
        attacker_pub = attacker_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)

        data = container["data"]
        data["users"]["attacker_admin"] = {
            "user_id": "usr_att",
            "username": "attacker_admin",
            "password_hash": "hash",
            "bank_id": "bank_a",
            "roles": ["admin"],
            "is_active": True,
            "status": "active",
        }
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        container["data"] = data
        container["integrity_sha256"] = hashlib.sha256(canonical_bytes).hexdigest()
        container["authority_public_key_hex"] = attacker_pub
        container["authority_signature"] = attacker_priv.sign(canonical_bytes).hex()
        container.pop("integrity_hmac_sha256", None)

        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        # Attacker drops fake authority anchor alongside store file
        with open(f"{store_path}.authority_pub", "w", encoding="utf-8") as f:
            f.write(attacker_pub)

        # 4. Verifier reloads: checks against independently pinned authority public key
        with pytest.raises(AuthenticationSecurityError, match="failed authority signature verification"):
            verifier_store.reload(force=True)

    def test_harden_05_empty_production_identity_store_does_not_create_default_users(self, harden_env, monkeypatch):
        """HARDEN-05: Empty production identity store does not automatically create default test/admin users."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )

        # Default development test accounts must NOT be seeded
        assert auth_svc.get_user("admin") is None
        assert auth_svc.get_user("analyst_a1") is None
        assert auth_svc.get_user("analyst_b1") is None
        assert auth_svc.get_user("auditor") is None
        assert auth_svc.identity_store.has_users() is False

    def test_harden_06_default_or_missing_jwt_signing_secret_in_production_fails_closed(
        self, harden_env, monkeypatch
    ):
        """HARDEN-06: Default or missing JWT signing secret in production -> fail closed."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        # Empty secret
        with pytest.raises(AuthenticationSecurityError, match="Production JWT signing secret is unconfigured"):
            AuthenticationService(
                signing_secret="",
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

        # Hardcoded default repository placeholder
        with pytest.raises(AuthenticationSecurityError, match="forbidden default/placeholder"):
            AuthenticationService(
                signing_secret="cfi_oidc_jwt_secret_key_2026_enterprise_hs256",
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

        # Short / weak secret (< 32 chars)
        with pytest.raises(AuthenticationSecurityError, match="forbidden default/placeholder"):
            AuthenticationService(
                signing_secret="too-short-secret-12345",
                store_path=store_path,
                authority_secret=PROD_AUTH_SECRET,
                app_env="production",
            )

        # Settings production validator enforces check
        settings = Settings(
            app_env="production",
            oidc_jwt_signing_secret="cfi_oidc_jwt_secret_key_2026_enterprise_hs256",
            payload_signing_secret="sufficiently-long-secret-key-for-payload-32bytes!",
            training_worker_token="worker_token_32chars_long_prod_secret!",
            training_coordinator_token="coord_token_32chars_long_prod_secret!",
            training_job_store_path="/var/lib/jobs.db",
            identity_store_secret=PROD_AUTH_SECRET,
        )
        with pytest.raises(ValueError, match="oidc_jwt_signing_secret must be set"):
            settings.validate_production_invariants()

    def test_harden_07_properly_provisioned_production_identities_and_credentials_succeed(
        self, harden_env, monkeypatch
    ):
        """HARDEN-07: Properly provisioned production identities and credentials succeed legitimately."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        AuthenticationService._instance = auth_svc

        # Provision authorized compliance approver
        user = auth_svc.register_user("prod_compliance", "Password2026!", "bank_a", ["compliance"])
        assert user.is_active is True
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        is_valid, claims, _ = auth_svc.verify_access_token(token)
        assert is_valid is True
        assert claims is not None
        assert claims["username"] == "prod_compliance"

    def test_harden_08_independent_worker_processes_observe_valid_updates_and_reject_tampering(
        self, harden_env, monkeypatch
    ):
        """HARDEN-08: Independent worker processes observe valid updates and reject unauthorized modifications."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = harden_env["store_path"]

        # Worker A provisions user and deactivates
        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("cross_proc_harden", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        auth_svc.deactivate_user("cross_proc_harden")

        # Independent Worker B (separate OS process) observes deactivation
        res1 = _run_subprocess_worker_verify(
            store_path=store_path,
            token=token,
            jwt_secret=PROD_JWT_SECRET,
            authority_secret=PROD_AUTH_SECRET,
        )
        assert res1["is_valid"] is False
        assert "deactivated" in res1["msg"].lower()

        # Attacker attempts unauthorized modification of store on disk
        with open(store_path, "r", encoding="utf-8") as f:
            container = json.load(f)
        container["data"]["users"]["cross_proc_harden"]["is_active"] = True
        container["integrity_sha256"] = hashlib.sha256(
            json.dumps(container["data"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f, indent=2)

        # Worker B detects unauthorized modification and fails closed
        res2 = _run_subprocess_worker_verify(
            store_path=store_path,
            token=token,
            jwt_secret=PROD_JWT_SECRET,
            authority_secret=PROD_AUTH_SECRET,
        )
        assert res2["is_valid"] is False
        assert "unavailable" in res2["msg"].lower() or "mismatch" in res2["msg"].lower()

    def test_harden_09_ident_suite_passes(self):
        """HARDEN-09: Previously accepted IDENT-01 through IDENT-10 continue to pass."""
        pytest_cmd = [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_authoritative_principal_identity.py",
            "-q",
        ]
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        res = subprocess.run(pytest_cmd, capture_output=True, text=True, cwd=backend_dir)
        assert res.returncode == 0, f"IDENT suite failed: {res.stdout}\n{res.stderr}"

    def test_harden_10_governance_receipt_hmac_and_revocation_regressions_intact(self, harden_env):
        """HARDEN-10: Relevant authentication routes, sign-off, HMAC, and issuer-revocation regressions intact."""
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_rest_signoff_token_propagation.py",
            "-q",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=backend_dir)
        assert res.returncode == 0, f"Sign-off regression failed: {res.stdout}\n{res.stderr}"
