"""Helper utility for generating valid X.509 self-signed certificates when Vault PKI engine is unconfigured/offline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_self_signed_pem(common_name: str, days_valid: int = 365) -> tuple[str, str]:
    """Generates valid X.509 certificate and RSA private key PEM pair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name), x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    return cert_pem, key_pem


def sign_csr_pem(
    csr_pem: str,
    days_valid: int = 365,
    ca_cert_pem: str | None = None,
    ca_key_pem: str | None = None,
) -> tuple[str, str, datetime]:
    """Cryptographically verifies and signs an X.509 Certificate Signing Request (CSR).

    Args:
        csr_pem: PEM-encoded X.509 Certificate Signing Request.
        days_valid: Certificate validity period in days (default 365).
        ca_cert_pem: Optional PEM-encoded CA certificate.
        ca_key_pem: Optional PEM-encoded CA private key.

    Returns:
        tuple[str, str, datetime]: (cert_pem, cert_fingerprint, expires_at)

    Raises:
        ValueError: If CSR PEM is malformed, unsupported, or contains an invalid signature.
    """
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Malformed or invalid X.509 CSR PEM: {exc}") from exc

    if not csr.is_signature_valid:
        raise ValueError("CSR signature verification failed: invalid cryptographic signature.")

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=days_valid)

    # Determine signing authority
    if ca_cert_pem and ca_key_pem:
        ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode("utf-8"))
        signing_key = serialization.load_pem_private_key(ca_key_pem.encode("utf-8"), password=None)
        issuer_name = ca_cert.subject
    else:
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer_name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "CF-Intelligence Consortium Root CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CF-Intelligence Consortium"),
            ]
        )

    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(issuer_name)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expires_at)
    )

    has_san = False
    for ext in csr.extensions:
        builder = builder.add_extension(ext.value, critical=ext.critical)
        if isinstance(ext.value, x509.SubjectAlternativeName):
            has_san = True

    if not has_san:
        cns = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        san_dns_names = [x509.DNSName("localhost")]
        if cns:
            cn_val = cns[0].value
            if isinstance(cn_val, bytes):
                cn_val = cn_val.decode("utf-8")
            san_dns_names.insert(0, x509.DNSName(str(cn_val)))
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_dns_names),
            critical=False,
        )

    cert = builder.sign(cast(Any, signing_key), hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    fingerprint = f"SHA256:{cert.fingerprint(hashes.SHA256()).hex()}"

    return cert_pem, fingerprint, cert.not_valid_after_utc

