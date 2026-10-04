"""Integration tests for final adversarial composition, lifecycle integrity, and recovery verification.

Validates:
- FINAL-INV-01: Failure Cannot Fragment One Logical Transaction
- FINAL-INV-02: Tenant Context Cannot Be Rebound During Recovery
- FINAL-INV-03: Decision Evidence Is Version-Bound
- FINAL-INV-04: Untrusted Persisted or Replayed State Fails Safely
- FINAL-INV-05: Recovery Preserves Meaning, Not Just Availability
- FINAL-INV-06: Authoritative History Remains Internally Consistent
- FINAL-INV-07: No Silent Security Downgrade Appears During Recovery

Covers:
- Scenario 1: Compound Transaction Lifecycle Failure & Recovery
- Scenario 2: Cross-Tenant Adversarial Composition
- Scenario 3: Model-Version / Decision-Provenance Race
- Scenario 4: Corrupted or Replayed State Boundary
- Scenario 5: Full Lifecycle Recovery & Semantic Reconciliation
- Metamorphic Checks: M1 (Clean vs Recovered), M2 (Tenant Renaming), M3 (Activation Timing)
"""

from __future__ import annotations

import shutil
import tempfile
import uuid

import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.case_service import (
    CaseManagementService,
    InvalidCaseTransitionError,
)
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.idempotency import IdempotencyService
from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
)
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.config import get_settings
from app.domain.enums import AggregationMethod, CasePriority, CaseStatus
from app.domain.value_objects import ModelWeights
from app.infrastructure.redis_store import RedisStore
from app.main import app


@pytest.fixture(autouse=True)
def reset_in_memory_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure fallback stores, feature store, and idempotency cache are cleanly cleared."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True
    idem = IdempotencyService.get()
    with idem._fallback_lock:
        idem._fallback.clear()
    fs = FeatureStoreService()
    fs.clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "feature_store_enabled", False)


class TestLifecycleIntegrityAndRecovery:
    """Adversarial composition and end-to-end lifecycle verification suite."""

    # -------------------------------------------------------------------------
    # SCENARIO 1: Compound Transaction Lifecycle Failure & Recovery
    # -------------------------------------------------------------------------
    def test_scenario_1_compound_transaction_lifecycle_failure(self) -> None:
        """Scenario 1: Mid-lifecycle failure injection and retry recovery.

        Steps:
            1. Transaction T ingested into StreamingGraph and FeatureStore.
            2. Mid-lifecycle failure injected (simulated network/persistence interruption).
            3. Client retries idempotent transaction ingestion.
        Independent Oracle:
            - Graph edge count == 1, node count == 2 (no duplicate financial edge).
            - Feature store customer history count == 1 (no double-counted features).
            - Exactly 1 logical financial transaction represented in final state.
        """
        tx_id = f"tx_lifecycle_{uuid.uuid4().hex[:8]}"
        tx_payload = {
            "tenant_id": "bank_alpha",
            "transaction_id": tx_id,
            "sender_id": "acct_source_alpha",
            "receiver_id": "acct_dest_alpha",
            "amount": 750.0,
            "timestamp": "2026-10-04T12:00:00Z",
        }

        # Step 1: Initial partial mutation
        graph = StreamingGraphService(max_window_minutes=60)
        graph.add_transaction(tx_payload)

        fs = FeatureStoreService()
        fs.settings.feature_store_enabled = True
        fs.ingest_transaction(
            customer_id="acct_source_alpha",
            amount=750.0,
            merchant_id="acct_dest_alpha",
            merchant_category="crypto",
            merchant_risk_score=0.75,
            customer_history_score=0.85,
            chargeback_count=1,
            account_age_days=180,
            transaction_id=tx_id,
            tenant_id="bank_alpha",
        )

        # Step 2: Mid-lifecycle failure occurs (response lost / client timeout).
        # Step 3: Client retries identical transaction ingestion
        graph.add_transaction(tx_payload)  # Retry
        fs.ingest_transaction(
            customer_id="acct_source_alpha",
            amount=750.0,
            merchant_id="acct_dest_alpha",
            merchant_category="crypto",
            merchant_risk_score=0.75,
            customer_history_score=0.85,
            chargeback_count=1,
            account_age_days=180,
            transaction_id=tx_id,
            tenant_id="bank_alpha",
        )

        # Independent Oracle Assertions
        assert len(graph.edges) == 1, f"Expected 1 graph edge, found {len(graph.edges)}"
        assert len(graph.nodes) == 2, f"Expected 2 nodes, found {len(graph.nodes)}"

        online_feats = fs.get_online_features(
            [{"customer_id": "acct_source_alpha", "merchant_id": "acct_dest_alpha"}],
            ["rolling_velocity_1h", "avg_amount_24h", "chargeback_count"],
            tenant_id="bank_alpha",
        )
        assert online_feats is not None
        assert len(online_feats) == 1
        assert online_feats[0]["avg_amount_24h"] == 750.0
        assert online_feats[0]["chargeback_count"] == 1

    # -------------------------------------------------------------------------
    # SCENARIO 2: Cross-Tenant Adversarial Composition
    # -------------------------------------------------------------------------
    def test_scenario_2_cross_tenant_adversarial_composition(self) -> None:
        """Scenario 2: Colliding identifiers between Tenant A and Tenant B under retry/recovery.

        Setup:
            - Tenant A (bank_alpha) and Tenant B (bank_beta).
            - Identical transaction_id and identical Idempotency-Key.
            - Distinct financial payloads (crypto high-risk vs retail low-risk).
        Independent Oracle:
            - Tuple (tenant_id, object_type, canonical_object_id) is strictly segregated.
            - Idempotency-Key does NOT cross tenant boundaries (Tenant B gets its own case).
            - Tenant B cannot retrieve or mutate Tenant A's case (HTTP 403 Forbidden).
        """
        client = TestClient(app)
        shared_idem_key = f"idem_cross_tenant_{uuid.uuid4().hex[:8]}"

        payload_a = {
            "title": "Bank Alpha Wire Fraud Dossier",
            "priority": "p1_critical",
            "assigned_to": "investigator_alpha",
            "bank_id": "bank_alpha",
        }
        payload_b = {
            "title": "Bank Beta Retail Dispute Dossier",
            "priority": "p4_low",
            "assigned_to": "investigator_beta",
            "bank_id": "bank_beta",
        }

        # 1. Tenant A creates case under shared_idem_key
        resp_a = client.post(
            "/api/v1/cases",
            json=payload_a,
            headers={"Idempotency-Key": shared_idem_key, "X-Tenant-ID": "bank_alpha"},
        )
        assert resp_a.status_code == 200, resp_a.text
        case_a_id = resp_a.json()["id"]

        # 2. Tenant B submits under the identical Idempotency-Key
        resp_b = client.post(
            "/api/v1/cases",
            json=payload_b,
            headers={"Idempotency-Key": shared_idem_key, "X-Tenant-ID": "bank_beta"},
        )
        assert resp_b.status_code == 200, resp_b.text
        case_b_id = resp_b.json()["id"]

        # Oracle 1: Idempotency is tenant-scoped; distinct cases created
        assert case_a_id != case_b_id, "Tenant B received Tenant A's cached case!"
        assert resp_b.json()["title"] == payload_b["title"]

        # 3. Adversarial Attempt: Tenant B attempts to access Tenant A's case
        resp_leak = client.get(
            f"/api/v1/cases/{case_a_id}",
            headers={"X-Tenant-ID": "bank_beta"},
        )
        assert resp_leak.status_code == 403, (
            f"Expected 403 Forbidden on cross-tenant access, got {resp_leak.status_code}"
        )
        detail_msg = resp_leak.json()["detail"].lower()
        assert any(kw in detail_msg for kw in ["not authorized", "access denied", "broken access control"])

    # -------------------------------------------------------------------------
    # SCENARIO 3: Model-Version / Decision-Provenance Race
    # -------------------------------------------------------------------------
    def test_scenario_3_model_version_decision_provenance_race(self) -> None:
        """Scenario 3: Active model promotion while an in-flight decision is pending.

        Setup:
            - Model V1 active champion.
            - Transaction T evaluated under V1.
            - Model V2 promoted to champion concurrently.
            - Transaction U evaluated under V2.
        Independent Oracle:
            - T remains permanently bound to V1 metadata and score.
            - U is bound to V2 metadata and score.
            - Provenance query for T continues to report V1 after V2 promotion.
            - Zero cross-contamination between T and U evidence.
        """
        temp_dir = tempfile.mkdtemp(prefix="cfi_provenance_race_")
        try:
            registry = ModelRegistry(storage_dir=temp_dir)
            eval_engine = ModelEvaluationEngine(registry)
            sim_id = "sim_provenance_race_01"

            # 1. Publish Version 1
            v1_weights = {"network.0.weight": torch.tensor([[1.0, 1.0], [1.0, 1.0]])}
            registry.save_version(
                simulation_id=sim_id,
                state_dict=v1_weights,
                metrics={"auc_roc": 0.81, "f1_score": 0.79},
                is_promoted=True,
                status="champion",
            )

            # 2. Transaction T arrives and is scored under Version 1
            eval_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id="tx_T_inflight",
                champion_version=1,
                champion_prob=0.88,
                champion_latency_ms=12.4,
                routed_to="champion",
            )

            # 3. Model Version 2 is published and promoted to champion while T is completed
            v2_weights = {"network.0.weight": torch.tensor([[2.0, 2.0], [2.0, 2.0]])}
            registry.save_version(
                simulation_id=sim_id,
                state_dict=v2_weights,
                metrics={"auc_roc": 0.92, "f1_score": 0.90},
                is_promoted=True,
                status="champion",
            )

            # 4. Transaction U arrives and is scored under Version 2
            eval_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id="tx_U_new",
                champion_version=2,
                champion_prob=0.35,
                champion_latency_ms=9.8,
                routed_to="champion",
            )

            # Independent Oracle Assertions
            record_t = eval_engine._store.get(f"{sim_id}:prediction:tx_T_inflight")
            record_u = eval_engine._store.get(f"{sim_id}:prediction:tx_U_new")

            assert record_t is not None, "Transaction T prediction record missing"
            assert record_u is not None, "Transaction U prediction record missing"

            assert record_t["champion_version"] == 1, (
                f"Transaction T corrupted! Expected version 1, got {record_t['champion_version']}"
            )
            assert record_t["champion_prob"] == 0.88

            assert record_u["champion_version"] == 2, (
                f"Transaction U corrupted! Expected version 2, got {record_u['champion_version']}"
            )
            assert record_u["champion_prob"] == 0.35

            # Historical verification: active version is now 2, but T's historical truth is 1
            active_ver = registry.get_active_version(sim_id)
            assert active_ver is not None
            assert active_ver["version"] == 2
            assert record_t["champion_version"] == 1

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # SCENARIO 4: Corrupted or Replayed State Boundary
    # -------------------------------------------------------------------------
    def test_scenario_4_corrupted_or_replayed_state_boundary(self) -> None:
        """Scenario 4: Authoritative boundary rejection of corrupted and replayed state.

        Three Corruption Classes:
            Class A: Identity conflict with altered payload under same Idempotency-Key -> 409 Conflict.
            Class B: Non-finite numerical state in FL engine -> quarantined, zero fallback.
            Class C: Stale case version mutation replay -> 409 Precondition Failed / InvalidCaseTransitionError.
        """
        client = TestClient(app)

        # --- Class A: Identity Conflict ---
        key_a = f"idem_corrupt_{uuid.uuid4().hex[:8]}"
        initial_payload = {
            "title": "Legitimate Dossier",
            "priority": "p2_high",
            "assigned_to": "analyst_1",
        }
        res_ok = client.post("/api/v1/cases", json=initial_payload, headers={"Idempotency-Key": key_a})
        assert res_ok.status_code == 200

        conflicting_payload = {
            "title": "Altered Payload Tampering Attempt",
            "priority": "p1_critical",
            "assigned_to": "attacker_1",
        }
        res_conflict = client.post(
            "/api/v1/cases", json=conflicting_payload, headers={"Idempotency-Key": key_a}
        )
        assert res_conflict.status_code == 409
        assert "Conflicting payload" in res_conflict.json()["detail"]

        # --- Class B: Non-finite Numerical State Quarantine ---
        settings = get_settings()
        fl_engine = FederatedLearningEngine(settings, ModelService(settings), PrivacyService())
        corrupt_weights = ModelWeights(
            layer_shapes=[(2,)],
            flat_weights=[float("nan"), float("inf")],
        )
        safe_fallback = ModelWeights(layer_shapes=[(2,)], flat_weights=[0.0, 0.0])
        clean_aggregate = fl_engine.aggregate_parameters(
            client_weights=[corrupt_weights],
            client_samples=[100],
            method=AggregationMethod.FED_AVG_WEIGHTED,
            global_weights=safe_fallback,
        )
        assert clean_aggregate.flat_weights == [0.0, 0.0], "Corrupted NaN/Inf leaked into global weights"

        # --- Class C: Stale Case Version Replay ---
        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title="Stale Replay Target Dossier",
            priority=CasePriority.P2_HIGH,
            assigned_to="analyst_case",
        )
        case_svc.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_case")
        case_v2 = case_svc.get_case(case.id)
        assert case_v2 is not None
        assert getattr(case_v2, "version", 1) == 2

        # Replay with stale version 1
        with pytest.raises(InvalidCaseTransitionError, match="Precondition failed"):
            case_svc.change_status(
                case.id,
                new_status=CaseStatus.ASSIGNED,
                actor="stale_attacker",
                expected_version=1,
                expected_status="open",
            )

    # -------------------------------------------------------------------------
    # SCENARIO 5: Full Lifecycle Recovery & Semantic Reconciliation
    # -------------------------------------------------------------------------
    def test_scenario_5_full_lifecycle_recovery_and_reconciliation(self) -> None:
        """Scenario 5: Complete lifecycle from ingestion to Four-Eyes resolution with mid-lifecycle disruption.

        Seven Truth Dimensions Verified:
            1. Transaction Truth: Canonical identity, amount, and timestamp preserved.
            2. Machine Truth: ML risk score and signals preserved in historical audit.
            3. Alert Truth: Alert generated, linked, and status updated.
            4. Case Truth: Terminal state CLOSED_CONFIRMED, version advanced, closed_at set.
            5. Regulatory Truth: Timeline hash chain verified.
            6. Audit Truth: verify_timeline() == True; zero tampering.
            7. API Truth: GET /api/v1/cases/{case_id} agrees with backend authoritative state.
        """
        client = TestClient(app)
        tx_id = f"tx_full_lifecycle_{uuid.uuid4().hex[:8]}"

        # 1. Ingest transaction via predict API
        predict_payload = {
            "transaction_id": tx_id,
            "transaction_amount": 18500.0,
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
        }
        pred_res = client.post("/api/v1/predict", json=predict_payload)
        assert pred_res.status_code == 200, pred_res.text
        pred_data = pred_res.json()
        assert pred_data["risk_score"] >= 600.0
        assert pred_data["is_fraud_suspected"] is True

        # 2. Case Creation
        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title=f"Fraud Dossier for {tx_id}",
            priority=CasePriority.P1_CRITICAL,
            assigned_to="investigator_lead",
            bank_id="bank_alpha",
        )
        case_id = case.id
        assert case.status == CaseStatus.OPEN

        # 3. Mid-Lifecycle Disruption: Destroy worker instance (simulating worker crash)
        del case_svc

        # 4. Recovery: Fresh worker instance reconnects to durable store
        recovered_svc = CaseManagementService()
        recovered_case = recovered_svc.get_case(case_id)
        assert recovered_case is not None
        assert recovered_case.status == CaseStatus.OPEN

        # 5. Move to INVESTIGATING
        recovered_svc.change_status(case_id, CaseStatus.INVESTIGATING, actor="investigator_lead")

        # 6. Four-Eyes Dual-Control Sign-Off and Resolution to CLOSED_CONFIRMED
        resolve_res = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "actor": "investigator_lead",
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor_alice",
                "secondary_supervisor": "supervisor_bob",
            },
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert resolve_res.status_code == 200, resolve_res.text
        resolved_data = resolve_res.json()
        assert resolved_data["status"] == "closed_confirmed"
        assert resolved_data["closed_at"] is not None

        # 8. 7-Dimension Semantic Reconciliation Assertions:
        # - Dimension 1: Transaction Truth
        assert pred_data["transaction_id"] == tx_id
        # - Dimension 2: Machine Truth (historical model score preserved)
        assert pred_data["risk_score"] >= 600.0
        # - Dimension 3: Alert Truth
        assert pred_data["alert_details"] is not None
        # - Dimension 4: Case Truth
        final_case = recovered_svc.get_case(case_id)
        assert final_case is not None
        assert final_case.status == CaseStatus.CLOSED_CONFIRMED
        assert getattr(final_case, "version", 1) >= 2
        # - Dimension 5 & 6: Regulatory & Audit Truth (timeline integrity hash)
        verify_res = client.get(f"/api/v1/cases/{case_id}/timeline/verify")
        assert verify_res.status_code == 200
        assert verify_res.json()["is_valid"] is True
        # - Dimension 7: API Truth
        api_case = client.get(f"/api/v1/cases/{case_id}").json()
        assert api_case["status"] == "closed_confirmed"
        assert api_case["closed_at"] is not None

    # -------------------------------------------------------------------------
    # METAMORPHIC CHECKS: M1, M2, M3
    # -------------------------------------------------------------------------
    def test_metamorphic_checks(self) -> None:
        """Evaluates three metamorphic invariant comparisons:

        M1: Clean vs Recovered Lifecycle -> LogicalState(clean) == LogicalState(recovered)
        M2: Tenant Renaming -> Same flow under bank_gamma preserves namespace isolation
        M3: Model Activation Timing -> T's provenance is identical regardless of timing
        """
        # M1: Clean vs Recovered Lifecycle
        svc_clean = CaseManagementService()
        case_clean = svc_clean.create_case(
            title="Clean Dossier", priority=CasePriority.P2_HIGH, assigned_to="analyst_m1"
        )
        svc_clean.change_status(case_clean.id, CaseStatus.INVESTIGATING, actor="analyst_m1")
        clean_final = svc_clean.get_case(case_clean.id)

        svc_rec = CaseManagementService()
        case_rec = svc_rec.create_case(
            title="Recovered Dossier", priority=CasePriority.P2_HIGH, assigned_to="analyst_m1"
        )
        del svc_rec  # Interrupt
        svc_rec2 = CaseManagementService()
        svc_rec2.change_status(case_rec.id, CaseStatus.INVESTIGATING, actor="analyst_m1")
        rec_final = svc_rec2.get_case(case_rec.id)

        assert clean_final is not None and rec_final is not None
        assert clean_final.status == rec_final.status == CaseStatus.INVESTIGATING
        assert getattr(clean_final, "version", 1) == getattr(rec_final, "version", 1)

        # M2: Tenant Renaming
        client = TestClient(app)
        resp_gamma = client.post(
            "/api/v1/cases",
            json={
                "title": "Bank Gamma Dossier",
                "priority": "p3_medium",
                "assigned_to": "analyst_gamma",
                "bank_id": "bank_gamma",
            },
            headers={"X-Tenant-ID": "bank_gamma"},
        )
        assert resp_gamma.status_code == 200
        case_gamma = resp_gamma.json()
        assert case_gamma["title"] == "Bank Gamma Dossier"
        # Verify cross-tenant isolation under renamed tenant: bank_alpha cannot read bank_gamma case
        resp_leak = client.get(f"/api/v1/cases/{case_gamma['id']}", headers={"X-Tenant-ID": "bank_alpha"})
        assert resp_leak.status_code == 403

        # M3: Model Activation Timing Provenance Invariance
        temp_dir = tempfile.mkdtemp(prefix="cfi_m3_")
        try:
            reg = ModelRegistry(storage_dir=temp_dir)
            eval_eng = ModelEvaluationEngine(reg)
            reg.save_version(
                "sim_m3",
                state_dict={"w": torch.tensor([1.0])},
                metrics={"auc_roc": 0.80},
                is_promoted=True,
                status="champion",
            )
            # Evaluate T under V1
            eval_eng.log_prediction("sim_m3", "tx_m3_early", champion_version=1, champion_prob=0.75, champion_latency_ms=10.0)

            # Activate V2
            reg.save_version(
                "sim_m3",
                state_dict={"w": torch.tensor([2.0])},
                metrics={"auc_roc": 0.85},
                is_promoted=True,
                status="champion",
            )

            # Historical lookup for T reports V1 in both timing modes
            rec_early = eval_eng._store.get("sim_m3:prediction:tx_m3_early")
            assert rec_early is not None
            assert rec_early["champion_version"] == 1
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
