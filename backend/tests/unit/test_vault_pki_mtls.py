"""Unit tests for HashiCorp Vault PKI Secrets Engine and mTLS Manager Integration."""

from __future__ import annotations

import unittest

from app.infrastructure.security.mtls_manager import MTLSManager, X509CertificateInfo
from app.infrastructure.security.vault_client import VaultClient


class TestVaultPKIMTLSIntegration(unittest.TestCase):
    """Verifies HashiCorp Vault PKI Secrets Engine certificate issuance, rotation, and revocation."""

    def setUp(self) -> None:
        self.vault_client_mock = VaultClient(
            vault_url="http://vault.internal:8200",
            vault_token="root",
            enabled=False,  # Fallback mode
        )
        self.vault_client_enabled = VaultClient(
            vault_url="http://localhost:8200",
            vault_token="root",
            enabled=True,
        )
        self.mtls = MTLSManager(ca_cn="CFI-Consortium-Root-CA")

    def test_vault_client_mock_pki_issuance(self) -> None:
        """Assert mock mode issues valid certificate payload structure."""
        cert_data = self.vault_client_mock.issue_pki_certificate(
            role="cfi-bank-role",
            common_name="bank-a.cfi.internal",
            alt_names=["bank-a.cfi.internal", "localhost"],
        )
        self.assertIn("certificate", cert_data)
        self.assertIn("private_key", cert_data)
        self.assertIn("issuing_ca", cert_data)
        self.assertEqual(cert_data["common_name"], "bank-a.cfi.internal")
        self.assertIn("Vault Circuit Breaker Fallback", cert_data["source"])

    def test_vault_client_ca_retrieval_offline_raises_error(self) -> None:
        """Assert CA certificate PEM retrieval raises VaultUnavailableError when Vault is offline."""
        from app.infrastructure.security.vault_client import VaultUnavailableError

        with self.assertRaises(VaultUnavailableError):
            self.vault_client_mock.get_ca_certificate()

    def test_vault_client_ca_retrieval_success(self) -> None:
        """Assert authoritative CA certificate PEM retrieval succeeds when Vault responds."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"-----BEGIN CERTIFICATE-----\nVALID_CA_CERT\n-----END CERTIFICATE-----"
        mock_resp.__enter__.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ca_pem = self.vault_client_mock.get_ca_certificate()
            self.assertIn("BEGIN CERTIFICATE", ca_pem)
            self.assertIn("VALID_CA_CERT", ca_pem)

    def test_vault_client_revoke_pki_offline_fails_closed(self) -> None:
        """Assert revocation of serial number fails closed (returns False) when Vault is offline."""
        res = self.vault_client_mock.revoke_pki_certificate("1234567890abcdef")
        self.assertFalse(res)

    def test_vault_client_revoke_pki_success(self) -> None:
        """Assert authoritative revocation returns True when Vault responds with 200/204."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = self.vault_client_mock.revoke_pki_certificate("1234567890abcdef")
            self.assertTrue(res)

    def test_mtls_manager_issue_vault_certificate(self) -> None:
        """Assert MTLSManager issues Vault certificate and returns parsed metadata."""
        cert_info, raw_bundle = self.mtls.issue_vault_certificate(
            vault_client=self.vault_client_mock,
            common_name="bank-b.cfi.internal",
            sans=["bank-b.cfi.internal", "localhost"],
        )
        self.assertIsInstance(cert_info, X509CertificateInfo)
        self.assertEqual(cert_info.subject_cn, "bank-b.cfi.internal")
        self.assertIn("localhost", cert_info.sans)
        self.assertFalse(cert_info.revoked)

    def test_mtls_manager_rotate_certificates(self) -> None:
        """Assert automated zero-downtime certificate rotation updates certificate serial & expiry."""
        cert_info_old, _ = self.mtls.issue_vault_certificate(
            vault_client=self.vault_client_mock,
            common_name="coordinator.cfi.internal",
        )
        cert_info_new, raw_new = self.mtls.rotate_certificates(
            cn="coordinator.cfi.internal",
            vault_client=self.vault_client_mock,
        )
        self.assertEqual(cert_info_new.subject_cn, "coordinator.cfi.internal")
        self.assertIn("certificate", raw_new)

    def test_mtls_manager_crl_revocation_flow(self) -> None:
        """Assert revoked certificate serial number triggers CRL validation failure."""
        cert_info, _ = self.mtls.issue_vault_certificate(
            vault_client=self.vault_client_mock,
            common_name="malicious-bank.cfi.internal",
        )
        # Verify initially valid
        valid, msg = self.mtls.validate_peer_certificate(
            cert_info, expected_san="malicious-bank.cfi.internal"
        )
        self.assertTrue(valid)

        # Revoke serial
        self.mtls.revoke_certificate(cert_info.serial_number, vault_client=self.vault_client_mock)
        cert_info.revoked = cert_info.serial_number in self.mtls.crl_revoked_serials

        # Verify validation fails
        valid_after, msg_after = self.mtls.validate_peer_certificate(
            cert_info, expected_san="malicious-bank.cfi.internal"
        )
        self.assertFalse(valid_after)
        self.assertIn("revoked in CRL", msg_after)


if __name__ == "__main__":
    unittest.main()
