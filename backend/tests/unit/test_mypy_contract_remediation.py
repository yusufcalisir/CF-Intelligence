"""Focused regression tests validating type-contract fixes and missingness preservation.

Tests cover:
- Bank.improvement: Arithmetic safety and preservation of None
- AutomatedRetrainingService: Direct narrowing and truthful missingness in drift auditing
- BenchmarkResult & BenchmarkRunner: Preservation of undefined/None metrics
- RollingFeatureAggregator: Safe handling of optional/missing destination country
- OpenBankingConnector: Cached token lifecycle annotations
- Dataloader: SynthAML synthetic generator provenance contract
- FeatureStoreService: Typed profile dictionaries and missingness preservation
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
    RetrainingCause,
)
from app.application.services.dataloader import _generate_mock_synthaml
from app.application.services.feature_store_service import FeatureStoreService
from app.domain.benchmark_runner import BenchmarkResult
from app.domain.entities import Bank
from app.domain.enums import DatasetProvenance
from app.domain.value_objects import EvaluationMetrics
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector
from app.infrastructure.feature_store.rolling_aggregators import (
    DEFAULT_COUNTRY_RISK,
    RollingFeatureAggregator,
)


def test_bank_improvement_all_operands_present() -> None:
    """Verify delta arithmetic when both federated and local metrics are present."""
    local = EvaluationMetrics(
        accuracy=0.80,
        precision=0.75,
        recall=0.70,
        f1_score=0.72,
        auc_roc=0.85,
    )
    federated = EvaluationMetrics(
        accuracy=0.90,
        precision=0.85,
        recall=0.80,
        f1_score=0.82,
        auc_roc=0.92,
    )
    bank = Bank(id="bank_1", name="Test Bank", local_metrics=local, federated_metrics=federated)
    imp = bank.improvement

    assert imp is not None
    assert imp["accuracy"] == pytest.approx(0.10)
    assert imp["precision"] == pytest.approx(0.10)
    assert imp["recall"] == pytest.approx(0.10)
    assert imp["f1_score"] == pytest.approx(0.10)
    assert imp["auc_roc"] == pytest.approx(0.07)


def test_bank_improvement_partial_operands_missing() -> None:
    """Verify that if either operand is None, the delta evaluates to None (never fabricated zero)."""
    # Left missing (fed has None, local has float)
    local1 = EvaluationMetrics(accuracy=0.80, precision=0.75, recall=None, f1_score=None, auc_roc=0.85)
    fed1 = EvaluationMetrics(accuracy=None, precision=0.85, recall=0.80, f1_score=None, auc_roc=None)
    bank1 = Bank(id="b1", local_metrics=local1, federated_metrics=fed1)
    imp1 = bank1.improvement

    assert imp1 is not None
    assert imp1["accuracy"] is None  # fed is None
    assert imp1["precision"] == pytest.approx(0.10)  # both present
    assert imp1["recall"] is None  # local is None
    assert imp1["f1_score"] is None  # both are None
    assert imp1["auc_roc"] is None  # fed is None


def test_bank_improvement_legitimate_numeric_zero() -> None:
    """Verify that a legitimate numeric zero delta (0.0 - 0.0) evaluates to 0.0, not None."""
    local = EvaluationMetrics(accuracy=0.0, precision=0.5, recall=0.0, f1_score=0.0, auc_roc=0.0)
    fed = EvaluationMetrics(accuracy=0.0, precision=0.5, recall=0.0, f1_score=0.0, auc_roc=0.0)
    bank = Bank(id="b_zero", local_metrics=local, federated_metrics=fed)
    imp = bank.improvement

    assert imp is not None
    assert imp["accuracy"] == 0.0
    assert imp["precision"] == 0.0
    assert imp["recall"] == 0.0
    assert imp["f1_score"] == 0.0
    assert imp["auc_roc"] == 0.0


def test_bank_improvement_uninitialized_metrics() -> None:
    """Verify improvement returns None when local or federated metrics are uninitialized."""
    bank_no_fed = Bank(id="b_nofed", local_metrics=EvaluationMetrics(accuracy=0.8))
    assert bank_no_fed.improvement is None

    bank_no_loc = Bank(id="b_noloc", federated_metrics=EvaluationMetrics(accuracy=0.9))
    assert bank_no_loc.improvement is None


def test_automated_retraining_drift_auditing_states() -> None:
    """Verify drift auditing with valid, degraded, missing, and non-finite AUC signals."""
    auditor = DriftTriggeredRetrainingService(min_auc_threshold=0.80, psi_threshold=0.25)

    # 1. Valid healthy AUC
    status_healthy = auditor.evaluate_drift_status(psi_score=0.05, current_auc=0.85)
    assert status_healthy.auc_monitoring_status == "METRIC_AVAILABLE"
    assert status_healthy.accuracy_degradation_status == "NO_DEGRADATION_DETECTED"
    assert status_healthy.monitoring_complete is True
    assert status_healthy.is_triggered is False
    assert status_healthy.cause is None

    # 2. Valid degraded AUC
    status_degraded = auditor.evaluate_drift_status(psi_score=0.05, current_auc=0.75)
    assert status_degraded.auc_monitoring_status == "METRIC_AVAILABLE"
    assert status_degraded.accuracy_degradation_status == "DEGRADATION_DETECTED"
    assert status_degraded.is_triggered is True
    assert status_degraded.cause == RetrainingCause.ACCURACY_DEGRADATION

    # 3. Missing AUC (None) -> Must not fabricate health or trigger false retraining
    status_missing = auditor.evaluate_drift_status(psi_score=0.05, current_auc=None)
    assert status_missing.auc_monitoring_status == "METRIC_UNAVAILABLE"
    assert status_missing.accuracy_degradation_status == "MONITORING_INCOMPLETE"
    assert status_missing.monitoring_complete is False
    assert status_missing.is_triggered is False
    assert status_missing.cause is None

    # 4. Non-finite AUC (NaN / Inf) -> Handled truthfully as unavailable
    status_nan = auditor.evaluate_drift_status(psi_score=0.05, current_auc=float("nan"))
    assert status_nan.auc_monitoring_status == "METRIC_UNAVAILABLE"
    assert status_nan.accuracy_degradation_status == "MONITORING_INCOMPLETE"
    assert status_nan.is_triggered is False

    status_inf = auditor.evaluate_drift_status(psi_score=0.05, current_auc=float("inf"))
    assert status_inf.auc_monitoring_status == "METRIC_UNAVAILABLE"
    assert status_inf.accuracy_degradation_status == "MONITORING_INCOMPLETE"
    assert status_inf.is_triggered is False


def test_benchmark_result_optional_metrics_schema() -> None:
    """Verify BenchmarkResult schema supports float | None without fabrication."""
    res_with_none = BenchmarkResult(
        config_id="C_TEST",
        name="Test Configuration",
        roc_auc=None,
        pr_auc=None,
        f1_score=None,
        recall_at_1pct_fpr=None,
        false_positive_rate=0.01,
        epsilon_consumed=1.0,
        total_bytes_transmitted=1024,
        rounds_to_convergence=5,
        training_time_seconds=1.23,
        inference_latency_p99_ms=4.5,
    )
    d = res_with_none.to_dict()
    assert d["roc_auc"] is None
    assert d["pr_auc"] is None
    assert d["f1_score"] is None
    assert d["recall_at_1pct_fpr"] is None


def test_rolling_aggregators_destination_country_semantics() -> None:
    """Verify destination_country missingness, whitespace, and case normalization."""
    agg = RollingFeatureAggregator()
    now = datetime.now(UTC)

    # 1. Missing destination_country (None) -> Must not crash, must use DEFAULT_COUNTRY_RISK
    tx_none = NormalizedTransaction(
        transaction_id="tx_1",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=100.0,
        destination_country=None,
        timestamp=now,
    )
    feats_none = agg.compute_features(tx_none)
    assert feats_none["country_risk_score"] == DEFAULT_COUNTRY_RISK
    # Verify None is preserved in history
    assert agg.account_history["acc_1"][-1]["destination_country"] is None

    # 2. Lowercase with whitespace: "  ir  " -> maps to FATF Blacklist "IR" (1.0)
    tx_ir = NormalizedTransaction(
        transaction_id="tx_2",
        account_id="acc_1",
        counterparty_account_id="acc_3",
        amount=50.0,
        destination_country="  ir  ",
        timestamp=now,
    )
    feats_ir = agg.compute_features(tx_ir)
    assert feats_ir["country_risk_score"] == 1.0

    # 3. Known low-risk country: "US" -> 0.05
    tx_us = NormalizedTransaction(
        transaction_id="tx_3",
        account_id="acc_1",
        counterparty_account_id="acc_4",
        amount=25.0,
        destination_country="US",
        timestamp=now,
    )
    feats_us = agg.compute_features(tx_us)
    assert feats_us["country_risk_score"] == 0.05

    # 4. Unknown/unmapped country -> DEFAULT_COUNTRY_RISK
    tx_unk = NormalizedTransaction(
        transaction_id="tx_4",
        account_id="acc_1",
        counterparty_account_id="acc_5",
        amount=15.0,
        destination_country="XX",
        timestamp=now,
    )
    feats_unk = agg.compute_features(tx_unk)
    assert feats_unk["country_risk_score"] == DEFAULT_COUNTRY_RISK


def test_open_banking_connector_token_initialization() -> None:
    """Verify OpenBankingConnector initializes _cached_token properly."""
    # When access_token is None
    conn_none = OpenBankingConnector(access_token=None)
    assert conn_none._cached_token is None
    assert conn_none._token_expires_at == 0.0

    # When access_token is provided
    conn_with_tok = OpenBankingConnector(access_token="test_bearer_token")
    assert conn_with_tok._cached_token == "test_bearer_token"
    assert conn_with_tok._token_expires_at == float("inf")


def test_synthaml_mock_generator_provenance() -> None:
    """Verify _generate_mock_synthaml returns valid synthetic fixture with provenance."""
    result = _generate_mock_synthaml(n_mock_alerts=50)
    assert result["is_synthetic"] is True
    assert result["provenance"] == DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value
    assert result["source"] == "synthetic_generator"
    assert result["n_alerts"] == 50
    assert "X" in result and "y" in result


def test_feature_store_service_profile_missingness() -> None:
    """Verify get_online_features preserves None for missing features and handles types cleanly."""
    fs = FeatureStoreService()
    fs.clear()

    # Ingest profile for customer 1
    fs.ingest_transaction(
        customer_id="cust_101",
        amount=250.0,
        merchant_id="merch_501",
        merchant_category="retail",
        merchant_risk_score=0.15,
        customer_history_score=0.92,
        chargeback_count=0,
        account_age_days=180,
    )

    requested_features = [
        "customer_history_score",
        "account_age_days",
        "merchant_risk_score",
        "non_existent_feature",
    ]

    # Query with existing customer and merchant
    results = fs.get_online_features(
        entity_rows=[{"customer_id": "cust_101", "merchant_id": "merch_501"}],
        features=requested_features,
    )
    assert len(results) == 1
    row = results[0]
    assert row["customer_history_score"] == pytest.approx(0.92)
    assert row["account_age_days"] == 180
    assert row["merchant_risk_score"] == pytest.approx(0.15)
    # Non-existent feature must be None, never fabricated
    assert row["non_existent_feature"] is None

    # Query with non-existent customer
    missing_results = fs.get_online_features(
        entity_rows=[{"customer_id": "cust_999", "merchant_id": "merch_999"}],
        features=requested_features,
    )
    assert len(missing_results) == 1
    missing_row = missing_results[0]
    for feat in requested_features:
        assert missing_row[feat] is None
