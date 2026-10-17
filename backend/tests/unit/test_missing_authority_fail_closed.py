"""MISSING-01 through MISSING-06: Missing Identity Authority Fail-Closed Test Suite.

Verifies:
1. MISSING-01: Loaded active account + deleted authority file -> subsequent JWT verification rejected.
2. MISSING-02: Previously loaded account + inaccessible identity store -> fail closed.
3. MISSING-03: Previously revoked token + authority disappearance -> remains rejected.
4. MISSING-04: Legitimate initial production bootstrap with no users -> no demo accounts or implicit auth.
5. MISSING-05: Restored valid signed identity authority -> legitimate authentication works again.
6. MISSING-06: Existing HARDEN-01-10 and IDENT-01-10 suites remain passing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import (
    AuthenticationSecurityError,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

PROD_JWT_SECRET = "missing-test-prod-jwt-signing-secret-64bytes-entropy!"
PROD_AUTH_SECRET = "missing-test-prod-identity-authority-secret-64bytes!"
PROD_PAYLOAD_SECRET = "missing-test-prod-payload-secret-64bytes-entropy!"


@pytest.fixture
def missing_env():
    """Isolated environment fixture for missing authority testing."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_missing_test_")
    store_path = os.path.join(temp_dir, "auth_identity_store.json")

    settings = get_settings()
    orig_env = settings.app_env
    orig_jwt = settings.oidc_jwt_signing_secret
    orig_ident_sec = settings.identity_store_secret
    orig_ident_path = settings.identity_store_path
    orig_payload_sec = settings.payload_signing_secret
    orig_audit_path = os.environ.get("CFI_AUDIT_LEDGER_PATH")

    settings.app_env = "production"
    settings.oidc_jwt_signing_secret = PROD_JWT_SECRET
    settings.identity_store_secret = PROD_AUTH_SECRET
    settings.identity_store_path = store_path
    settings.payload_signing_secret = PROD_PAYLOAD_SECRET
    os.environ["CFI_AUDIT_LEDGER_PATH"] = os.path.join(temp_dir, "test_audit_ledger.json")

    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()

    yield {
        "temp_dir": temp_dir,
        "store_path": store_path,
        "settings": settings,
    }

    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()
    settings.app_env = orig_env
    settings.oidc_jwt_signing_secret = orig_jwt
    settings.identity_store_secret = orig_ident_sec
    settings.identity_store_path = orig_ident_path
    settings.payload_signing_secret = orig_payload_sec
    if orig_audit_path is not None:
        os.environ["CFI_AUDIT_LEDGER_PATH"] = orig_audit_path
    else:
        os.environ.pop("CFI_AUDIT_LEDGER_PATH", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestMissingIdentityAuthorityFailClosed:
    """MISSING-01 through MISSING-06 Suite."""

    def test_missing_01_loaded_active_account_deleted_authority_file_jwt_rejected(self, missing_env, monkeypatch):
        """MISSING-01: Loaded active account + deleted authority file -> subsequent JWT verification rejected."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = missing_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("victim_officer", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # 1. Valid while store exists
        is_val, claims, msg = auth_svc.verify_access_token(token)
        assert is_val is True
        assert claims is not None
        assert os.path.exists(store_path)

        # 2. Delete the authority store file
        os.remove(store_path)
        assert not os.path.exists(store_path)

        # 3. Subsequent verification on the existing instance MUST fail closed
        is_val_after, claims_after, msg_after = auth_svc.verify_access_token(token)
        assert is_val_after is False
        assert claims_after is None
        assert "unavailable" in msg_after.lower() or "disappeared" in msg_after.lower() or "missing" in msg_after.lower()

        # In-memory cached query operations also fail closed
        with pytest.raises(AuthenticationSecurityError, match="disappeared|inaccessible|corrupted"):
            auth_svc.identity_store.get_user("victim_officer")

        with pytest.raises(AuthenticationSecurityError, match="disappeared|inaccessible|corrupted"):
            auth_svc.identity_store.has_users()

    def test_missing_02_previously_loaded_account_inaccessible_identity_store_fail_closed(self, missing_env, monkeypatch):
        """MISSING-02: Previously loaded account + inaccessible identity store -> fail closed."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = missing_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("officer_inaccessible", "Password2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        is_val, _, _ = auth_svc.verify_access_token(token)
        assert is_val is True

        # Simulate storage volume unmounted or filesystem inaccessibility error
        orig_stat = os.stat

        def mock_stat_forbidden(path, *args, **kwargs):
            if os.path.abspath(str(path)) == os.path.abspath(store_path):
                raise PermissionError("EACCES: storage volume unmounted or permission denied")
            return orig_stat(path, *args, **kwargs)

        with patch("os.stat", side_effect=mock_stat_forbidden):
            is_val_after, claims_after, msg_after = auth_svc.verify_access_token(token)
            assert is_val_after is False
            assert claims_after is None
            assert (
                "unavailable" in msg_after.lower()
                or "failed to read" in msg_after.lower()
                or "inaccessible" in msg_after.lower()
                or "disappeared" in msg_after.lower()
            )

            # Direct store access fails closed
            with pytest.raises(
                AuthenticationSecurityError,
                match="Failed to read|corrupted|inaccessible|disappeared",
            ):
                auth_svc.identity_store.get_user("officer_inaccessible")

    def test_missing_03_previously_revoked_token_authority_disappearance_remains_rejected(self, missing_env, monkeypatch):
        """MISSING-03: Previously revoked token + authority disappearance -> remains rejected."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = missing_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("revoked_user", "Password2026!", "bank_a", ["ml_engineer"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Revoke token while store is intact
        assert auth_svc.revoke_token(token) is True
        is_val, _, msg = auth_svc.verify_access_token(token)
        assert is_val is False
        assert "revoked" in msg.lower()

        # Delete store file
        os.remove(store_path)

        # Under NO circumstances does the revoked token become usable
        is_val_after, _, msg_after = auth_svc.verify_access_token(token)
        assert is_val_after is False
        assert "unavailable" in msg_after.lower() or "disappeared" in msg_after.lower() or "revoked" in msg_after.lower()

    def test_missing_04_legitimate_initial_production_bootstrap_no_users_no_demo_accounts(self, missing_env, monkeypatch):
        """MISSING-04: Legitimate initial production bootstrap with no users -> no demo accounts or implicit auth."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = missing_env["store_path"]

        assert not os.path.exists(store_path)

        # Cold boot on uncreated store path
        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )

        # Unestablished store is clean and empty
        assert not os.path.exists(store_path)
        assert auth_svc.identity_store.has_users() is False
        assert auth_svc.get_user("admin") is None
        assert auth_svc.get_user("analyst_a1") is None

        # Authenticate fails closed for non-existent users
        auth_ok, _, auth_msg, _ = auth_svc.authenticate("admin", "AdminSecure2026!")
        assert auth_ok is False
        assert "invalid username or password" in auth_msg.lower() or "unavailable" in auth_msg.lower()

        # Legitimate initial provisioning works and establishes the authority file
        initial_admin = auth_svc.register_user("initial_admin", "SuperSecurePassword2026!", "bank_a", ["admin"])
        assert initial_admin.is_active is True
        assert os.path.exists(store_path)
        assert auth_svc.identity_store.has_users() is True

    def test_missing_05_restored_valid_signed_identity_authority_authentication_succeeds(self, missing_env, monkeypatch):
        """MISSING-05: Restored valid signed identity authority -> legitimate authentication works again."""
        monkeypatch.setenv("APP_ENV", "production")
        store_path = missing_env["store_path"]

        auth_svc = AuthenticationService(
            signing_secret=PROD_JWT_SECRET,
            store_path=store_path,
            authority_secret=PROD_AUTH_SECRET,
            app_env="production",
        )
        user = auth_svc.register_user("restorable_user", "ValidPassword2026!", "bank_a", ["compliance"])
        bundle = auth_svc.create_token_bundle(user)
        token = bundle.access_token

        # Verify initial validity
        is_val, _, _ = auth_svc.verify_access_token(token)
        assert is_val is True

        # Backup valid file contents
        with open(store_path, "rb") as f:
            valid_backup_bytes = f.read()

        # Disappearance triggers fail-closed
        os.remove(store_path)
        is_val_missing, _, msg_missing = auth_svc.verify_access_token(token)
        assert is_val_missing is False
        assert "unavailable" in msg_missing.lower()

        # Restore valid signed store
        with open(store_path, "wb") as f:
            f.write(valid_backup_bytes)

        # Verification succeeds immediately upon restoration
        is_val_restored, claims_restored, msg_restored = auth_svc.verify_access_token(token)
        assert is_val_restored is True
        assert claims_restored is not None
        assert claims_restored["username"] == "restorable_user"

        # Authentication also succeeds
        auth_ok, u_auth, _, _ = auth_svc.authenticate("restorable_user", "ValidPassword2026!")
        assert auth_ok is True
        assert u_auth is not None
        assert u_auth.username == "restorable_user"

    def test_missing_06_existing_harden_and_ident_suites_remain_passing(self):
        """MISSING-06: Existing HARDEN-01-10 and IDENT-01-10 suites remain passing."""
        import shutil
        import tempfile

        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        sub_env = dict(os.environ)
        tmp_ident_dir = tempfile.mkdtemp()
        try:
            sub_env["CFI_INTERNAL_NONCE_STORE_PATH"] = os.path.join(tmp_ident_dir, "consumed_nonces.json")
            sub_env["CFI_AUDIT_LEDGER_PATH"] = os.path.join(tmp_ident_dir, "audit_ledger.json")

            # Run IDENT suite
            cmd_ident = [sys.executable, "-m", "pytest", "tests/unit/test_authoritative_principal_identity.py", "-q"]
            res_ident = subprocess.run(cmd_ident, cwd=backend_dir, capture_output=True, text=True, timeout=600, env=sub_env)
            assert res_ident.returncode == 0, f"IDENT regression failed:\nSTDOUT:\n{res_ident.stdout}\nSTDERR:\n{res_ident.stderr}"

            # Run HARDEN suite
            cmd_harden = [sys.executable, "-m", "pytest", "tests/unit/test_identity_authority_hardening.py", "-q"]
            res_harden = subprocess.run(cmd_harden, cwd=backend_dir, capture_output=True, text=True, timeout=600, env=sub_env)
            assert res_harden.returncode == 0, f"HARDEN regression failed:\nSTDOUT:\n{res_harden.stdout}\nSTDERR:\n{res_harden.stderr}"
        finally:
            shutil.rmtree(tmp_ident_dir, ignore_errors=True)

