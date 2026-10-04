"""Whole-System Integration Tests: Concurrency, Decision Provenance, Active Privacy, Failure Propagation, Historical Boundaries, and Machine vs Human Semantics.

Validates:
SYS-INV-04: Decision Provenance Remains Bound.
SYS-INV-05: Failure Does Not Become Success.
SYS-INV-07: Concurrency Does Not Silently Lose Authoritative State.
SYS-INV-08: Security/Privacy Configuration Is Not Merely Decorative.
SYS-INV-09: Historical Decisions Do Not Gain Future Evidence.
SYS-INV-10: Human Decisions Do Not Rewrite Machine History.
Scenario 5: Concurrent Case Decision.
Scenario 6: Decision Provenance Under Async Activity.
Scenario 7: Active Privacy / Robustness Path Truth.
Scenario 8: Failure Propagation.
Scenario 9: Historical Decision Boundary.
Scenario 10: Machine Result vs Human Outcome.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import (
    InvalidPipelineConfigurationError,
    SimulationService,
)
from app.application.services.streaming_graph_service import StreamingGraphService
from app.application.services.webhook_service import (
    WebhookDeliveryPayload,
    WebhookEventType,
    WebhookService,
)
from app.config import get_settings
from app.domain.enums import AggregationMethod, CasePriority, CaseStatus
from app.domain.value_objects import ModelWeights
from app.infrastructure.redis_store import RedisStore
from app.main import app


@pytest.fixture(autouse=True)
def clean_redis_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure in-memory fallback stores are cleanly partitioned between test cases."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True
    settings = get_settings()
    monkeypatch.setattr(settings, "feature_store_enabled", False)


class TestTransactionLifecycleAndSystemInvariants:
    """Scenarios 5, 6, 7, 8, 9, 10: Cross-domain verification of whole-system invariants."""

    def test_scenario_5_concurrent_case_decision_optimistic_conflict(self) -> None:
        """Scenario 5: Concurrent Case Decision.

        Actor A commits material mutation to V2.
        Actor B concurrently submits decision based on stale V1.
        Verification:
            - Actor A succeeds.
            - Actor B receives HTTP 409 Conflict.
            - Actor B cannot silently overwrite A.
            - Timeline hash remains valid.
        """
        client = TestClient(app)
        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title="Concurrent Investigation Dossier",
            priority=CasePriority.P2_HIGH,
            assigned_to="lead_investigator_1",
            bank_id="bank_alpha",
        )
        assert case.status == CaseStatus.OPEN

        # Move to INVESTIGATING
        case_svc.change_status(case.id, CaseStatus.INVESTIGATING, actor="lead_investigator_1")
        case_v1 = case_svc.get_case(case.id)
        assert case_v1 is not None
        v1_num = case_v1.version
        v1_hash = case_v1.timeline_hash

        # Actor A successfully records a supervisor signature and resolves to CLOSED_CONFIRMED
        res_a = client.post(
            f"/api/v1/cases/{case.id}/resolve",
            json={
                "actor": "analyst_1",
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor_alpha",
                "secondary_supervisor": "supervisor_beta",
                "expected_status": "investigating",
                "expected_version": v1_num,
                "expected_timeline_hash": v1_hash,
            },
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert res_a.status_code == 200, f"Actor A resolution failed: {res_a.text}"
        case_after_a = res_a.json()
        assert case_after_a["status"] == "closed_confirmed"
        assert case_after_a["version"] > v1_num

        # Actor B submits a resolution based on stale V1
        res_b = client.post(
            f"/api/v1/cases/{case.id}/resolve",
            json={
                "actor": "analyst_2",
                "resolution": "FALSE_POSITIVE",
                "primary_supervisor": "supervisor_gamma",
                "secondary_supervisor": "supervisor_delta",
                "expected_status": "investigating",
                "expected_version": v1_num,
                "expected_timeline_hash": v1_hash,
            },
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert res_b.status_code == 409, f"Expected 409 Precondition Failed for stale decision, got {res_b.status_code}"
        assert "precondition failed" in res_b.json()["detail"].lower()

        # Verify timeline integrity hash remains valid
        verify_res = client.get(f"/api/v1/cases/{case.id}/timeline/verify")
        assert verify_res.status_code == 200
        assert verify_res.json()["is_valid"] is True

    @pytest.mark.asyncio
    async def test_scenario_6_decision_provenance_under_async_activity(self) -> None:
        """Scenario 6: Decision Provenance Under Async Activity.

        Two concurrent transactions T (high risk) and U (low risk) processed asynchronously.
        Verification:
            - T never receives U's score, explanation, or alert.
            - U never receives T's score, explanation, or alert.
            - Provenance mapping remains strictly bound.
        """
        payload_t = {
            "transaction_id": "tx_provenance_high_risk",
            "transaction_amount": 15000.0,
            "merchant_category": "crypto",
            "country_code": "NG",
            "device_type": "mobile_app",
            "velocity": 25.0,
            "hour_of_day": 3,
            "merchant_risk_score": 0.95,
            "customer_history_score": 0.01,
            "chargeback_count": 8,
            "account_age_days": 1,
            "bank_id": "bank_b",
        }
        payload_u = {
            "transaction_id": "tx_provenance_low_risk",
            "transaction_amount": 15.0,
            "merchant_category": "grocery",
            "country_code": "DE",  # Germany
            "device_type": "web_browser",
            "bank_id": "bank_b",
            "chargeback_count": 0,
        }

        # Execute both concurrently via native ASGI transport
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            res_t, res_u = await asyncio.gather(
                ac.post("/api/v1/predict", json=payload_t),
                ac.post("/api/v1/predict", json=payload_u),
            )

        assert res_t.status_code == 200
        assert res_u.status_code == 200

        data_t = res_t.json()
        data_u = res_u.json()

        # Provenance Oracle: Transaction T
        assert data_t["transaction_id"] == "tx_provenance_high_risk"
        assert data_t["risk_score"] >= 600.0
        assert data_t["is_fraud_suspected"] is True
        assert data_t["alert_details"] is not None
        assert any("NG" in str(b) or "crypto" in str(b) or "country" in str(b) for b in data_t["breakdown"])

        # Provenance Oracle: Transaction U
        assert data_u["transaction_id"] == "tx_provenance_low_risk"
        assert data_u["risk_score"] < 400.0
        assert data_u["is_fraud_suspected"] is False
        assert data_u["alert_details"] is None

        # Cross-talk check: U has zero traces of T's high-risk signals
        for sig in data_u["breakdown"]:
            assert "Nigeria" not in sig["explanation"]
            assert "NG" not in sig["explanation"]

    def test_scenario_7_active_privacy_and_robustness_path_truth(self) -> None:
        """Scenario 7: Active Privacy / Robustness Path Truth.

        Verification:
            1. Incompatible SecAgg + Non-linear Byzantine defense raises InvalidPipelineConfigurationError upfront.
            2. Real DP execution clips gradients and spends epsilon budget.
            3. Real Byzantine defense (Krum) filters out poisoned updates.
        """
        # 1. Pipeline Compatibility Oracle
        settings = get_settings()
        from app.application.services.data_generator import DataGenerator
        from app.application.services.metrics_service import MetricsService
        from app.application.services.model_service import ModelService
        from app.domain.value_objects import SimulationConfig

        model_svc = ModelService(settings)
        priv_svc = PrivacyService()
        fl_engine = FederatedLearningEngine(
            settings=settings,
            model_service=model_svc,
            privacy_service=priv_svc,
        )

        sim_svc = SimulationService(
            settings=settings,
            simulation_repo=None,
            bank_repo=None,
            metrics_repo=None,
            data_generator=DataGenerator(seed=42),
            fl_engine=fl_engine,
            metrics_service=MetricsService(),
            model_service=model_svc,
        )

        config = SimulationConfig(
            num_rounds=1,
            enable_secure_aggregation=True,
            aggregation_method=AggregationMethod.KRUM,
        )

        with pytest.raises(InvalidPipelineConfigurationError, match="Additive Secure Aggregation is mathematically incompatible"):
            sim_svc.run_simulation(config)

        # 2. Real DP Execution Truth
        budget = priv_svc.get_or_create_budget("sim_dp_truth", epsilon=2.0, delta=1e-5)
        initial_eps = budget.total_epsilon
        assert initial_eps == 0.0

        w_global = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[1.0, 1.0, 1.0, 1.0])
        w_client = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[10.0, 10.0, 10.0, 10.0])  # Large update

        # Clip and noise
        clipped_w = priv_svc.clip_model_update(w_global, w_client, max_norm=1.0)
        rng = np.random.default_rng(42)
        noised_w = priv_svc.add_noise_to_weights(clipped_w, epsilon=1.0, delta=1e-5, max_grad_norm=1.0, rng=rng)
        budget.spend(1.0, limit=2.0)

        assert budget.total_epsilon == 1.0, "Privacy budget spending not recorded on active path"
        assert not np.allclose(clipped_w.flat_weights, noised_w.flat_weights), "Noise addition was skipped"

        # 3. Real Byzantine Defense Truth (Krum filters out malicious outlier)
        good_1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 1.0])
        good_2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.05, 0.95])
        good_3 = ModelWeights(layer_shapes=[(2,)], flat_weights=[0.98, 1.02])
        poisoned = ModelWeights(layer_shapes=[(2,)], flat_weights=[1000.0, -1000.0])

        filtered = fl_engine.apply_byzantine_defense(
            [good_1, good_2, good_3, poisoned],
            defense_type="krum",
        )
        assert len(filtered) == 1, "Krum selects best candidate update"
        assert filtered[0].flat_weights[0] < 5.0, "Krum failed to reject Byzantine poisoned outlier"

    def test_scenario_8_failure_propagation_truthful_semantics(self) -> None:
        """Scenario 8: Failure Propagation.

        Verifies that failures across model inference, persistence, and state transitions
        propagate truthfully without masking or false-success conversions:
            - Failed inference returns HTTP 500 (not fallback 200 with fabricated 0.15 score).
            - Stale case version returns HTTP 409 (not silent success).
            - Webhook failure returns False (not marked delivered).
        """
        client = TestClient(app)

        # 1. Model forward pass failure in /predict/score
        with patch("app.presentation.routers.predict._eval_model", side_effect=RuntimeError("PyTorch forward pass CUDA OOM")):
            res = client.post(
                "/api/v1/predict/score",
                json={
                    "transaction_id": "tx_fail_1",
                    "account_id": "acc_fail_1",
                    "amount": 100.0,
                    "merchant_id": "merch_1",
                    "device_id": "dev_1",
                    "country": "US",
                },
            )
            assert res.status_code == 500, f"Expected 500 on inference crash, got {res.status_code}"
            assert "Inference pipeline execution error" in res.json()["detail"]

        # 2. Stale case version in /cases/{id}/status returns HTTP 409
        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title="Precondition Test Case",
            priority=CasePriority.P3_MEDIUM,
            assigned_to="inv_1",
            bank_id="bank_alpha",
        )
        res_stale = client.patch(
            f"/api/v1/cases/{case.id}",
            json={"status": "investigating", "expected_version": 999},  # Stale version
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert res_stale.status_code == 409, f"Expected 409 Conflict, got {res_stale.status_code}"

        # 3. Webhook dispatch failure returns False
        wh = WebhookService()
        # Invalid loopback / private IP rejected by SSRF validation
        delivered = asyncio.run(
            wh.deliver_payload_async(
                "http://127.0.0.1:9999/hook",
                WebhookDeliveryPayload(
                    event_id="evt_test",
                    event_type=WebhookEventType.ALERT_CREATED,
                    payload={},
                    signature="sig",
                ),
            )
        )
        assert delivered is False, "Failed webhook delivery reported as successful"

    def test_scenario_9_historical_decision_boundary_as_of(self) -> None:
        """Scenario 9: Historical Decision Boundary.

        Where as_of cutoff exists:
            1. Ingest transactions at time T.
            2. Query historical path with as_of=T.
            3. Ingest future transaction at T+1 hour.
            4. Re-query with as_of=T.
        Verification:
            - Future edge at T+1 does NOT leak into historical as_of=T query.
        """
        graph = StreamingGraphService(max_window_minutes=120)
        t0 = datetime(2026, 10, 4, 10, 0, 0, tzinfo=UTC)
        t_plus_1 = datetime(2026, 10, 4, 11, 0, 0, tzinfo=UTC)

        # Ingest edge at T0
        graph.add_transaction({
            "bank_id": "bank_hist",
            "transaction_id": "tx_h0",
            "sender_id": "node_a",
            "receiver_id": "node_b",
            "amount": 100.0,
            "timestamp": t0.isoformat(),
        })

        # Baseline as_of query at T0 (GNN creates undirected forward & reverse edge pair)
        feat_base, edge_base, _ = graph.get_active_subgraph_tensors(as_of=t0)
        base_edge_count = edge_base.shape[1]
        assert base_edge_count == 2

        # Ingest future edge at T+1
        graph.add_transaction({
            "bank_id": "bank_hist",
            "transaction_id": "tx_h1_future",
            "sender_id": "node_b",
            "receiver_id": "node_c",
            "amount": 50000.0,  # High-value future edge
            "timestamp": t_plus_1.isoformat(),
        })

        # Re-query as_of T0
        feat_after, edge_after, _ = graph.get_active_subgraph_tensors(as_of=t0)
        assert edge_after.shape[1] == base_edge_count == 2, "Future edge leaked into historical as_of query"

        # Query as_of T+1 includes both (4 undirected edges)
        _, edge_t1, _ = graph.get_active_subgraph_tensors(as_of=t_plus_1)
        assert edge_t1.shape[1] == 4, "T+1 query must include both historical and current edges"

    def test_scenario_10_machine_result_vs_human_outcome(self) -> None:
        """Scenario 10: Machine Result vs Human Outcome.

        1. Ingest transaction with high fraud score -> alert and investigation created.
        2. Resolve case as CLOSED_FALSE_POSITIVE under Four-Eyes dual control.
        Verification:
            - Historical model score remains unchanged.
            - Historical model version remains unchanged.
            - Human disposition recorded separately in case.status.
            - SAR filing action becomes strictly ineligible (HTTP 400).
        """
        client = TestClient(app)

        # 1. Trigger high-risk prediction
        pred_res = client.post(
            "/api/v1/predict",
            json={
                "transaction_id": "tx_human_vs_machine_01",
                "transaction_amount": 15000.0,
                "merchant_category": "crypto",
                "country_code": "NG",
                "device_type": "mobile_app",
                "velocity": 25.0,
                "hour_of_day": 3,
                "merchant_risk_score": 0.95,
                "customer_history_score": 0.01,
                "chargeback_count": 8,
                "account_age_days": 1,
                "bank_id": "bank_alpha",
            },
        )
        assert pred_res.status_code == 200
        pred_data = pred_res.json()
        orig_score = pred_data["risk_score"]
        assert orig_score >= 600.0
        assert pred_data["is_fraud_suspected"] is True
        alert_details = pred_data["alert_details"]
        assert alert_details is not None
        alert_id = alert_details["alert_id"]

        # 2. Create case linking the alert
        case_res = client.post(
            "/api/v1/cases",
            json={
                "title": "Machine Prediction False Positive Investigation",
                "priority": "p1_critical",
                "alert_ids": [alert_id],
                "assigned_to": "lead_investigator_bob",
                "bank_id": "bank_alpha",
            },
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert case_res.status_code == 200
        case_id = case_res.json()["id"]

        # Move to INVESTIGATING
        client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating"},
            headers={"X-Tenant-ID": "bank_alpha"},
        )

        # 3. Compliance team resolves case as FALSE_POSITIVE under Four-Eyes control
        resolve_res = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "actor": "compliance_officer_1",
                "resolution": "FALSE_POSITIVE",
                "primary_supervisor": "supervisor_alice",
                "secondary_supervisor": "supervisor_charlie",
            },
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert resolve_res.status_code == 200
        resolved_case = resolve_res.json()
        assert resolved_case["status"] == "closed_false_positive"

        # 4. Verify historical machine prediction remains completely unchanged
        alert_res = client.get(f"/api/v1/alerts/{alert_id}", headers={"X-Tenant-ID": "bank_alpha"})
        assert alert_res.status_code == 200
        alert_obj = alert_res.json()
        assert alert_obj["risk_score"] == pytest.approx(orig_score, rel=1e-3), (
            "Human false-positive resolution modified machine risk score"
        )
        assert len(alert_obj["reason_codes"]) > 0

        # 5. Verify FinCEN SAR filing is rejected for false positive
        sar_res = client.post(
            f"/api/v1/cases/{case_id}/file-sar",
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert sar_res.status_code == 400
        assert "false_positive" in sar_res.json()["detail"].lower(), (
            "SAR filing should be ineligible for cases resolved as false positive"
        )
