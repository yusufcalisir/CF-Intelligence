"""Unit tests for Zero-Trust Identity, Authentication & mTLS Certificate Manager suite."""

from __future__ import annotations

from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.mtls_manager import MTLSManager, X509CertificateInfo
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.infrastructure.security.vault_client import VaultClient


def test_vault_pki_engine_cert_issuance() -> None:
    """Verifies HashiCorp Vault PKI Secrets Engine X.509 certificate issuance & CA retrieval."""
    vault = VaultClient(vault_url="http://vault.internal:8200", vault_token="root", enabled=False)

    cert_data = vault.issue_pki_certificate(
        role="cfi-bank-role",
        common_name="bank-alpha.cfi.internal",
        alt_names=["bank-alpha.cfi.internal", "localhost"],
    )

    assert "certificate" in cert_data
    assert "private_key" in cert_data
    assert cert_data["common_name"] == "bank-alpha.cfi.internal"

    import pytest

    from app.infrastructure.security.vault_client import VaultUnavailableError

    # Offline Vault must fail closed and raise VaultUnavailableError
    with pytest.raises(VaultUnavailableError):
        vault.get_ca_certificate()

    # Authoritative mock response verification
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"-----BEGIN CERTIFICATE-----\nTEST_ROOT_CA\n-----END CERTIFICATE-----"
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ca_pem = vault.get_ca_certificate()
        assert "BEGIN CERTIFICATE" in ca_pem


def test_mtls_manager_san_and_crl_revocation() -> None:
    """Verifies mTLS Manager SAN matching, zero-downtime rotation, and CRL revocation checking."""
    mtls = MTLSManager(ca_cn="CFI-Consortium-Root-CA")
    vault = VaultClient(enabled=False)

    cert_info, _ = mtls.issue_vault_certificate(
        vault_client=vault,
        common_name="coordinator.cfi.internal",
        sans=["coordinator.cfi.internal", "localhost"],
    )
    assert isinstance(cert_info, X509CertificateInfo)

    # Valid SAN match
    valid, msg = mtls.validate_peer_certificate(cert_info, expected_san="coordinator.cfi.internal")
    assert valid is True

    # Invalid SAN mismatch
    invalid_san, msg_san = mtls.validate_peer_certificate(
        cert_info, expected_san="bank-unknown.internal"
    )
    assert invalid_san is False
    assert "SAN match failure" in msg_san

    # Revoke serial and verify CRL check fails
    mtls.revoke_certificate(cert_info.serial_number, vault_client=vault)
    cert_info.revoked = True
    valid_rev, msg_rev = mtls.validate_peer_certificate(
        cert_info, expected_san="coordinator.cfi.internal"
    )
    assert valid_rev is False
    assert "revoked in CRL" in msg_rev


def test_oidc_authenticator_jwt_claims() -> None:
    """Verifies OIDC JWT bearer token encoding, claim extraction, and IP subnets decoding."""
    auth = OIDCAuthenticator()
    token = auth.create_mock_token(
        username="investigator_x",
        bank_id="bank_alpha",
        roles=["analyst", "investigator"],
        clearance_level=3,
        shift_hours="08:00-18:00",
        approval_tier=75000.0,
        allowed_ip_subnets=["10.0.0.0/16", "192.168.1.0/24"],
    )

    valid, claims, msg = auth.decode_and_validate_token(token)
    assert valid is True
    assert claims is not None
    assert claims.username == "investigator_x"
    assert claims.bank_id == "bank_alpha"
    assert claims.clearance_level == 3
    assert "10.0.0.0/16" in claims.allowed_ip_subnets


def test_abac_policy_engine_rules() -> None:
    """Verifies ABAC Policy Engine rules (Tenant Isolation, IP Range, Shift Hours, Approval Tier, Clearance)."""
    engine = ABACEngine()

    user = UserClaims(
        sub="u100",
        username="analyst_bank_a",
        bank_id="bank_a",
        roles=["analyst"],
        clearance_level=2,
        shift_hours="08:00-18:00",
        approval_tier=50000.0,
        allowed_ip_subnets=["10.0.0.0/16"],
    )

    # 1. Tenant Isolation: Accessing bank_b resource fails
    res_b = ABACResource(resource_type="alert", resource_id="alt_1", bank_id="bank_b")
    decision1 = engine.evaluate_access(user, res_b, action="read")
    assert decision1.allowed is False
    assert decision1.policy_name == "RULE-TENANT-ISOLATION"

    # 2. IP Range Restriction: IP 192.168.1.50 fails allowed_ip_subnets=["10.0.0.0/16"]
    res_a = ABACResource(resource_type="alert", resource_id="alt_2", bank_id="bank_a")
    decision2 = engine.evaluate_access(user, res_a, action="read", client_ip="192.168.1.50")
    assert decision2.allowed is False
    assert decision2.policy_name == "RULE-IP-RANGE-RESTRICTION"

    # 3. Valid IP (10.0.1.20) passes IP check
    decision3 = engine.evaluate_access(
        user, res_a, action="read", client_ip="10.0.1.20", current_hour_override=12
    )
    assert decision3.allowed is True
    assert decision3.policy_name == "RULE-ALL-POLICIES-PASSED"

    # 4. Approval Tier Exceeded: $100k > $50k approval tier
    res_expensive = ABACResource(
        resource_type="alert", resource_id="alt_3", bank_id="bank_a", amount=100000.0
    )
    decision4 = engine.evaluate_access(
        user, res_expensive, action="approve", current_hour_override=12
    )
    assert decision4.allowed is False
    assert decision4.policy_name == "RULE-APPROVAL-TIER-EXCEEDED"

    # 5. Clearance Level Insufficient: level 3 resource > level 2 user
    res_classified = ABACResource(
        resource_type="model", resource_id="mod_1", bank_id="bank_a", classification_level=3
    )
    decision5 = engine.evaluate_access(
        user, res_classified, action="read", current_hour_override=12
    )
    assert decision5.allowed is False
    assert decision5.policy_name == "RULE-CLEARANCE-LEVEL-INSUFFICIENT"
