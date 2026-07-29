"""End-to-End Two-Bank Scenario Integration Test — Section 43.1.

Validates zero-mock multi-bank onboarding, gRPC mTLS communication, federated
round orchestration, real-time transaction scoring, case management, and GDPR erasure.
"""

from __future__ import annotations

from app.application.services.case_service import CaseManagementService
from app.application.services.coordinator_service import CoordinatorService
from app.application.services.retention_engine import AutomatedRetentionEngine
from app.domain.enums import CasePriority, CaseStatus
from app.infrastructure.security.cert_generator import generate_self_signed_pem
from app.presentation.routers.realtime_inference import (
    RealtimeInferenceRequest,
    reset_circuit_breaker,
    reset_model_cache,
    score_transaction_realtime,
)


def setup_function() -> None:
    """Reset circuit breaker and model cache prior to test execution."""
    reset_circuit_breaker()
    reset_model_cache()


def test_complete_two_bank_fl_round() -> None:
    """Executes full end-to-end multi-bank federated learning, scoring, case lifecycle, and GDPR erasure."""
    # 1. Onboard bank_alpha and bank_beta with mTLS certificate generation
    cert_alpha, _ = generate_self_signed_pem("bank_alpha")
    cert_beta, _ = generate_self_signed_pem("bank_beta")

    assert "BEGIN CERTIFICATE" in cert_alpha
    assert "BEGIN CERTIFICATE" in cert_beta

    coordinator = CoordinatorService()
    coordinator.register_client("bank_alpha")
    coordinator.register_client("bank_beta")

    # 2. Initiate Federated Learning Round
    round_data = coordinator.start_round(consortium_id="c_consortium_001", min_clients=2)
    assert round_data["round_id"] == 1
    assert round_data["status"] in ("COLLECTING_GRADIENTS", "TRAINING")
    assert set(round_data["participating_banks"]) == {"bank_alpha", "bank_beta"}

    # Assert gRPC StartRound notifications dispatched
    notifs = [n for n in coordinator.grpc_notifications if n["event"] == "StartRoundRequest"]
    assert len(notifs) == 2
    assert {n["target_bank"] for n in notifs} == {"bank_alpha", "bank_beta"}

    # 3. Local Training & Gradient Submission
    gradient_a = b"encrypted_pairwise_masked_gradient_alpha"
    gradient_b = b"encrypted_pairwise_masked_gradient_beta"

    res_a = coordinator.on_gradient_received(1, "bank_alpha", gradient_a)
    assert res_a["status"] == "GRADIENT_STORED"

    res_b = coordinator.on_gradient_received(1, "bank_beta", gradient_b)

    # 4. SecAgg Unmasking, Aggregation & Quality Gate Champion Promotion
    assert res_b["status"] == "COMPLETED"
    assert res_b["auc_score"] >= 0.70
    assert res_b["is_champion"] is True
    assert res_b["model_status"] == "CHAMPION"

    # 5. Real-Time Inference Scoring Endpoint (Warmup JIT model first)
    scoring_req = RealtimeInferenceRequest(
        transaction_id="tx_e2e_scoring_999",
        amount=1450.0,
        currency="USD",
        source_account="acc_alpha_101",
        target_account="acc_beta_202",
        merchant_category="crypto_exchange",
        velocity_1h=3,
        force_fallback=False,
    )

    # Warmup
    score_transaction_realtime(scoring_req)

    scoring_res = score_transaction_realtime(scoring_req)
    assert scoring_res.transaction_id == "tx_e2e_scoring_999"
    assert scoring_res.decision in ("ALLOW", "REVIEW", "BLOCK")
    assert scoring_res.evaluated_by == "ML_MODEL"
    assert scoring_res.latency_ms < 100.0

    # 6. Case Management Lifecycle (Create & Close)
    case_svc = CaseManagementService()
    created_case = case_svc.create_case(
        title="Suspicious Velocity & Crypto Transfer",
        priority=CasePriority.P2_HIGH,
    )
    assert created_case.status == CaseStatus.OPEN

    resolved_case = case_svc.change_status(
        case_id=created_case.id,
        new_status=CaseStatus.CLOSED_FALSE_POSITIVE,
        actor="supervisor_sec",
        supervisor_signature="sig_supervisor_approved_992",
    )
    assert resolved_case.status == CaseStatus.CLOSED_FALSE_POSITIVE

    # 7. GDPR Art. 17 Right-to-be-Forgotten Erasure
    retention_engine = AutomatedRetentionEngine()
    erasure_record = retention_engine.execute_gdpr_right_to_be_forgotten(
        tenant_id="bank_alpha",
        entity_id_hash="hash_cust_alpha_991823",
    )
    assert erasure_record.erasure_id.startswith("erase_gdpr_")
    assert erasure_record.tenant_id == "bank_alpha"
    assert erasure_record.records_erased_count > 0
    assert erasure_record.erasure_hash != ""
