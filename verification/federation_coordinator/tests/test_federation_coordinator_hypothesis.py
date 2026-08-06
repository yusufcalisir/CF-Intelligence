"""Hypothesis Property-Based Test Suite for Federation Coordinator Subsystem.

Verifies distributed system and orchestration invariants across hundreds of randomized scenarios:
  Invariant 1: Client Registration Uniqueness & Identity Count
  Invariant 2: Virtual Batch Size Invariant (B * A >= min(32, B_base))
  Invariant 3: Liveness Eviction Threshold (t - t_last > 15s => OFFLINE)
  Invariant 4: Round ID Strict Monotonicity & Notification Count
  Invariant 5: Quorum Aggregation Triggering & State Transition
  Invariant 6: Quality Gate Model Status Promotion Branching
"""

from __future__ import annotations

import sys
import pytest
import numpy as np
from hypothesis import given, settings, strategies as st

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.coordinator_service import CoordinatorService

# Strategies for generating randomized inputs
bank_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_'),
    min_size=1,
    max_size=20,
)

hardware_strategy = st.sampled_from(["cuda", "cpu", "tpu", "mps"])


# =====================================================================
# Invariant 1: Client Registration Uniqueness & Identity Count
# =====================================================================

@given(
    bank_ids=st.lists(bank_id_strategy, min_size=1, max_size=30),
    ram_values=st.lists(st.floats(min_value=1.0, max_value=256.0), min_size=1, max_size=30),
)
@settings(deadline=None, max_examples=100)
def test_inv1_client_registration_uniqueness(bank_ids: list[str], ram_values: list[float]):
    """Invariant 1: Registry length equals unique lowercased bank IDs registered."""
    coord = CoordinatorService()
    unique_expected = set()

    for bid, ram in zip(bank_ids, ram_values):
        clean_bid = bid.lower().strip()
        res = coord.register_client(bank_id=bid, ram_gb=ram)
        if res["registered"]:
            unique_expected.add(clean_bid)

    assert len(coord.registry) == len(unique_expected), (
        f"Registry size {len(coord.registry)} != unique count {len(unique_expected)}"
    )


# =====================================================================
# Invariant 2: Dynamic Hyperparameter Virtual Batch Invariant
# =====================================================================

@given(
    base_batch=st.integers(min_value=16, max_value=512),
    base_epochs=st.integers(min_value=1, max_value=20),
    ram_gb=st.floats(min_value=1.0, max_value=256.0),
    hardware=hardware_strategy,
)
@settings(deadline=None, max_examples=100)
def test_inv2_virtual_batch_size_invariant(base_batch: int, base_epochs: int, ram_gb: float, hardware: str):
    """Invariant 2: Virtual batch size B_negotiated * A_negotiated >= min(32, B_base)."""
    coord = CoordinatorService()
    bank_id = "bank_inv2"
    coord.register_client(bank_id=bank_id, hardware_type=hardware, ram_gb=ram_gb)

    neg = coord.negotiate_parameters(bank_id, base_batch_size=base_batch, base_epochs=base_epochs)

    virtual_batch = neg.batch_size * neg.gradient_accumulation_steps
    target_min = min(32, base_batch)
    assert virtual_batch >= target_min, (
        f"Virtual batch size {virtual_batch} < target minimum {target_min}"
    )
    assert neg.batch_size >= 16, f"Negotiated batch size {neg.batch_size} < 16"
    assert neg.local_epochs >= 1, f"Negotiated local epochs {neg.local_epochs} < 1"


# =====================================================================
# Invariant 3: Liveness Eviction Threshold Invariant
# =====================================================================

@given(
    time_deltas=st.lists(st.floats(min_value=0.0, max_value=60.0), min_size=1, max_size=20),
)
@settings(deadline=None, max_examples=100)
def test_inv3_liveness_eviction_threshold(time_deltas: list[float]):
    """Invariant 3: Clients with heartbeat age > 15s are excluded from get_active_clients()."""
    coord = CoordinatorService(heartbeat_timeout_seconds=15.0)

    import time
    now = time.time()
    active_expected = []

    for i, delta in enumerate(time_deltas):
        bid = f"bank_liveness_{i}"
        coord.register_client(bid)
        coord.registry[bid].last_heartbeat = now - delta
        if delta < 14.0:  # Safety margin for test execution time
            active_expected.append(bid)

    active_clients = coord.get_active_clients()
    active_ids = [c.bank_id for c in active_clients]

    for bid in active_expected:
        assert bid in active_ids, f"Active bank {bid} (age < 14s) missing from active clients"

    for client in active_clients:
        age = now - client.last_heartbeat
        assert age <= 16.0, f"Client {client.bank_id} has heartbeat age {age:.1f}s > 16.0s"


# =====================================================================
# Invariant 4: Round ID Strict Monotonicity & Notification Count
# =====================================================================

@given(
    round_count=st.integers(min_value=1, max_value=15),
    client_count=st.integers(min_value=1, max_value=10),
)
@settings(deadline=None, max_examples=100)
def test_inv4_round_id_monotonicity_and_notifications(round_count: int, client_count: int):
    """Invariant 4: Round IDs increase strictly by +1; StartRound notifications count == active clients."""
    coord = CoordinatorService()
    for i in range(client_count):
        coord.register_client(f"bank_round_{i}")

    prev_round = 0
    for r in range(round_count):
        rnd = coord.start_round(min_clients=1)
        assert rnd["round_id"] == prev_round + 1, (
            f"Round ID {rnd['round_id']} not monotonic increment from {prev_round}"
        )
        prev_round = rnd["round_id"]

        # Check notification count for this round
        notifs_for_round = [
            n for n in coord.grpc_notifications
            if n.get("event") == "StartRoundRequest" and n.get("round_id") == rnd["round_id"]
        ]
        assert len(notifs_for_round) == client_count, (
            f"Expected {client_count} notifications for round {rnd['round_id']}, got {len(notifs_for_round)}"
        )


# =====================================================================
# Invariant 5: Quorum Aggregation Triggering & State Transition
# =====================================================================

@given(
    min_clients=st.integers(min_value=2, max_value=10),
    submissions_count=st.integers(min_value=0, max_value=15),
)
@settings(deadline=None, max_examples=100)
def test_inv5_quorum_aggregation_state_transition(min_clients: int, submissions_count: int):
    """Invariant 5: Round state transitions to COMPLETED iff submitted count >= min_clients."""
    coord = CoordinatorService()
    for i in range(max(min_clients, submissions_count)):
        coord.register_client(f"bank_quorum_{i}")

    rnd = coord.start_round(min_clients=min_clients)
    round_id = rnd["round_id"]

    for i in range(submissions_count):
        resp = coord.on_gradient_received(round_id, f"bank_quorum_{i}", f"grad_{i}".encode())
        if i == min_clients - 1:
            # At exact quorum point, response status must be COMPLETED
            assert resp["status"] == "COMPLETED"

    if submissions_count >= min_clients:
        assert coord.rounds[round_id]["status"] == "COMPLETED", (
            f"Round status should be COMPLETED for submissions {submissions_count} >= min {min_clients}"
        )
    else:
        assert coord.rounds[round_id]["status"] == "COLLECTING_GRADIENTS", (
            f"Round status should remain COLLECTING_GRADIENTS for submissions {submissions_count} < min {min_clients}"
        )


# =====================================================================
# Invariant 6: Quality Gate Model Status Promotion Branching
# =====================================================================

@given(
    threshold=st.floats(min_value=0.50, max_value=0.95),
    auc_score=st.floats(min_value=0.00, max_value=1.00),
)
@settings(deadline=None, max_examples=100)
def test_inv6_quality_gate_promotion_branching(threshold: float, auc_score: float):
    """Invariant 6: is_champion == True and status == CHAMPION iff auc_score >= threshold."""
    coord = CoordinatorService()
    coord.register_client("bank_qg")

    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]
    coord.on_gradient_received(round_id, "bank_qg", b"grad_bytes")

    res = coord.aggregate_and_deploy(round_id, min_auc_threshold=threshold, mock_auc=auc_score)

    expected_champion = (auc_score >= threshold)
    assert res["is_champion"] == expected_champion, (
        f"is_champion {res['is_champion']} != expected {expected_champion} for AUC {auc_score:.4f} vs thresh {threshold:.4f}"
    )
    expected_status = "CHAMPION" if expected_champion else "REJECTED_LOW_AUC"
    assert res["model_status"] == expected_status, (
        f"model_status {res['model_status']} != expected {expected_status}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
