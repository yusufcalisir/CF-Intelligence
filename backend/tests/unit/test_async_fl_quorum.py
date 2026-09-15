"""Unit tests for Asynchronous FL Engine & Dynamic Quorum Timeout Manager (Section 6.2)."""

from __future__ import annotations

import datetime

import numpy as np

from app.domain.async_fl_engine import AsyncFLEngine, staleness_attenuation
from app.domain.quorum_manager import DynamicQuorumManager, QuorumState


def test_staleness_attenuation_calculation() -> None:
    """Verifies staleness attenuation factor S(tau) = (1 + tau)^(-alpha)."""
    assert staleness_attenuation(tau=0, alpha=0.5) == 1.0
    assert round(staleness_attenuation(tau=1, alpha=0.5), 4) == 0.7071
    assert round(staleness_attenuation(tau=3, alpha=0.5), 4) == 0.5000


def test_async_fl_engine_parameter_update() -> None:
    """Verifies AsyncFLEngine accumulates asynchronous updates with staleness weighting."""
    engine = AsyncFLEngine(current_round=5, alpha_staleness=0.5, learning_rate=1.0)

    global_w = {"fc": np.array([10.0, 20.0])}
    engine.set_global_weights(global_w)

    client_w = {"fc": np.array([20.0, 40.0])}
    # Update from round 1 -> tau = 4 -> s(tau) = (1+4)^(-0.5) = 1 / sqrt(5) ~= 0.4472
    new_w = engine.apply_async_update(
        node_id="bank_slow",
        submitted_round=1,
        client_weights=client_w,
        sample_count=100,
    )

    assert "fc" in new_w
    # Verify global weights moved toward client weights proportionally to attenuated alpha
    assert new_w["fc"][0] > 10.0
    assert new_w["fc"][0] < 20.0


def test_dynamic_quorum_manager_threshold_triggering() -> None:
    """Verifies DynamicQuorumManager triggers QUORUM_REACHED state when >= 60% nodes submit."""
    manager = DynamicQuorumManager(quorum_threshold_pct=0.60, target_window_seconds=300)

    nodes = ["bank_a", "bank_b", "bank_c", "bank_d", "bank_e"]  # 5 nodes
    manager.register_nodes(nodes)

    # 1 node = 20% -> WAITING
    state1 = manager.record_node_submission("bank_a")
    assert state1 == QuorumState.WAITING

    # 2 nodes = 40% -> WAITING
    state2 = manager.record_node_submission("bank_b")
    assert state2 == QuorumState.WAITING

    # 3 nodes = 60% -> QUORUM_REACHED
    state3 = manager.record_node_submission("bank_c")
    assert state3 == QuorumState.QUORUM_REACHED


def test_dynamic_quorum_manager_timeout_expiration() -> None:
    """Verifies DynamicQuorumManager transitions to TIMEOUT_EXPIRED when target window elapses."""
    manager = DynamicQuorumManager(quorum_threshold_pct=0.60, target_window_seconds=10)

    nodes = ["bank_a", "bank_b", "bank_c", "bank_d", "bank_e"]
    manager.register_nodes(nodes)

    # Force start time to 20 seconds in the past to simulate timeout
    manager.round_start_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=20)

    status = manager.evaluate_quorum_status()
    assert status.state == QuorumState.TIMEOUT_EXPIRED
    assert status.time_remaining_seconds == 0.0


def test_staleness_functions_multi_formulations() -> None:
    """Verifies exponential, constant, and hinge staleness attenuation formulations."""
    from app.domain.async_fl_engine import StalenessFunction

    # Constant
    assert staleness_attenuation(tau=5, func=StalenessFunction.CONSTANT) == 1.0

    # Exponential: exp(-0.5 * 2) = exp(-1) ~= 0.367879
    exp_val = staleness_attenuation(tau=2, alpha=0.5, func=StalenessFunction.EXPONENTIAL)
    assert round(exp_val, 4) == round(float(np.exp(-1.0)), 4)

    # Hinge: tau <= 2 => 1.0, tau = 4 => 1 / (0.5 * 2 + 1) = 0.5
    assert staleness_attenuation(tau=1, func=StalenessFunction.HINGE) == 1.0
    assert staleness_attenuation(tau=2, func=StalenessFunction.HINGE) == 1.0
    assert staleness_attenuation(tau=4, alpha=0.5, func=StalenessFunction.HINGE) == 0.5


def test_staleness_max_staleness_cutoff() -> None:
    """Verifies updates with tau > max_staleness yield 0 attenuation and are dropped."""
    engine = AsyncFLEngine(current_round=20, max_staleness=10, learning_rate=1.0)
    base_w = {"w1": np.array([5.0, 10.0])}
    engine.set_global_weights(base_w)

    # tau = 20 - 5 = 15 > max_staleness (10)
    stale_client_w = {"w1": np.array([100.0, 200.0])}
    res_w = engine.apply_async_update(
        node_id="bank_very_slow",
        submitted_round=5,
        client_weights=stale_client_w,
    )

    # Weights must remain completely unchanged
    np.testing.assert_array_equal(res_w["w1"], base_w["w1"])
    assert engine.dropped_updates == 1
    metrics = engine.get_staleness_metrics()
    assert metrics["dropped_updates"] == 1


def test_async_fl_engine_rejects_nan_inf_weights() -> None:
    """Verifies Byzantine non-finite weights raise ValueError and leave global weights unpoisoned."""
    import pytest

    engine = AsyncFLEngine(current_round=5)
    clean_w = {"fc": np.array([1.0, 2.0])}
    engine.set_global_weights(clean_w)

    nan_w = {"fc": np.array([1.0, np.nan])}
    inf_w = {"fc": np.array([np.inf, 2.0])}

    with pytest.raises(ValueError, match="non-finite"):
        engine.apply_async_update("bank_byzantine_1", submitted_round=5, client_weights=nan_w)

    with pytest.raises(ValueError, match="non-finite"):
        engine.apply_async_update("bank_byzantine_2", submitted_round=5, client_weights=inf_w)

    # Global weights untainted
    np.testing.assert_array_equal(engine.get_global_weights()["fc"], np.array([1.0, 2.0]))


def test_async_fl_engine_rejects_layer_shape_mismatch() -> None:
    """Verifies layer dimension mismatch raises ValueError."""
    import pytest

    engine = AsyncFLEngine(current_round=5)
    engine.set_global_weights({"fc": np.array([[1.0, 2.0], [3.0, 4.0]])})

    mismatched_w = {"fc": np.array([1.0, 2.0])}  # 1D instead of 2D
    with pytest.raises(ValueError, match="Layer shape mismatch"):
        engine.apply_async_update("bank_bad_shape", submitted_round=5, client_weights=mismatched_w)


def test_async_fl_engine_thread_safety_concurrent_updates() -> None:
    """Verifies AsyncFLEngine handles 20 concurrent update threads without race conditions."""
    import concurrent.futures

    engine = AsyncFLEngine(current_round=10, alpha_staleness=0.5, learning_rate=0.5)
    engine.set_global_weights({"dense": np.zeros(100, dtype=np.float32)})

    def submit_worker(worker_id: int) -> None:
        client_w = {"dense": np.full(100, float(worker_id), dtype=np.float32)}
        engine.apply_async_update(
            node_id=f"worker_{worker_id}",
            submitted_round=max(1, 10 - (worker_id % 5)),
            client_weights=client_w,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(submit_worker, i) for i in range(20)]
        concurrent.futures.wait(futures)

    assert engine.total_updates == 20
    final_w = engine.get_global_weights()
    assert np.isfinite(final_w["dense"]).all()
    assert len(engine.update_history) == 20


def test_dynamic_quorum_manager_thread_safety() -> None:
    """Verifies DynamicQuorumManager is thread-safe across concurrent node submissions."""
    import concurrent.futures

    manager = DynamicQuorumManager(quorum_threshold_pct=0.60, target_window_seconds=300)
    nodes = [f"bank_node_{i}" for i in range(30)]
    manager.register_nodes(nodes)

    def submit_node(node_id: str) -> QuorumState:
        return manager.record_node_submission(node_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(submit_node, n) for n in nodes[:20]]
        concurrent.futures.wait(futures)

    status = manager.evaluate_quorum_status()
    assert status.submitted_nodes_count == 20
    assert status.state == QuorumState.QUORUM_REACHED


def test_coordinator_service_submit_async_update_and_metrics() -> None:
    """Verifies CoordinatorService async update submission, staleness damping, and pruning."""
    from app.application.services.coordinator_service import CoordinatorService

    coord = CoordinatorService()
    coord.register_client("bank_async_1")
    coord.async_fl_engine.set_global_weights({"layer": np.array([10.0, 20.0], dtype=np.float32)})

    res = coord.submit_async_update(
        bank_id="bank_async_1",
        submitted_round=1,
        client_weights={"layer": np.array([30.0, 40.0], dtype=np.float32)},
    )
    assert res["success"] is True
    assert res["bank_id"] == "bank_async_1"
    assert res["submitted_round"] == 1
    assert "layer" in res["layer_keys"]

    # Test round pruning
    for i in range(10):
        coord.rounds[i] = {"round_id": i, "status": "COMPLETED"}
        coord.gradient_submissions[i] = {"b": b"data"}

    pruned = coord.prune_completed_rounds(keep_last=3)
    assert pruned == 7
    assert len(coord.rounds) == 3


def test_coordinator_router_async_endpoints() -> None:
    """Verifies FastAPI TestClient against async coordinator REST endpoints."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    # 1. Handshake bank
    hs_resp = client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank_test_client_async",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cuda",
            "ram_gb": 32.0,
            "device_count": 2,
        },
    )
    assert hs_resp.status_code == 200
    assert hs_resp.json()["registered"] is True

    # 2. Async update
    upd_resp = client.post(
        "/api/v1/coordinator/async-update",
        json={
            "bank_id": "bank_test_client_async",
            "submitted_round": 1,
            "client_weights": {"head": [1.5, 2.5, 3.5]},
            "sample_count": 200,
        },
    )
    assert upd_resp.status_code == 200
    upd_data = upd_resp.json()
    assert upd_data["success"] is True
    assert upd_data["bank_id"] == "bank_test_client_async"

    # 3. Async status
    status_resp = client.get("/api/v1/coordinator/async-status")
    assert status_resp.status_code == 200
    assert "alpha_staleness" in status_resp.json()
    assert "total_updates" in status_resp.json()

    # 4. Quorum status
    q_resp = client.get("/api/v1/coordinator/quorum-status")
    assert q_resp.status_code == 200
    assert "state" in q_resp.json()
    assert "quorum_threshold_pct" in q_resp.json()

    # 5. Prune rounds
    prune_resp = client.post("/api/v1/coordinator/rounds/prune?keep_last=10")
    assert prune_resp.status_code == 200
    assert "pruned_rounds_count" in prune_resp.json()

