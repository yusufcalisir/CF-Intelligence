"""Unit and contract tests for Model Registry, Champion-Challenger Rollout & SR 11-7 Governance API."""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime

import pytest
import torch
from fastapi.testclient import TestClient

from app.main import app
from app.presentation.routers.model_registry import deployer, registry

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_registry_and_deployer():
    """Ensure isolated clean temporary storage for model registry and deployment sessions."""
    temp_dir = tempfile.mkdtemp()
    old_storage = registry.storage_dir
    old_root = registry.registry_root

    registry.storage_dir = temp_dir
    registry.registry_root = f"{temp_dir}/registry"
    import os
    os.makedirs(registry.registry_root, exist_ok=True)

    yield

    shutil.rmtree(temp_dir, ignore_errors=True)
    registry.storage_dir = old_storage
    registry.registry_root = old_root


def _seed_sample_model(sim_id: str, version: int = 1, auc: float = 0.92, fairness: float = 0.90) -> dict:
    """Helper to seed a test model version directly in the isolated test registry."""
    dummy_state = {"linear.weight": torch.randn(5, 5), "linear.bias": torch.randn(5)}
    metrics = {"auc_roc": auc, "pr_auc": auc - 0.05, "f1_score": 0.88, "latency_ms": 12.0}
    entry = registry.save_version(
        simulation_id=sim_id,
        state_dict=dummy_state,
        metrics=metrics,
        is_promoted=(version == 1),
        status="champion" if version == 1 else "challenger",
    )
    # Add initial sign-off
    registry.sign_off(
        simulation_id=sim_id,
        version=entry["version"],
        role="ml_engineer",
        user="test_ml_engineer",
        signature="sha256_mock_sig_12345678",
        fairness_score=fairness,
        bias_metric=0.02,
        drift_divergence=0.01,
    )
    return entry


class TestModelRegistryMultiPrefixParity:
    """Validate zero-breakage multi-prefix routing parity across /api/v1/registry, /v1/registry, /api/v1/models, /v1/models."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/registry",
            "/v1/registry",
            "/api/v1/models",
            "/v1/models",
        ],
    )
    def test_global_inventory_prefix_parity(self, prefix: str):
        """Verify GET / responds with ModelInventoryResponse across all 4 route prefixes."""
        resp = client.get(prefix)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "total_models" in data
        assert data["total_models"] >= 1
        model_0 = data["models"][0]
        assert "simulation_id" in model_0
        assert "sr11_7_compliant" in model_0

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/registry",
            "/v1/registry",
            "/api/v1/models",
            "/v1/models",
        ],
    )
    def test_deployment_status_prefix_parity(self, prefix: str):
        """Verify GET /deployment/status responds consistently across all 4 route prefixes."""
        resp = client.get(f"{prefix}/deployment/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_version" in data
        assert "total_sessions" in data
        assert "has_active_session" in data

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/registry",
            "/v1/registry",
            "/api/v1/models",
            "/v1/models",
        ],
    )
    def test_versions_listing_prefix_parity(self, prefix: str):
        """Verify GET /{sim_id}/versions responds consistently across all 4 route prefixes."""
        _seed_sample_model("sim_test_prefix", version=1)
        resp = client.get(f"{prefix}/sim_test_prefix/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["version"] == 1


class TestZeroDowntimeDeploymentLifecycle:
    """Validate complete zero-downtime rolling upgrade lifecycle endpoints."""

    def test_full_upgrade_lifecycle(self):
        # 1. Initiate upgrade session
        init_resp = client.post(
            "/api/v1/registry/deployment/initiate",
            json={
                "target_version": "v2.2.0",
                "compatibility_window_hours": 48,
                "initial_connections": 100,
            },
        )
        assert init_resp.status_code == 200
        session_data = init_resp.json()
        session_id = session_data["session_id"]
        assert session_data["target_version"] == "v2.2.0"
        assert session_data["stage"] == "DRAINING_CONNECTIONS"

        # 2. Query active session
        active_resp = client.get("/api/v1/registry/deployment/active")
        assert active_resp.status_code == 200
        assert active_resp.json()["session_id"] == session_id

        # 3. Drain connections in batches
        drain_resp = client.post(
            f"/api/v1/registry/deployment/{session_id}/drain",
            json={"batch_size": 40},
        )
        assert drain_resp.status_code == 200
        drain_data = drain_resp.json()
        assert drain_data["active_connections_count"] == 60
        assert drain_data["drained_connections_count"] == 40

        # 4. Rolling instance updates
        roll_resp = client.post(
            f"/api/v1/registry/deployment/{session_id}/rolling-update",
            json={"instance_ids": ["inst_node_1", "inst_node_2"]},
        )
        assert roll_resp.status_code == 200
        assert "inst_node_1" in roll_resp.json()["updated_instances"]

        # 5. Finalize upgrade session
        fin_resp = client.post(f"/api/v1/registry/deployment/{session_id}/finalize")
        assert fin_resp.status_code == 200
        assert fin_resp.json()["stage"] == "UPGRADE_COMPLETED"

    def test_deployment_abort_flow(self):
        # Initiate and then abort
        init_resp = client.post(
            "/api/v1/registry/deployment/initiate",
            json={"target_version": "v2.3.0_canary"},
        )
        session_id = init_resp.json()["session_id"]

        abort_resp = client.post(
            f"/api/v1/registry/deployment/{session_id}/abort",
            json={"reason": "Canary error rate spike detected in bank node C"},
        )
        assert abort_resp.status_code == 200
        assert abort_resp.json()["stage"] == "ABORTED"
        assert "spike detected" in abort_resp.json()["abort_reason"]

    def test_deployment_404_handling(self):
        resp = client.get("/api/v1/registry/deployment/nonexistent_session_999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestModelVersionAndGovernanceLifecycle:
    """Validate model version tracking, dual sign-offs, and champion promotion gates."""

    def test_version_retrieval_and_active_champion(self):
        _seed_sample_model("sim_gov_1", version=1, auc=0.91)
        _seed_sample_model("sim_gov_1", version=2, auc=0.95)

        # 1. List versions
        list_resp = client.get("/api/v1/registry/sim_gov_1/versions")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 2

        # 2. Specific version
        v1_resp = client.get("/api/v1/registry/sim_gov_1/versions/1")
        assert v1_resp.status_code == 200
        assert v1_resp.json()["version"] == 1

        # 3. Active / champion
        champ_resp = client.get("/api/v1/registry/sim_gov_1/champion")
        assert champ_resp.status_code == 200
        assert champ_resp.json()["is_active"] is True

    def test_dual_sign_off_workflow(self):
        _seed_sample_model("sim_dual_sign", version=1)

        # First sign-off already added by ml_engineer in seed helper.
        # Adding duplicate role must be rejected with 400
        dup_resp = client.post(
            "/api/v1/registry/sim_dual_sign/versions/1/signoff",
            json={
                "role": "ml_engineer",
                "user": "engineer_two",
                "signature": "sha256_mock_sig_engineer2",
                "fairness_score": 0.92,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert dup_resp.status_code == 400
        assert "already signed off" in dup_resp.json()["detail"].lower()

        # Valid compliance officer sign-off
        comp_resp = client.post(
            "/api/v1/registry/sim_dual_sign/versions/1/signoff",
            json={
                "role": "compliance",
                "user": "officer_jane",
                "signature": "sha256_compliance_signature_valid",
                "fairness_score": 0.95,
                "bias_metric": 0.01,
                "drift_divergence": 0.01,
            },
        )
        assert comp_resp.status_code == 200
        data = comp_resp.json()
        assert len(data["sign_offs"]) == 2

    def test_sr11_7_quality_gate_promotion_success(self):
        """Validate successful promotion to champion when model satisfies SR 11-7 performance and fairness gates."""
        _seed_sample_model("sim_sr11_7_pass", version=1, auc=0.94, fairness=0.92)

        promo_resp = client.post(
            "/api/v1/registry/sim_sr11_7_pass/versions/1/promote",
            json={
                "target_status": "champion",
                "enforce_sr11_7": True,
                "min_auc": 0.65,
                "min_fairness_score": 0.80,
            },
        )
        assert promo_resp.status_code == 200
        data = promo_resp.json()
        assert data["version"] == 1
        assert data["target_status"] == "champion"
        assert data["sr11_7_validation"] is not None
        assert data["sr11_7_validation"]["passed"] is True

    def test_sr11_7_quality_gate_rejection_low_auc(self):
        """SR 11-7 gate strictly rejects promotion with 422 if model AUC fails threshold."""
        _seed_sample_model("sim_sr11_7_fail_auc", version=1, auc=0.60, fairness=0.90)

        promo_resp = client.post(
            "/api/v1/registry/sim_sr11_7_fail_auc/versions/1/promote",
            json={
                "target_status": "champion",
                "enforce_sr11_7": True,
                "min_auc": 0.75,  # Model has 0.60 -> must be rejected
            },
        )
        assert promo_resp.status_code == 422
        detail = promo_resp.json()["detail"]
        assert "SR 11-7 Quality Gate Rejection" in detail
        assert "AUC-ROC" in detail

    def test_sr11_7_quality_gate_rejection_fairness_violation(self):
        """SR 11-7 gate strictly rejects promotion with 422 if disparate impact violates EEOC 80% rule."""
        _seed_sample_model("sim_sr11_7_fail_fair", version=1, auc=0.92, fairness=0.72)

        promo_resp = client.post(
            "/api/v1/registry/sim_sr11_7_fail_fair/versions/1/promote",
            json={
                "target_status": "champion",
                "enforce_sr11_7": True,
                "min_auc": 0.65,
                "min_fairness_score": 0.80,  # Model has 0.72 -> must be rejected
            },
        )
        assert promo_resp.status_code == 422
        detail = promo_resp.json()["detail"]
        assert "SR 11-7 Quality Gate Rejection" in detail
        assert "disparate impact ratio" in detail.lower()

    def test_convenience_models_promote_alias(self):
        """Verify /api/v1/models/{model_id}/promote convenience endpoint."""
        _seed_sample_model("sim_alias_test", version=1, auc=0.95, fairness=0.90)

        resp = client.post(
            "/api/v1/models/sim_alias_test/promote",
            json={"target_status": "champion", "enforce_sr11_7": False},
        )
        assert resp.status_code == 200
        assert resp.json()["target_status"] == "champion"


class TestRollbackCanaryAndShadowMetrics:
    """Validate rollback execution, canary decision telemetry, and shadow metrics endpoints."""

    def test_rollback_to_historical_version(self):
        _seed_sample_model("sim_rollback", version=1, auc=0.90)
        _seed_sample_model("sim_rollback", version=2, auc=0.95)

        # Rollback to v1
        rb_resp = client.post("/api/v1/registry/sim_rollback/rollback/1")
        assert rb_resp.status_code == 200
        data = rb_resp.json()
        assert data["version"] == 1
        assert data["is_active"] is True
        assert data["status"] == "champion"

        # Active version should now be v1
        champ_resp = client.get("/api/v1/registry/sim_rollback/champion")
        assert champ_resp.status_code == 200
        assert champ_resp.json()["version"] == 1

    def test_canary_history_endpoint(self):
        from app.presentation.routers.simulation import _simulation_events

        # Push mock canary event
        _simulation_events.push_list(
            "sim_canary_test",
            {
                "event_type": "round_complete",
                "data": {
                    "round": 3,
                    "canary_info": {
                        "version": 2,
                        "candidate_auc": 0.941,
                        "promoted_auc": 0.912,
                        "is_promoted": True,
                        "reason": "Challenger PR-AUC superior on holdout validation set.",
                    },
                },
            },
        )

        resp = client.get("/api/v1/registry/sim_canary_test/canary")
        assert resp.status_code == 200
        canary_data = resp.json()
        assert len(canary_data) == 1
        assert canary_data[0]["round"] == 3
        assert canary_data[0]["candidate_auc"] == 0.941
        assert canary_data[0]["is_promoted"] is True

    def test_shadow_metrics_empty_returns_clean_schema(self):
        resp = client.get("/api/v1/registry/sim_unscored_new/shadow/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 0

    def test_404_responses_for_missing_resources(self):
        # Missing simulation
        resp = client.get("/api/v1/registry/missing_simulation_id_999/versions/99")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        # Missing champion
        resp = client.get("/api/v1/registry/missing_simulation_id_999/champion")
        assert resp.status_code == 404
        assert "no active champion" in resp.json()["detail"].lower()

        # Rollback missing version
        resp = client.post("/api/v1/registry/missing_simulation_id_999/rollback/99")
        assert resp.status_code == 404
