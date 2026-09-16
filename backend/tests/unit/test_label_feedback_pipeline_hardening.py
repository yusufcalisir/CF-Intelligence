"""Hardened Unit Tests for Local Label Feedback Pipeline & Priority Retraining Store (Stage 44 / Phase 63)."""

from __future__ import annotations

import concurrent.futures
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService
from app.application.services.label_feedback_pipeline import (
    FeedbackLabel,
    LocalLabelFeedbackPipeline,
)
from app.domain.enums import CasePriority, CaseStatus
from app.main import app

client = TestClient(app)


@pytest.fixture
def temp_storage_dir():
    temp_dir = tempfile.mkdtemp(prefix="cfi_feedback_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_ingest_with_alert_id_auto_hashing_satisfies_zero_pii(temp_storage_dir: str) -> None:
    """Verifies that arbitrary short alert IDs are deterministically hashed to HMAC-SHA256 >= 32 chars."""
    pipeline = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)

    item = pipeline.ingest_analyst_determination(
        tenant_id="bank_test_1",
        alert_id="alt_short_99",
        determination="CONFIRMED_FRAUD",
        notes="Confirmed structuring ring",
    )

    assert len(item.transaction_id_hash) >= 32
    assert item.label == FeedbackLabel.CONFIRMED_FRAUD
    assert item.priority == 3  # Auto-calibrated critical priority for confirmed fraud
    assert item.weight == 2.0  # Auto-calibrated loss weight
    assert item.notes == "Confirmed structuring ring"
    assert pipeline.get_buffer_size("bank_test_1") == 1


def test_priority_queueing_and_weighted_retraining_batch_sampling(temp_storage_dir: str) -> None:
    """Verifies that retraining batch sampling respects priority ordering (3 -> 2 -> 1)."""
    pipeline = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)
    tenant = "bank_test_2"

    # Ingest standard priority items (priority 1)
    for i in range(5):
        pipeline.ingest_analyst_determination(
            tenant_id=tenant,
            alert_id=f"alt_standard_{i}",
            determination="FALSE_POSITIVE",
            priority=1,
        )

    # Ingest critical priority item (priority 3)
    critical_item = pipeline.ingest_analyst_determination(
        tenant_id=tenant,
        alert_id="alt_critical_mule",
        determination="CONFIRMED_FRAUD",
        priority=3,
        weight=5.0,
    )

    # Sample batch with unstratified ordering
    batch_res = pipeline.get_priority_retraining_batch(
        tenant_id=tenant,
        batch_size=2,
        mark_consumed=True,
        stratified=False,
    )

    assert batch_res["batch_size"] == 2
    items = batch_res["items"]
    # First item in batch must be the highest priority item
    assert items[0]["transaction_id_hash"] == critical_item.transaction_id_hash
    assert items[0]["priority"] == 3
    assert items[0]["consumed_for_retraining"] is True

    # Next batch should not include the already consumed critical item
    next_batch = pipeline.get_priority_retraining_batch(tenant_id=tenant, batch_size=2, stratified=False)
    for it in next_batch["items"]:
        assert it["transaction_id_hash"] != critical_item.transaction_id_hash


def test_stratified_batch_sampling_with_class_balance(temp_storage_dir: str) -> None:
    """Verifies stratified batch sampling balances fraud and false positive samples."""
    pipeline = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)
    tenant = "bank_test_3"

    # Ingest 10 false positives and 2 confirmed frauds (imbalanced)
    for i in range(10):
        pipeline.ingest_analyst_determination(
            tenant_id=tenant,
            alert_id=f"fp_alert_{i}",
            determination="FALSE_POSITIVE",
        )
    for i in range(2):
        pipeline.ingest_analyst_determination(
            tenant_id=tenant,
            alert_id=f"fraud_alert_{i}",
            determination="CONFIRMED_FRAUD",
        )

    batch_res = pipeline.get_priority_retraining_batch(
        tenant_id=tenant,
        batch_size=6,
        stratified=True,
    )

    assert batch_res["batch_size"] == 6
    assert batch_res["fraud_count"] == 2  # Both available fraud cases included
    assert batch_res["false_positive_count"] == 4  # Filled up to batch size


def test_thread_safe_concurrent_feedback_ingestion(temp_storage_dir: str) -> None:
    """Verifies that concurrent ingestion across multiple threads is completely race-free."""
    pipeline = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)
    tenant = "bank_concurrent"
    num_threads = 20

    def ingest_worker(idx: int) -> None:
        pipeline.ingest_analyst_determination(
            tenant_id=tenant,
            alert_id=f"concurrent_tx_{idx:03d}",
            determination="CONFIRMED_FRAUD" if idx % 2 == 0 else "FALSE_POSITIVE",
            priority=2,
            auto_persist=False,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(ingest_worker, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert pipeline.get_buffer_size(tenant) == num_threads
    stats = pipeline.get_buffer_stats(tenant)
    assert stats["total_count"] == num_threads
    assert stats["fraud_count"] == 10
    assert stats["false_positive_count"] == 10


def test_buffer_persistence_and_atomic_file_restore(temp_storage_dir: str) -> None:
    """Verifies atomic on-disk persistence and clean restoration across process lifecycles."""
    pipeline_a = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)
    tenant = "bank_persistence"

    pipeline_a.ingest_analyst_determination(
        tenant_id=tenant,
        alert_id="alt_persist_01",
        determination="CONFIRMED_FRAUD",
        notes="Saved to disk",
        auto_persist=True,
    )
    pipeline_a.ingest_analyst_determination(
        tenant_id=tenant,
        alert_id="alt_persist_02",
        determination="FALSE_POSITIVE",
        auto_persist=True,
    )

    expected_file = Path(temp_storage_dir) / tenant / "label_buffer.json"
    assert expected_file.exists()

    # Create fresh pipeline pointing to same storage and load
    pipeline_b = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=42)
    assert pipeline_b.get_buffer_size(tenant) == 0
    loaded_count = pipeline_b.load_from_disk(tenant)

    assert loaded_count == 2
    assert pipeline_b.get_buffer_size(tenant) == 2
    stats = pipeline_b.get_buffer_stats(tenant)
    assert stats["fraud_count"] == 1
    assert stats["false_positive_count"] == 1

    # Clear buffer should remove file
    pipeline_b.clear_buffer(tenant)
    assert not expected_file.exists()
    assert pipeline_b.get_buffer_size(tenant) == 0


def test_dp_gradient_analytical_sigma_and_gaussian_noise(temp_storage_dir: str) -> None:
    """Verifies analytical DP noise scale calculation and gradient delta computation."""
    pipeline = LocalLabelFeedbackPipeline(storage_dir=temp_storage_dir, seed=123)
    tenant = "bank_dp_eval"

    for i in range(4):
        pipeline.ingest_analyst_determination(
            tenant_id=tenant,
            alert_id=f"dp_alt_{i}",
            determination="CONFIRMED_FRAUD",
            weight=1.5,
        )

    res = pipeline.compute_dp_gradient_update(
        tenant_id=tenant,
        epsilon=1.0,
        delta=1e-5,
        clip_norm=1.0,
    )

    assert res["tenant_id"] == tenant
    assert res["sample_count"] == 4
    assert res["epsilon"] == 1.0
    assert res["delta"] == 1e-5
    # Analytical sigma = 1.0 * sqrt(2 * ln(1.25 / 1e-5)) / 1.0 = ~4.84
    assert res["sigma"] > 4.5
    assert len(res["delta_weights"]) == 4
    for w in res["delta_weights"]:
        assert isinstance(w, float)


def test_end_to_end_case_service_real_feedback_ingestion() -> None:
    """Verifies that CaseManagementService terminal transitions record feedback into pipeline without error."""
    case_service = CaseManagementService()
    case = case_service.create_case(
        title="Automated Retraining Integration Test",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=["alt_integration_777"],
    )
    case_service.assign_case(case.id, investigator="investigator_john")
    case_service.change_status(case.id, CaseStatus.INVESTIGATING, actor="investigator_john")

    # Close confirmed with secondary supervisor signature
    closed_case = case_service.change_status(
        case.id,
        CaseStatus.CLOSED_CONFIRMED,
        actor="investigator_john",
        supervisor_signature="supervisor_diane",
    )

    assert closed_case.status == CaseStatus.CLOSED_CONFIRMED
    timeline = case_service.get_timeline(case.id)
    status_events = [e for e in timeline if e.event_type == "status_changed"]
    assert len(status_events) > 0
    last_event = status_events[-1]
    assert last_event.metadata.get("retraining_feedback_recorded") is True
    assert last_event.metadata.get("retraining_feedback_label") == 1


def test_feedback_router_ingest_and_stats_endpoints() -> None:
    """Verifies REST endpoints /api/v1/feedback/ingest and /api/v1/feedback/stats."""
    tenant = "bank_api_test"
    payload = {
        "tenant_id": tenant,
        "alert_id": "alt_api_555",
        "determination": "CONFIRMED_FRAUD",
        "priority": 3,
        "notes": "API test verification",
    }

    resp = client.post("/api/v1/feedback/ingest", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "success"
    assert data["item"]["label"] == "CONFIRMED_FRAUD"
    assert data["item"]["priority"] == 3

    stats_resp = client.get(f"/api/v1/feedback/stats/{tenant}")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["tenant_id"] == tenant
    assert stats["total_count"] >= 1
    assert stats["fraud_count"] >= 1


def test_feedback_router_retraining_batch_and_dp_gradient_endpoints() -> None:
    """Verifies REST endpoints /api/v1/feedback/retraining-batch and /api/v1/feedback/dp-gradient."""
    tenant = "bank_api_batch"
    # Seed feedback
    client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_batch_001",
            "determination": "CONFIRMED_FRAUD",
        },
    )
    client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": tenant,
            "alert_id": "alt_batch_002",
            "determination": "FALSE_POSITIVE",
        },
    )

    batch_resp = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": tenant, "batch_size": 10, "stratified": True},
    )
    assert batch_resp.status_code == 200
    batch_data = batch_resp.json()
    assert batch_data["tenant_id"] == tenant
    assert batch_data["batch_size"] >= 2
    assert batch_data["fraud_count"] >= 1
    assert batch_data["false_positive_count"] >= 1

    grad_resp = client.post(
        "/api/v1/feedback/dp-gradient",
        json={"tenant_id": tenant, "epsilon": 1.0, "delta": 1e-5},
    )
    assert grad_resp.status_code == 200
    grad_data = grad_resp.json()
    assert grad_data["tenant_id"] == tenant
    assert len(grad_data["delta_weights"]) == 4
    assert grad_data["sigma"] > 4.0


def test_invalid_inputs_and_privacy_violation_rejection() -> None:
    """Verifies that missing identifiers and cleartext PII violations are rejected with HTTP 400."""
    # 1. Missing both transaction_id_hash and alert_id -> 400
    resp1 = client.post(
        "/api/v1/feedback/ingest",
        json={"tenant_id": "bank_err", "determination": "CONFIRMED_FRAUD"},
    )
    assert resp1.status_code == 400
    assert "Either transaction_id_hash" in resp1.json()["detail"]

    # 2. Raw IBAN identifier -> 400
    resp2 = client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": "bank_err",
            "transaction_id_hash": "TR990001000123456789012345678901",
            "determination": "CONFIRMED_FRAUD",
        },
    )
    assert resp2.status_code == 400
    assert "matches raw PII format" in resp2.json()["detail"]

    # 3. Invalid DP epsilon > 2.0 -> 422 Unprocessable Entity
    resp3 = client.post(
        "/api/v1/feedback/dp-gradient",
        json={"tenant_id": "bank_err", "epsilon": 5.0},
    )
    assert resp3.status_code == 422
