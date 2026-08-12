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
    assert key.startswith("-----BEGIN PRIVATE KEY-----")

    await service.provision_tenant_schema("bank_test2")
    await service.provision_kms_key("bank_test2")
    activated = await service.activate_bank("bank_test2")

    assert activated is not None
    assert activated.status == BankStatus.ACTIVE
    assert activated.schema_provisioned
    assert activated.vault_key_path == "transit/keys/tenant_bank_test2"


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
