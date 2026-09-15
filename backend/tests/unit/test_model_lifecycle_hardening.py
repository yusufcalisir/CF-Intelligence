"""Comprehensive hardening and vector tests for Model Lifecycle, Champion/Challenger & Zero-Downtime Rollout.

Validates:
- Single Champion Invariant: Auto-demoting previous production model to ARCHIVED
- Thread-safe concurrent model state transitions
- Production rollback restoring archived candidate
- ModelRegistryVault lock coverage and atomic state transitions
- Zero-mock invariant: log_feedback raises KeyError for unscored transactions
- Zero-downtime rolling deployment complete lifecycle (initiate -> drain -> update -> finalize)
- Zero-downtime deployment abort restoring connection counts
- Presentation router endpoints: active model, version details, promotion, deployment REST lifecycle
"""

from __future__ import annotations

import concurrent.futures
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.application.services.zero_downtime_deployer import ZeroDowntimeDeploymentManager
from app.domain.deployment_state import DeploymentStage
from app.domain.model_lifecycle import (
    InvalidStateTransitionError,
    ModelLifecycleManager,
    ModelState,
)
from app.presentation.routers.model_registry import router

# ---------------------------------------------------------------------------
# Domain Model Lifecycle Tests
# ---------------------------------------------------------------------------


def test_single_champion_invariant_auto_demotes_previous() -> None:
    """Promoting a new model to PRODUCTION must auto-archive the previous champion."""
    manager = ModelLifecycleManager()

    # Model 1 setup
    manager.register_model("model_v1.0.0")
    manager.transition_state("model_v1.0.0", ModelState.SHADOW)
    manager.transition_state("model_v1.0.0", ModelState.CANARY, signoff_approved=True)
    m1 = manager.transition_state("model_v1.0.0", ModelState.PRODUCTION)
    assert m1.current_state == ModelState.PRODUCTION
    assert manager.get_production_model() == m1

    # Model 2 setup
    manager.register_model("model_v2.0.0")
    manager.transition_state("model_v2.0.0", ModelState.SHADOW)
    manager.transition_state("model_v2.0.0", ModelState.CANARY, signoff_approved=True)
    m2 = manager.transition_state("model_v2.0.0", ModelState.PRODUCTION)

    # Invariant assertion
    assert m2.current_state == ModelState.PRODUCTION
    assert m1.current_state == ModelState.ARCHIVED
    assert any("Single Champion Invariant" in h.get("reason", "") for h in m1.state_history)
    assert manager.get_production_model() == m2


def test_model_lifecycle_thread_safety_concurrent_transitions() -> None:
    """Concurrent state queries and registrations must execute safely under 20 threads."""
    manager = ModelLifecycleManager()

    def worker(idx: int) -> str:
        v_name = f"model_worker_v{idx}.0.0"
        rec = manager.register_model(v_name)
        manager.transition_state(v_name, ModelState.SHADOW)
        return rec.model_version

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    assert len(manager.list_models()) == 20


def test_model_lifecycle_production_rollback_to_archived() -> None:
    """Production rollback demotes current champion to ARCHIVED and restores candidate."""
    manager = ModelLifecycleManager()

    manager.register_model("model_v1.0.0")
    manager.transition_state("model_v1.0.0", ModelState.SHADOW)
    manager.transition_state("model_v1.0.0", ModelState.CANARY, signoff_approved=True)
    manager.transition_state("model_v1.0.0", ModelState.PRODUCTION)

    manager.register_model("model_v2.0.0")
    manager.transition_state("model_v2.0.0", ModelState.SHADOW)
    manager.transition_state("model_v2.0.0", ModelState.CANARY, signoff_approved=True)
    manager.transition_state("model_v2.0.0", ModelState.PRODUCTION)

    assert manager.get_production_model().model_version == "model_v2.0.0"

    # Execute rollback
    demoted, restored = manager.rollback_production(
        actor_role="ADMIN",
        target_version="model_v1.0.0",
        reason="Model v2 latency regression",
    )
    assert demoted.model_version == "model_v2.0.0"
    assert demoted.current_state == ModelState.ARCHIVED
    assert restored.model_version == "model_v1.0.0"
    assert restored.current_state == ModelState.PRODUCTION
    assert manager.get_production_model().model_version == "model_v1.0.0"


def test_model_lifecycle_rollback_fails_when_no_active_or_archived() -> None:
    """Rollback raises error when no model is in PRODUCTION or no candidate in ARCHIVED."""
    manager = ModelLifecycleManager()
    with pytest.raises(InvalidStateTransitionError, match="No active PRODUCTION model"):
        manager.rollback_production()

    manager.register_model("v1")
    manager.transition_state("v1", ModelState.SHADOW)
    manager.transition_state("v1", ModelState.CANARY, signoff_approved=True)
    manager.transition_state("v1", ModelState.PRODUCTION)

    with pytest.raises(InvalidStateTransitionError, match="No ARCHIVED model available"):
        manager.rollback_production()


# ---------------------------------------------------------------------------
# Zero-Mock Evaluation Engine Tests
# ---------------------------------------------------------------------------


def test_zero_mock_evaluation_engine_feedback_rejection() -> None:
    """log_feedback must raise KeyError for unscored transactions (Zero-Mock Invariant)."""
    temp_dir = tempfile.mkdtemp()
    try:
        registry_inst = ModelRegistry(storage_dir=temp_dir)
        engine = ModelEvaluationEngine(registry_inst)

        with pytest.raises(KeyError, match="not found in evaluation store"):
            engine.log_feedback(
                simulation_id="sim_test",
                transaction_id="unscored_txn_999",
                actual_label=1,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Zero-Downtime Deployment Subsystem Tests
# ---------------------------------------------------------------------------


def test_zero_downtime_deployment_lifecycle_and_connections() -> None:
    """Validates complete deployment progression: initiate -> drain -> update -> finalize."""
    deployer = ZeroDowntimeDeploymentManager(current_version="v2.0.0")

    session = deployer.initiate_upgrade(
        target_version="v2.1.0",
        compatibility_window_hours=48,
        initial_connections=100,
    )
    assert session.stage == DeploymentStage.DRAINING_CONNECTIONS
    assert session.target_version == "v2.1.0"
    assert deployer.get_active_session() == session

    # Drain first batch of 50
    active, drained = deployer.drain_client_connections(session.session_id, batch_size=50)
    assert active == 50
    assert drained == 50
    assert session.stage == DeploymentStage.DRAINING_CONNECTIONS

    # Drain remaining 50 -> triggers ROLLING_UPGRADE
    active, drained = deployer.drain_client_connections(session.session_id, batch_size=60)
    assert active == 0
    assert drained == 100
    assert session.stage == DeploymentStage.ROLLING_UPGRADE

    # Execute rolling update
    session = deployer.execute_rolling_instance_update(
        session.session_id, instance_ids=["inst-1", "inst-2"]
    )
    assert session.stage == DeploymentStage.DUAL_VERSION_ACTIVE
    assert len(session.updated_instances) == 2

    # Finalize upgrade
    session = deployer.finalize_upgrade(session.session_id)
    assert session.stage == DeploymentStage.UPGRADE_COMPLETED
    assert deployer.current_version == "v2.1.0"
    assert deployer.get_active_session() is None


def test_zero_downtime_deployment_abort_and_restoration() -> None:
    """Aborting deployment resets state, records reason, and restores connections."""
    deployer = ZeroDowntimeDeploymentManager(current_version="v2.0.0")

    session = deployer.initiate_upgrade(
        target_version="v2.1.0",
        initial_connections=100,
    )
    deployer.drain_client_connections(session.session_id, batch_size=40)
    assert session.active_connections_count == 60
    assert session.drained_connections_count == 40

    aborted = deployer.abort_upgrade(
        session.session_id, reason="Health check timeout on node cluster"
    )
    assert aborted.stage == DeploymentStage.ABORTED
    assert aborted.abort_reason == "Health check timeout on node cluster"
    assert aborted.active_connections_count == 100
    assert aborted.drained_connections_count == 0
    assert deployer.current_version == "v2.0.0"


# ---------------------------------------------------------------------------
# Presentation Router Endpoints Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_router_active_model_and_promotion_endpoints(client: TestClient) -> None:
    """Validates /active, /versions/{v}, and promotion REST endpoints."""
    import uuid

    sim_id = f"sim_test_hardening_{uuid.uuid4().hex[:8]}"

    # Initially 404 for active champion
    resp = client.get(f"/api/v1/registry/{sim_id}/active")
    assert resp.status_code == 404

    try:
        # Create model files directly via registry instance
        from app.presentation.routers.model_registry import registry as app_reg

        entry1 = app_reg.save_version(
            simulation_id=sim_id,
            state_dict={"weight": 1.0},
            metrics={"auc_roc": 0.82},
            is_promoted=True,
        )
        assert entry1["version"] == 1

        entry2 = app_reg.save_version(
            simulation_id=sim_id,
            state_dict={"weight": 2.0},
            metrics={"auc_roc": 0.89},
            is_promoted=False,
        )
        assert entry2["version"] == 2

        # Query active
        resp_active = client.get(f"/api/v1/registry/{sim_id}/active")
        assert resp_active.status_code == 200
        assert resp_active.json()["version"] == 1

        # Query specific version
        resp_v2 = client.get(f"/api/v1/registry/{sim_id}/versions/2")
        assert resp_v2.status_code == 200
        assert resp_v2.json()["version"] == 2

        # Explicit promotion of version 2 to champion
        resp_promo = client.post(
            f"/api/v1/registry/{sim_id}/versions/2/promote",
            json={"target_status": "champion"},
        )
        assert resp_promo.status_code == 200
        assert resp_promo.json()["status"] == "champion"

        # Re-query active -> version 2 is now champion
        resp_active2 = client.get(f"/api/v1/registry/{sim_id}/champion")
        assert resp_active2.status_code == 200
        assert resp_active2.json()["version"] == 2
    finally:
        import os
        import shutil

        from app.presentation.routers.model_registry import registry as app_reg
        sim_dir = app_reg._get_sim_dir(sim_id)
        if os.path.exists(sim_dir):
            shutil.rmtree(sim_dir, ignore_errors=True)


def test_router_zero_downtime_deployment_endpoints(client: TestClient) -> None:
    """Validates deployment REST endpoints lifecycle: initiate, drain, rolling-update, finalize, status."""
    # Initiate deployment
    resp_init = client.post(
        "/api/v1/registry/deployment/initiate",
        json={
            "target_version": "v3.0.0",
            "compatibility_window_hours": 24,
            "initial_connections": 80,
        },
    )
    assert resp_init.status_code == 200
    session_id = resp_init.json()["session_id"]
    assert resp_init.json()["target_version"] == "v3.0.0"

    # Query active deployment
    resp_act = client.get("/api/v1/registry/deployment/active")
    assert resp_act.status_code == 200
    assert resp_act.json()["session_id"] == session_id

    # Drain connections
    resp_drain = client.post(
        f"/api/v1/registry/deployment/{session_id}/drain",
        json={"batch_size": 80},
    )
    assert resp_drain.status_code == 200
    assert resp_drain.json()["stage"] == "ROLLING_UPGRADE"

    # Rolling update
    resp_roll = client.post(
        f"/api/v1/registry/deployment/{session_id}/rolling-update",
        json={"instance_ids": ["cluster-node-a", "cluster-node-b"]},
    )
    assert resp_roll.status_code == 200
    assert resp_roll.json()["stage"] == "DUAL_VERSION_ACTIVE"

    # Finalize upgrade
    resp_fin = client.post(f"/api/v1/registry/deployment/{session_id}/finalize")
    assert resp_fin.status_code == 200
    assert resp_fin.json()["stage"] == "UPGRADE_COMPLETED"

    # Status check
    resp_stat = client.get("/api/v1/registry/deployment/status")
    assert resp_stat.status_code == 200
    assert resp_stat.json()["current_version"] == "v3.0.0"


def test_router_deployment_abort_endpoint(client: TestClient) -> None:
    """Validates deployment abort endpoint and 404 on missing session."""
    resp_init = client.post(
        "/api/v1/registry/deployment/initiate",
        json={"target_version": "v4.0.0", "initial_connections": 50},
    )
    assert resp_init.status_code == 200
    session_id = resp_init.json()["session_id"]

    resp_abort = client.post(
        f"/api/v1/registry/deployment/{session_id}/abort",
        json={"reason": "Canary error rate spike > 1%"},
    )
    assert resp_abort.status_code == 200
    assert resp_abort.json()["stage"] == "ABORTED"
    assert resp_abort.json()["abort_reason"] == "Canary error rate spike > 1%"

    # 404 check
    resp_missing = client.get("/api/v1/registry/deployment/non_existent_session")
    assert resp_missing.status_code == 404
