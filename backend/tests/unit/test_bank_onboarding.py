"""Unit tests for Section 36.1 — Bank Onboarding Pipeline.

Tests:
  1. test_register_bank_creates_db_record
  2. test_full_onboarding_pipeline_sets_active
  3. test_duplicate_bank_id_rejected
  4. test_connector_config_contains_required_fields
  5. test_onboarding_endpoint_returns_bundle
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.bank_onboarding_service import (
    BankAlreadyExistsError,
    BankOnboardingService,
)
from app.domain.enums import BankStatus
from app.infrastructure.database import Base, get_async_session
from app.infrastructure.grpc import FederatedLearningServicer, GRPCBankClient
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


# ── Service Unit Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_bank_creates_db_record(db_session: AsyncSession) -> None:
    """register_bank must insert a TenantConfigModel row in pending_verification status."""
    service = BankOnboardingService(db_session)
    registration = await service.register_bank(
        bank_id="bank_test1",
        legal_name="Test Bank One",
        jurisdiction="TR",
        contact_email="security@testbank1.com",
        data_residency_region="eu-west-1",
    )

    assert registration.bank_id == "bank_test1"
    assert registration.legal_name == "Test Bank One"
    assert registration.status == BankStatus.PENDING_VERIFICATION
    assert not registration.schema_provisioned


@pytest.mark.asyncio
@patch(
    "app.application.services.bank_onboarding_service.init_tenant_tables", new_callable=AsyncMock
)
async def test_full_onboarding_pipeline_sets_active(
    mock_init_tables: AsyncMock, db_session: AsyncSession
) -> None:
    """Full onboarding pipeline must transition bank status to ACTIVE."""
    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_test2",
        legal_name="Test Bank Two",
        jurisdiction="DE",
        contact_email="admin@testbank2.de",
        data_residency_region="eu-central-1",
    )
    cert, key = await service.issue_mtls_certificate("bank_test2")
    assert cert.startswith("-----BEGIN CERTIFICATE-----")
    assert key.startswith("-----BEGIN RSA PRIVATE KEY-----")

    # Cryptographically verify the issued X.509 certificate and private key
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    parsed_cert = x509.load_pem_x509_certificate(cert.encode("utf-8"))
    parsed_key = serialization.load_pem_private_key(key.encode("utf-8"), password=None)
    assert parsed_cert.subject.rfc4514_string() == "CN=bank_test2.client.cf-intelligence.io"
    assert parsed_key.key_size == 2048

    await service.provision_tenant_schema("bank_test2")
    await service.provision_kms_key("bank_test2")
    activated = await service.activate_bank("bank_test2")

    assert activated is not None
    assert activated.status == BankStatus.ACTIVE
    assert activated.schema_provisioned
    assert activated.vault_key_path == "transit/keys/tenant_bank_test2"
    assert activated.cert_fingerprint.startswith("SHA256:")


@pytest.mark.asyncio
async def test_two_banks_receive_distinct_x509_certificates(db_session: AsyncSession) -> None:
    """Verify onboarding two distinct banks produces distinct RSA keypairs and distinct certs."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_node_1",
        legal_name="Bank One Corp",
        jurisdiction="US",
        contact_email="node1@bankone.com",
        data_residency_region="us-east-1",
    )
    await service.register_bank(
        bank_id="bank_node_2",
        legal_name="Bank Two Ltd",
        jurisdiction="UK",
        contact_email="node2@banktwo.com",
        data_residency_region="eu-west-2",
    )

    cert1, key1 = await service.issue_mtls_certificate("bank_node_1")
    cert2, key2 = await service.issue_mtls_certificate("bank_node_2")

    x509_1 = x509.load_pem_x509_certificate(cert1.encode("utf-8"))
    x509_2 = x509.load_pem_x509_certificate(cert2.encode("utf-8"))

    # Assert distinct certificates and public key material
    assert cert1 != cert2
    assert key1 != key2
    assert x509_1.public_key().public_numbers().n != x509_2.public_key().public_numbers().n
    assert x509_1.fingerprint(hashes.SHA256()) != x509_2.fingerprint(hashes.SHA256())


@pytest.mark.asyncio
async def test_grpc_cross_tenant_certificate_spoofing_rejected(db_session: AsyncSession) -> None:
    """Verify gRPC servicer rejects cross-tenant certificate fingerprint presentation."""
    from app.infrastructure.grpc.client import GRPCBankClient
    from app.infrastructure.grpc.servicer import FederatedLearningServicer

    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_legit",
        legal_name="Legit Bank",
        jurisdiction="US",
        contact_email="legit@bank.com",
        data_residency_region="us-east-1",
    )
    cert_legit, _ = await service.issue_mtls_certificate("bank_legit")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    parsed_legit = x509.load_pem_x509_certificate(cert_legit.encode("utf-8"))
    legit_fp = f"SHA256:{parsed_legit.fingerprint(hashes.SHA256()).hex()}"

    servicer = FederatedLearningServicer()
    # Explicitly bind legit bank fingerprint
    servicer.register_bank_fingerprint("bank_legit", legit_fp)

    client = GRPCBankClient(servicer=servicer)

    # 1. Legit registration with correct fingerprint -> Succeeds
    resp1 = await client.register(
        bank_id="bank_legit",
        bank_name="Legit Bank",
        cert_fingerprint=legit_fp,
    )
    assert resp1.is_accepted is True
    assert resp1.session_token.startswith("grpc_sess_")

    # 2. Rogue node 'bank_adversary' presents bank_legit's certificate fingerprint -> Blocked!
    resp_spoof = await client.register(
        bank_id="bank_adversary",
        bank_name="Adversary Attacker Bank",
        cert_fingerprint=legit_fp,
    )
    assert resp_spoof.is_accepted is False
    assert resp_spoof.session_token == ""

    # 3. Bank legit presenting mismatched altered fingerprint -> Blocked!
    resp_mismatch = await client.register(
        bank_id="bank_legit",
        bank_name="Legit Bank",
        cert_fingerprint="SHA256:0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert resp_mismatch.is_accepted is False


@pytest.mark.asyncio
async def test_tofu_race_condition_rejected_for_unonboarded_bank(db_session: AsyncSession) -> None:
    """Verifies that an un-onboarded bank cannot self-bind a certificate on first gRPC contact (TOFU prevention).

    Attacker calling RegisterClient before real onboarding must be rejected.
    Subsequent legitimate onboarding allows legitimate registration with the issued fingerprint.
    Subsequent registration with mismatched fingerprint remains strictly rejected.
    """
    service = BankOnboardingService(db_session)
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    attacker_fingerprint = "SHA256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    # 1. Attacker attempts to register as 'bank_delta' BEFORE onboarding -> Strictly REJECTED (no TOFU binding)
    resp_attacker = await client.register(
        bank_id="bank_delta",
        bank_name="Attacker Impersonating Delta",
        cert_fingerprint=attacker_fingerprint,
    )
    assert resp_attacker.is_accepted is False
    assert resp_attacker.session_token == ""
    assert "bank_delta" not in servicer.active_sessions

    # 2. Legitimate onboarding flow occurs for 'bank_delta'
    await service.register_bank(
        bank_id="bank_delta",
        legal_name="Delta National Bank",
        jurisdiction="US",
        contact_email="security@deltabank.com",
        data_residency_region="us-east-1",
    )
    cert_pem, _ = await service.issue_mtls_certificate("bank_delta")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    parsed_cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    legit_fingerprint = f"SHA256:{parsed_cert.fingerprint(hashes.SHA256()).hex()}"

    # 3. Attacker still tries with old/forged fingerprint -> REJECTED
    resp_attacker_retry = await client.register(
        bank_id="bank_delta",
        bank_name="Attacker Impersonating Delta",
        cert_fingerprint=attacker_fingerprint,
    )
    assert resp_attacker_retry.is_accepted is False

    # 4. Legitimate bank node presents the authentic issued fingerprint -> SUCCEEDS
    resp_legit = await client.register(
        bank_id="bank_delta",
        bank_name="Delta National Bank",
        cert_fingerprint=legit_fingerprint,
    )
    assert resp_legit.is_accepted is True
    assert resp_legit.session_token.startswith("grpc_sess_")
    assert "bank_delta" in servicer.active_sessions


@pytest.mark.asyncio
async def test_duplicate_bank_id_rejected(db_session: AsyncSession) -> None:
    """Registering the same bank_id twice must raise BankAlreadyExistsError."""
    service = BankOnboardingService(db_session)
    await service.register_bank(
        bank_id="bank_alpha",
        legal_name="Alpha Bank",
        jurisdiction="TR",
        contact_email="a@alpha.com",
        data_residency_region="eu-west-1",
    )

    with pytest.raises(BankAlreadyExistsError):
        await service.register_bank(
            bank_id="bank_alpha",
            legal_name="Alpha Bank Dup",
            jurisdiction="TR",
            contact_email="b@alpha.com",
            data_residency_region="eu-west-1",
        )


@pytest.mark.asyncio
async def test_connector_config_contains_required_fields(db_session: AsyncSession) -> None:
    """generate_connector_config must return valid YAML string with required keys."""
    service = BankOnboardingService(db_session)
    yaml_str = service.generate_connector_config("bank_gamma")

    assert 'bank_id: "bank_gamma"' in yaml_str
    assert "coordinator_url:" in yaml_str
    assert "cert_path:" in yaml_str
    assert "key_path:" in yaml_str


# ── FastAPI Router Unit Tests ─────────────────────────────────────────────────


@patch(
    "app.application.services.bank_onboarding_service.init_tenant_tables", new_callable=AsyncMock
)
def test_onboarding_endpoint_returns_bundle(
    mock_init_tables: AsyncMock, db_session: AsyncSession
) -> None:
    """POST /api/v1/onboarding/register must return complete onboarding bundle."""
    app.dependency_overrides[get_async_session] = lambda: db_session
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/onboarding/register",
            json={
                "bank_id": "bank_delta",
                "legal_name": "Delta Savings Bank",
                "jurisdiction": "US",
                "contact_email": "compliance@deltasavings.com",
                "data_residency_region": "us-east-1",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()

        assert data["bank_id"] == "bank_delta"
        assert data["status"] == "active"
        assert "mtls_cert_pem" in data
        assert "mtls_key_pem" in data
        assert "connector_config_yaml" in data
        assert data["cert_fingerprint"] != ""

        # Test duplicate -> 409
        dup_resp = client.post(
            "/api/v1/onboarding/register",
            json={
                "bank_id": "bank_delta",
                "legal_name": "Delta Savings Bank",
                "jurisdiction": "US",
                "contact_email": "compliance@deltasavings.com",
                "data_residency_region": "us-east-1",
            },
        )
        assert dup_resp.status_code == 409

        # Test list -> 200
        list_resp = client.get("/api/v1/onboarding/banks")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # Test single status -> 200
        status_resp = client.get("/api/v1/onboarding/banks/bank_delta/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["bank_id"] == "bank_delta"

    finally:
        app.dependency_overrides.clear()
