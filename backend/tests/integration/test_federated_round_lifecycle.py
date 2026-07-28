"""Integration tests for Section 41.2: Federated Round Lifecycle — Real Coordinator Orchestration."""

from __future__ import annotations

from app.application.services.coordinator_service import CoordinatorService


def test_round_starts_and_notifies_banks() -> None:
    """Verifies starting FL round dispatches StartRoundRequest gRPC notifications to active banks."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")

    round_data = svc.start_round(consortium_id="c_test", min_clients=3)

    assert round_data["round_id"] == 1
    assert round_data["status"] == "COLLECTING_GRADIENTS"
    assert len(round_data["participating_banks"]) == 3

    notifs = [n for n in svc.grpc_notifications if n["event"] == "StartRoundRequest"]
    assert len(notifs) == 3
    assert {n["target_bank"] for n in notifs} == {"bank_alpha", "bank_beta", "bank_gamma"}


def test_quorum_triggers_aggregation() -> None:
    """Verifies submitting 3 gradients meets quorum threshold and triggers aggregation."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")
    svc.start_round(consortium_id="c_test", min_clients=3)

    res1 = svc.on_gradient_received(1, "bank_alpha", b"gradient_a")
    assert res1["status"] == "GRADIENT_STORED"

    res2 = svc.on_gradient_received(1, "bank_beta", b"gradient_b")
    assert res2["status"] == "GRADIENT_STORED"

    res3 = svc.on_gradient_received(1, "bank_gamma", b"gradient_c")
    assert res3["status"] == "COMPLETED"
    assert res3["is_champion"] is True


def test_aggregation_produces_global_model() -> None:
    """Verifies running full aggregation cycle completes round and produces AUC metric."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")
    svc.start_round(consortium_id="c_test", min_clients=3)

    svc.on_gradient_received(1, "bank_alpha", b"grad_a")
    svc.on_gradient_received(1, "bank_beta", b"grad_b")
    res = svc.on_gradient_received(1, "bank_gamma", b"grad_c")

    assert res["status"] == "COMPLETED"
    assert res["auc_score"] >= 0.70
    assert res["is_champion"] is True
    assert res["model_status"] == "CHAMPION"


def test_low_auc_blocks_promotion() -> None:
    """Verifies that model with AUC below 0.70 threshold fails Quality Gate and blocks champion promotion."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")
    svc.start_round(consortium_id="c_test", min_clients=3)

    svc.on_gradient_received(1, "bank_alpha", b"grad_a")
    svc.on_gradient_received(1, "bank_beta", b"grad_b")
    svc.gradient_submissions[1]["bank_gamma"] = b"grad_c"

    # Aggregation with AUC=0.50 (< 0.70 threshold)
    res = svc.aggregate_and_deploy(1, min_auc_threshold=0.70, mock_auc=0.50)

    assert res["status"] == "COMPLETED"
    assert res["auc_score"] == 0.50
    assert res["is_champion"] is False
    assert res["model_status"] == "REJECTED_LOW_AUC"


def test_round_complete_notification_sent() -> None:
    """Verifies that completing a round dispatches RoundCompleteNotification gRPC messages to all banks."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")
    svc.start_round(consortium_id="c_test", min_clients=3)

    svc.on_gradient_received(1, "bank_alpha", b"grad_a")
    svc.on_gradient_received(1, "bank_beta", b"grad_b")
    svc.on_gradient_received(1, "bank_gamma", b"grad_c")

    comp_notifs = [n for n in svc.grpc_notifications if n["event"] == "RoundCompleteNotification"]
    assert len(comp_notifs) == 3
    assert {n["target_bank"] for n in comp_notifs} == {"bank_alpha", "bank_beta", "bank_gamma"}
