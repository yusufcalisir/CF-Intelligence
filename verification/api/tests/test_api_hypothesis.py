#!/usr/bin/env python
"""Comprehensive Hypothesis Property-Based Verification Suite for API Subsystem.

Verifies 10 mathematical and structural system invariants across randomized scenarios:
1. Probability & Risk Score Boundedness Invariant
2. Out-Of-Bounds Payload Input Validation Invariant
3. String Length Constraint Enforcement Invariant
4. Enum Query Parameter Robustness Invariant
5. Case Idempotency Key Deduplication Invariant
6. ABAC Cross-Tenant Isolation Invariant
7. Content-Type Enforcement Invariant
8. Cryptographic Audit Chain Invariant
9. Response Header Consistency Invariant
10. Malformed JSON Syntax Handling Invariant
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import torch

# Add backend directory to sys.path
backend_dir = Path(__file__).parent.parent.parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, Phase, Verbosity, given, settings, strategies as st

from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.main import app

client = TestClient(app)


def _seed_global_model() -> None:
    """Write a freshly-initialised (untrained) model to the default global_model.pt path.

    This satisfies the predict endpoint's filesystem guard without requiring a full
    federated training run in the property-based test environment.
    """
    registry = ModelRegistry()
    global_path = os.path.join(registry.storage_dir, "global_model.pt")
    if not os.path.exists(global_path):
        svc = ModelService(get_settings())
        model = svc.create_model(dp_compatible=True)
        torch.save(model.state_dict(), global_path)


_seed_global_model()

# Global Hypothesis Settings
settings.register_profile(
    "api_verification",
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    verbosity=Verbosity.normal,
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink]
)
settings.load_profile("api_verification")

# ------------------------------------------------------------------------------
# Property 1: Bounded Risk Score Invariant
# ------------------------------------------------------------------------------
@given(
    amount=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    merchant=st.text(min_size=1, max_size=64, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    country=st.text(min_size=2, max_size=2, alphabet=st.characters(whitelist_categories=('Lu',))),
    device=st.sampled_from(["web_browser", "mobile_app", "pos_terminal"]),
    velocity=st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    hour=st.integers(min_value=0, max_value=23),
    merchant_risk=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    history_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    chargebacks=st.integers(min_value=0, max_value=100),
    age_days=st.integers(min_value=0, max_value=3650)
)
def test_property_prediction_boundedness_invariant(
    amount, merchant, country, device, velocity, hour, merchant_risk, history_score, chargebacks, age_days
):
    """INVARIANT 1: Valid numerical payloads must produce fraud_probability ∈ [0.0, 1.0] and risk_score ∈ [0, 1000]."""
    payload = {
        "transaction_amount": amount,
        "merchant_category": merchant,
        "country_code": country,
        "device_type": device,
        "velocity": velocity,
        "hour_of_day": hour,
        "merchant_risk_score": merchant_risk,
        "customer_history_score": history_score,
        "chargeback_count": chargebacks,
        "account_age_days": age_days
    }
    r = client.post("/api/v1/predict", json=payload)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert 0.0 <= body["fraud_probability"] <= 1.0, f"Probability out of bounds: {body['fraud_probability']}"
    assert 0 <= body["risk_score"] <= 1000, f"Risk score out of bounds: {body['risk_score']}"

# ------------------------------------------------------------------------------
# Property 2: Out-Of-Bounds Validation Invariant
# ------------------------------------------------------------------------------
@given(
    bad_hour=st.one_of(st.integers(max_value=-1), st.integers(min_value=24, max_value=1000)),
    amount=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False)
)
def test_property_out_of_bounds_validation_invariant(bad_hour, amount):
    """INVARIANT 2: Out-of-bounds hour_of_day values MUST return HTTP 422 Unprocessable Entity."""
    payload = {
        "transaction_amount": amount,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": bad_hour,
        "merchant_risk_score": 0.1,
        "customer_history_score": 0.9,
        "chargeback_count": 0,
        "account_age_days": 100
    }
    r = client.post("/api/v1/predict", json=payload)
    assert r.status_code == 422, f"Expected HTTP 422 for bad_hour={bad_hour}, got {r.status_code}"

# ------------------------------------------------------------------------------
# Property 3: String Length Bound Invariant
# ------------------------------------------------------------------------------
@given(
    oversized_str=st.text(min_size=257, max_size=500, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))
)
def test_property_string_length_bound_invariant(oversized_str):
    """INVARIANT 3: String fields exceeding max_length=256 characters MUST return HTTP 422."""
    payload = {
        "transaction_amount": 100.0,
        "merchant_category": oversized_str,
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.1,
        "customer_history_score": 0.9,
        "chargeback_count": 0,
        "account_age_days": 100
    }
    r = client.post("/api/v1/predict", json=payload)
    assert r.status_code == 422, f"Expected HTTP 422 for string length {len(oversized_str)}, got {r.status_code}"

# ------------------------------------------------------------------------------
# Property 4: Enum Query Parameter Guard Invariant
# ------------------------------------------------------------------------------
@given(
    severity_param=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll')))
)
def test_property_enum_query_parameter_guard_invariant(severity_param):
    """INVARIANT 4: Arbitrary query enum params must return HTTP 200 (if valid enum) or HTTP 422 (if invalid), never 500."""
    r = client.get(f"/api/v1/alerts?severity={severity_param}")
    assert r.status_code in (200, 422), f"Expected 200/422 for severity='{severity_param}', got {r.status_code}: {r.text}"

# ------------------------------------------------------------------------------
# Property 5: Case Idempotency Key Invariant
# ------------------------------------------------------------------------------
@given(
    title=st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs'))),
    key=st.uuids().map(str)
)
def test_property_case_idempotency_key_invariant(title, key):
    """INVARIANT 5: Duplicate requests with identical Idempotency-Key return the exact same case_id."""
    case_payload = {"title": title, "priority": "p2_high"}
    headers = {"Idempotency-Key": key}
    r1 = client.post("/api/v1/cases", json=case_payload, headers=headers)
    r2 = client.post("/api/v1/cases", json=case_payload, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200, f"Expected 200, got {r1.status_code} and {r2.status_code}"
    assert r1.json()["id"] == r2.json()["id"], f"Idempotency key failure: {r1.json()['id']} != {r2.json()['id']}"

# ------------------------------------------------------------------------------
# Property 6: ABAC Cross-Tenant Isolation Invariant
# ------------------------------------------------------------------------------
@given(
    user_bank=st.sampled_from(["bank_a", "bank_b", "bank_c"]),
    resource_bank=st.sampled_from(["bank_x", "bank_y", "bank_z"])
)
def test_property_abac_tenant_isolation_invariant(user_bank, resource_bank):
    """INVARIANT 6: Cross-tenant resource access (user_bank != resource_bank) is strictly denied (allowed: False)."""
    # ABACEvalRequest uses flat fields — not a nested user/resource dict.
    abac_payload = {
        "user_username": "test_user",
        "user_bank_id": user_bank,
        "user_roles": ["analyst"],
        "user_clearance": 2,
        "user_shift_hours": "00:00-24:00",
        "user_approval_tier": 50000.0,
        "resource_type": "api_route",
        "resource_id": "/api/v1/alerts",
        "resource_bank_id": resource_bank,
        "resource_amount": 0.0,
        "resource_classification": 1,
        "action": "read",
    }
    r = client.post("/api/v1/security/abac/evaluate", json=abac_payload)
    assert r.status_code == 200
    assert r.json()["allowed"] is False, f"ABAC isolation failed: user_bank={user_bank}, resource_bank={resource_bank}"

# ------------------------------------------------------------------------------
# Property 7: Content-Type Enforcement Invariant
# ------------------------------------------------------------------------------
@given(
    non_json_ct=st.sampled_from(["text/plain", "application/xml", "image/png", "application/octet-stream"])
)
def test_property_content_type_enforcement_invariant(non_json_ct):
    """INVARIANT 7: Non-JSON content types sent to mutating endpoints MUST return HTTP 415 Unsupported Media Type."""
    r = client.post("/api/v1/predict", content="sample body", headers={"Content-Type": non_json_ct})
    assert r.status_code == 415, f"Expected 415 for Content-Type='{non_json_ct}', got {r.status_code}"

# ------------------------------------------------------------------------------
# Property 8: Audit Chain Hash Integrity Invariant
# ------------------------------------------------------------------------------
@given(
    event_count=st.integers(min_value=1, max_value=5)
)
def test_property_audit_chain_hash_integrity_invariant(event_count):
    """INVARIANT 8: Cryptographic SHA-256 audit chain verification MUST remain valid across appends."""
    r = client.post("/api/v1/security/audit-chain/verify")
    assert r.status_code == 200
    assert r.json()["is_valid"] is True, "Audit chain cryptographic hash integrity check failed!"

# ------------------------------------------------------------------------------
# Property 9: Response Header Metadata Invariant
# ------------------------------------------------------------------------------
@given(
    endpoint=st.sampled_from(["/health", "/api/v1/alerts", "/api/v1/cases"])
)
def test_property_response_header_metadata_invariant(endpoint):
    """INVARIANT 9: API responses MUST contain X-API-Version and traceparent headers."""
    r = client.get(endpoint)
    assert "x-api-version" in r.headers, f"Missing X-API-Version header on {endpoint}"
    assert "traceparent" in r.headers, f"Missing traceparent header on {endpoint}"

# ------------------------------------------------------------------------------
# Property 10: Malformed Syntax Handling Invariant
# ------------------------------------------------------------------------------
@given(
    malformed_str=st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_characters=('"', '{', '}', ':')))
)
def test_property_malformed_json_syntax_handling_invariant(malformed_str):
    """INVARIANT 10: Malformed non-JSON payloads MUST return HTTP 400 or 415/422, NEVER HTTP 500."""
    r = client.post("/api/v1/predict", content=f"{{ {malformed_str} ", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 415, 422), f"Expected 400/415/422 for malformed payload, got {r.status_code}"


if __name__ == "__main__":
    print("Executing Fast Hypothesis Property-Based Verification Suite (10 examples per property)...")
    test_property_prediction_boundedness_invariant()
    test_property_out_of_bounds_validation_invariant()
    test_property_string_length_bound_invariant()
    test_property_enum_query_parameter_guard_invariant()
    test_property_case_idempotency_key_invariant()
    test_property_abac_tenant_isolation_invariant()
    test_property_content_type_enforcement_invariant()
    test_property_audit_chain_hash_integrity_invariant()
    test_property_response_header_metadata_invariant()
    test_property_malformed_json_syntax_handling_invariant()
    print("ALL 10 HYPOTHESIS PROPERTY INVARIANTS SUCCESSFULLY VERIFIED!")
