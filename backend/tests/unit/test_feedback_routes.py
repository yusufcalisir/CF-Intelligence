"""Unit Tests for Analyst Ground-Truth Feedback & Retraining Buffer API.

Validates dual-prefix routing (/api/v1/feedback and /v1/feedback), alias endpoints,
Pydantic v2 schema compliance, zero-PII validation, differential privacy gradient calculations,
and retraining buffer management.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_test_buffers() -> None:
    """Clean up test tenant buffers before and after each test."""
    tenants = ["bank_feedback_test_1", "bank_feedback_test_2", "bank_dual_prefix"]
    for tenant in tenants:
        client.delete(f"/api/v1/feedback/buffer/{tenant}")
    yield
    for tenant in tenants:
        client.delete(f"/api/v1/feedback/buffer/{tenant}")


def test_feedback_dual_prefix_routing() -> None:
    """Verifies that endpoints are accessible under both /api/v1/feedback and /v1/feedback."""
    tenant = "bank_dual_prefix"

    # Ingest under /api/v1/feedback
    resp1 = client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_dual_01",
            "determination": "CONFIRMED_FRAUD",
        },
    )
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["status"] == "success"
    assert data1["item"]["label"] == "CONFIRMED_FRAUD"
    assert data1["item"]["consumed_for_retraining"] is False

    # Ingest under /v1/feedback
    resp2 = client.post(
        "/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_dual_02",
            "determination": "FALSE_POSITIVE",
        },
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["status"] == "success"
    assert data2["item"]["label"] == "FALSE_POSITIVE"
    assert data2["item"]["consumed_for_retraining"] is False

    # Verify stats under both prefixes
    stats_resp1 = client.get(f"/api/v1/feedback/stats/{tenant}")
    assert stats_resp1.status_code == 200
    assert stats_resp1.json()["total_count"] == 2

    stats_resp2 = client.get(f"/v1/feedback/stats/{tenant}")
    assert stats_resp2.status_code == 200
    assert stats_resp2.json()["total_count"] == 2


def test_submit_alias_endpoint() -> None:
    """Verifies that POST /submit functions identically to POST /ingest as per API_REGISTRY.md."""
    tenant = "bank_feedback_test_1"

    # Submit alias under /api/v1/feedback/submit
    resp_submit_1 = client.post(
        "/api/v1/feedback/submit",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_submit_101",
            "determination": "CONFIRMED_FRAUD",
            "notes": "Verified fraudulent mule account via /submit",
        },
    )
    assert resp_submit_1.status_code == 201
    data_1 = resp_submit_1.json()
    assert data_1["status"] == "success"
    assert data_1["item"]["label"] == "CONFIRMED_FRAUD"
    assert len(data_1["item"]["transaction_id_hash"]) >= 32

    # Submit alias under /v1/feedback/submit
    resp_submit_2 = client.post(
        "/v1/feedback/submit",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_submit_102",
            "determination": "FALSE_POSITIVE",
            "notes": "Legitimate seasonal merchant spend",
        },
    )
    assert resp_submit_2.status_code == 201
    data_2 = resp_submit_2.json()
    assert data_2["status"] == "success"
    assert data_2["item"]["label"] == "FALSE_POSITIVE"

    # Check stats endpoint with query param
    stats_query_resp = client.get("/api/v1/feedback/stats", params={"tenant_id": tenant})
    assert stats_query_resp.status_code == 200
    stats_query_data = stats_query_resp.json()
    assert stats_query_data["total_count"] == 2
    assert stats_query_data["fraud_count"] == 1
    assert stats_query_data["false_positive_count"] == 1

    # Check stats endpoint with path param under /v1/feedback
    stats_path_resp = client.get(f"/v1/feedback/stats/{tenant}")
    assert stats_path_resp.status_code == 200
    assert stats_path_resp.json()["total_count"] == 2


def test_retraining_batch_sampling_and_consumption() -> None:
    """Verifies stratified batch sampling, priority weighting, and consumption tracking."""
    tenant = "bank_feedback_test_2"

    # Ingest 3 fraud items and 2 false positive items
    for i in range(3):
        client.post(
            "/api/v1/feedback/ingest",
            json={
                "tenant_id": tenant,
                "alert_id": f"alt_fraud_{i}",
                "determination": "CONFIRMED_FRAUD",
            },
        )
    for i in range(2):
        client.post(
            "/api/v1/feedback/ingest",
            json={
                "tenant_id": tenant,
                "alert_id": f"alt_fp_{i}",
                "determination": "FALSE_POSITIVE",
            },
        )

    # Sample batch without consuming
    batch_resp1 = client.post(
        "/api/v1/feedback/retraining-batch",
        json={
            "tenant_id": tenant,
            "batch_size": 4,
            "stratified": True,
            "mark_consumed": False,
        },
    )
    assert batch_resp1.status_code == 200
    batch_data1 = batch_resp1.json()
    assert batch_data1["batch_size"] == 4
    assert batch_data1["fraud_count"] >= 1
    assert batch_data1["false_positive_count"] >= 1
    assert len(batch_data1["items"]) == 4

    # Buffer should still have all 5 items unconsumed
    stats_resp1 = client.get(f"/api/v1/feedback/stats/{tenant}")
    assert stats_resp1.json()["unconsumed_count"] == 5

    # Sample and mark consumed under /v1/feedback/retraining-batch
    batch_resp2 = client.post(
        "/v1/feedback/retraining-batch",
        json={
            "tenant_id": tenant,
            "batch_size": 10,
            "stratified": False,
            "mark_consumed": True,
        },
    )
    assert batch_resp2.status_code == 200
    batch_data2 = batch_resp2.json()
    assert batch_data2["batch_size"] == 5

    # Buffer should now have 0 unconsumed items
    stats_resp2 = client.get(f"/api/v1/feedback/stats/{tenant}")
    assert stats_resp2.json()["unconsumed_count"] == 0


def test_dp_gradient_computation_and_noise_addition() -> None:
    """Verifies DP gradient calculation with calibrated Gaussian noise."""
    tenant = "bank_feedback_test_1"

    # Seed buffer
    client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_dp_01",
            "determination": "CONFIRMED_FRAUD",
        },
    )

    # Compute DP gradient under /api/v1/feedback/dp-gradient
    dp_resp1 = client.post(
        "/api/v1/feedback/dp-gradient",
        json={
            "tenant_id": tenant,
            "epsilon": 1.0,
            "delta": 1e-5,
            "clip_norm": 1.0,
        },
    )
    assert dp_resp1.status_code == 200
    dp_data1 = dp_resp1.json()
    assert dp_data1["tenant_id"] == tenant
    assert len(dp_data1["delta_weights"]) == 4
    assert dp_data1["sigma"] > 4.0
    assert dp_data1["epsilon"] == 1.0
    assert dp_data1["delta"] == 1e-5

    # Compute DP gradient under /v1/feedback/dp-gradient
    dp_resp2 = client.post(
        "/v1/feedback/dp-gradient",
        json={
            "tenant_id": tenant,
            "epsilon": 0.5,
            "delta": 1e-5,
            "clip_norm": 1.5,
        },
    )
    assert dp_resp2.status_code == 200
    dp_data2 = dp_resp2.json()
    assert dp_data2["sigma"] > dp_data1["sigma"]  # Lower epsilon -> higher noise scale


def test_clear_feedback_buffer() -> None:
    """Verifies DELETE /buffer/{tenant_id} resets the tenant buffer."""
    tenant = "bank_feedback_test_1"

    # Add item
    client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_clear_01",
            "determination": "CONFIRMED_FRAUD",
        },
    )

    # Clear via /api/v1/feedback/buffer/{tenant_id}
    clear_resp = client.delete(f"/api/v1/feedback/buffer/{tenant}")
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["tenant_id"] == tenant
    assert clear_data["cleared_count"] >= 1
    assert clear_data["status"] == "success"

    # Verify buffer is now empty
    stats_resp = client.get(f"/api/v1/feedback/stats/{tenant}")
    assert stats_resp.json()["total_count"] == 0


def test_privacy_and_input_validation() -> None:
    """Verifies zero-PII enforcement and RFC-compliant error responses."""
    # Missing both transaction_id_hash and alert_id -> 400 Bad Request
    resp_missing = client.post(
        "/api/v1/feedback/submit",
        json={"tenant_id": "bank_err", "determination": "CONFIRMED_FRAUD"},
    )
    assert resp_missing.status_code == 400
    assert "Either transaction_id_hash" in resp_missing.json()["detail"]

    # Raw IBAN identifier (>= 32 chars) in transaction_id_hash -> 400 Bad Request
    resp_iban = client.post(
        "/api/v1/feedback/submit",
        json={
            "tenant_id": "bank_err",
            "transaction_id_hash": "TR990001000123456789012345678901",
            "determination": "CONFIRMED_FRAUD",
        },
    )
    assert resp_iban.status_code == 400
    assert "matches raw PII format" in resp_iban.json()["detail"]

    # Short transaction_id_hash (< 32 chars) -> 400 Bad Request
    resp_short = client.post(
        "/api/v1/feedback/submit",
        json={
            "tenant_id": "bank_err",
            "transaction_id_hash": "short_hash_123",
            "determination": "CONFIRMED_FRAUD",
        },
    )
    assert resp_short.status_code == 400
    assert "must be an HMAC-SHA256 hash (>= 32 chars)" in resp_short.json()["detail"]

    # DP Epsilon > 2.0 -> 422 Unprocessable Entity
    resp_dp_eps = client.post(
        "/api/v1/feedback/dp-gradient",
        json={"tenant_id": "bank_err", "epsilon": 5.0},
    )
    assert resp_dp_eps.status_code == 422

    # Retraining batch_size < 1 -> 422 Unprocessable Entity
    resp_batch_size = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_err", "batch_size": 0},
    )
    assert resp_batch_size.status_code == 422
