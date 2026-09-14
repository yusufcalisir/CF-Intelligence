"""Unit tests for Production Enterprise Security Suite (mTLS, OIDC, ABAC, Vault, Audit Chain)."""

from __future__ import annotations

from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.infrastructure.security.vault_client import VaultClient


class TestMTLSManager:
    """Verify mTLS X.509 PKI certificate generation and SAN matching."""

    def test_cert_info_generation_and_validation(self):
        mgr = MTLSManager(ca_cn="CFI-Test-CA")
        cert = mgr.generate_cert_info("fl-coordinator")

        assert cert.subject_cn == "fl-coordinator"
        assert cert.issuer_cn == "CFI-Test-CA"
        assert "fl-coordinator.internal" in cert.sans

        valid, msg = mgr.validate_peer_certificate(cert, "fl-coordinator.internal")
        assert valid is True
        assert "verified" in msg

    def test_crl_revocation_detection(self):
        mgr = MTLSManager()
        cert = mgr.generate_cert_info("bank-a")
        mgr.revoke_certificate(cert.serial_number)

        cert_rev = mgr.generate_cert_info("bank-a")
        cert_rev = mgr.generate_cert_info(cert.subject_cn)
        # Manually mark serial as revoked
        cert_rev.revoked = True

        valid, msg = mgr.validate_peer_certificate(cert_rev, "bank-a.internal")
        assert valid is False
        assert "revoked" in msg


class TestOIDCAuthenticator:
    """Verify OIDC JWT decoding and claim extraction."""

    def test_create_and_validate_token(self):
        auth = OIDCAuthenticator()
        token = auth.create_mock_token(
            username="investigator_b",
            bank_id="bank_b",
            roles=["analyst"],
            clearance_level=3,
        )

        valid, claims, msg = auth.decode_and_validate_token(token)
        assert valid is True
        assert claims is not None
        assert claims.username == "investigator_b"
        assert claims.bank_id == "bank_b"
        assert claims.clearance_level == 3
        assert "analyst" in claims.roles


class TestABACEngine:
    """Verify Attribute-Based Access Control policy decisions."""

    def test_tenant_isolation_rule_denies_cross_bank(self):
        engine = ABACEngine()
        user = UserClaims(sub="u1", username="bank_a_user", bank_id="bank_a")
        resource = ABACResource(resource_type="alert", resource_id="alt_9", bank_id="bank_b")

        result = engine.evaluate_access(user, resource, action="read")
        assert result.allowed is False
        assert result.policy_name == "RULE-TENANT-ISOLATION"

    def test_shift_hours_restriction(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u2",
            username="night_shift",
            bank_id="bank_a",
            shift_hours="08:00-17:00",
        )
        resource = ABACResource(resource_type="alert", resource_id="alt_10", bank_id="bank_a")

        # Evaluate at 22:00 (out of shift window)
        result = engine.evaluate_access(user, resource, action="read", current_hour_override=22)
        assert result.allowed is False
        assert result.policy_name == "RULE-SHIFT-HOURS-RESTRICTION"

    def test_approval_tier_exceeded(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u3",
            username="junior_analyst",
            bank_id="bank_a",
            approval_tier=10000.0,
        )
        resource = ABACResource(
            resource_type="alert",
            resource_id="alt_11",
            bank_id="bank_a",
            amount=50000.0,
        )

        result = engine.evaluate_access(user, resource, action="approve")
        assert result.allowed is False
        assert result.policy_name == "RULE-APPROVAL-TIER-EXCEEDED"

    def test_malformed_ip_fails_closed(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u4",
            username="analyst_strict_ip",
            bank_id="bank_a",
            allowed_ip_subnets=["10.0.0.0/16"],
        )
        resource = ABACResource(resource_type="alert", resource_id="alt_12", bank_id="bank_a")

        result = engine.evaluate_access(user, resource, action="read", client_ip="invalid.ip.format.here")
        assert result.allowed is False
        assert result.policy_name == "RULE-IP-RANGE-RESTRICTION"
        assert "Malformed or unparseable client IP" in result.reason

    def test_malformed_shift_hours_fails_closed(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u5",
            username="analyst_bad_shift",
            bank_id="bank_a",
            shift_hours="bad-range-format",
        )
        resource = ABACResource(resource_type="alert", resource_id="alt_13", bank_id="bank_a")

        result = engine.evaluate_access(user, resource, action="read")
        assert result.allowed is False
        assert result.policy_name == "RULE-SHIFT-HOURS-RESTRICTION"
        assert "Unable to evaluate shift window" in result.reason

    def test_clearance_level_exceeded(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u6",
            username="low_clearance",
            bank_id="bank_a",
            clearance_level=1,
        )
        resource = ABACResource(
            resource_type="model",
            resource_id="mod_top_secret",
            bank_id="bank_a",
            classification_level=3,
        )

        result = engine.evaluate_access(user, resource, action="read")
        assert result.allowed is False
        assert result.policy_name == "RULE-CLEARANCE-LEVEL-INSUFFICIENT"

    def test_superadmin_override(self):
        engine = ABACEngine()
        user = UserClaims(
            sub="u_admin",
            username="super_user",
            bank_id="bank_a",
            roles=["super_admin"],
        )
        resource = ABACResource(resource_type="alert", resource_id="alt_99", bank_id="bank_b")

        result = engine.evaluate_access(user, resource, action="read")
        assert result.allowed is True
        assert result.policy_name == "RULE-SUPERADMIN-OVERRIDE"


class TestVaultClient:
    """Verify HashiCorp Vault secrets manager adapter."""

    def test_vault_client_fallback_retrieval(self):
        vault = VaultClient()
        creds = vault.get_secret("database/credentials")

        assert creds["username"] == "fraud_user"
        meta = vault.get_secret_metadata("database/credentials")
        assert "secret/data/database/credentials" in meta.path


class TestImmutableAuditChain:
    """Verify SHA-256 cryptographic audit chain and tamper detection."""

    def test_audit_chain_validity(self):
        chain = ImmutableAuditChain()
        initial_len = len(chain.chain)

        chain.append_event("TEST_EVENT", "tester", "target_1", {"key": "value"})
        assert len(chain.chain) == initial_len + 1

        rpt = chain.verify_chain_integrity()
        assert rpt.is_valid is True
        assert rpt.total_records == len(chain.chain)

    def test_detect_retrospective_tampering(self):
        chain = ImmutableAuditChain()
        chain.append_event("GENUINE_EVENT", "analyst_1", "alert_500")

        rpt_before = chain.verify_chain_integrity()
        assert rpt_before.is_valid is True

        # Tamper with an existing entry's details
        chain.chain[-1].details["tampered"] = True

        rpt_after = chain.verify_chain_integrity()
        assert rpt_after.is_valid is False
        assert rpt_after.broken_index is not None
        assert "Tampering detected" in (rpt_after.tamper_reason or "")
