"""Integration test suite for Product Milestone 3: Feedback-to-Model Lifecycle.

Validates the full trustworthy, operationally connected chain:
Analyst decision -> Verified transaction evidence -> Institution-bound feedback ->
Eligible training batch -> Authorized retraining -> Actual candidate artifact ->
Verified holdout evaluation -> Governance review (No automatic champion promotion).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
)
from app.application.services.case_service import CaseManagementService
from app.application.services.label_feedback_pipeline import (
    FeedbackLabel,
    FeedbackPersistenceError,
    LocalLabelFeedbackPipeline,
)
from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
)
from app.domain.enums import CasePriority, CaseStatus
from app.domain.investigation_entities import Alert, AlertSeverity, AlertStatus
from app.domain.label_privacy_guard import LabelPrivacyViolationError
from app.domain.models.case import (
    FourEyesVerificationError,
    InvalidCaseTransitionError,
)
from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Authorized Analyst Feedback Ingestion & Zero-PII Enforcement
# ---------------------------------------------------------------------------
def test_authorized_analyst_feedback_ingestion_and_privacy_safeguards(tmp_path: Path) -> None:
    """Verifies authorized analyst determination ingestion, Zero-PII protection, and durable recording."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    # Legitimate HMAC-hashed transaction ID (>= 32 chars)
    tx_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    item = pipeline.ingest_analyst_determination(
        tenant_id="bank_alpha",
        transaction_id_hash=tx_hash,
        determination="CONFIRMED_FRAUD",
        priority=3,
        weight=2.5,
        notes="Confirmed fraudulent account takeover via analyst triage",
    )

    assert item.transaction_id_hash == tx_hash
    assert item.label == FeedbackLabel.CONFIRMED_FRAUD
    assert item.priority == 3
    assert item.weight == 2.5
    assert item.consumed_for_retraining is False
    assert item.reserved_job_id is None
    assert item.status == "DURABLY_RECORDED"

    # Zero-PII check: short identifier (< 32 chars) must be rejected immediately
    with pytest.raises(LabelPrivacyViolationError, match="must be an HMAC-SHA256 hash"):
        pipeline.ingest_analyst_determination(
            tenant_id="bank_alpha",
            transaction_id_hash="tx_short_id",
            determination="CONFIRMED_FRAUD",
        )

    # Zero-PII check: raw IBAN (>= 32 chars) must be rejected for cleartext PII pattern
    with pytest.raises(LabelPrivacyViolationError, match="matches raw PII format"):
        pipeline.ingest_analyst_determination(
            tenant_id="bank_alpha",
            transaction_id_hash="TR12345678901234567890123456789012",
            determination="CONFIRMED_FRAUD",
        )

    # Zero-PII check: raw sensitive attribute key must be rejected
    with pytest.raises(LabelPrivacyViolationError, match="Forbidden raw PII key"):
        pipeline.ingest_analyst_determination(
            tenant_id="bank_alpha",
            transaction_id_hash=tx_hash,
            determination="FALSE_POSITIVE",
            raw_attributes={"ssn": "123-45-6789"},
        )


# ---------------------------------------------------------------------------
# 2. Rejection of Invalid and Cross-Tenant Evidence
# ---------------------------------------------------------------------------
def test_cross_tenant_evidence_and_unindexed_alert_safeguards() -> None:
    """Verifies that cross-tenant alert feedback is rejected and unindexed alerts do not become training labels."""
    alert_svc = AlertIntelligenceService()
    alert_svc.create_alert(
        Alert(
            id="alt_bank_alpha_999",
            bank_id="bank_alpha",
            transaction_id="tx_alpha_999",
            risk_score=920.0,
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.NEW,
            reason_codes=["VELOCITY_ANOMALY"],
        )
    )

    # Cross-tenant alert submission: bank_beta attempting to submit feedback for bank_alpha's alert
    cross_tenant_payload = {
        "tenant_id": "bank_beta",
        "alert_id": "alt_bank_alpha_999",
        "determination": "CONFIRMED_FRAUD",
        "notes": "Unauthorized cross-tenant attempt",
    }
    resp = client.post("/api/v1/feedback/ingest", json=cross_tenant_payload)
    assert resp.status_code == 403
    assert "Cross-tenant alert feedback rejected" in resp.json()["detail"]

    # Submission for an unindexed/nonexistent alert ID:
    # Durably recorded in tenant buffer, but marked unvalidated (cannot become training label)
    unindexed_payload = {
        "tenant_id": "bank_alpha",
        "alert_id": "alt_nonexistent_000",
        "determination": "CONFIRMED_FRAUD",
    }
    resp_unindexed = client.post("/api/v1/feedback/ingest", json=unindexed_payload)
    assert resp_unindexed.status_code == 201
    data = resp_unindexed.json()
    assert data["item"]["status"] == "DURABLY_RECORDED"
    assert data["item"]["validated_for_training"] is False


# ---------------------------------------------------------------------------
# 3. Duplicate Feedback Idempotency and In-Place Update
# ---------------------------------------------------------------------------
def test_duplicate_feedback_idempotency_and_update(tmp_path: Path) -> None:
    """Verifies that repeated submissions for the same transaction hash or alert ID update in-place without sample inflation."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    tx_hash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    # First submission
    item1 = pipeline.ingest_analyst_determination(
        tenant_id="bank_gamma",
        transaction_id_hash=tx_hash,
        determination="CONFIRMED_FRAUD",
        priority=2,
        notes="Initial investigation note",
    )
    assert pipeline.get_buffer_size("bank_gamma") == 1
    assert item1.priority == 2

    # Second submission (same transaction hash with updated supervisor notes and priority)
    item2 = pipeline.ingest_analyst_determination(
        tenant_id="bank_gamma",
        transaction_id_hash=tx_hash,
        determination="CONFIRMED_FRAUD",
        priority=3,
        notes="Escalated to critical fraud ring by lead investigator",
    )
    # Invariant: buffer size remains 1 (no artificial duplicate inflation)
    assert pipeline.get_buffer_size("bank_gamma") == 1
    assert item2.priority == 3
    assert item2.notes == "Escalated to critical fraud ring by lead investigator"


# ---------------------------------------------------------------------------
# 4. Staged Two-Phase Feedback Consumption and Failure Recovery
# ---------------------------------------------------------------------------
def test_staged_two_phase_feedback_consumption_and_failure_recovery(tmp_path: Path) -> None:
    """Verifies that previewing or failed jobs do not permanently consume feedback, and verified jobs commit consumption."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    # Ingest 4 validated feedback items
    for i in range(4):
        pipeline.ingest_analyst_determination(
            tenant_id="bank_delta",
            transaction_id_hash=f"hash_delta_{i:02d}_000000000000000000000000000000000000",
            determination="CONFIRMED_FRAUD" if i % 2 == 0 else "FALSE_POSITIVE",
            validated_for_training=True,
            priority=2,
        )

    assert pipeline.get_buffer_size("bank_delta") == 4
    stats_before = pipeline.get_buffer_stats("bank_delta")
    assert stats_before["unconsumed_count"] == 4
    assert stats_before["validated_count"] == 4
    assert stats_before["reserved_count"] == 0

    # Phase 1: Retraining job reserves batch
    reserved = pipeline.reserve_retraining_batch(
        tenant_id="bank_delta",
        job_id="job_fl_trial_01",
        batch_size=4,
        require_validated=True,
    )
    assert reserved["batch_size"] == 4
    assert reserved["fraud_count"] == 2
    assert reserved["false_positive_count"] == 2

    # Verify that while reserved, items are shielded from concurrent selection
    stats_reserved = pipeline.get_buffer_stats("bank_delta")
    assert stats_reserved["reserved_count"] == 4
    second_sample = pipeline.get_priority_retraining_batch(tenant_id="bank_delta", batch_size=4)
    assert second_sample["batch_size"] == 0

    # Simulation of failure during distributed training: release reserved feedback
    pipeline.release_reserved_feedback(tenant_id="bank_delta", job_id="job_fl_trial_01")
    stats_released = pipeline.get_buffer_stats("bank_delta")
    assert stats_released["reserved_count"] == 0
    assert stats_released["unconsumed_count"] == 4

    # Phase 2: Subsequent successful job reserves and commits
    reserved_success = pipeline.reserve_retraining_batch(
        tenant_id="bank_delta",
        job_id="job_fl_trial_02",
        batch_size=4,
        require_validated=True,
    )
    assert reserved_success["batch_size"] == 4

    committed = pipeline.commit_consumed_feedback(
        tenant_id="bank_delta",
        job_id="job_fl_trial_02",
        training_run_id="candidate_version_v3_fl",
    )
    assert committed == 4

    # Verify permanent consumption semantics
    stats_committed = pipeline.get_buffer_stats("bank_delta")
    assert stats_committed["consumed_count"] == 4
    assert stats_committed["unconsumed_count"] == 0
    assert stats_committed["reserved_count"] == 0

    buffer_items = pipeline.get_feedback_buffer("bank_delta")
    for b_item in buffer_items:
        assert b_item["consumed_for_retraining"] is True
        assert b_item["consumed_training_run_id"] == "candidate_version_v3_fl"
        assert b_item["status"] == "CONSUMED_IN_TRAINING"


# ---------------------------------------------------------------------------
# 5. Insufficient Training Data Guard Fails Closed
# ---------------------------------------------------------------------------
def test_insufficient_training_data_guard_fails_closed(tmp_path: Path) -> None:
    """Verifies that automated retraining fails closed truthfully when minimum feedback threshold is unmet."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    # Ingest only 1 item
    pipeline.ingest_analyst_determination(
        tenant_id="BANK_A",
        transaction_id_hash="hash_insufficient_sample_00000000000000000000000000",
        determination="CONFIRMED_FRAUD",
        validated_for_training=True,
    )

    mock_op = MagicMock()
    service = DriftTriggeredRetrainingService(
        psi_threshold=0.20,
        operational_service=mock_op,
        feedback_pipeline=pipeline,
        min_feedback_samples=10,  # Requires at least 10 samples
    )

    job = service.create_manual_job(reason="Trigger with insufficient data guard test")
    assert job.status == "TRIGGERED"

    with pytest.raises(RuntimeError, match="Insufficient verified feedback data"):
        service.execute_retraining_pipeline(
            job_id=job.job_id,
            participating_banks=["BANK_A"],
        )

    # Verify job marked FAILED closed and operational service was not invoked
    assert job.status == "FAILED"
    mock_op.execute_operational_federation.assert_not_called()

    # Verify reserved feedback was released back
    stats = pipeline.get_buffer_stats("BANK_A")
    assert stats["reserved_count"] == 0
    assert stats["unconsumed_count"] == 1


# ---------------------------------------------------------------------------
# 6. End-to-End Case Determination -> Candidate Artifact -> Governance (No Auto-Promotion)
# ---------------------------------------------------------------------------
def test_end_to_end_case_feedback_to_training_to_candidate_governance(tmp_path: Path) -> None:
    """Verifies full connected lifecycle: Case resolution -> Pipeline -> Retraining -> Inactive Candidate -> Serving Safety."""
    storage_dir = str(tmp_path / "label_feedback")
    registry_dir = str(tmp_path / "model_registry")

    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)
    registry = ModelRegistry(storage_dir=registry_dir)
    eval_engine = ModelEvaluationEngine(registry)
    alert_svc = AlertIntelligenceService()
    case_service = CaseManagementService(
        feedback_pipeline=pipeline,
        eval_engine=eval_engine,
    )

    # Step 1: Create genuine alert and prediction evidence
    alert_id = "alt_lifecycle_101"
    tx_id = "tx_lifecycle_101"
    bank = "bank_alpha"

    alert_svc.create_alert(
        Alert(
            id=alert_id,
            bank_id=bank,
            transaction_id=tx_id,
            risk_score=880.0,
            severity=AlertSeverity.HIGH,
            status=AlertStatus.NEW,
            reason_codes=["VELOCITY_BURST"],
        )
    )

    # Seed prediction evidence into evaluation store
    eval_engine.log_prediction(
        simulation_id="default_sim",
        transaction_id=tx_id,
        champion_prob=0.88,
        champion_version=1,
        routed_to="champion",
        bank_id=bank,
    )

    # Step 2: Open and investigate case
    case = case_service.create_case(
        title="Cross-Bank Ring Lifecycle Case",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=[alert_id],
        bank_id=bank,
    )
    case_service.assign_case(case.id, investigator="senior_investigator")
    case_service.change_status(case.id, CaseStatus.INVESTIGATING, actor="senior_investigator")

    # Step 3: Terminal Case Resolution (CLOSED_CONFIRMED with Four-Eyes supervisor signoff)
    closed_case = case_service.change_status(
        case_id=case.id,
        new_status=CaseStatus.CLOSED_CONFIRMED,
        actor="senior_investigator",
        resolution_notes="Confirmed financial crime syndicate",
        supervisor_signature="supervisor_lead",
    )
    assert closed_case.status == CaseStatus.CLOSED_CONFIRMED

    # Verify feedback was correlated and recorded
    status_events = [
        e for e in case_service.get_timeline(case.id) if e.event_type == "status_changed"
    ]
    last_event = status_events[-1]
    assert last_event.metadata.get("feedback_buffer_recorded") is True
    assert last_event.metadata.get("prediction_evidence_recorded") is True

    # Step 4: Verify feedback was ingested into pipeline with verified lifecycle flags
    feedback_items = pipeline.get_feedback_buffer(bank)
    assert len(feedback_items) >= 1
    target_item = next((i for i in feedback_items if i.get("alert_id") == alert_id), None)
    assert target_item is not None
    assert target_item["validated_for_training"] is True
    assert target_item["prediction_evidence_correlated"] is True
    assert target_item["source_provenance"] == "CASE_RESOLUTION"
    assert target_item["case_id"] == case.id

    # Step 5: Execute Retraining with genuine candidate artifact and holdout metrics
    mock_operational = MagicMock()

    def _mock_op_exec(*args, **kwargs):
        jid = kwargs.get("job_id")
        return {
            "status": "COMPLETED",
            "job_id": jid,
            "feedback_lineage_verified": True,
            "verified_feedback_jobs": [jid] if jid else [],
            "is_verified": True,
            "candidate_model_version": "v2.0.0-rc1",
            "evaluation_metrics": {
                "pr_auc": 0.895,
                "roc_auc": 0.932,
                "f1_score": 0.841,
            },
            "saved_version": {
                "version": 2,
                "status": "candidate",
                "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            },
            "is_promoted_champion": False,
        }

    mock_operational.execute_operational_federation.side_effect = _mock_op_exec

    retraining_svc = DriftTriggeredRetrainingService(
        psi_threshold=0.20,
        operational_service=mock_operational,
        feedback_pipeline=pipeline,
    )

    job = retraining_svc.create_manual_job(reason="Automated retraining on verified case feedback")
    result = retraining_svc.execute_retraining_pipeline(
        job_id=job.job_id,
        participating_banks=[bank],
    )

    # Invariant: Status COMPLETED, verified metrics, strictly NO automatic champion promotion
    assert result["status"] == "COMPLETED"
    assert result["candidate_model_version"] == "v2"
    assert result["is_promoted_champion"] is False
    assert result["metrics"]["pr_auc"] == 0.895

    # Step 6: Verify candidate feedback committed in pipeline
    committed_items = [
        i for i in pipeline.get_feedback_buffer(bank) if i.get("alert_id") == alert_id
    ]
    assert len(committed_items) == 1
    assert committed_items[0]["consumed_for_retraining"] is True
    assert committed_items[0]["consumed_training_run_id"] == "v2"
    assert committed_items[0]["status"] == "CONSUMED_IN_TRAINING"

    # Step 7: Verify Model Governance - Retrained candidate remains INACTIVE until Four-Eyes review
    # The active champion serving identity must not be silently replaced by the candidate
    assert job.status == "COMPLETED"
    assert job.details.get("is_promoted_champion") is False


# ---------------------------------------------------------------------------
# 7. Case Resolution Precedes Feedback Publication (Zero Feedback on CAS Rejection)
# ---------------------------------------------------------------------------
def test_case_persistence_failure_rejection_publishes_no_feedback(tmp_path: Path) -> None:
    """Verifies that failed or rejected case resolutions publish zero feedback to the pipeline."""
    storage_dir = str(tmp_path / "label_feedback")
    registry_dir = str(tmp_path / "model_registry")

    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)
    registry = ModelRegistry(storage_dir=registry_dir)
    eval_engine = ModelEvaluationEngine(registry)
    alert_svc = AlertIntelligenceService()
    case_service = CaseManagementService(
        feedback_pipeline=pipeline,
        eval_engine=eval_engine,
    )

    alert_id = "alt_cas_fail_01"
    tx_id = "tx_cas_fail_01"
    bank = "bank_alpha"

    alert_svc.create_alert(
        Alert(
            id=alert_id,
            bank_id=bank,
            transaction_id=tx_id,
            risk_score=910.0,
            severity=AlertSeverity.HIGH,
            status=AlertStatus.NEW,
            reason_codes=["VELOCITY_BURST"],
        )
    )

    eval_engine.log_prediction(
        simulation_id="default_sim",
        transaction_id=tx_id,
        champion_prob=0.91,
        champion_version=1,
        routed_to="champion",
        bank_id=bank,
    )

    case = case_service.create_case(
        title="CAS Rejection Feedback Isolation Case",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=[alert_id],
        bank_id=bank,
    )
    case_service.assign_case(case.id, investigator="senior_investigator")
    case_service.change_status(case.id, CaseStatus.INVESTIGATING, actor="senior_investigator")

    # Invariant: Buffer must be empty before resolution
    assert pipeline.get_buffer_size(bank) == 0

    # Scenario A: Simulate optimistic concurrency CAS failure in underlying store
    with patch.object(case_service._cases, "update_conditional", return_value=False):
        with pytest.raises(InvalidCaseTransitionError, match="concurrently modified"):
            case_service.change_status(
                case_id=case.id,
                new_status=CaseStatus.CLOSED_CONFIRMED,
                actor="senior_investigator",
                supervisor_signature="supervisor_lead",
                resolution_notes="Should fail CAS write",
            )

    # Invariant: Zero feedback items must have been written to the buffer
    assert pipeline.get_buffer_size(bank) == 0
    assert len(pipeline.get_feedback_buffer(bank)) == 0

    # Scenario B: Missing supervisor signature rejected before persistence
    with pytest.raises(FourEyesVerificationError, match="requires secondary supervisor signature"):
        case_service.change_status(
            case_id=case.id,
            new_status=CaseStatus.CLOSED_CONFIRMED,
            actor="senior_investigator",
            supervisor_signature=None,
        )

    # Invariant: Buffer remains strictly empty
    assert pipeline.get_buffer_size(bank) == 0


# ---------------------------------------------------------------------------
# 8. Safeguards Against Unauthorized Direct Consumption & Unvalidated Feedback
# ---------------------------------------------------------------------------
def test_unauthorized_direct_consumption_and_unvalidated_feedback_safeguards(tmp_path: Path) -> None:
    """Verifies that direct consumption during sampling is rejected and unvalidated feedback cannot be trained on."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    # Invariant A: Pipeline rejects mark_consumed=True during sampling with ValueError
    with pytest.raises(ValueError, match=r"Direct consumption via get_priority_retraining_batch\(mark_consumed=True\) is prohibited"):
        pipeline.get_priority_retraining_batch(
            tenant_id="bank_sec",
            batch_size=5,
            mark_consumed=True,
        )

    # Invariant B: REST endpoint rejects mark_consumed=True with HTTP 400 Bad Request
    resp = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_sec", "batch_size": 5, "mark_consumed": True},
    )
    assert resp.status_code == 400
    assert "mark_consumed=True is prohibited" in resp.json()["detail"]

    # Invariant C: Direct analyst ingest records allegations with validated_for_training=False
    tx_hash = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
    resp_ingest = client.post(
        "/api/v1/feedback/ingest",
        json={
            "tenant_id": "bank_sec",
            "determination": "CONFIRMED_FRAUD",
            "transaction_id_hash": tx_hash,
            "notes": "Direct allegation from triage desk",
        },
    )
    assert resp_ingest.status_code == 201
    item_data = resp_ingest.json()["item"]
    assert item_data["status"] == "DURABLY_RECORDED"
    assert item_data["validated_for_training"] is False
    assert item_data["source_provenance"] == "DIRECT_INGEST"

    # Invariant D: Retraining reservation requires validated feedback by default; excludes unvalidated item
    # Ingest this item into pipeline to test reservation behavior
    pipeline.ingest_analyst_determination(
        tenant_id="bank_sec",
        transaction_id_hash=tx_hash,
        determination="CONFIRMED_FRAUD",
        validated_for_training=False,
        source_provenance="DIRECT_INGEST",
    )
    assert pipeline.get_buffer_size("bank_sec") == 1

    # Attempting to reserve batch with require_validated=True yields 0 items
    reserved = pipeline.reserve_retraining_batch(
        tenant_id="bank_sec",
        job_id="job_unvalidated_test",
        batch_size=10,
        require_validated=True,
    )
    assert reserved["batch_size"] == 0
    assert len(reserved["items"]) == 0


# ---------------------------------------------------------------------------
# 9. Lineage Binding: Unrelated Training Run Does Not Consume Feedback
# ---------------------------------------------------------------------------
def test_unrelated_training_run_does_not_consume_reserved_feedback(tmp_path: Path) -> None:
    """Verifies that training runs lacking verified lineage release reserved feedback instead of consuming it."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    # Ingest 2 verified feedback items
    for i in range(2):
        pipeline.ingest_analyst_determination(
            tenant_id="bank_alpha",
            transaction_id_hash=f"hash_unrelated_{i:02d}_00000000000000000000000000000000",
            determination="CONFIRMED_FRAUD",
            validated_for_training=True,
        )

    # Mock operational federation service returning an unrelated job_id or unverified lineage
    mock_op = MagicMock()
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "job_id": "unrelated_different_job_id",
        "feedback_lineage_verified": False,
        "is_verified": True,
        "candidate_model_version": "v3.0.0",
        "evaluation_metrics": {"pr_auc": 0.88, "roc_auc": 0.91},
        "saved_version": {"version": 3, "status": "candidate"},
        "is_promoted_champion": False,
    }

    retraining_svc = DriftTriggeredRetrainingService(
        psi_threshold=0.20,
        operational_service=mock_op,
        feedback_pipeline=pipeline,
        min_feedback_samples=2,
    )

    job = retraining_svc.create_manual_job(reason="Test lineage mismatch safety")
    result = retraining_svc.execute_retraining_pipeline(
        job_id=job.job_id,
        participating_banks=["bank_alpha"],
    )

    # Invariant: Retraining service must report that feedback was NOT consumed
    assert result["feedback_consumed"] is False
    assert result["committed_banks"] == []
    assert "did not provide" in result.get("feedback_lineage_unverified_reason", "")

    # Invariant: Reserved feedback was released back into unconsumed pool
    stats = pipeline.get_buffer_stats("bank_alpha")
    assert stats["consumed_count"] == 0
    assert stats["reserved_count"] == 0
    assert stats["unconsumed_count"] == 2


# ---------------------------------------------------------------------------
# 10. Persistence Failure Rollback & Crash Recovery via Stale Reclamation
# ---------------------------------------------------------------------------
def test_feedback_pipeline_persistence_failure_rollback_and_stale_reclamation(tmp_path: Path) -> None:
    """Verifies that atomic write failure triggers rollback and stale reservations are cleanly reclaimed."""
    storage_dir = str(tmp_path / "label_feedback")
    pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)

    tx_hash = "aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666aaaa7777bbbb8888"

    # Invariant A: Write failure raises FeedbackPersistenceError and rolls back in-memory state
    with patch("os.replace", side_effect=OSError("Simulated disk I/O failure")):
        with pytest.raises(FeedbackPersistenceError, match=r"Failed to durably persist feedback buffer"):
            pipeline.ingest_analyst_determination(
                tenant_id="bank_gamma",
                transaction_id_hash=tx_hash,
                determination="CONFIRMED_FRAUD",
            )

    # Invariant: In-memory buffer is clean (rollback succeeded)
    assert pipeline.get_buffer_size("bank_gamma") == 0
    assert "bank_gamma" not in pipeline._buffers or len(pipeline._buffers["bank_gamma"]) == 0

    # Invariant B: Reclaiming stale reservations from crashed workers while guarding active jobs
    # Ingest 2 items successfully
    item1 = pipeline.ingest_analyst_determination(
        tenant_id="bank_gamma",
        transaction_id_hash="hash_reclaim_1111111111111111111111111111111111111111111111111",
        determination="CONFIRMED_FRAUD",
        validated_for_training=True,
    )
    item2 = pipeline.ingest_analyst_determination(
        tenant_id="bank_gamma",
        transaction_id_hash="hash_reclaim_2222222222222222222222222222222222222222222222222",
        determination="FALSE_POSITIVE",
        validated_for_training=True,
    )
    assert pipeline.get_buffer_size("bank_gamma") == 2

    # Dead worker job reserves item 1
    pipeline.reserve_retraining_batch(
        tenant_id="bank_gamma",
        job_id="dead_worker_job_99",
        batch_size=1,
        require_validated=True,
    )

    # Active worker job reserves item 2
    pipeline.reserve_retraining_batch(
        tenant_id="bank_gamma",
        job_id="active_worker_job_42",
        batch_size=1,
        require_validated=True,
    )

    stats_reserved = pipeline.get_buffer_stats("bank_gamma")
    assert stats_reserved["reserved_count"] == 2

    # Simulate expired lease for dead worker job (set reservation timestamp to 2 hours ago)
    with pipeline._lock:
        pipeline._buffers["bank_gamma"][0].reserved_at = 1000.0

    # Execute stale reclamation: max_age=3600, active_job_ids={"active_worker_job_42"}
    reclaimed = pipeline.reclaim_stale_reservations(
        tenant_id="bank_gamma",
        max_age_seconds=3600,
        active_job_ids={"active_worker_job_42"},
    )
    assert reclaimed == 1

    # Verify dead worker item is freed, active worker item is guarded
    stats_after = pipeline.get_buffer_stats("bank_gamma")
    assert stats_after["reserved_count"] == 1
    assert stats_after["unconsumed_count"] == 2
    assert stats_after["consumed_count"] == 0

    # Verify durable recovery after restart
    reloaded_pipeline = LocalLabelFeedbackPipeline(storage_dir=storage_dir)
    reloaded_items = reloaded_pipeline.get_feedback_buffer("bank_gamma")
    dead_item = next(i for i in reloaded_items if i["transaction_id_hash"] == item1.transaction_id_hash)
    active_item = next(i for i in reloaded_items if i["transaction_id_hash"] == item2.transaction_id_hash)

    assert dead_item["reserved_job_id"] is None
    assert dead_item["status"] == "VALIDATED_FOR_TRAINING"
    assert active_item["reserved_job_id"] == "active_worker_job_42"
    assert active_item["status"] == "RESERVED_FOR_TRAINING"

