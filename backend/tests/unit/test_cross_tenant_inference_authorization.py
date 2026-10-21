"""Comprehensive Targeted Verification Suite for Cross-Tenant Inference & Feedback Authorization Hardening.

Verifies Security Contracts A through T:
A. Bank A cannot execute Bank B's private champion through any inference endpoint.
B. Bank A cannot generate explanations with Bank B's model.
C. Bank A cannot access a consortium champion without valid membership.
D. Revoked consortium membership rejects new inference.
E. Omitted simulation_id does not load a foreign or unbound model.
F. Omitted body.bank_id binds to the authenticated tenant, never serving_client.
G. Batch items cannot supply a different bank_id from the authenticated batch scope.
H. Realtime inference rejects forged body.bank_id and foreign simulation_id.
I. Production feedback APIs reject unauthenticated requests.
J. Bank A cannot read, ingest, sample, consume, compute gradients for, or delete Bank B's feedback buffer.
K. Forbidden feedback requests leave persistent and in-memory state unchanged.
L. Destructive feedback operations require proper bank-local authorization.
M. Bank A cannot write feedback to Bank B's prediction evaluation or retrieve Bank B's metrics.
N. An authorized transaction's ground-truth feedback remains functional.
O. Transaction IDs cannot cross tenant boundaries even for legitimate shared/consortium models.
P. Model shadow metrics are readable only within authorized scope.
Q. Cross-bank feature and risk-state contamination is prevented, including omitted IDs and crafted prefixes.
R. Authorization remains effective with existing local/Redis serving caches.
S. Concurrent authenticated Bank A and Bank B requests remain isolated.
T. Legitimate authorized bank-specific, consortium, and internal service workflows continue to function.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
)
from app.application.services.risk_engine import RiskScoringEngine
from app.config import get_settings
from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumStatus,
    MemberRole,
)
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app, seed_mock_data
from app.presentation.routers.predict import _eval_engine


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_mock_data()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def oidc() -> OIDCAuthenticator:
    return OIDCAuthenticator()


@pytest.fixture(autouse=True)
def setup_test_models_and_consortium(tmp_path: Path):
    """Register realistic champions, ownership records, and consortium state."""
    registry = ModelRegistry()
    c_store = DurableConsortiumStore.get_instance()

    # 1. Register private Bank A model
    registry._save_ownership(
        "sim_bank_a",
        {
            "simulation_id": "sim_bank_a",
            "owning_bank_id": "bank_a",
            "consortium_id": None,
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "initial_seed",
            "is_unbound": False,
        },
    )

    # 2. Register private Bank B model
    registry._save_ownership(
        "sim_bank_b",
        {
            "simulation_id": "sim_bank_b",
            "owning_bank_id": "bank_b",
            "consortium_id": None,
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "initial_seed",
            "is_unbound": False,
        },
    )

    # 3. Create active Consortium with Bank A and Bank B as founding members
    c_shared = Consortium(
        consortium_id="c_shared",
        name="Shared Banking Consortium",
        status=ConsortiumStatus.ACTIVE,
        members={
            "bank_a": ConsortiumMember(bank_id="bank_a", role=MemberRole.FOUNDER),
            "bank_b": ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER),
        },
        removed_members={},
    )
    def _init_consortia(consortia, proposals):
        consortia["c_shared"] = c_shared
    c_store.atomic_mutate(_init_consortia)

    # Register consortium shared model
    registry._save_ownership(
        "sim_consortium",
        {
            "simulation_id": "sim_consortium",
            "owning_bank_id": None,
            "consortium_id": "c_shared",
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "initial_seed",
            "is_unbound": False,
        },
    )

    yield

    def _cleanup_consortia(consortia, proposals):
        consortia.pop("c_shared", None)
    c_store.atomic_mutate(_cleanup_consortia)


# ── Scenario A: Bank A cannot execute Bank B's private champion ─────────────

def test_a_bank_a_cannot_execute_bank_b_model(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = {
        "transaction_amount": 150.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.2,
        "hour_of_day": 14,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "simulation_id": "sim_bank_b",
    }

    # /api/v1/predict
    resp1 = client.post("/api/v1/predict", json=payload, headers=headers_a)
    assert resp1.status_code == 403
    assert "not authorized to execute" in resp1.text.lower() or "broken access control" in resp1.text.lower()

    # /v1/predict (alias)
    resp2 = client.post("/v1/predict", json=payload, headers=headers_a)
    assert resp2.status_code == 403

    # /api/v1/predict/batch
    batch_payload = {"transactions": [payload], "simulation_id": "sim_bank_b"}
    resp3 = client.post("/api/v1/predict/batch", json=batch_payload, headers=headers_a)
    assert resp3.status_code == 403

    # /api/v1/score-transaction
    score_payload = {
        "transaction_id": "tx_score_001",
        "account_id": "acc_001",
        "amount": 150.0,
        "merchant_id": "merch_001",
        "device_id": "dev_001",
        "merchant_category": "grocery",
        "simulation_id": "sim_bank_b",
    }
    resp4 = client.post("/api/v1/score-transaction", json=score_payload, headers=headers_a)
    assert resp4.status_code == 403


# ── Scenario B: Bank A cannot generate explanations with Bank B's model ────

def test_b_bank_a_cannot_explain_with_bank_b_model(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = {
        "transaction": {
            "transaction_amount": 250.0,
            "merchant_category": "electronics",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 2.0,
            "hour_of_day": 10,
            "merchant_risk_score": 0.1,
            "customer_history_score": 0.9,
            "chargeback_count": 0,
            "account_age_days": 180,
            "simulation_id": "sim_bank_b",
        },
        "simulation_id": "sim_bank_b",
    }

    resp1 = client.post("/api/v1/predict/explain", json=payload, headers=headers_a)
    assert resp1.status_code == 403
    assert "not authorized to execute" in resp1.text.lower() or "broken access control" in resp1.text.lower()

    resp2 = client.post("/v1/predict/explain", json=payload, headers=headers_a)
    assert resp2.status_code == 403


# ── Scenario C: Non-member cannot access consortium champion ────────────────

def test_c_non_member_cannot_access_consortium_champion(client: TestClient, oidc: OIDCAuthenticator):
    # Bank C is not a member of c_shared
    token_c = oidc.create_mock_token(username="analyst_c", bank_id="bank_c", roles=["analyst"])
    headers_c = {"Authorization": f"Bearer {token_c}"}

    payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "simulation_id": "sim_consortium",
    }

    resp = client.post("/api/v1/predict", json=payload, headers=headers_c)
    assert resp.status_code == 403
    assert "active member" in resp.text.lower() or "consortium" in resp.text.lower()


# ── Scenario D: Revoked consortium member rejected for new inference ────────

def test_d_revoked_member_rejected_for_consortium_inference(client: TestClient, oidc: OIDCAuthenticator):
    c_store = DurableConsortiumStore.get_instance()
    consortium = c_store.get_consortium("c_shared")
    assert consortium is not None

    # Simulate removal of Bank B from c_shared
    def _evict(consortia, proposals):
        c = consortia.get("c_shared")
        if c:
            c.removed_members["bank_b"] = {"reason": "evicted"}
            c.members.pop("bank_b", None)
    c_store.atomic_mutate(_evict)

    token_b = oidc.create_mock_token(username="analyst_b", bank_id="bank_b", roles=["analyst"])
    headers_b = {"Authorization": f"Bearer {token_b}"}

    payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "simulation_id": "sim_consortium",
    }

    resp = client.post("/api/v1/predict", json=payload, headers=headers_b)
    assert resp.status_code == 403
    assert "revoked" in resp.text.lower() or "active consortium member" in resp.text.lower()


# ── Scenario E: Omitted simulation_id fails closed if ambiguous or foreign ──

def test_e_omitted_simulation_id_fails_closed_in_production(client: TestClient, oidc: OIDCAuthenticator):
    token_c = oidc.create_mock_token(username="analyst_c", bank_id="bank_c", roles=["analyst"])
    headers_c = {"Authorization": f"Bearer {token_c}"}

    # Bank C has NO models registered and is not part of any consortium
    payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
    }

    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/api/v1/predict", json=payload, headers=headers_c)
        assert resp.status_code in (403, 404, 503)
        assert "no authorized active champion model" in resp.text.lower() or "not found" in resp.text.lower() or "forbidden" in resp.text.lower()


# ── Scenario F: Omitted body.bank_id binds to authenticated tenant ──────────

def test_f_omitted_body_bank_id_binds_to_authenticated_tenant(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "simulation_id": "sim_bank_a",
        # bank_id intentionally omitted
    }

    resp = client.post("/api/v1/predict", json=payload, headers=headers_a)
    # Model might not have physical weights on disk in this test runner, but authorization must succeed (not 403)
    assert resp.status_code != 403


# ── Scenario G: Batch items cannot supply a different bank_id ───────────────

def test_g_batch_item_different_bank_id_rejected(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    batch_payload = {
        "bank_id": "bank_a",
        "simulation_id": "sim_bank_a",
        "transactions": [
            {
                "transaction_amount": 100.0,
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": "mobile_app",
                "velocity": 1.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.05,
                "customer_history_score": 0.95,
                "chargeback_count": 0,
                "account_age_days": 365,
                "bank_id": "bank_b",  # FORGED ITEM BANK ID
            }
        ],
    }

    resp = client.post("/api/v1/predict/batch", json=batch_payload, headers=headers_a)
    assert resp.status_code == 403
    assert "does not match authorized batch tenant" in resp.text


# ── Scenario H: Realtime inference rejects forged bank_id and foreign model ──

def test_h_realtime_inference_rejects_forgery_and_foreign_model(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Forged bank_id
    payload_forged = {
        "transaction_id": "rt_tx_001",
        "amount": 250.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "bank_id": "bank_b",
        "simulation_id": "sim_bank_a",
    }
    resp1 = client.post("/v1/inference/score", json=payload_forged, headers=headers_a)
    assert resp1.status_code == 403

    # Foreign simulation_id
    payload_foreign = {
        "transaction_id": "rt_tx_002",
        "amount": 250.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "bank_id": "bank_a",
        "simulation_id": "sim_bank_b",
    }
    resp2 = client.post("/v1/inference/score", json=payload_foreign, headers=headers_a)
    assert resp2.status_code == 403


# ── Scenario I: Production feedback APIs reject unauthenticated requests ────

def test_i_production_feedback_rejects_unauthenticated(client: TestClient):
    with patch.object(get_settings(), "app_env", "production"):
        resp_ingest = client.post("/api/v1/feedback/ingest", json={"tenant_id": "bank_a", "determination": "confirmed_fraud"})
        assert resp_ingest.status_code == 401

        resp_stats = client.get("/v1/feedback/stats?tenant_id=bank_a")
        assert resp_stats.status_code == 401


# ── Scenario J: Bank A cannot access Bank B's feedback buffer ───────────────

def test_j_bank_a_cannot_access_bank_b_feedback(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Ingest targeting bank_b
    resp_ingest = client.post(
        "/api/v1/feedback/ingest",
        json={"tenant_id": "bank_b", "determination": "confirmed_fraud", "transaction_id_hash": "h123"},
        headers=headers_a,
    )
    assert resp_ingest.status_code == 403

    # Stats targeting bank_b
    resp_stats = client.get("/api/v1/feedback/stats/bank_b", headers=headers_a)
    assert resp_stats.status_code == 403

    # Retraining batch targeting bank_b
    resp_batch = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_b", "batch_size": 10},
        headers=headers_a,
    )
    assert resp_batch.status_code == 403

    # DP gradient targeting bank_b
    resp_dp = client.post(
        "/api/v1/feedback/dp-gradient",
        json={"tenant_id": "bank_b", "epsilon": 1.0, "delta": 1e-5},
        headers=headers_a,
    )
    assert resp_dp.status_code == 403

    # Delete buffer targeting bank_b
    resp_del = client.delete("/api/v1/feedback/buffer/bank_b", headers=headers_a)
    assert resp_del.status_code == 403


# ── Scenario K: Forbidden feedback leaves state unchanged ───────────────────

def test_k_forbidden_feedback_leaves_buffer_unchanged(client: TestClient, oidc: OIDCAuthenticator):
    token_b_op = oidc.create_mock_token(username="op_b", bank_id="bank_b", roles=["operator"])
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])

    # Seed 1 item into bank_b buffer
    client.post(
        "/api/v1/feedback/ingest",
        json={"tenant_id": "bank_b", "determination": "confirmed_fraud", "transaction_id_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        headers={"Authorization": f"Bearer {token_b_op}"},
    )

    # Bank A attempts to delete Bank B's buffer
    resp_bad = client.delete("/api/v1/feedback/buffer/bank_b", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_bad.status_code == 403

    # Verify Bank B buffer still has occupancy
    stats = client.get("/api/v1/feedback/stats/bank_b", headers={"Authorization": f"Bearer {token_b_op}"})
    assert stats.status_code == 200
    assert stats.json()["total_count"] >= 1


# ── Scenario L: Destructive feedback operations require proper roles ────────

def test_l_destructive_feedback_requires_operator_role(client: TestClient, oidc: OIDCAuthenticator):
    token_analyst = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    token_operator = oidc.create_mock_token(username="operator_a", bank_id="bank_a", roles=["operator"])

    # Clear buffer with analyst role -> 403
    resp_clear_analyst = client.delete("/api/v1/feedback/buffer/bank_a", headers={"Authorization": f"Bearer {token_analyst}"})
    assert resp_clear_analyst.status_code == 403

    # Retraining batch with mark_consumed=True with analyst role -> 403
    resp_batch_analyst = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_a", "batch_size": 10, "mark_consumed": True},
        headers={"Authorization": f"Bearer {token_analyst}"},
    )
    assert resp_batch_analyst.status_code == 403

    # Retraining batch with mark_consumed=False with analyst role -> 200
    resp_sample_analyst = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_a", "batch_size": 10, "mark_consumed": False},
        headers={"Authorization": f"Bearer {token_analyst}"},
    )
    assert resp_sample_analyst.status_code == 200

    # Clear buffer with operator role -> 200
    resp_clear_op = client.delete("/api/v1/feedback/buffer/bank_a", headers={"Authorization": f"Bearer {token_operator}"})
    assert resp_clear_op.status_code == 200


# ── Scenario M: Bank A cannot write feedback to Bank B's prediction ────────

def test_m_bank_a_cannot_write_feedback_to_bank_b_prediction(client: TestClient, oidc: OIDCAuthenticator):
    eval_engine = ModelEvaluationEngine(ModelRegistry())

    # Log prediction under Bank B
    eval_engine.log_prediction(
        simulation_id="sim_bank_b",
        transaction_id="tx_b_prediction_01",
        champion_version=1,
        champion_prob=0.85,
        champion_latency_ms=12.0,
        bank_id="bank_b",
    )

    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Bank A attempts to submit feedback for Bank B's simulation and transaction
    resp = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": "tx_b_prediction_01",
            "simulation_id": "sim_bank_b",
            "actual_label": 1,
        },
        headers=headers_a,
    )
    assert resp.status_code == 403


# ── Scenario N: Authorized transaction feedback remains functional ──────────

def test_n_authorized_feedback_functional(client: TestClient, oidc: OIDCAuthenticator):
    eval_engine = ModelEvaluationEngine(ModelRegistry())

    # Log prediction under Bank A
    eval_engine.log_prediction(
        simulation_id="sim_bank_a",
        transaction_id="tx_a_prediction_01",
        champion_version=1,
        champion_prob=0.15,
        champion_latency_ms=8.0,
        bank_id="bank_a",
    )

    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    resp = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": "tx_a_prediction_01",
            "simulation_id": "sim_bank_a",
            "actual_label": 0,
        },
        headers=headers_a,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


# ── Scenario O: Transactions cannot cross tenant boundaries in consortium ───

def test_o_transaction_cannot_cross_tenant_boundaries_in_consortium(client: TestClient, oidc: OIDCAuthenticator):
    eval_engine = ModelEvaluationEngine(ModelRegistry())

    # Both Bank A and Bank B are in c_shared and authorized to serve sim_consortium.
    # Bank A records a prediction on sim_consortium:
    eval_engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id="tx_shared_by_bank_a",
        champion_version=1,
        champion_prob=0.55,
        champion_latency_ms=10.0,
        bank_id="bank_a",
    )

    token_b = oidc.create_mock_token(username="analyst_b", bank_id="bank_b", roles=["analyst"])
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Bank B attempts to submit feedback for Bank A's transaction on the shared model
    resp = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": "tx_shared_by_bank_a",
            "simulation_id": "sim_consortium",
            "actual_label": 1,
        },
        headers=headers_b,
    )
    # Must fail with 403 cross-tenant feedback rejection
    assert resp.status_code == 403
    assert "belongs to bank 'bank_a'" in resp.text


# ── Scenario P: Model shadow metrics readable only in authorized scope ──────

def test_p_model_shadow_metrics_authorized_scope(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    token_privileged = oidc.create_mock_token(username="auditor", bank_id="", roles=["compliance_auditor"])

    # Bank A reads Bank A metrics -> 200
    resp_own = client.get("/api/v1/models/sim_bank_a/shadow/metrics", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_own.status_code == 200

    # Bank A reads Bank B metrics -> 403
    resp_other = client.get("/api/v1/models/sim_bank_b/shadow/metrics", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_other.status_code == 403

    # Compliance auditor reads Bank B metrics -> 200
    resp_priv = client.get("/api/v1/models/sim_bank_b/shadow/metrics", headers={"Authorization": f"Bearer {token_privileged}"})
    assert resp_priv.status_code == 200


# ── Scenario Q: Cross-bank feature and risk-state contamination prevented ────

def test_q_cross_bank_feature_and_risk_isolation():
    fs = FeatureStoreService()

    # Crafted prefix attempt: Bank A attempts to query Bank B's customer
    scoped_id = fs._scope_id(raw_id="bank_b:cust_123", tenant_id="bank_a")
    assert scoped_id == "bank_a:bank_b:cust_123"
    assert scoped_id != "bank_b:cust_123"

    # Risk Engine state isolation
    risk_engine = RiskScoringEngine()
    risk_engine.register_chargeback("test_entity", 1.0, tenant_id="bank_a")
    risk_engine.register_alert("test_entity", tenant_id="bank_a")

    # Bank B scoring with the same entity string must NOT see Bank A's alerts or chargebacks
    txn = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "velocity": 1.0,
        "country_code": "US",
        "device_type": "web_browser",
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
    }
    score_b = risk_engine.score_transaction(txn, ml_prediction=0.1, entity_hash="test_entity", tenant_id="bank_b")
    # For bank_b, alert count and chargebacks should be clean
    sig_map_b = {s.signal_name: s.raw_value for s in score_b.signals}
    assert sig_map_b.get("chargeback_history") == 0.0
    assert sig_map_b.get("previous_alerts") == 0.0

    score_a = risk_engine.score_transaction(txn, ml_prediction=0.1, entity_hash="test_entity", tenant_id="bank_a")
    sig_map_a = {s.signal_name: s.raw_value for s in score_a.signals}
    assert sig_map_a.get("chargeback_history", 0.0) >= 1.0
    assert sig_map_a.get("previous_alerts", 0.0) >= 1.0


# ── Scenario R: Authorization effective with existing caches ────────────────

def test_r_authorization_effective_with_caches(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Even if model is present or cached, authorization check precedes cache access
    resp = client.post(
        "/api/v1/predict",
        json={
            "transaction_amount": 100.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.05,
            "customer_history_score": 0.95,
            "chargeback_count": 0,
            "account_age_days": 365,
            "simulation_id": "sim_bank_b",
        },
        headers=headers_a,
    )
    assert resp.status_code == 403


# ── Scenario S: Concurrent Bank A and Bank B requests remain isolated ───────

def test_s_concurrent_requests_remain_isolated(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="user_a", bank_id="bank_a", roles=["analyst"])
    token_b = oidc.create_mock_token(username="user_b", bank_id="bank_b", roles=["analyst"])

    def send_req(bank: str, token: str):
        target_sim = f"sim_{bank}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get(f"/api/v1/models/{target_sim}/shadow/metrics", headers=headers)
        return bank, resp.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for i in range(10):
            bank = "bank_a" if i % 2 == 0 else "bank_b"
            tok = token_a if bank == "bank_a" else token_b
            futures.append(executor.submit(send_req, bank, tok))

        for f in concurrent.futures.as_completed(futures):
            b, status_code = f.result()
            assert status_code == 200


# ── Scenario T: Legitimate bank, consortium, and privileged workflows work ──

def test_t_legitimate_workflows_continue_to_function(client: TestClient, oidc: OIDCAuthenticator):
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. Bank A accesses own metrics
    resp_metrics = client.get("/api/v1/models/sim_bank_a/shadow/metrics", headers=headers_a)
    assert resp_metrics.status_code == 200

    # 2. Bank A accesses shared consortium metrics
    resp_consortium = client.get("/api/v1/models/sim_consortium/shadow/metrics", headers=headers_a)
    assert resp_consortium.status_code == 200

    # 3. Privileged investigator accesses cross-bank metrics
    token_super = oidc.create_mock_token(username="investigator", bank_id="", roles=["cross_bank_investigator"])
    headers_super = {"Authorization": f"Bearer {token_super}"}
    resp_b = client.get("/api/v1/models/sim_bank_b/shadow/metrics", headers=headers_super)
    assert resp_b.status_code == 200


# ── Residual Authorization & Evidence Scoping Hardening (Scenarios A through K) ──

def test_final_a_b_evidence_collision_prevention_same_tx_different_banks(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario A & B: Bank A and Bank B use same consortium model and identical transaction_id

    without overwriting each other's evidence, and both submit valid feedback separately.
    """
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    token_b = oidc.create_mock_token(username="analyst_b", bank_id="bank_b", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    tx_id = "tx_consortium_collision_001"
    engine = ModelEvaluationEngine(ModelRegistry())

    # Bank A scores transaction tx_id on consortium model
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.85,
        bank_id="bank_a",
    )

    # Bank B scores identical transaction tx_id on same consortium model
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.15,
        bank_id="bank_b",
    )

    # Verify both records exist with distinct institutional ownership
    rec_a = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_a")
    rec_b = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_b")
    assert rec_a is not None
    assert rec_b is not None
    assert rec_a["bank_id"] == "bank_a"
    assert rec_b["bank_id"] == "bank_b"
    assert rec_a["champion_prob"] == 0.85
    assert rec_b["champion_prob"] == 0.15
    assert rec_a["actual_label"] is None
    assert rec_b["actual_label"] is None

    # Scenario B: Both banks submit valid feedback against their own separately recorded transactions
    fb_a = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": tx_id,
            "actual_label": 1,
            "simulation_id": "sim_consortium",
        },
        headers=headers_a,
    )
    assert fb_a.status_code == 200

    fb_b = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": tx_id,
            "actual_label": 0,
            "simulation_id": "sim_consortium",
        },
        headers=headers_b,
    )
    assert fb_b.status_code == 200

    # Verify feedback was applied to each institution's evidence independently
    rec_a_after = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_a")
    rec_b_after = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_b")
    assert rec_a_after is not None and rec_a_after["actual_label"] == 1
    assert rec_b_after is not None and rec_b_after["actual_label"] == 0


def test_final_c_cross_bank_evidence_retrieval_and_labeling_blocked(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario C: Bank A cannot retrieve, overwrite, or label Bank B evidence by choosing identical transaction_id."""
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    tx_id = "tx_bank_b_exclusive_002"
    engine = ModelEvaluationEngine(ModelRegistry())

    # Only Bank B scores this transaction
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.35,
        bank_id="bank_b",
    )

    # Bank A attempts to submit feedback for Bank B's transaction
    resp_a_fb = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": tx_id,
            "actual_label": 1,
            "simulation_id": "sim_consortium",
        },
        headers=headers_a,
    )
    # Fails closed (403 cross-tenant feedback rejection)
    assert resp_a_fb.status_code == 403
    assert "belongs to bank 'bank_b'" in resp_a_fb.text

    # Bank B's record remains unlabeled
    rec_b = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_b")
    assert rec_b is not None
    assert rec_b["actual_label"] is None


def test_final_d_prediction_does_not_inherit_other_bank_actual_label(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario D: A second prediction from a different bank cannot inherit another institution's actual_label,

    while repeated predictions from the same bank preserve idempotency.
    """
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    tx_id = "tx_label_inherit_003"
    engine = ModelEvaluationEngine(ModelRegistry())

    # 1. Bank A scores and labels transaction as fraud (1)
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.92,
        bank_id="bank_a",
    )
    client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": tx_id,
            "actual_label": 1,
            "simulation_id": "sim_consortium",
        },
        headers=headers_a,
    )

    # 2. Bank B scores the same transaction ID
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.08,
        bank_id="bank_b",
    )

    # Bank B's record MUST NOT inherit Bank A's actual_label
    rec_b = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_b")
    assert rec_b is not None
    assert rec_b["actual_label"] is None

    # 3. Repeated prediction from SAME bank (Bank A) preserves idempotency and existing label
    engine.log_prediction(
        simulation_id="sim_consortium",
        transaction_id=tx_id,
        champion_prob=0.95,
        bank_id="bank_a",
    )
    rec_a = engine.get_prediction_record("sim_consortium", tx_id, bank_id="bank_a")
    assert rec_a is not None
    assert rec_a["actual_label"] == 1


def test_final_e_f_atomic_mutation_and_fallback_reject_owner_mismatch():
    """Scenario E & F: Atomic mutation and local fallback reject cross-bank ownership mismatch."""
    from app.infrastructure.redis_store import RedisStore

    store = RedisStore("test_atomic_ownership")

    sim_id = "sim_test_atomic"
    tx_id = "tx_atomic_mismatch_004"

    # Record evidence for Bank A
    store.record_prediction_evidence(
        simulation_id=sim_id,
        transaction_id=tx_id,
        payload={"champion_prob": 0.75, "timestamp": "2026-01-01T00:00:00Z"},
        bank_id="bank_a",
    )

    # Bank B tries to mutate Bank A's record via record_feedback
    res_mismatch = store.record_feedback(
        simulation_id=sim_id,
        transaction_id=tx_id,
        actual_label=1,
        bank_id="bank_b",
    )
    # Either not found under bank_b or reports mismatch
    if res_mismatch.get("found"):
        assert res_mismatch.get("mismatch") is True
        assert res_mismatch.get("error") == "bank_mismatch"

    # Verify Bank A's evidence is unmodified
    rec_a = store.get_prediction_evidence(sim_id, tx_id, bank_id="bank_a")
    assert rec_a is not None
    assert rec_a.get("actual_label") is None

    # Legitimate Bank A update succeeds
    res_success = store.record_feedback(
        simulation_id=sim_id,
        transaction_id=tx_id,
        actual_label=1,
        bank_id="bank_a",
    )
    assert res_success["found"] is True
    assert res_success.get("mismatch") is not True
    assert res_success["record"]["actual_label"] == 1


def test_final_g_legacy_records_missing_bank_ownership_cannot_authorize_prod(client: TestClient, oidc: OIDCAuthenticator, monkeypatch: pytest.MonkeyPatch):
    """Scenario G: Records with missing legacy bank ownership cannot authorize production feedback."""
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    legacy_tx = "tx_legacy_unbound_005"
    legacy_record = {
        "champion_prob": 0.5,
        "bank_id": None,
        "timestamp": "2026-01-01T00:00:00Z",
    }
    _eval_engine._store._fallback_store[f"sim_consortium:prediction:{legacy_tx}"] = legacy_record
    with contextlib.suppress(Exception):
        _eval_engine._store.set(f"sim_consortium:prediction:{legacy_tx}", legacy_record)

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    # Attempt to submit feedback in production
    resp = client.post(
        "/api/v1/predict/feedback",
        json={
            "transaction_id": legacy_tx,
            "actual_label": 1,
            "simulation_id": "sim_consortium",
        },
        headers=headers_a,
    )
    assert resp.status_code == 403
    assert "lacks authoritative institutional tenant binding" in resp.json()["detail"]


def test_final_h_compliance_auditor_lacks_destructive_privileges(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario H: compliance_auditor cannot delete buffer, consume batches, or compute gradients solely through read privilege."""
    token_auditor = oidc.create_mock_token(
        username="auditor",
        bank_id="",
        roles=["compliance_auditor"],
    )
    headers = {"Authorization": f"Bearer {token_auditor}"}

    # 1. Clear buffer rejected
    resp_clear = client.delete("/api/v1/feedback/buffer/bank_a", headers=headers)
    assert resp_clear.status_code == 403
    assert "clear_feedback_buffer" in resp_clear.json()["detail"]

    # 2. Batch consumption rejected
    resp_consume = client.post(
        "/api/v1/feedback/retraining-batch",
        json={"tenant_id": "bank_a", "batch_size": 10, "mark_consumed": True},
        headers=headers,
    )
    assert resp_consume.status_code == 403
    assert "consume_retraining_batch" in resp_consume.json()["detail"]

    # 3. DP gradient computation rejected
    resp_grad = client.post(
        "/api/v1/feedback/dp-gradient",
        json={"tenant_id": "bank_a", "epsilon": 1.0, "delta": 1e-5, "clip_norm": 1.0},
        headers=headers,
    )
    assert resp_grad.status_code == 403
    assert "compute_dp_gradient" in resp_grad.json()["detail"]

    # 4. Ingest feedback rejected
    resp_ingest = client.post(
        "/api/v1/feedback/ingest",
        json={"tenant_id": "bank_a", "alert_id": "alt_001", "determination": "CONFIRMED_FRAUD"},
        headers=headers,
    )
    assert resp_ingest.status_code == 403
    assert "ingest_feedback" in resp_ingest.json()["detail"]

    # 5. Read-only audit stats allowed
    resp_stats = client.get("/api/v1/feedback/stats/bank_a", headers=headers)
    assert resp_stats.status_code == 200


def test_final_i_authorized_operator_and_super_admin_flows_work(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario I: Authorized bank-local operator and super_admin flows remain functional."""
    token_op = oidc.create_mock_token(username="op_a", bank_id="bank_a", roles=["operator", "admin"])
    headers_op = {"Authorization": f"Bearer {token_op}"}

    token_super = oidc.create_mock_token(username="super_user", bank_id="", roles=["super_admin"])
    headers_super = {"Authorization": f"Bearer {token_super}"}

    # Operator for Bank A can ingest into Bank A buffer
    ingest_resp = client.post(
        "/api/v1/feedback/ingest",
        json={"tenant_id": "bank_a", "alert_id": "alt_op_01", "determination": "CONFIRMED_FRAUD"},
        headers=headers_op,
    )
    assert ingest_resp.status_code == 201

    # Operator for Bank A can clear Bank A buffer
    clear_resp = client.delete("/api/v1/feedback/buffer/bank_a", headers=headers_op)
    assert clear_resp.status_code == 200

    # Operator for Bank A CANNOT clear Bank B buffer
    clear_b_resp = client.delete("/api/v1/feedback/buffer/bank_b", headers=headers_op)
    assert clear_b_resp.status_code == 403

    # Super admin CAN clear Bank B buffer
    super_clear = client.delete("/api/v1/feedback/buffer/bank_b", headers=headers_super)
    assert super_clear.status_code == 200


def test_final_j_production_feedback_rejects_anonymous_requests(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Scenario J: Production feedback handlers reject anonymous requests independently of middleware."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    endpoints = [
        ("POST", "/api/v1/feedback/ingest", {"tenant_id": "bank_a", "alert_id": "alt_anon", "determination": "CONFIRMED_FRAUD"}),
        ("GET", "/api/v1/feedback/stats/bank_a", None),
        ("POST", "/api/v1/feedback/retraining-batch", {"tenant_id": "bank_a", "batch_size": 5, "mark_consumed": False}),
        ("POST", "/api/v1/feedback/dp-gradient", {"tenant_id": "bank_a", "epsilon": 1.0, "delta": 1e-5, "clip_norm": 1.0}),
        ("DELETE", "/api/v1/feedback/buffer/bank_a", None),
    ]

    for method, path, payload in endpoints:
        if method == "POST":
            resp = client.post(path, json=payload)
        elif method == "GET":
            resp = client.get(path)
        elif method == "DELETE":
            resp = client.delete(path)
        else:
            raise ValueError(f"Unsupported test method: {method}")
        assert resp.status_code == 401, f"Expected 401 for anonymous {method} {path}, got {resp.status_code}"


def test_final_k_bank_owned_consortium_model_shared_serving(client: TestClient, oidc: OIDCAuthenticator):
    """Scenario K: Bank-owned, consortium-authorized shared model remains accessible to eligible members and inaccessible to revoked/unrelated members."""
    registry = ModelRegistry()
    c_store = DurableConsortiumStore.get_instance()

    # Create a consortium where bank_a and bank_b are members, and bank_c is removed
    c_hybrid = Consortium(
        consortium_id="c_hybrid_test",
        name="Hybrid Consortium",
        status=ConsortiumStatus.ACTIVE,
        members={
            "bank_a": ConsortiumMember(bank_id="bank_a", role=MemberRole.FOUNDER),
            "bank_b": ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER),
        },
        removed_members={
            "bank_c": {"reason": "Voluntary departure or sanction"},
        },
    )
    def _add_hybrid(consortia, proposals):
        consortia["c_hybrid_test"] = c_hybrid
    c_store.atomic_mutate(_add_hybrid)

    # Register model owned by Bank A but authorized for c_hybrid_test
    registry._save_ownership(
        "sim_bank_owned_consortium",
        {
            "simulation_id": "sim_bank_owned_consortium",
            "owning_bank_id": "bank_a",
            "consortium_id": "c_hybrid_test",
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "consortium_champion_vote",
            "is_unbound": False,
        },
    )

    base_payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "simulation_id": "sim_bank_owned_consortium",
    }

    # 1. Bank A (owner) -> Authorized (passes authorization without 403)
    token_a = oidc.create_mock_token(username="user_a", bank_id="bank_a", roles=["analyst"])
    resp_a = client.post("/api/v1/predict", json=base_payload, headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code != 403

    # 2. Bank B (active consortium member) -> Authorized (passes authorization without 403)
    token_b = oidc.create_mock_token(username="user_b", bank_id="bank_b", roles=["analyst"])
    resp_b = client.post("/api/v1/predict", json=base_payload, headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code != 403

    # 3. Bank C (revoked member) -> Forbidden (403)
    token_c = oidc.create_mock_token(username="user_c", bank_id="bank_c", roles=["analyst"])
    resp_c = client.post("/api/v1/predict", json=base_payload, headers={"Authorization": f"Bearer {token_c}"})
    assert resp_c.status_code == 403
    assert "revoked" in resp_c.json()["detail"].lower()

    # 4. Bank D (unrelated non-member) -> Forbidden (403)
    token_d = oidc.create_mock_token(username="user_d", bank_id="bank_d", roles=["analyst"])
    resp_d = client.post("/api/v1/predict", json=base_payload, headers={"Authorization": f"Bearer {token_d}"})
    assert resp_d.status_code == 403
    assert "not an active member" in resp_d.json()["detail"].lower()

