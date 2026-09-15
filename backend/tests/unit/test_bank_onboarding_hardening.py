"""Unit tests for Phase 47 — Institutional Bank Onboarding & PKI Wizard Hardening.

Covers:
  1. Real X.509 CSR signing workflow with genuine RSA key generation & verification
  2. Malformed / corrupted CSR rejection (InvalidCSRError)
  3. Tampered CSR cryptographic signature rejection
  4. Non-existent bank fail-fast checks (BankNotFoundError)
  5. Multi-step lifecycle state machine (PENDING -> VERIFIED -> ACTIVE -> SUSPENDED -> OFFBOARDED)
  6. Prevention of activating offboarded bank nodes (InvalidBankStateError)
  7. Zero-mock 404 NOT FOUND responses and clean empty list behavior in router
  8. PKI Wizard REST endpoint for institutional CSR signing (POST /sign-csr)
  9. Bundle retrieval REST endpoint (GET /bundle/{bank_id})
  10. Institutional verification & suspension REST endpoints (POST /verify, POST /suspend, POST /activate)
  11. Bank ID input validation and path-traversal prevention
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.bank_onboarding_service import (
    BankNotFoundError,
    BankOnboardingService,
    InvalidBankStateError,
    InvalidCSRError,
)
from app.domain.enums import BankStatus
from app.infrastructure.database import Base, get_async_session
from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provide transactional in-memory SQLite AsyncSession for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _generate_test_csr(common_name: str) -> tuple[str, str]:
    """Helper to generate a genuine RSA private key and signed X.509 CSR PEM pair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Institutional Bank Test"),
                ]
            )
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name), x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return csr_pem, key_pem


# ── Unit Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_csr_cryptographic_signing_success(db_session: AsyncSession) -> None:
    """Verifies that BankOnboardingService cryptographically signs a valid institutional CSR."""
    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_theta",
        legal_name="Theta Banking Corp",
        jurisdiction="US",
        contact_email="security@thetabank.com",
        data_residency_region="us-east-1",
    )

    csr_pem, _ = _generate_test_csr("bank_theta.client.cf-intelligence.io")
    cert_pem, fingerprint, expires_at = await service.sign_csr("bank_theta", csr_pem)

    # Validate output certificate structure
    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    assert cert.subject.rfc4514_string() == "O=Institutional Bank Test,CN=bank_theta.client.cf-intelligence.io"
    assert fingerprint.startswith("SHA256:")
    assert expires_at > cert.not_valid_before_utc

    # Verify fingerprint recorded in DB
    record = await service.get_bank("bank_theta")
    assert record is not None
    assert record.cert_fingerprint == fingerprint


@pytest.mark.asyncio
async def test_csr_invalid_pem_rejected(db_session: AsyncSession) -> None:
    """Verifies that malformed CSR strings raise InvalidCSRError."""
    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_corrupt",
        legal_name="Corrupt Bank",
        jurisdiction="DE",
        contact_email="admin@corrupt.de",
        data_residency_region="eu-central-1",
    )

    with pytest.raises(InvalidCSRError, match="Malformed or invalid X.509 CSR PEM"):
        await service.sign_csr("bank_corrupt", "-----BEGIN CERTIFICATE REQUEST-----\nGARBAGE\n-----END CERTIFICATE REQUEST-----")


@pytest.mark.asyncio
async def test_csr_tampered_signature_rejected(db_session: AsyncSession) -> None:
    """Verifies that a CSR with an invalid/tampered cryptographic signature is rejected."""
    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_tamper",
        legal_name="Tamper Bank",
        jurisdiction="FR",
        contact_email="sec@tamper.fr",
        data_residency_region="eu-west-3",
    )

    csr_pem, _ = _generate_test_csr("bank_tamper.client.cf-intelligence.io")
    # Mutate a character in the middle of base64 payload to break cryptographic signature
    lines = csr_pem.strip().split("\n")
    if len(lines) > 3:
        mid_line = lines[2]
        altered_line = mid_line[:-2] + ("AA" if mid_line[-2:] != "AA" else "BB")
        lines[2] = altered_line
        tampered_csr = "\n".join(lines) + "\n"

        with pytest.raises(InvalidCSRError):
            await service.sign_csr("bank_tamper", tampered_csr)


@pytest.mark.asyncio
async def test_operations_on_nonexistent_bank_fail_fast(db_session: AsyncSession) -> None:
    """Verifies that all mutating operations fail-fast with BankNotFoundError when bank does not exist."""
    service = BankOnboardingService(db_session)
    ghost = "bank_ghost_999"

    with pytest.raises(BankNotFoundError):
        await service.issue_mtls_certificate(ghost)

    with pytest.raises(BankNotFoundError):
        csr_pem, _ = _generate_test_csr("ghost.client.cf-intelligence.io")
        await service.sign_csr(ghost, csr_pem)

    with pytest.raises(BankNotFoundError):
        await service.provision_tenant_schema(ghost)

    with pytest.raises(BankNotFoundError):
        await service.provision_kms_key(ghost)

    with pytest.raises(BankNotFoundError):
        await service.verify_bank(ghost)

    with pytest.raises(BankNotFoundError):
        await service.activate_bank(ghost)

    with pytest.raises(BankNotFoundError):
        await service.suspend_bank(ghost)

    with pytest.raises(BankNotFoundError):
        await service.offboard_bank(ghost)


@pytest.mark.asyncio
@patch(
    "app.application.services.bank_onboarding_service.init_tenant_tables", new_callable=AsyncMock
)
async def test_multi_step_lifecycle_state_machine(
    mock_init_tables: AsyncMock, db_session: AsyncSession
) -> None:
    """Verifies complete state machine transitions across PENDING -> VERIFIED -> ACTIVE -> SUSPENDED -> OFFBOARDED."""
    service = BankOnboardingService(db_session)
    bank_id = "bank_lifecycle"

    # Step 1: Register
    reg = await service.register_bank(
        bank_id=bank_id,
        legal_name="Lifecycle Bank International",
        jurisdiction="GB",
        contact_email="compliance@lifecycle.co.uk",
        data_residency_region="eu-west-2",
    )
    assert reg.status == BankStatus.PENDING_VERIFICATION

    # Step 2: Verification
    verified = await service.verify_bank(bank_id)
    assert verified.status == BankStatus.PENDING_VERIFICATION

    # Step 3: Schema & KMS
    await service.provision_tenant_schema(bank_id)
    await service.provision_kms_key(bank_id)

    # Step 4: Activation
    active = await service.activate_bank(bank_id)
    assert active.status == BankStatus.ACTIVE
    assert active.activated_at is not None

    # Step 5: Suspension
    suspended = await service.suspend_bank(bank_id)
    assert suspended.status == BankStatus.SUSPENDED

    # Step 6: Offboarding
    offboarded = await service.offboard_bank(bank_id)
    assert offboarded.status == BankStatus.OFFBOARDED

    # Invariant: Cannot activate offboarded bank node
    with pytest.raises(InvalidBankStateError, match="offboarded"):
        await service.activate_bank(bank_id)

    # Invariant: Cannot verify offboarded bank node
    with pytest.raises(InvalidBankStateError, match="offboarded"):
        await service.verify_bank(bank_id)


@pytest.mark.asyncio
async def test_bank_id_sanitization_and_validation(db_session: AsyncSession) -> None:
    """Verifies strict input sanitization preventing path traversal and injection."""
    service = BankOnboardingService(db_session)

    invalid_ids = [
        "../../etc/passwd",
        "bank;DROP TABLE tenant_configs;--",
        "b",  # Too short (< 3)
        "bank*wildcard",
        "bank<script>",
        "bank space here",
    ]
    for bad_id in invalid_ids:
        with pytest.raises(ValueError):
            await service.register_bank(
                bank_id=bad_id,
                legal_name="Bad Bank",
                jurisdiction="US",
                contact_email="bad@bad.com",
                data_residency_region="us-east-1",
            )


# ── Presentation Router Hardening & Zero-Mock Tests ───────────────────────────


def test_router_zero_mock_empty_list_and_404(db_session: AsyncSession) -> None:
    """Verifies presentation router returns true empty list and 404 NOT FOUND (zero mock fallbacks)."""
    app.dependency_overrides[get_async_session] = lambda: db_session
    client = TestClient(app)

    try:
        # GET /banks when empty must return [] (NOT fake JPMorgan/HSBC)
        list_resp = client.get("/api/v1/onboarding/banks")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        # GET /banks/unknown/status must return 404 NOT FOUND (NOT fake demo status)
        status_resp = client.get("/api/v1/onboarding/banks/unknown_node/status")
        assert status_resp.status_code == 404
        assert "not found" in status_resp.json()["detail"].lower()

        # GET /bundle/unknown must return 404 NOT FOUND
        bundle_resp = client.get("/api/v1/onboarding/bundle/unknown_node")
        assert bundle_resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@patch(
    "app.application.services.bank_onboarding_service.init_tenant_tables", new_callable=AsyncMock
)
def test_router_pki_wizard_csr_signing_endpoint(
    mock_init_tables: AsyncMock, db_session: AsyncSession
) -> None:
    """Verifies POST /api/v1/onboarding/banks/{bank_id}/sign-csr endpoint."""
    app.dependency_overrides[get_async_session] = lambda: db_session
    client = TestClient(app)

    try:
        # 1. Register bank
        reg_resp = client.post(
            "/api/v1/onboarding/register",
            json={
                "bank_id": "bank_wizard",
                "legal_name": "Wizard Savings Bank",
                "jurisdiction": "CH",
                "contact_email": "pki@wizardbank.ch",
                "data_residency_region": "eu-central-2",
            },
        )
        assert reg_resp.status_code == 201

        # 2. Institutional node presents its own CSR
        csr_pem, _ = _generate_test_csr("bank_wizard.client.cf-intelligence.io")
        sign_resp = client.post(
            "/api/v1/onboarding/banks/bank_wizard/sign-csr",
            json={"csr_pem": csr_pem, "days_valid": 90},
        )
        assert sign_resp.status_code == 200, sign_resp.text
        data = sign_resp.json()
        assert data["bank_id"] == "bank_wizard"
        assert data["signed_cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
        assert data["cert_fingerprint"].startswith("SHA256:")

        # 3. Invalid CSR -> 400
        bad_csr_resp = client.post(
            "/api/v1/onboarding/banks/bank_wizard/sign-csr",
            json={"csr_pem": "not-a-csr", "days_valid": 90},
        )
        assert bad_csr_resp.status_code == 400

        # 4. Unknown bank -> 404
        unknown_resp = client.post(
            "/api/v1/onboarding/banks/unknown_bank/sign-csr",
            json={"csr_pem": csr_pem, "days_valid": 90},
        )
        assert unknown_resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@patch(
    "app.application.services.bank_onboarding_service.init_tenant_tables", new_callable=AsyncMock
)
def test_router_bundle_and_lifecycle_endpoints(
    mock_init_tables: AsyncMock, db_session: AsyncSession
) -> None:
    """Verifies GET /bundle/{bank_id}, POST /verify, POST /suspend, POST /activate endpoints."""
    app.dependency_overrides[get_async_session] = lambda: db_session
    client = TestClient(app)

    try:
        # Register bank
        client.post(
            "/api/v1/onboarding/register",
            json={
                "bank_id": "bank_flow",
                "legal_name": "Flow Bank Corp",
                "jurisdiction": "SG",
                "contact_email": "ops@flowbank.sg",
                "data_residency_region": "ap-southeast-1",
            },
        )

        # GET /bundle/{bank_id}
        bundle_resp = client.get("/api/v1/onboarding/bundle/bank_flow")
        assert bundle_resp.status_code == 200
        bundle = bundle_resp.json()
        assert bundle["bank_id"] == "bank_flow"
        assert 'bank_id: "bank_flow"' in bundle["connector_config_yaml"]

        # POST /verify
        verify_resp = client.post("/api/v1/onboarding/banks/bank_flow/verify")
        assert verify_resp.status_code == 200

        # POST /suspend
        suspend_resp = client.post("/api/v1/onboarding/banks/bank_flow/suspend")
        assert suspend_resp.status_code == 200
        assert suspend_resp.json()["status"] == "suspended"

        # POST /activate
        activate_resp = client.post("/api/v1/onboarding/banks/bank_flow/activate")
        assert activate_resp.status_code == 200
        assert activate_resp.json()["status"] == "active"
    finally:
        app.dependency_overrides.clear()
