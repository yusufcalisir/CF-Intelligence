"""Unit tests for Bank Onboarding, Admin Console, and Maintenance Cron API routes.

Validates multi-prefix routing, CSR signing, CA trust bundle download,
tenant lifecycle transitions, dynamic admin dashboard metrics, and cron jobs.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database import Base, get_async_session
from app.main import app
from app.presentation.routers.maintenance_cron import DEFAULT_CRON_SECRET


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[TestClient, None]:
    """TestClient with in-memory SQLite database session override."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_async_session, None)
    await engine.dispose()


def _generate_valid_csr(common_name: str) -> str:
    """Helper to generate a cryptographically valid RSA private key and signed CSR PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Bank Corp"),
                ]
            )
        )
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")


# ── Bank Onboarding Routes ───────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["/api/v1/onboarding", "/v1/onboarding"])
def test_get_ca_bundle_success(client: TestClient, prefix: str) -> None:
    """Verify GET /ca-bundle returns valid root CA certificate and fingerprint."""
    response = client.get(f"{prefix}/ca-bundle")
    assert response.status_code == 200
    data = response.json()
    assert "root_ca_pem" in data
    assert data["root_ca_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert data["ca_fingerprint"].startswith("SHA256:")
    assert "CF-Intelligence Consortium Root CA" in data["issuer"]
    assert "valid_until" in data
    assert "crl_distribution_point" in data


@pytest.mark.parametrize("prefix", ["/api/v1/onboarding", "/v1/onboarding"])
def test_register_bank_success(client: TestClient, prefix: str) -> None:
    """Verify POST /register creates a new bank node and returns full bundle."""
    payload = {
        "bank_id": "bank_test_alpha",
        "legal_name": "Alpha Bank Global AG",
        "jurisdiction": "DE",
        "contact_email": "security@alphabank.de",
        "data_residency_region": "eu-central-1",
    }
    response = client.post(f"{prefix}/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["bank_id"] == "bank_test_alpha"
    assert data["status"].lower() == "active"
    assert data["cert_fingerprint"].startswith("SHA256:")
    assert data["mtls_cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert data["mtls_key_pem"].startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert "bank_test_alpha" in data["connector_config_yaml"]


def test_register_bank_validation_errors(client: TestClient) -> None:
    """Verify input validation rejects invalid jurisdiction, email, and extra fields."""
    # Invalid jurisdiction code (must be 2 uppercase characters)
    bad_jur = {
        "bank_id": "bank_valid",
        "legal_name": "Test Bank",
        "jurisdiction": "INVALID_LONG",
        "contact_email": "sec@bank.com",
        "data_residency_region": "us-east-1",
    }
    res1 = client.post("/api/v1/onboarding/register", json=bad_jur)
    assert res1.status_code == 422

    # Invalid email format
    bad_email = {
        "bank_id": "bank_valid",
        "legal_name": "Test Bank",
        "jurisdiction": "US",
        "contact_email": "not-an-email",
        "data_residency_region": "us-east-1",
    }
    res2 = client.post("/api/v1/onboarding/register", json=bad_email)
    assert res2.status_code == 422

    # Extra forbidden field
    extra_field = {
        "bank_id": "bank_valid",
        "legal_name": "Test Bank",
        "jurisdiction": "US",
        "contact_email": "sec@bank.com",
        "data_residency_region": "us-east-1",
        "unauthorized_field": "exploit",
    }
    res3 = client.post("/api/v1/onboarding/register", json=extra_field)
    assert res3.status_code == 422


def test_register_duplicate_bank_id_returns_409(client: TestClient) -> None:
    """Verify duplicate bank_id registration raises 409 Conflict."""
    payload = {
        "bank_id": "bank_duplicate",
        "legal_name": "Duplicate Bank Ltd",
        "jurisdiction": "UK",
        "contact_email": "admin@duplicate.co.uk",
        "data_residency_region": "eu-west-1",
    }
    res1 = client.post("/api/v1/onboarding/register", json=payload)
    assert res1.status_code == 201

    res2 = client.post("/api/v1/onboarding/register", json=payload)
    assert res2.status_code == 409
    assert "already registered" in res2.json()["detail"].lower()


def test_get_onboarding_bundle_lifecycle(client: TestClient) -> None:
    """Verify GET /bundle/{bank_id} returns existing configuration or 404."""
    # Unknown bank returns 404
    missing_res = client.get("/api/v1/onboarding/bundle/nonexistent_bank")
    assert missing_res.status_code == 404

    # Register bank
    payload = {
        "bank_id": "bank_bundle_test",
        "legal_name": "Bundle Bank",
        "jurisdiction": "FR",
        "contact_email": "bundle@bank.fr",
        "data_residency_region": "eu-west-3",
    }
    reg_res = client.post("/api/v1/onboarding/register", json=payload)
    assert reg_res.status_code == 201

    # Retrieve bundle
    bundle_res = client.get("/api/v1/onboarding/bundle/bank_bundle_test")
    assert bundle_res.status_code == 200
    bundle_data = bundle_res.json()
    assert bundle_data["bank_id"] == "bank_bundle_test"
    assert "bank_bundle_test" in bundle_data["connector_config_yaml"]


def test_list_and_get_bank_status(client: TestClient) -> None:
    """Verify GET /banks and GET /banks/{bank_id}/status."""
    payload = {
        "bank_id": "bank_status_check",
        "legal_name": "Status Check Bank",
        "jurisdiction": "CH",
        "contact_email": "ops@swissbank.ch",
        "data_residency_region": "eu-central-1",
    }
    client.post("/api/v1/onboarding/register", json=payload)

    # List banks
    list_res = client.get("/api/v1/onboarding/banks")
    assert list_res.status_code == 200
    banks = list_res.json()
    assert len(banks) >= 1
    assert any(b["bank_id"] == "bank_status_check" for b in banks)

    # Get single status
    status_res = client.get("/api/v1/onboarding/banks/bank_status_check/status")
    assert status_res.status_code == 200
    assert status_res.json()["bank_id"] == "bank_status_check"

    # Nonexistent status returns 404
    not_found = client.get("/api/v1/onboarding/banks/missing_bank_xyz/status")
    assert not_found.status_code == 404


def test_sign_csr_flow(client: TestClient) -> None:
    """Verify POST /banks/{bank_id}/sign-csr workflow."""
    # Register bank first
    payload = {
        "bank_id": "bank_csr_target",
        "legal_name": "CSR Signer Target Bank",
        "jurisdiction": "SG",
        "contact_email": "csr@bank.sg",
        "data_residency_region": "ap-southeast-1",
    }
    client.post("/api/v1/onboarding/register", json=payload)

    csr_pem = _generate_valid_csr("bank_csr_target.client.cf-intelligence.io")
    sign_res = client.post(
        "/api/v1/onboarding/banks/bank_csr_target/sign-csr",
        json={"csr_pem": csr_pem, "days_valid": 180},
    )
    assert sign_res.status_code == 200
    sign_data = sign_res.json()
    assert sign_data["signed_cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert sign_data["cert_fingerprint"].startswith("SHA256:")

    # Invalid CSR string returns 400
    bad_csr = client.post(
        "/api/v1/onboarding/banks/bank_csr_target/sign-csr",
        json={"csr_pem": "MALFORMED_GARBAGE_STRING", "days_valid": 180},
    )
    assert bad_csr.status_code == 400

    # Nonexistent bank returns 404
    missing_bank = client.post(
        "/api/v1/onboarding/banks/nonexistent_bank_csr/sign-csr",
        json={"csr_pem": csr_pem, "days_valid": 180},
    )
    assert missing_bank.status_code == 404


def test_bank_lifecycle_transitions(client: TestClient) -> None:
    """Verify suspend and rotate-cert lifecycle state transitions."""
    payload = {
        "bank_id": "bank_lifecycle",
        "legal_name": "Lifecycle Bank Corp",
        "jurisdiction": "US",
        "contact_email": "sec@lifecycle.com",
        "data_residency_region": "us-east-1",
    }
    client.post("/api/v1/onboarding/register", json=payload)

    # Suspend
    suspend_res = client.post("/api/v1/onboarding/banks/bank_lifecycle/suspend")
    assert suspend_res.status_code == 200
    assert suspend_res.json()["status"].lower() == "suspended"

    # Rotate cert
    rotate_res = client.post("/api/v1/onboarding/banks/bank_lifecycle/rotate-cert")
    assert rotate_res.status_code == 200
    assert rotate_res.json()["mtls_cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert rotate_res.json()["cert_fingerprint"].startswith("SHA256:")


def test_path_regex_bounds_validation(client: TestClient) -> None:
    """Verify that path parameters with invalid characters or out-of-bound lengths are rejected."""
    # Special characters like spaces or colons in bank_id should trigger 422
    res = client.get("/api/v1/onboarding/bundle/invalid%20bank%20id!")
    assert res.status_code == 422


# ── Admin Web Console Routes ─────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["/v1/admin/dashboard", "/api/v1/admin/dashboard", "/api/v1/admin"])
def test_admin_dashboard_summary(client: TestClient, prefix: str) -> None:
    """Verify admin dashboard summary returns valid non-negative metrics."""
    response = client.get(f"{prefix}/summary")
    assert response.status_code == 200
    data = response.json()
    assert "active_bank_nodes_count" in data
    assert "federated_rounds_completed" in data
    assert "global_model_auc" in data
    assert "total_cases_opened" in data
    assert "sla_compliance_pct" in data
    assert isinstance(data["active_bank_nodes_count"], int)
    assert isinstance(data["global_model_auc"], float)


@pytest.mark.parametrize("role", ["EXECUTIVE", "COMPLIANCE_OFFICER", "ML_ENGINEER", "FRAUD_INVESTIGATOR"])
def test_admin_role_configuration(client: TestClient, role: str) -> None:
    """Verify role-based widget and permission configuration."""
    response = client.get(f"/api/v1/admin/dashboard/role-config?role={role}")
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == role
    assert len(data["visible_widgets"]) > 0
    assert len(data["permissions"]) > 0


def test_admin_system_config(client: TestClient) -> None:
    """Verify GET /api/v1/admin/config returns system feature flags and operational bounds."""
    response = client.get("/api/v1/admin/config")
    assert response.status_code == 200
    data = response.json()
    assert data["multi_tenant_enabled"] is True
    assert "hsm_vault_status" in data
    assert data["rate_limit_rpm"] >= 60
    assert "active_features" in data
    assert data["active_features"]["homomorphic_encryption_ckks"] is True


def test_admin_tenants_listing(client: TestClient) -> None:
    """Verify GET /api/v1/admin/tenants returns registered tenant list."""
    response = client.get("/api/v1/admin/tenants")
    assert response.status_code == 200
    data = response.json()
    assert "total_tenants" in data
    assert isinstance(data["tenants"], list)


def test_admin_maintenance_window(client: TestClient) -> None:
    """Verify GET /api/v1/admin/maintenance returns maintenance intervals."""
    response = client.get("/api/v1/admin/maintenance")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "next_scheduled_maintenance" in data
    assert data["storage_healthy"] is True


# ── Maintenance CronJob Routes ───────────────────────────────────────────────


def test_cron_cleanup_unauthorized_without_token(client: TestClient) -> None:
    """Verify cron invocation without secret returns 401 Unauthorized."""
    response = client.post("/api/v1/cron/cleanup-sessions")
    assert response.status_code == 401


def test_cron_cleanup_success_with_header(client: TestClient) -> None:
    """Verify cron cleanup succeeds when secret header is provided."""
    headers = {"X-Cron-Secret": DEFAULT_CRON_SECRET}
    response = client.post("/api/v1/cron/cleanup-sessions", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "timestamp_iso" in data


def test_cron_run_alias(client: TestClient) -> None:
    """Verify POST /api/v1/cron/run alias succeeds."""
    headers = {"X-Cron-Secret": DEFAULT_CRON_SECRET}
    response = client.post("/api/v1/cron/run", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"


def test_cron_health_check_success(client: TestClient) -> None:
    """Verify GET /api/v1/cron/health-check and /status."""
    headers = {"X-Cron-Secret": DEFAULT_CRON_SECRET}
    res1 = client.get("/api/v1/cron/health-check", headers=headers)
    assert res1.status_code == 200
    assert res1.json()["status"] == "HEALTHY"
    assert res1.json()["database_pool_active"] is True

    res2 = client.get("/api/v1/cron/status", headers=headers)
    assert res2.status_code == 200
    assert res2.json()["status"] == "HEALTHY"


def test_cron_key_rotation(client: TestClient) -> None:
    """Verify POST /api/v1/cron/rotate-keys."""
    headers = {"X-Cron-Secret": DEFAULT_CRON_SECRET}
    response = client.post(
        "/api/v1/cron/rotate-keys",
        headers=headers,
        json={"tenant_ids": ["bank_a"], "auto_reencrypt": True, "revoke_retired": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "bank_a" in data["tenants_rotated"]


def test_cron_schedules_list(client: TestClient) -> None:
    """Verify GET /api/v1/cron/schedule returns scheduled jobs."""
    headers = {"X-Cron-Secret": DEFAULT_CRON_SECRET}
    response = client.get("/api/v1/cron/schedule", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["scheduled_jobs"]) >= 3
