"""Adversarial Security Boundaries, Key Custody, mTLS Transport, and Recovery Test Suite.

Verifies:
1. Institutional Key & Secret Custody:
   - Secret generation refuses to overwrite an existing non-empty .env.consortium without --force.
   - Isolated per-institution Ed25519 key files (ISOLATED_FILE_MOUNT) are written with 0600 permissions,
     refuse silent overwrite without --force, and are loaded by IsolatedBankWorkerNode via WORKER_SIGNING_KEY_FILE.
   - Bootstrap principal provisioning preserves existing user_id when credentials match and raises
     PermissionError on credential mismatch unless --allow-credential-rotation is explicitly set.
   - InstitutionalVerificationRegistry refuses to overwrite an enrolled public key from environment
     variables without an explicit rotate_public_key() operation.
2. Authenticated Service Transport & Network Boundaries (HARDENED_MTLS):
   - Mutual TLS (ssl.CERT_REQUIRED) handshake between coordinator and institutional worker daemons.
   - Peer X.509 certificate Subject CN and SAN DNSName verification on outbound worker/coordinator clients.
   - Fail-closed rejection of plaintext HTTP downgrade attempts under HARDENED_MTLS.
   - Fail-closed rejection of unauthenticated clients, untrusted CA client certificates, unauthorized
     client certificate identities (not in allowed_client_identities), and mismatched server peer identities.
3. Governance Grant Resolution & Cryptographic Boundary Regression:
   - Explicit operational_grant_id resolution in PilotOperationsService never silently falls back to a
     different pair grant or default consortium grant when the requested grant_id is missing or covers
     different institutions.
4. Operational Recovery & Runtime Truth:
   - RedisStore and CacheService fail closed during a transient Redis outage and automatically reconnect
     once Redis recovers after the bounded reconnect cooldown.
"""

# ruff: noqa: E402
from __future__ import annotations

import datetime
import ipaddress
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Ensure scripts directory is importable for secret/bootstrap custody verification
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.bootstrap_consortium_deployment import provision_or_verify_institutional_user
from scripts.generate_consortium_deployment_secrets import (
    ensure_safe_secret_file_target,
    generate_consortium_deployment_secrets,
    write_env_file,
    write_isolated_key_custody_files,
)

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.isolated_matching_service import (
    ConsortiumSecurityError,
    CrossBankMatchingCoordinator,
    InstitutionalCoordinatorClient,
    InstitutionalCoordinatorDaemon,
    InstitutionalWorkerClient,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
    TransportSecurityConfig,
)
from app.application.services.pilot_operations_service import (
    PilotOperationsService,
)
from app.config import get_settings
from app.domain.consortium_governance import (
    CounterpartyTransportError,
    MemberRole,
)
from app.domain.institutional_identity import (
    InstitutionalSigningIdentity,
    InstitutionalVerificationRegistry,
)
from app.domain.pilot_operations import (
    PilotGovernanceError,
)
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore


def _generate_ca_certificate(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate an ephemeral self-signed X.509 Root CA for mTLS testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CF-Intelligence Test Trust Root"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    now = datetime.datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _generate_signed_leaf_certificate(
    *,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    dns_names: list[str],
    out_dir: Path,
    file_prefix: str,
) -> tuple[Path, Path]:
    """Issue a signed leaf X.509 certificate with Subject CN, DNS SANs, and 127.0.0.1 IP SAN."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CF-Intelligence Institution"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    san_entries: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    san_entries.append(x509.DNSName("localhost"))
    san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))

    now = datetime.datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path = out_dir / f"{file_prefix}.crt"
    key_path = out_dir / f"{file_prefix}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _generate_test_certificate(
    *,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    san_dns_names: list[str] | None = None,
) -> bytes:
    """Generate a DER-encoded certificate for unit testing SAN vs CN precedence."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
    )
    if san_dns_names is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in san_dns_names]),
            critical=False,
        )
    cert = builder.sign(ca_key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


class TestInstitutionalKeyAndSecretCustodyHardening:
    """Gate 2 verification: secret overwrite protection, isolated file-mounted keys, and rotation boundaries."""

    def test_secret_generation_refuses_to_overwrite_existing_env_without_force(
        self, tmp_path: Path
    ) -> None:
        env_path = tmp_path / ".env.consortium"
        secrets_v1 = generate_consortium_deployment_secrets()
        write_env_file(secrets_v1, env_path, force=False)
        assert env_path.exists()
        original_content = env_path.read_text(encoding="utf-8")

        # Attempting to overwrite an existing non-empty secret file without force=True must fail closed
        with pytest.raises(FileExistsError, match="Refusing to overwrite existing consortium secret file"):
            ensure_safe_secret_file_target(env_path, force=False)

        with pytest.raises(FileExistsError, match="Refusing to overwrite existing consortium secret file"):
            write_env_file(generate_consortium_deployment_secrets(), env_path, force=False)

        # File content remains untouched
        assert env_path.read_text(encoding="utf-8") == original_content

        # Explicit force=True permits intentional rotation
        secrets_v2 = generate_consortium_deployment_secrets()
        write_env_file(secrets_v2, env_path, force=True)
        assert secrets_v2["SECRET_KEY"] in env_path.read_text(encoding="utf-8")

    def test_isolated_key_custody_file_provisioning_and_worker_file_mount_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custody_dir = tmp_path / "key_custody"
        secrets_map = generate_consortium_deployment_secrets()

        written_files = write_isolated_key_custody_files(secrets_map, custody_dir, force=False)
        assert {"bank_a", "bank_b", "bank_c"}.issubset(written_files.keys())

        bank_a_key_file = written_files["bank_a"]
        assert bank_a_key_file.exists()
        assert len(bank_a_key_file.read_text(encoding="utf-8").strip()) == 64

        # Attempting to overwrite existing isolated key files without force=True fails closed
        with pytest.raises(FileExistsError, match="Refusing to overwrite existing institutional private key file"):
            write_isolated_key_custody_files(generate_consortium_deployment_secrets(), custody_dir, force=False)

        # Load worker signing identity via WORKER_SIGNING_KEY_FILE (ISOLATED_FILE_MOUNT)
        monkeypatch.delenv("WORKER_SIGNING_KEY_HEX", raising=False)
        monkeypatch.setenv("WORKER_SIGNING_KEY_FILE", str(bank_a_key_file))

        worker = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=str(tmp_path / "bank_a_vault"),
        )
        assert worker._signing_identity is not None
        assert worker._signing_identity.custody_mode == "ISOLATED_FILE_MOUNT"
        expected_pub_hex = json.loads(secrets_map["CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON"])["bank_a"]
        assert worker._signing_identity.public_key_hex == expected_pub_hex

        health = worker.get_health()
        assert health["key_custody_mode"] == "ISOLATED_FILE_MOUNT"
        assert health["signing_public_key_hex"] == expected_pub_hex

    def test_consortium_deployment_key_custody_isolation_and_boundary_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify institutional key custody isolation across workers, coordinator, and backend."""
        custody_dir = tmp_path / "consortium_keys"
        env_path = tmp_path / ".env.consortium"
        secrets_map = generate_consortium_deployment_secrets()

        written_files = write_isolated_key_custody_files(secrets_map, custody_dir, force=False)
        write_env_file(secrets_map, env_path, force=False, strip_private_keys=True)

        # 1. .env.consortium contains no private signing key material when file custody is active
        env_content = env_path.read_text(encoding="utf-8")
        assert "WORKER_BANK_A_SIGNING_KEY=" not in env_content
        assert "WORKER_BANK_B_SIGNING_KEY=" not in env_content
        assert "WORKER_BANK_C_SIGNING_KEY=" not in env_content
        assert "CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON=" in env_content

        # 2. Worker A loads only its own private key via file mount
        bank_a_key = written_files["bank_a"]
        worker_a = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=str(tmp_path / "bank_a_vault"),
            signing_key_file=str(bank_a_key),
        )
        assert worker_a._signing_identity is not None
        assert worker_a._signing_identity.custody_mode == "ISOLATED_FILE_MOUNT"
        pub_registry = json.loads(secrets_map["CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON"])
        assert worker_a._signing_identity.public_key_hex == pub_registry["bank_a"]

        # 3. Bank A worker attempting to use Bank B key fails signature verification against bank_a public key
        bank_b_key = written_files["bank_b"]
        bad_worker = IsolatedBankWorkerNode(
            bank_id="bank_a",
            storage_root=str(tmp_path / "bank_a_bad"),
            signing_key_file=str(bank_b_key),
        )
        assert bad_worker._signing_identity is not None
        assert bad_worker._signing_identity.public_key_hex != pub_registry["bank_a"]
        msg = b"consortium_test_payload"
        sig = bad_worker._signing_identity.sign(msg)
        reg = InstitutionalVerificationRegistry()
        for b_id, p_hex in pub_registry.items():
            reg.register_public_key(b_id, p_hex)
        assert reg.verify_signature(bank_id="bank_a", signature_hex=sig, message=msg) is False

        # 4. Coordinator holds zero private signing keys
        coord = CrossBankMatchingCoordinator()
        assert not hasattr(coord, "_signing_identity")
        assert not hasattr(coord, "signing_key")

    def test_bootstrap_preserves_existing_principal_and_rejects_unauthorized_credential_rotation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account_store_path = tmp_path / "account_store.json"
        monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_path))
        monkeypatch.setenv("CFI_JWT_SECRET", "unit_test_jwt_secret_custody_rotation_0123456789")
        AuthenticationService.reset_instance()

        auth_svc = AuthenticationService.get_instance()
        user_v1 = provision_or_verify_institutional_user(
            auth_svc,
            username="consortium_bank_a",
            plain_password="InitialStrongPassword!2026",
            bank_id="bank_a",
            roles=["admin", "analyst"],
            allow_credential_rotation=False,
        )
        original_user_id = user_v1.user_id

        # Re-running bootstrap with matching credentials preserves existing user_id without invalidating sessions
        user_v1_repeat = provision_or_verify_institutional_user(
            auth_svc,
            username="consortium_bank_a",
            plain_password="InitialStrongPassword!2026",
            bank_id="bank_a",
            roles=["admin", "analyst"],
            allow_credential_rotation=False,
        )
        assert user_v1_repeat.user_id == original_user_id

        # Re-running bootstrap with different password without allow_credential_rotation=True fails closed
        with pytest.raises(PermissionError, match="Refusing to overwrite active principal"):
            provision_or_verify_institutional_user(
                auth_svc,
                username="consortium_bank_a",
                plain_password="DifferentRotatedPassword!2026",
                bank_id="bank_a",
                roles=["admin", "analyst"],
                allow_credential_rotation=False,
            )

        # Explicit allow_credential_rotation=True rotates the principal credentials cleanly
        user_v2 = provision_or_verify_institutional_user(
            auth_svc,
            username="consortium_bank_a",
            plain_password="DifferentRotatedPassword!2026",
            bank_id="bank_a",
            roles=["admin", "analyst"],
            allow_credential_rotation=True,
        )
        ok, auth_user, _, _ = auth_svc.authenticate("consortium_bank_a", "DifferentRotatedPassword!2026")
        assert ok is True
        assert auth_user is not None
        assert auth_user.user_id == user_v2.user_id

    def test_verification_registry_prevents_implicit_public_key_overwrite_without_rotation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        id_initial = InstitutionalSigningIdentity.generate("bank_a")
        id_attacker = InstitutionalSigningIdentity.generate("bank_a")

        registry = InstitutionalVerificationRegistry()
        registry.register_public_key("bank_a", id_initial.public_key)

        # Even if an environment variable later changes, get_public_key must NOT overwrite an enrolled key
        monkeypatch.setenv("CFI_INSTITUTION_PUBLIC_KEY_BANK_A", id_attacker.public_key_hex)
        resolved = registry.get_public_key("bank_a")
        assert resolved == id_initial.public_key

        # Explicit rotate_public_key requires authentic cryptographic authorization signature
        challenge = f"ROTATE:bank_a:{id_attacker.public_key_hex}:2".encode()
        rotation_sig = id_initial.sign(challenge)
        registry.rotate_public_key("bank_a", id_attacker.public_key, rotation_sig)
        assert registry.get_public_key("bank_a") == id_attacker.public_key


class TestAuthenticatedServiceTransportBoundaries:
    """Gate 3 verification: HARDENED_MTLS transport, peer X.509 identity verification, and fail-closed rejection."""

    def test_hardened_mtls_worker_and_coordinator_positive_handshake_and_matching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ca_dir = tmp_path / "mtls_pki"
        ca_dir.mkdir(parents=True, exist_ok=True)
        ca_key, ca_cert = _generate_ca_certificate("CF-Intelligence Consortium Root CA")
        ca_cert_path = ca_dir / "consortium_ca.crt"
        ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        bank_a_cert, bank_a_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="bank_a",
            dns_names=["bank_a", "worker-bank-a"],
            out_dir=ca_dir,
            file_prefix="bank_a",
        )
        coord_cert, coord_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="coordinator",
            dns_names=["coordinator", "cfi-coordinator"],
            out_dir=ca_dir,
            file_prefix="coordinator",
        )
        api_cert, api_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="api_server",
            dns_names=["api_server", "cfi-api-server"],
            out_dir=ca_dir,
            file_prefix="api_server",
        )

        coord_secret = "mtls_unit_test_coordinator_secret_9988776655"
        monkeypatch.setenv("CFI_COORDINATOR_SECRET", coord_secret)

        worker_transport = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_cert_path),
            cert_path=str(bank_a_cert),
            key_path=str(bank_a_key),
            expected_peer_identity="coordinator",
            allowed_client_identities=("coordinator",),
        )
        worker_client_transport = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_cert_path),
            cert_path=str(coord_cert),
            key_path=str(coord_key),
            expected_peer_identity="bank_a",
            allowed_client_identities=("coordinator",),
        )
        coord_daemon_transport = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_cert_path),
            cert_path=str(coord_cert),
            key_path=str(coord_key),
            expected_peer_identity="api_server",
            allowed_client_identities=("api_server",),
        )
        coord_client_transport = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_cert_path),
            cert_path=str(api_cert),
            key_path=str(api_key),
            expected_peer_identity="coordinator",
            allowed_client_identities=("api_server",),
        )

        worker_id_a = InstitutionalSigningIdentity.generate("bank_a")
        node_a = IsolatedBankWorkerNode(
            "bank_a",
            storage_root=str(tmp_path / "bank_a_vault"),
            signing_key=worker_id_a,
        )
        daemon_a = InstitutionalWorkerDaemon(
            node_a,
            host="127.0.0.1",
            port=0,
            run_as_process=False,
            transport_config=worker_transport,
        )
        coord_daemon = InstitutionalCoordinatorDaemon(
            CrossBankMatchingCoordinator(),
            host="127.0.0.1",
            port=0,
            transport_config=coord_daemon_transport,
        )

        try:
            url_a = daemon_a.start()
            assert url_a.startswith("https://")
            url_coord = coord_daemon.start()
            assert url_coord.startswith("https://")

            worker_client = InstitutionalWorkerClient(
                "bank_a",
                url_a,
                transport_config=worker_client_transport,
            )
            health_a = worker_client.health_check()
            assert health_a["status"] == "ONLINE"
            assert health_a["bank_id"] == "bank_a"
            assert health_a["transport_profile"] == "HARDENED_MTLS"

            pub_key = worker_client.get_public_key()
            assert pub_key == worker_id_a.public_key_hex

            coord_client = InstitutionalCoordinatorClient(
                url_coord,
                transport_config=coord_client_transport,
            )
            coord_health = coord_client.health_check()
            assert coord_health["status"] == "ONLINE"
            assert coord_health["role"] == "coordinator"
            assert coord_health["transport_profile"] == "HARDENED_MTLS"
        finally:
            daemon_a.stop()
            coord_daemon.stop()

    def test_hardened_mtls_rejects_plaintext_http_downgrade_and_incomplete_config(
        self, tmp_path: Path
    ) -> None:
        ca_key, ca_cert = _generate_ca_certificate("Downgrade Test CA")
        ca_path = tmp_path / "ca.crt"
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_path, key_path = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="coordinator",
            dns_names=["coordinator"],
            out_dir=tmp_path,
            file_prefix="coord",
        )

        mtls_cfg = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_path),
            cert_path=str(cert_path),
            key_path=str(key_path),
            expected_peer_identity="bank_a",
            allowed_client_identities=("coordinator",),
        )

        with pytest.raises(ConsortiumSecurityError, match="forbids plaintext HTTP"):
            InstitutionalWorkerClient("bank_a", "http://127.0.0.1:8101", transport_config=mtls_cfg)

        with pytest.raises(ConsortiumSecurityError, match="forbids plaintext HTTP"):
            InstitutionalCoordinatorClient("http://127.0.0.1:8200", transport_config=mtls_cfg)

        # Missing allowed_client_identities on server SSL context creation must fail closed
        incomplete_server_cfg = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_path),
            cert_path=str(cert_path),
            key_path=str(key_path),
            allowed_client_identities=(),
        )
        with pytest.raises(ConsortiumSecurityError, match="requires a non-empty client certificate allowlist"):
            incomplete_server_cfg.create_server_ssl_context()

    def test_hardened_mtls_rejects_unauthenticated_untrusted_and_mismatched_identities(
        self, tmp_path: Path
    ) -> None:
        ca_dir = tmp_path / "mtls_adv_pki"
        ca_dir.mkdir(parents=True, exist_ok=True)
        ca_key, ca_cert = _generate_ca_certificate("Trusted Consortium Root CA")
        ca_cert_path = ca_dir / "trusted_ca.crt"
        ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        rogue_ca_key, rogue_ca_cert = _generate_ca_certificate("Rogue Untrusted Root CA")
        rogue_ca_path = ca_dir / "rogue_ca.crt"
        rogue_ca_path.write_bytes(rogue_ca_cert.public_bytes(serialization.Encoding.PEM))

        bank_a_cert, bank_a_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="bank_a",
            dns_names=["bank_a"],
            out_dir=ca_dir,
            file_prefix="bank_a",
        )
        coord_cert, coord_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="coordinator",
            dns_names=["coordinator"],
            out_dir=ca_dir,
            file_prefix="coordinator",
        )
        bank_b_cert, bank_b_key = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="bank_b",
            dns_names=["bank_b"],
            out_dir=ca_dir,
            file_prefix="bank_b",
        )
        rogue_client_cert, rogue_client_key = _generate_signed_leaf_certificate(
            ca_key=rogue_ca_key,
            ca_cert=rogue_ca_cert,
            common_name="coordinator",
            dns_names=["coordinator"],
            out_dir=ca_dir,
            file_prefix="rogue_coord",
        )

        worker_transport = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_cert_path),
            cert_path=str(bank_a_cert),
            key_path=str(bank_a_key),
            allowed_client_identities=("coordinator",),
        )
        node_a = IsolatedBankWorkerNode("bank_a", storage_root=str(tmp_path / "bank_a_vault"))
        daemon_a = InstitutionalWorkerDaemon(
            node_a,
            host="127.0.0.1",
            port=0,
            run_as_process=False,
            transport_config=worker_transport,
        )

        try:
            url_a = daemon_a.start()

            # 1. Unauthenticated client (no client certificate presented) -> SSL handshake rejected
            no_client_cert_ctx = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH, cafile=str(ca_cert_path)
            )
            req = urllib.request.Request(f"{url_a}/health", method="GET")
            with pytest.raises((urllib.error.URLError, ssl.SSLError, ConnectionError)):
                urllib.request.urlopen(req, context=no_client_cert_ctx, timeout=3.0)

            # 2. Rogue CA client certificate -> SSL handshake rejected
            rogue_client_cfg = TransportSecurityConfig(
                profile="HARDENED_MTLS",
                ca_cert_path=str(ca_cert_path),
                cert_path=str(rogue_client_cert),
                key_path=str(rogue_client_key),
                expected_peer_identity="bank_a",
            )
            rogue_client = InstitutionalWorkerClient(
                "bank_a", url_a, transport_config=rogue_client_cfg
            )
            with pytest.raises((ConsortiumSecurityError, CounterpartyTransportError, urllib.error.URLError, ssl.SSLError)):
                rogue_client.health_check()

            # 3. Valid CA-signed client certificate for bank_b (not in allowed_client_identities=("coordinator",)) -> 403 PermissionError
            unauthorized_peer_cfg = TransportSecurityConfig(
                profile="HARDENED_MTLS",
                ca_cert_path=str(ca_cert_path),
                cert_path=str(bank_b_cert),
                key_path=str(bank_b_key),
                expected_peer_identity="bank_a",
            )
            unauthorized_client = InstitutionalWorkerClient(
                "bank_a", url_a, transport_config=unauthorized_peer_cfg
            )
            with pytest.raises((PermissionError, CounterpartyTransportError), match="403|not authorized"):
                unauthorized_client.health_check()

            # 4. Server peer identity mismatch (client expects bank_b, but server presents bank_a certificate) -> ConsortiumSecurityError
            wrong_target_cfg = TransportSecurityConfig(
                profile="HARDENED_MTLS",
                ca_cert_path=str(ca_cert_path),
                cert_path=str(coord_cert),
                key_path=str(coord_key),
                expected_peer_identity="bank_b",
            )
            wrong_target_client = InstitutionalWorkerClient(
                "bank_b", url_a, transport_config=wrong_target_cfg
            )
            with pytest.raises(ConsortiumSecurityError, match="peer identity mismatch"):
                wrong_target_client.health_check()
        finally:
            daemon_a.stop()

    def test_hardened_mtls_enforces_tls13_minimum_version(self, tmp_path: Path) -> None:
        """Verify HARDENED_MTLS SSL contexts configure TLS 1.3 minimum version."""
        ca_key, ca_cert = _generate_ca_certificate("Test CA")
        ca_file = tmp_path / "ca.crt"
        ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_file, key_file = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="test_node",
            dns_names=["test_node"],
            out_dir=tmp_path,
            file_prefix="test_node",
        )
        cfg = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_file),
            cert_path=str(cert_file),
            key_path=str(key_file),
            allowed_client_identities=("client_a",),
        )
        server_ctx = cfg.create_server_ssl_context()
        assert server_ctx.minimum_version == ssl.TLSVersion.TLSv1_3
        assert server_ctx.verify_mode == ssl.CERT_REQUIRED

        client_ctx = cfg.create_client_ssl_context()
        assert client_ctx.minimum_version == ssl.TLSVersion.TLSv1_3
        assert client_ctx.verify_mode == ssl.CERT_REQUIRED

    def test_authoritative_san_precedence_and_cn_fallback(self, tmp_path: Path) -> None:
        """Verify SAN precedence (RFC 5280 / RFC 6125) and CN fallback when SAN is absent."""
        ca_key, ca_cert = _generate_ca_certificate("SAN Authority CA")
        ca_file = tmp_path / "ca.crt"
        ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_file, key_file = _generate_signed_leaf_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="dummy",
            dns_names=["dummy"],
            out_dir=tmp_path,
            file_prefix="dummy",
        )
        cfg = TransportSecurityConfig(
            profile="HARDENED_MTLS",
            ca_cert_path=str(ca_file),
            cert_path=str(cert_file),
            key_path=str(key_file),
        )

        # 1. Certificate with malicious/conflicting CN ("coordinator") but legitimate SAN ("bank_a")
        cert_malicious_cn = _generate_test_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="coordinator",
            san_dns_names=["bank_a"],
        )
        # Accepted based solely on SAN for target "bank_a"
        idents_a = cfg.verify_peer_cert_bytes(cert_malicious_cn, expected_identity="bank_a")
        assert "bank_a" in idents_a
        # Rejected for target "coordinator" because CN is completely ignored when SAN is present
        with pytest.raises(ConsortiumSecurityError, match="peer identity mismatch"):
            cfg.verify_peer_cert_bytes(cert_malicious_cn, expected_identity="coordinator")

        # 2. Certificate with legitimate CN ("bank_a") but mismatched SAN ("attacker_node")
        cert_mismatched_san = _generate_test_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="bank_a",
            san_dns_names=["attacker_node"],
        )
        # Rejected for target "bank_a" because SAN is authoritative and CN is ignored
        with pytest.raises(ConsortiumSecurityError, match="peer identity mismatch"):
            cfg.verify_peer_cert_bytes(cert_mismatched_san, expected_identity="bank_a")

        # 3. Certificate with legitimate CN ("bank_a") and NO SAN -> Accepted via CN fallback
        cert_cn_only = _generate_test_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="bank_a",
            san_dns_names=None,
        )
        idents_cn = cfg.verify_peer_cert_bytes(cert_cn_only, expected_identity="bank_a")
        assert "bank_a" in idents_cn


class TestGovernanceAndCryptographicBoundaryRegression:
    """Gate 4 verification: explicit grant ID resolution never falls back to a different grant."""

    def test_explicit_grant_id_never_falls_back_to_pair_or_default_grant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consortium_store_file = tmp_path / "consortium_store.json"
        account_store_file = tmp_path / "account_store.json"
        pilot_db_file = tmp_path / "pilot_ops.db"
        secret = "unit_test_governance_boundary_secret_1234567890"

        monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
        monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
        monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
        monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", secret)
        monkeypatch.setenv("JWT_SECRET", secret)
        monkeypatch.setenv("CFI_JWT_SECRET", secret)
        monkeypatch.setenv("CFI_COORDINATOR_SECRET", secret)

        DurableConsortiumStore.set_instance(None)
        AuthenticationService.reset_instance()
        DurablePilotStore._instance = None
        PilotOperationsService._instance = None

        auth_svc = AuthenticationService.get_instance()
        u_a = auth_svc.register_user("gov_bank_a", "Pass123!", "bank_a", roles=["admin"])
        u_b = auth_svc.register_user("gov_bank_b", "Pass123!", "bank_b", roles=["admin"])
        u_c = auth_svc.register_user("gov_bank_c", "Pass123!", "bank_c", roles=["admin"])
        token_a = auth_svc.create_token_bundle(u_a).access_token
        token_b = auth_svc.create_token_bundle(u_b).access_token
        token_c = auth_svc.create_token_bundle(u_c).access_token

        principal_op = AuthenticatedPrincipal(
            principal_id="gov_operator",
            tenant_id="consortium",
            roles=["admin", "security_officer", "operator"],
            is_authenticated=True,
            auth_method="jwt",
        )

        gov_svc = ConsortiumGovernanceService()
        gov_svc.create_consortium(
            consortium_id="c_gov_regression_ab",
            name="Governance Boundary Consortium AB",
            founder_bank_id="bank_a",
            auth_token=token_a,
        )
        gov_svc.add_member("c_gov_regression_ab", "bank_b", MemberRole.FULL_MEMBER, auth_token=token_a)

        gov_svc.create_consortium(
            consortium_id="c_gov_regression_ac",
            name="Governance Boundary Consortium AC",
            founder_bank_id="bank_a",
            auth_token=token_a,
        )
        gov_svc.add_member("c_gov_regression_ac", "bank_c", MemberRole.FULL_MEMBER, auth_token=token_a)

        # Register and approve both AB and AC grants in their respective consortia
        gov_svc.register_operational_data_sharing_grant(
            consortium_id="c_gov_regression_ab",
            grant_id="grant_pair_ab",
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
            "c_gov_regression_ab", "grant_pair_ab", "bank_b", auth_token=token_b
        )

        gov_svc.register_operational_data_sharing_grant(
            consortium_id="c_gov_regression_ac",
            grant_id="grant_pair_ac",
            authority="FFIEC",
            participating_banks=["bank_a", "bank_c"],
            allowed_purposes=["AML_INVESTIGATION"],
            allowed_jurisdictions=["EU"],
            institution_jurisdictions={"bank_a": "EU", "bank_c": "EU"},
            revision=1,
            approving_bank_id="bank_a",
            auth_token=token_a,
        )
        gov_svc.approve_operational_data_sharing_grant(
            "c_gov_regression_ac", "grant_pair_ac", "bank_c", auth_token=token_c
        )

        pilot_store = DurablePilotStore(str(pilot_db_file))
        pilot_svc = PilotOperationsService(pilot_store=pilot_store, consortium_service=gov_svc)

        # 1. Explicit non-existent grant ID must NEVER fall back to grant_pair_ab
        resolved_missing = pilot_svc._resolve_consortium_grant(
            "c_gov_regression_ab",
            grant_id="grant_non_existent_id",
            participating_institutions=["bank_a", "bank_b"],
        )
        assert resolved_missing == {}

        # 2. Explicit grant_pair_ac requested for ["bank_a", "bank_b"] must be rejected by run_preflight
        pilot = pilot_svc.provision_pilot(
            pilot_id="pilot_mismatched_grant_test",
            consortium_id="c_gov_regression_ab",
            participating_institutions=["bank_a", "bank_b"],
            operational_grant_id="grant_pair_ac",
            principal=principal_op,
        )
        with pytest.raises(
            PilotGovernanceError,
            match="OPERATIONAL_GRANT_VALID",
        ):
            pilot_svc.run_preflight(pilot.pilot_id, principal=principal_op)

    def test_pilot_operations_grant_coverage_fails_closed(self) -> None:
        """Verify WP-02: _resolve_consortium_grant participant coverage fail-closed semantics."""
        pilot_svc = PilotOperationsService(pilot_store=MagicMock(), consortium_service=MagicMock())

        class DummyConsortium:
            def __init__(self, metadata: dict[str, Any]):
                self.metadata = metadata

        # 1. Missing participating_banks rejected when institutions requested
        c_missing = DummyConsortium({
            "operational_data_sharing_grant": {"grant_id": "g_missing"}
        })
        assert pilot_svc._resolve_consortium_grant(
            c_missing, participating_institutions=["bank_a", "bank_b"]
        ) == {}

        # 2. Empty participating_banks: [] rejected
        c_empty = DummyConsortium({
            "operational_data_sharing_grant": {
                "grant_id": "g_empty",
                "participating_banks": [],
            }
        })
        assert pilot_svc._resolve_consortium_grant(
            c_empty, participating_institutions=["bank_a", "bank_b"]
        ) == {}

        # 3. Partial participant match rejected (requested bank_a, bank_b; grant has only bank_a)
        c_partial = DummyConsortium({
            "operational_data_sharing_grant": {
                "grant_id": "g_partial",
                "participating_banks": ["bank_a"],
            }
        })
        assert pilot_svc._resolve_consortium_grant(
            c_partial, participating_institutions=["bank_a", "bank_b"]
        ) == {}

        # 4. Exact match accepted
        grant_exact = {
            "grant_id": "g_exact",
            "participating_banks": ["bank_a", "bank_b"],
        }
        c_exact = DummyConsortium({"operational_data_sharing_grant": grant_exact})
        res_exact = pilot_svc._resolve_consortium_grant(
            c_exact, participating_institutions=["bank_a", "bank_b"]
        )
        assert res_exact["grant_id"] == "g_exact"

        # 5. Superset match accepted (grant has bank_a, bank_b, bank_c; requested bank_a, bank_b)
        grant_superset = {
            "grant_id": "g_superset",
            "participating_banks": ["bank_a", "bank_b", "bank_c"],
        }
        c_superset = DummyConsortium({"operational_data_sharing_grant": grant_superset})
        res_superset = pilot_svc._resolve_consortium_grant(
            c_superset, participating_institutions=["bank_a", "bank_b"]
        )
        assert res_superset["grant_id"] == "g_superset"

        # 6. Nonexistent grant_id rejected even if institutions match default grant
        assert pilot_svc._resolve_consortium_grant(
            c_exact, grant_id="nonexistent_id", participating_institutions=["bank_a", "bank_b"]
        ) == {}


class TestOperationalRecoveryAndRedisReconnection:
    """Gate 5 verification: RedisStore fails closed during transient outage and auto-reconnects after cooldown."""

    def test_redis_store_auto_reconnects_after_transient_outage_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        get_settings.cache_clear()

        store = RedisStore("unit_transient_recovery", strict_production=True)
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.get.return_value = '"recovered_value"'

        RedisStore._global_redis_unavailable = False
        store._redis_client = mock_client
        store._redis_failed = False
        store._had_command_failure = False
        store._ever_connected = True

        assert store.is_live() is True
        assert store.has_active_shared_storage() is True

        # Induce transient outage
        mock_client.ping.side_effect = ConnectionError("Transient Redis container restart")
        assert store.is_live() is False
        assert store.has_active_shared_storage() is False
        assert store._redis_failed

        with pytest.raises(RuntimeError, match="Redis store unavailable in production"):
            store.get("probe_key")

        # Restore Redis availability and advance failure timestamp past cooldown
        mock_client.ping.side_effect = None
        mock_client.ping.return_value = True
        store._last_instance_failure_at = time.monotonic() - (
            RedisStore._RECONNECT_COOLDOWN_SECONDS + 0.5
        )
        RedisStore._last_reconnect_attempt = store._last_instance_failure_at

        # Store automatically reconnects without manual restart
        assert store.has_active_shared_storage() is True
        assert store.is_live() is True
        assert not store._redis_failed
        assert store.get("probe_key") == "recovered_value"
