"""Unit tests for Bank Onboarding API routes across dual-prefix mount points.

Tests registration, bundle retrieval, bank listing, status checks,
CSR signing, lifecycle transitions (verify, activate, suspend), and cert rotation.
Uses dynamic UUIDs to ensure full test isolation and idempotency.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _generate_test_csr(common_name: str = "test-bank-csr.internal") -> str:
    """Helper to generate a valid PEM-encoded X.509 CSR for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Bank Consortium"),
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            ])
        )
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")


def _register_helper(bank_id: str, legal_name: str = "Test Bank", jurisdiction: str = "TR") -> dict:
    """Helper to register a bank and return JSON response."""
    payload = {
        "bank_id": bank_id,
        "legal_name": legal_name,
        "jurisdiction": jurisdiction,
        "contact_email": f"sec@{bank_id}.com",
        "data_residency_region": "eu-central-1",
    }
    res = client.post("/api/v1/onboarding/register", json=payload)
    return res.json()


def test_register_bank_success_and_multi_prefix() -> None:
    """Test bank registration on both /api/v1/onboarding/register and /v1/onboarding/register."""
    b_id1 = f"bank_rt_{uuid.uuid4().hex[:8]}"
    payload_1 = {
        "bank_id": b_id1,
        "legal_name": "Test Route Bank One",
        "jurisdiction": "TR",
        "contact_email": f"security@{b_id1}.com",
        "data_residency_region": "eu-central-1",
    }
    res1 = client.post("/api/v1/onboarding/register", json=payload_1)
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["bank_id"] == b_id1
    assert data1["status"].lower() == "active"
    assert data1["cert_fingerprint"]
    assert "BEGIN CERTIFICATE" in data1["mtls_cert_pem"]

    # Test dual prefix /v1/onboarding/register
    b_id2 = f"bank_rt_{uuid.uuid4().hex[:8]}"
    payload_2 = {
        "bank_id": b_id2,
        "legal_name": "Test Route Bank Two",
        "jurisdiction": "DE",
        "contact_email": f"security@{b_id2}.de",
        "data_residency_region": "eu-west-1",
    }
    res2 = client.post("/v1/onboarding/register", json=payload_2)
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["bank_id"] == b_id2
    assert data2["jurisdiction"] == "DE"


def test_register_bank_duplicate_rejected() -> None:
    """Test duplicate registration returns 409 Conflict."""
    b_id = f"bank_dup_{uuid.uuid4().hex[:8]}"
    _register_helper(b_id, "Initial Bank")

    payload = {
        "bank_id": b_id,
        "legal_name": "Duplicate Bank Registration",
        "jurisdiction": "TR",
        "contact_email": "admin@duplicate.com",
        "data_residency_region": "eu-central-1",
    }
    res = client.post("/api/v1/onboarding/register", json=payload)
    assert res.status_code == 409


def test_register_bank_validation_errors() -> None:
    """Test invalid payloads return 422 Unprocessable Entity."""
    # Invalid email
    res1 = client.post(
        "/api/v1/onboarding/register",
        json={
            "bank_id": "bank_invalid_email",
            "legal_name": "Invalid Email Bank",
            "jurisdiction": "TR",
            "contact_email": "not-an-email",
            "data_residency_region": "eu-west-1",
        },
    )
    assert res1.status_code == 422

    # Invalid jurisdiction (must be 2 uppercase chars)
    res2 = client.post(
        "/api/v1/onboarding/register",
        json={
            "bank_id": "bank_invalid_jurisdiction",
            "legal_name": "Invalid Jurisdiction Bank",
            "jurisdiction": "TURKEY",
            "contact_email": "sec@bank.com",
            "data_residency_region": "eu-west-1",
        },
    )
    assert res2.status_code == 422


def test_get_onboarding_bundle_success_and_404() -> None:
    """Test GET /bundle/{bank_id} retrieves config bundle and 404 on missing bank."""
    b_id = f"bank_bndl_{uuid.uuid4().hex[:8]}"
    _register_helper(b_id, "Bundle Test Bank")

    res_ok = client.get(f"/api/v1/onboarding/bundle/{b_id}")
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["bank_id"] == b_id
    assert "connector_config_yaml" in data
    assert "https://" in data["coordinator_endpoint"]

    # Dual prefix
    res_v1 = client.get(f"/v1/onboarding/bundle/{b_id}")
    assert res_v1.status_code == 200

    # Non-existent bank
    res_404 = client.get(f"/api/v1/onboarding/bundle/bank_nonexistent_{uuid.uuid4().hex[:6]}")
    assert res_404.status_code == 404


def test_list_and_get_bank_status() -> None:
    """Test listing registered banks and retrieving single bank status."""
    b_id = f"bank_st_{uuid.uuid4().hex[:8]}"
    _register_helper(b_id, "Status Test Bank")

    res_list = client.get("/api/v1/onboarding/banks")
    assert res_list.status_code == 200
    banks = res_list.json()
    assert isinstance(banks, list)
    assert len(banks) >= 1

    # Get status of single registered bank
    res_status = client.get(f"/api/v1/onboarding/banks/{b_id}/status")
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert status_data["bank_id"] == b_id
    assert status_data["status"].lower() == "active"

    # Status of nonexistent bank
    res_missing = client.get(f"/api/v1/onboarding/banks/bank_missing_{uuid.uuid4().hex[:6]}/status")
    assert res_missing.status_code == 404


def test_sign_csr_success_and_error_handling() -> None:
    """Test X.509 CSR signing via /banks/{bank_id}/sign-csr."""
    b_id = f"bank_csr_{uuid.uuid4().hex[:8]}"
    _register_helper(b_id, "CSR Test Bank")

    csr_pem = _generate_test_csr(f"{b_id}.internal")
    sign_res = client.post(
        f"/api/v1/onboarding/banks/{b_id}/sign-csr",
        json={"csr_pem": csr_pem, "days_valid": 365},
    )
    assert sign_res.status_code == 200
    sign_data = sign_res.json()
    assert sign_data["bank_id"] == b_id
    assert "BEGIN CERTIFICATE" in sign_data["signed_cert_pem"]
    assert sign_data["cert_fingerprint"]
    assert "expires_at" in sign_data

    # Invalid CSR string
    bad_csr_res = client.post(
        f"/api/v1/onboarding/banks/{b_id}/sign-csr",
        json={"csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\nINVALID_DATA\n-----END CERTIFICATE REQUEST-----"},
    )
    assert bad_csr_res.status_code == 400

    # Nonexistent bank
    missing_bank_res = client.post(
        f"/api/v1/onboarding/banks/bank_nonexistent_{uuid.uuid4().hex[:6]}/sign-csr",
        json={"csr_pem": csr_pem},
    )
    assert missing_bank_res.status_code == 404


def test_bank_lifecycle_transitions() -> None:
    """Test verify, activate, suspend, and cert rotation endpoints."""
    b_id = f"bank_life_{uuid.uuid4().hex[:8]}"
    _register_helper(b_id, "Lifecycle Test Bank")

    # Suspend bank
    suspend_res = client.post(f"/api/v1/onboarding/banks/{b_id}/suspend")
    assert suspend_res.status_code == 200
    assert suspend_res.json()["status"].lower() == "suspended"

    # Reactivate bank
    activate_res = client.post(f"/api/v1/onboarding/banks/{b_id}/activate")
    assert activate_res.status_code == 200
    assert activate_res.json()["status"].lower() == "active"

    # Rotate cert
    rotate_res = client.post(f"/api/v1/onboarding/banks/{b_id}/rotate-cert")
    assert rotate_res.status_code == 200
    rotate_data = rotate_res.json()
    assert rotate_data["bank_id"] == b_id
    assert "BEGIN CERTIFICATE" in rotate_data["mtls_cert_pem"]
    assert rotate_data["cert_fingerprint"]

    # Lifecycle on missing bank
    missing = f"missing_{uuid.uuid4().hex[:6]}"
    assert client.post(f"/api/v1/onboarding/banks/{missing}/suspend").status_code == 404
    assert client.post(f"/api/v1/onboarding/banks/{missing}/activate").status_code == 404
    assert client.post(f"/api/v1/onboarding/banks/{missing}/verify").status_code == 404
    assert client.post(f"/api/v1/onboarding/banks/{missing}/rotate-cert").status_code == 404
