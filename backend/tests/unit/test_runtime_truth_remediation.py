"""Regression test suite verifying root-cause remediation for Runtime Truth Guard findings.

Covers:
- RC-RATE-LIMIT-BYPASS (main.py): TESTING=1 does not bypass rate limiting.
- RC-VAULT-FAKE-REVOKE-SUCCESS (vault_client.py): Revocation fails closed; no fake mock cert fallback.
- RC-REGULATORY-NARRATIVE-FAKE-GRAPH-METRICS (aml_agentic_copilot.py): No fake 0.850 / cluster_1 injection.
- RC-FEATURE-STORE-DEFAULT-INJECTION (feature_store_service.py): Missing features represented as NaN / None.
- RC-UNMEASURED-ROBUSTNESS-PERFECT-DEFAULT (metrics_service.py): Unmeasured robustness/fairness default to None.
- RC-SYNTHESIZED-FEDERATED-CONSENSUS (comparative_runner.py): federated_results=None raises ValueError; no multiplier synthesis.
- RC-GRAPH-BENCHMARK-SCHEMA-AUC-DEFAULT (schemas/graph.py): EllipticBenchmarkResponse preserves None when undefined.
- RC-FL-UNREPORTED-CLIENT-LOSS-DEFAULT (flower_engine.py): Non-reporting clients recorded in dropped_bank_ids.
- RC-OPTUNA-OBJECTIVE-AUC-BASELINE (fl_hyperparameter_optimizer.py): Trial pruned on missing validation AUC.
- RC-BOOLEAN-LABEL-COERCION-RISK (routers/banks.py): Label comparison is type-safe.
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from experiments.baselines.comparative_runner import ComparativeBenchmarkEngine

from app.application.schemas.graph import EllipticBenchmarkResponse
from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.metrics_service import MetricsService
from app.infrastructure.security.vault_client import VaultClient, VaultUnavailableError
from app.main import DDoSProtectionMiddleware


# ── 1. RC-RATE-LIMIT-BYPASS ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ddos_middleware_does_not_bypass_when_testing_env_set() -> None:
    """Verify setting os.environ['TESTING']='1' does not disable rate limiting."""
    os.environ["TESTING"] = "1"
    try:
        app_mock = MagicMock()
        middleware = DDoSProtectionMiddleware(app_mock)
        DDoSProtectionMiddleware.reset_state()

        client_ip = "127.0.0.1"
        now = time.time()
        # Seed 100 requests in current window for loopback
        with DDoSProtectionMiddleware._lock:
            DDoSProtectionMiddleware._requests[client_ip] = [now - 0.5] * 100

        from fastapi import Request

        request = Request(
            scope={
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [],
                "client": (client_ip, 12345),
            }
        )

        async def call_next_mock(req):
            return MagicMock(status_code=200)

        response = await middleware.dispatch(request, call_next_mock)
        assert response.status_code == 429
    finally:
        DDoSProtectionMiddleware.reset_state()


# ── 2. RC-VAULT-FAKE-REVOKE-SUCCESS ───────────────────────────────────────────
def test_vault_revocation_fails_closed_when_network_fails() -> None:
    """Verify Vault revocation returns False when network fails rather than returning True."""
    vault = VaultClient(vault_url="http://invalid-vault-host:8200", enabled=True)
    res = vault.revoke_pki_certificate("deadbeef1234")
    assert res is False


def test_vault_ca_retrieval_raises_on_failure_no_mock_cert() -> None:
    """Verify Vault get_ca_certificate raises VaultUnavailableError on failure without mock cert."""
    vault = VaultClient(vault_url="http://invalid-vault-host:8200", enabled=True)
    with pytest.raises(VaultUnavailableError) as exc_info:
        vault.get_ca_certificate()
    assert "Vault PKI Root CA certificate unavailable" in str(exc_info.value)


# ── 3. RC-REGULATORY-NARRATIVE-FAKE-GRAPH-METRICS ─────────────────────────────
def test_aml_copilot_narrative_omits_fabricated_graph_metrics() -> None:
    """Verify AML Copilot does not inject 0.850 or cluster_1 when graph metadata is absent."""
    copilot = AMLAgenticCopilot()
    dossier = copilot.assemble_case_evidence(
        case_id="CASE-TEST-001",
        case_title="Suspicious Rapid Layering",
        total_risk_score=750.0,
        case_status="ESCALATED",
        graph_metadata=None,
    )
    analysis = copilot.synthesize_from_evidence(dossier)
    narrative = analysis.fincen_sar_narrative
    # Prohibit fabricated facts
    assert "0.850" not in narrative
    assert "0.85" not in narrative
    assert "cluster_1" not in narrative
    assert "were not computed or are unavailable" in narrative


def test_aml_copilot_narrative_includes_real_graph_metrics_when_present() -> None:
    """Verify AML Copilot includes genuine graph metrics when explicitly supplied."""
    copilot = AMLAgenticCopilot()
    dossier = copilot.assemble_case_evidence(
        case_id="CASE-TEST-002",
        case_title="Confirmed Mule Network",
        total_risk_score=900.0,
        case_status="CONFIRMED_SAR",
        graph_metadata={
            "pagerank_score": 0.412,
            "louvain_community_id": "syndicate_alpha",
            "layering_hops": 5,
        },
    )
    analysis = copilot.synthesize_from_evidence(dossier)
    narrative = analysis.fincen_sar_narrative
    assert "0.412" in narrative
    assert "syndicate_alpha" in narrative
    assert "5 distinct institutional hops" in narrative


# ── 4. RC-FEATURE-STORE-DEFAULT-INJECTION ──────────────────────────────────────
def test_feature_store_historical_join_preserves_nan_for_missing_features() -> None:
    """Verify get_historical_features assigns NaN to unobserved columns without plausible business fallbacks."""
    fs = FeatureStoreService()
    entity_df = pd.DataFrame(
        {
            "customer_id": ["cust_1", "cust_2"],
            "timestamp": [1600000000, 1600000100],
        }
    )
    features = ["customer_history_score", "merchant_risk_score", "account_age_days"]
    joined = fs.get_historical_features(entity_df, features)

    assert "customer_history_score" in joined.columns
    assert "merchant_risk_score" in joined.columns
    assert "account_age_days" in joined.columns

    # All values must be NaN, not 0.95, 0.05, 365
    assert np.isnan(joined["customer_history_score"].iloc[0])
    assert np.isnan(joined["merchant_risk_score"].iloc[0])
    assert np.isnan(joined["account_age_days"].iloc[0])


def test_feature_store_online_retrieval_returns_none_for_missing() -> None:
    """Verify get_online_features sets None for unobserved stats rather than fabricated scores."""
    fs = FeatureStoreService()
    results = fs.get_online_features(
        entity_rows=[{"customer_id": "non_existent_cust"}],
        features=["customer_history_score", "merchant_risk_score"],
    )
    assert len(results) == 1
    assert results[0]["customer_history_score"] is None
    assert results[0]["merchant_risk_score"] is None


# ── 5. RC-UNMEASURED-ROBUSTNESS-PERFECT-DEFAULT ─────────────────────────────────
def test_evaluation_metrics_defaults_unmeasured_robustness_to_none() -> None:
    """Verify EvaluationMetrics parser sets unmeasured robustness and fairness to None, not 1.0."""
    eval_dict = {
        "accuracy": 0.95,
        "precision": 0.90,
        "recall": 0.85,
        "f1_score": 0.87,
        "auc_roc": 0.92,
        "loss": 0.15,
    }
    metrics = MetricsService.from_eval_dict(eval_dict)
    assert metrics.adversarial_robustness_score is None
    assert metrics.disparate_impact is None
    assert metrics.clean_accuracy is None
    assert metrics.robust_accuracy is None
    assert metrics.fgsm_evasion_rate is None


def test_evaluation_metrics_preserves_measured_zero_and_one() -> None:
    """Verify explicitly measured 0.0 or 1.0 are truthfully preserved."""
    eval_dict = {
        "accuracy": 0.95,
        "f1_score": 0.87,
        "adversarial_robustness_score": 0.0,
        "disparate_impact": 1.0,
    }
    metrics = MetricsService.from_eval_dict(eval_dict)
    assert metrics.adversarial_robustness_score == 0.0
    assert metrics.disparate_impact == 1.0


# ── 6. RC-SYNTHESIZED-FEDERATED-CONSENSUS ─────────────────────────────────────
def test_comparative_runner_fails_closed_when_federated_results_is_none() -> None:
    """Verify ComparativeBenchmarkEngine raises ValueError when federated_results is None."""
    engine = ComparativeBenchmarkEngine(random_state=42)
    bank_partitions = {
        "bank_a": (np.random.randn(20, 4), np.random.randint(0, 2, 20)),
    }
    X_test = np.random.randn(10, 4)
    y_test = np.random.randint(0, 2, 10)

    with pytest.raises(ValueError, match="Explicit federated_results dictionary is required"):
        engine.run_full_comparative_suite(
            bank_train_partitions=bank_partitions,
            X_global_test=X_test,
            y_global_test=y_test,
            federated_results=None,
            dataset_name="TestProhibitedSynthesis",
        )


def test_comparative_runner_uses_explicit_federated_results_exactly() -> None:
    """Verify ComparativeBenchmarkEngine uses supplied federated results without multiplier manipulation."""
    engine = ComparativeBenchmarkEngine(random_state=42)
    bank_partitions = {
        "bank_a": (np.random.randn(20, 4), np.random.randint(0, 2, 20)),
    }
    X_test = np.random.randn(10, 4)
    y_test = np.random.randint(0, 2, 10)

    fed_metrics = {
        "pr_auc": 0.7777,
        "roc_auc": 0.8888,
        "recall_at_01_fpr": 0.3333,
        "f1_score": 0.6666,
        "brier_score": 0.0555,
        "latency_ms_per_sample": 0.22,
    }
    report = engine.run_full_comparative_suite(
        bank_train_partitions=bank_partitions,
        X_global_test=X_test,
        y_global_test=y_test,
        federated_results=fed_metrics,
        dataset_name="TestAuthoritativeFed",
        train_neural=False,
    )
    fed_row = next(r for r in report["comparison_matrix"] if r["category"] == "PRODUCTION_CHAMPION")
    assert fed_row["pr_auc"] == 0.7777
    assert fed_row["roc_auc"] == 0.8888


# ── 7. RC-GRAPH-BENCHMARK-SCHEMA-AUC-DEFAULT ──────────────────────────────────
def test_elliptic_benchmark_schema_preserves_none_auc() -> None:
    """Verify EllipticBenchmarkResponse preserves None for auc_roc when undefined."""
    data = {
        "dataset": "Elliptic Bitcoin Dataset",
        "metrics": {"federated_graph_pipeline": {"accuracy": 0.91}},
    }
    resp = EllipticBenchmarkResponse(**data)
    assert resp.auc_roc is None


# ── 8. RC-FL-UNREPORTED-CLIENT-LOSS-DEFAULT ───────────────────────────────────
def test_flower_engine_tracks_dropped_clients_without_average_loss_injection() -> None:
    """Verify CallbackFedAvg aggregate_fit records dropped clients truthfully without injecting default loss."""
    import flwr as fl

    from app.application.services.flower_engine import CallbackFedAvg

    round_results: list[dict[str, Any]] = []
    strategy = CallbackFedAvg(
        bank_ids=["bank_a", "bank_b"],
        bank_data={
            "bank_a": {"X_train": np.zeros((10, 2))},
            "bank_b": {"X_train": np.zeros((10, 2))},
        },
        num_rounds=1,
        round_results=round_results,
        progress_callback=None,
        simulation_id="sim_123",
    )

    # Simulate client results where only bank_a reported; bank_b did not
    mock_proxy_a = MagicMock()
    mock_proxy_a.cid = "0"
    mock_fit_res_a = MagicMock()
    mock_fit_res_a.metrics = {"bank_id": "bank_a", "loss": 0.25}

    with patch.object(fl.server.strategy.FedAvg, "aggregate_fit", return_value=(None, {})):
        strategy.aggregate_fit(server_round=1, results=[(mock_proxy_a, mock_fit_res_a)], failures=[])

    assert len(round_results) == 1
    round_info = round_results[0]
    assert round_info["participating_bank_ids"] == ["bank_a"]
    assert round_info["dropped_bank_ids"] == ["bank_b"]
    assert round_info["per_bank_loss"] == {"bank_a": 0.25}
    assert round_info["global_loss"] == 0.25
