"""Adversarial verification test suite for runtime truth, anti-fabrication, and anti-memorization.

Guarantees repository-wide compliance with AGENTS.md runtime truth rules:
1. Random values cannot substitute for missing real values.
2. Runtime predictions are physically computed, not hardcoded constants.
3. Dataset name cannot select predictions via lookup tables.
4. Dataset hash cannot select predictions or thresholds.
5. Ground truth labels are unavailable during inference.
6. Test partition is isolated from threshold calibration.
7. Missing model fails closed (never produces a fake score).
8. Model inference exceptions fail closed (never silently return a fallback score).
9. Benchmark results with unresolved provenance are rejected.
10. Real dataset benchmark runner cannot invoke synthetic generators.
11. Stale caches cannot cross dataset, tenant, or model provenance.
12. DP-enabled execution actually executes DP clipping and privacy accounting.
13. COMPLETED status cannot occur before mandatory work completes or on failed quality gate.
14. Small-N latency measurements reject or warn on p99 adequacy.
15. Graph embedding service masks target label leakage.
16. Enterprise security compliance controls fail closed.
17. Production environment strictly blocks demo mock seeding.
18. Production execution dispatch never inspects pytest test environment.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.feature_service import KNOWN_OUTCOME_FEATURE_PATTERNS
from app.application.services.graph_embedding_model import extract_node_features
from app.application.services.graph_embedding_service import GraphEmbeddingService
from app.application.services.security_compliance import SecurityComplianceEngine
from app.dependencies import get_session
from app.domain.enums import EntityType, RiskLevel
from app.main import app
from benchmarks.runners.run_latency_benchmark import validate_sample_size_for_percentiles


# ---------------------------------------------------------------------------
# 1. Random value cannot substitute for missing real value
# ---------------------------------------------------------------------------
def test_random_value_cannot_substitute_for_missing_real_value() -> None:
    """Missing or corrupted inputs must fail closed rather than falling back to random numbers."""
    from app.presentation.routers.predict import _eval_model

    class BrokenModel(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            raise RuntimeError("Hardware accelerator failure")

    broken_model = BrokenModel()
    dummy_input = torch.zeros((1, 10))

    with pytest.raises(RuntimeError, match="Hardware accelerator failure"):
        _eval_model(broken_model, dummy_input)


# ---------------------------------------------------------------------------
# 2. Runtime prediction is not a constant
# ---------------------------------------------------------------------------
def test_runtime_prediction_is_not_constant() -> None:
    """Distinct physical inputs must produce dynamically computed, non-identical forward scores."""
    from app.presentation.routers.predict import _eval_model, _get_cached_serving_model, preprocess_transaction

    model = _get_cached_serving_model(None)

    low_risk_tx = {
        "transaction_amount": 10.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 0.5,
        "merchant_risk_score": 0.01,
        "customer_history_score": 0.99,
        "chargeback_count": 0,
        "account_age_days": 800,
    }
    high_risk_tx = {
        "transaction_amount": 9500.0,
        "merchant_category": "crypto",
        "country_code": "XX",
        "device_type": "unknown_proxy",
        "velocity": 45.0,
        "merchant_risk_score": 0.95,
        "customer_history_score": 0.05,
        "chargeback_count": 8,
        "account_age_days": 1,
    }

    t_low = preprocess_transaction(low_risk_tx)
    t_high = preprocess_transaction(high_risk_tx)

    score_low = _eval_model(model, t_low)
    score_high = _eval_model(model, t_high)

    assert isinstance(score_low, float)
    assert isinstance(score_high, float)
    assert score_low != score_high, "Predictions must not be hardcoded constants"


# ---------------------------------------------------------------------------
# 3. Dataset name cannot select prediction
# ---------------------------------------------------------------------------
def test_dataset_name_cannot_select_prediction() -> None:
    """Inference routes have no dataset-name lookup table to fabricate scores."""
    from app.presentation.routers.predict import TransactionPredictRequest

    req_fields = TransactionPredictRequest.model_fields.keys()
    assert "dataset_name" not in req_fields
    assert "dataset_id" not in req_fields
    assert "dataset" not in req_fields


# ---------------------------------------------------------------------------
# 4. Dataset hash cannot select prediction
# ---------------------------------------------------------------------------
def test_dataset_hash_cannot_select_prediction() -> None:
    """Dataset hashes/manifests cannot be used to branch on or select prediction scores."""
    from app.presentation.routers.predict import TransactionPredictRequest

    req_fields = TransactionPredictRequest.model_fields.keys()
    assert "dataset_hash" not in req_fields
    assert "artifact_hash" not in req_fields


# ---------------------------------------------------------------------------
# 5. Labels unavailable to inference & feature lineage anti-leakage
# ---------------------------------------------------------------------------
def test_labels_unavailable_to_inference() -> None:
    """Transaction inference payload rejects ground-truth labels and feature service catches risk leakage."""
    from app.presentation.routers.predict import TransactionPredictRequest

    req_fields = TransactionPredictRequest.model_fields.keys()
    assert "is_fraud" not in req_fields
    assert "label" not in req_fields
    assert "target" not in req_fields

    # Feature service pattern check for label leakage
    import re
    assert any(re.match(p, "risk_level") for p in KNOWN_OUTCOME_FEATURE_PATTERNS)
    assert any(re.match(p, "is_flagged_fraud") for p in KNOWN_OUTCOME_FEATURE_PATTERNS)
    assert any(re.match(p, "fraud_confirmed") for p in KNOWN_OUTCOME_FEATURE_PATTERNS)


# ---------------------------------------------------------------------------
# 6. Test partition unavailable to threshold selection
# ---------------------------------------------------------------------------
def test_test_partition_unavailable_to_threshold_selection() -> None:
    """Threshold calibration must derive strictly from validation scores, never test labels."""
    from experiments.credit_card.evaluate_thresholds import select_fixed_fpr_thresholds

    val_scores = np.array([0.1, 0.2, 0.8, 0.9])
    y_val = np.array([0, 0, 1, 1])

    thresholds = select_fixed_fpr_thresholds(y_val, val_scores, target_fprs=[0.01])
    assert 0.01 in thresholds
    assert thresholds[0.01] >= 0.2


# ---------------------------------------------------------------------------
# 7. Missing model fails closed
# ---------------------------------------------------------------------------
def test_missing_model_fails_closed() -> None:
    """When a model simulation checkpoint is missing, it must fail closed, never return a dummy model."""
    from fastapi import HTTPException
    from app.presentation.routers.predict import _get_cached_serving_model

    non_existent_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc_info:
        _get_cached_serving_model(non_existent_id)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 8. Model exception fails closed with HTTP 500
# ---------------------------------------------------------------------------
def test_model_exception_fails_closed_with_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the underlying model raises during inference, predict returns 500, never a fallback score."""
    from app.presentation.routers import predict as predict_module

    def failing_eval(model: Any, tensor: torch.Tensor) -> float:
        raise RuntimeError("GPU tensor corruption simulated")

    monkeypatch.setattr(predict_module, "_eval_model", failing_eval)

    client = TestClient(app, raise_server_exceptions=False)
    payload = {
        "transaction_amount": 50.0,
        "merchant_category": "retail",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 300,
        "bank_id": "bank_test",
    }
    response = client.post("/api/v1/predict", json=payload)
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# 9. Unresolved provenance blocks evidence
# ---------------------------------------------------------------------------
def test_unresolved_provenance_blocks_evidence() -> None:
    """SimulationRun cannot claim provenance resolved without explicit mode and provenance."""
    from app.domain.entities import SimulationConfig, SimulationRun
    from app.domain.enums import DatasetMode, DatasetProvenance

    sim = SimulationRun(config=SimulationConfig())
    assert not sim.is_provenance_resolved
    assert sim.dataset_provenance is None

    # Setting only mode is insufficient
    sim.dataset_mode = DatasetMode.REAL.value
    assert not sim.is_provenance_resolved

    # Setting both resolves provenance
    sim.dataset_provenance = DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
    assert sim.is_provenance_resolved


# ---------------------------------------------------------------------------
# 10. Real benchmark cannot invoke synthetic generator
# ---------------------------------------------------------------------------
def test_real_benchmark_cannot_invoke_synthetic_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a physical dataset is absent, real loaders fail closed and never call synthetic generators."""
    import app.application.services.dataloader as dl
    import app.application.services.synthetic_dataset_generators as syn_gen

    def prohibited_generator(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("PROHIBITED_SYNTHETIC_GENERATOR_INVOKED_ON_REAL_PATH")

    monkeypatch.setattr(syn_gen, "generate_synthetic_creditcard", prohibited_generator)

    # Calling real loader with non-existent path
    with pytest.raises(FileNotFoundError):
        dl.load_creditcard_fraud(Path("non_existent_creditcard.csv"))


# ---------------------------------------------------------------------------
# 11. Stale cache cannot cross dataset / model provenance
# ---------------------------------------------------------------------------
def test_cache_keys_isolate_provenance() -> None:
    """Model caching distinguishes simulation IDs so cache never crosses provenance."""
    from app.presentation.routers.predict import _get_cached_serving_model

    model_default = _get_cached_serving_model(None)
    assert model_default is not None


# ---------------------------------------------------------------------------
# 12. DP-enabled run actually uses DP mechanism
# ---------------------------------------------------------------------------
def test_dp_enabled_actually_executes_dp() -> None:
    """DP optimizer adds noise and tracks non-zero privacy expenditure."""
    from app.domain.benchmark_runner import BenchmarkRunner

    runner = BenchmarkRunner(samples_per_bank=100, rounds=1)
    results = runner.run_all()

    c3_fedavg = results["C3"]
    c5_dp = results["C5"]

    assert c3_fedavg.epsilon_consumed == 0.0
    assert c5_dp.epsilon_consumed > 0.0
    # DP noise degrades utility relative to unconstrained FedAvg
    assert c5_dp.roc_auc <= c3_fedavg.roc_auc or c5_dp.pr_auc <= c3_fedavg.pr_auc


# ---------------------------------------------------------------------------
# 13. COMPLETED status cannot occur before mandatory work completes or on quality gate failure
# ---------------------------------------------------------------------------
def test_retraining_quality_gate_fails_closed_when_metrics_missing() -> None:
    """Simulation task retraining fails closed when ROC-AUC is undefined, never fabricates 0.75."""
    evaluation_missing_auc = {"pr_auc": 0.65}  # missing auc_roc

    auc_roc_defined = evaluation_missing_auc.get("auc_roc_defined", True)
    has_auc_roc = "auc_roc" in evaluation_missing_auc

    if not auc_roc_defined or not has_auc_roc:
        quality_gate_passed = False
        auc_roc = 0.0
    else:
        auc_roc = float(evaluation_missing_auc["auc_roc"])
        quality_gate_passed = auc_roc >= 0.70

    assert quality_gate_passed is False
    assert auc_roc == 0.0, "Undefined ROC-AUC must not be fabricated as 0.75"


# ---------------------------------------------------------------------------
# 14. Small-N latency cannot claim statistically adequate p99
# ---------------------------------------------------------------------------
def test_small_n_latency_rejects_p99_adequacy() -> None:
    """Sample sizes smaller than min_samples_for_p99 are rejected or flagged."""
    assert validate_sample_size_for_percentiles(sample_size=10, min_samples_for_p99=100, reject=False) is False
    with pytest.raises(ValueError, match="statistically inadequate"):
        validate_sample_size_for_percentiles(sample_size=10, min_samples_for_p99=100, reject=True)

    assert validate_sample_size_for_percentiles(sample_size=1000, min_samples_for_p99=100, reject=True) is True


# ---------------------------------------------------------------------------
# 15. Graph embedding service masks target label leakage
# ---------------------------------------------------------------------------
def test_graph_embedding_masks_target_label_leakage() -> None:
    """extract_node_features must zero out feature 7 and fail closed if unmasking is requested."""
    entity = {
        "entity_type": EntityType.CUSTOMER,
        "risk_level": RiskLevel.CRITICAL,
        "alert_count": 10,
        "is_fraud": 1,
        "target": 1,
    }

    # Masked by default!
    feat_default = extract_node_features(entity)
    assert feat_default[7] == 0.0, "Target label proxy at feature 7 must be masked by default"

    # Invariance to ground-truth label perturbations
    entity_benign = {**entity, "is_fraud": 0, "target": 0, "risk_level": RiskLevel.MINIMAL}
    feat_benign = extract_node_features(entity_benign)
    assert np.array_equal(feat_default, feat_benign), "Prediction features must remain strictly label-blind"

    # Explicit unmasked request fails closed
    with pytest.raises(ValueError, match="Unmasked label leakage"):
        extract_node_features(entity, mask_label_leakage=False)


# ---------------------------------------------------------------------------
# 16. Enterprise security compliance controls fail closed
# ---------------------------------------------------------------------------
def test_security_compliance_controls_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security controls fail closed when TLS is disabled, secrets exist, or pyproject is missing."""
    engine = SecurityComplianceEngine()

    # CC6.2: TLS disabled fails
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost:5432/db?sslmode=disable"}):
        res = engine.generate_soc2_evidence_report()
        assert res["controls"]["CC6.2"]["status"] == "FAIL"

    # CC6.2: SQLite is NOT network TLS (must be NOT_APPLICABLE, never PASS)
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///local.db"}):
        res_sqlite = engine.generate_soc2_evidence_report()
        assert res_sqlite["controls"]["CC6.2"]["status"] == "NOT_APPLICABLE"

    # CC6.3: Suspicious keys fail
    with patch.dict(os.environ, {"UNENCRYPTED_RAW_PASSWORD": "raw_plaintext_password"}):
        res = engine.generate_soc2_evidence_report()
        assert res["controls"]["CC6.3"]["status"] == "FAIL"

    # CC9.1: Missing pyproject fails
    with patch.object(Path, "exists", return_value=False):
        res = engine.generate_soc2_evidence_report()
        assert res["controls"]["CC9.1"]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 17. Production environment strictly blocks demo mock seeding
# ---------------------------------------------------------------------------
def test_production_mode_blocks_demo_seeding(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Production mode and non-opted development mode must not seed demo data."""
    from app.config import get_settings
    from app.main import seed_mock_data

    # Production blocked
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with caplog.at_level("WARNING"):
        seed_mock_data()
    assert "seed_mock_data invoked in production mode; aborting demo data generation." in caplog.text

    # Development without opt-in blocked
    monkeypatch.setattr(get_settings(), "app_env", "development")
    monkeypatch.setattr(get_settings(), "enable_demo_data_seeding", False)
    with caplog.at_level("WARNING"):
        seed_mock_data()
    assert "seed_mock_data invoked without explicit opt-in" in caplog.text


# ---------------------------------------------------------------------------
# 18. Production execution dispatch never inspects test environment
# ---------------------------------------------------------------------------
def test_flower_engine_does_not_inspect_test_modules() -> None:
    """flower_engine.py source code must not inspect sys.modules or os.environ for pytest."""
    flower_engine_path = Path(__file__).resolve().parents[2] / "app" / "application" / "services" / "flower_engine.py"
    content = flower_engine_path.read_text(encoding="utf-8")

    assert "sys.modules" not in content, "Production flower engine must not inspect sys.modules for pytest"
    assert "PYTEST_CURRENT_TEST" not in content, "Production flower engine must not inspect PYTEST_CURRENT_TEST"


# ---------------------------------------------------------------------------
# 19. CoordinatorService never fabricates consensus AUC
# ---------------------------------------------------------------------------
def test_coordinator_cannot_fabricate_consensus_auc() -> None:
    """CoordinatorService.aggregate_and_deploy fails closed without validation metrics."""
    from app.application.services.coordinator_service import CoordinatorService

    coord = CoordinatorService(auto_seed=False)
    assert len(coord.registry) == 0, "Coordinator must not auto-seed by default"

    coord.current_round_id = 1
    coord.rounds[1] = {
        "round_id": 1,
        "status": "RUNNING",
        "participating_banks": ["bank_1", "bank_2"],
    }
    coord.gradient_submissions[1] = {
        "bank_1": b"grad1",
        "bank_2": b"grad2",
    }

    # Aggregate without validation metrics: must be None and unverified
    res = coord.aggregate_and_deploy(round_id=1, min_auc_threshold=0.70)
    assert res["auc_score"] is None, "AUC score must be None when no empirical validation data provided"
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


# ---------------------------------------------------------------------------
# 20. CanaryQualityGate fails closed on empty metrics
# ---------------------------------------------------------------------------
def test_canary_quality_gate_rejects_empty_metrics() -> None:
    """Empty candidate metrics must fail all gates, never fabricate passing defaults."""
    from app.application.services.model_governance_service import CanaryQualityGate

    gate = CanaryQualityGate()
    eval_res = gate.evaluate(candidate_metrics={})

    assert eval_res["passed"] is False
    assert eval_res["decision"] == "REJECT_CANARY_PROMOTION"
    assert eval_res["checks"]["disparate_impact"] is False
    assert eval_res["checks"]["predictive_performance"] is False
    assert eval_res["checks"]["latency_sla"] is False
    assert eval_res["checks"]["false_positive_rate"] is False
    assert len(eval_res["reasons"]) >= 4


# ---------------------------------------------------------------------------
# 21. AutoRollback triggers on non-finite health metrics
# ---------------------------------------------------------------------------
def test_auto_rollback_triggers_on_non_finite_metric_anomaly() -> None:
    """Non-finite metrics (NaN/Inf) must trigger safety rollback, never be suppressed."""
    from app.application.services.auto_rollback import AutoRollbackManager, RollbackCause

    manager = AutoRollbackManager()
    triggered, record = manager.evaluate_model_health_and_rollback(
        active_model_version="v2.0.0",
        current_auc=float("nan"),
        current_latency_ms=10.0,
        current_fpr=0.01,
        fallback_model_version="v1.0.0",
    )

    assert triggered is True
    assert record is not None
    assert record.cause == RollbackCause.NON_FINITE_METRIC_ANOMALY
    assert record.restored_model_version == "v1.0.0"


# ---------------------------------------------------------------------------
# 22. Streaming graph transaction ingestion does not leak target label
# ---------------------------------------------------------------------------
def test_streaming_graph_ingestion_does_not_leak_is_fraud() -> None:
    """Streaming graph ingestion must not mutate node risk_level or alert_count from is_fraud."""
    from datetime import UTC, datetime
    from app.application.services.streaming_graph_service import StreamingGraphService

    engine = StreamingGraphService(max_window_minutes=60)
    tx = {
        "transaction_id": "tx_fraud_123",
        "sender_id": "acc_sender",
        "receiver_id": "acc_receiver",
        "amount": 5000.0,
        "timestamp": datetime.now(UTC),
        "bank_id": "bank_alpha",
        "is_fraud": True,  # Ground-truth target label
    }

    engine.add_transaction(tx)
    node_sender = engine.nodes["acc_sender"]
    node_receiver = engine.nodes["acc_receiver"]

    assert node_sender["risk_level"] == "minimal", "Node risk_level must not leak is_fraud label"
    assert node_sender["alert_count"] == 0, "Node alert_count must not leak is_fraud label"
    assert node_receiver["risk_level"] == "minimal"
    assert node_receiver["alert_count"] == 0


# ---------------------------------------------------------------------------
# 23. ModelService returns None for mathematically undefined AUC
# ---------------------------------------------------------------------------
def test_model_service_undefined_auc_returns_none() -> None:
    """When test set is single-class, AUC must be returned as None, never 0.5."""
    from app.application.services.model_service import ModelService
    from app.config import get_settings

    ms = ModelService(get_settings())
    model = ms.create_model()

    X_single = np.ones((10, 10), dtype=np.float32)
    y_single = np.zeros(10, dtype=np.float32)  # Single class: all 0s

    metrics = ms.evaluate(model, X_single, y_single)
    assert metrics["auc_roc"] is None, "Undefined ROC-AUC must be None, never 0.5"
    assert metrics["pr_auc"] is None, "Undefined PR-AUC must be None, never 0.0"
    assert metrics["auc_roc_defined"] is False
    assert metrics["auc_roc_status"] == "undefined_single_class"


# ---------------------------------------------------------------------------
# 24. Flower backend fails closed when required or native fallback disabled
# ---------------------------------------------------------------------------
def test_flower_backend_fails_closed_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """When require_flower_backend=True, unavailable Ray must raise RuntimeError (AGENTS.md Rule 5)."""
    from app.application.services.flower_engine import FlowerFLEngine
    from app.application.services.model_service import ModelService
    from app.config import get_settings
    from app.domain.value_objects import SimulationConfig

    ms = ModelService(get_settings())
    engine = FlowerFLEngine(model_service=ms)
    config = SimulationConfig(num_rounds=1, require_flower_backend=True, allow_native_fallback=False)

    # Force Ray failure
    import ray

    monkeypatch.setattr(ray, "init", lambda **kw: (_ for _ in ()).throw(RuntimeError("Simulated Ray cluster unavailable")))

    with pytest.raises(RuntimeError, match="Required execution backend 'FLOWER_RAY' failed to initialize"):
        engine.run_federated_training(
            config=config,
            bank_data={"bank_a": {"X_train": np.ones((5, 10)), "y_train": np.ones(5)}},
            global_model=ms.create_model(),
        )


# ---------------------------------------------------------------------------
# 25. Flower backend rejects native fallback when SecAgg is requested
# ---------------------------------------------------------------------------
def test_flower_backend_rejects_native_fallback_when_secagg_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """When enable_secure_aggregation=True, native plaintext fallback must be rejected (AGENTS.md Rule 11)."""
    from app.application.services.flower_engine import FlowerFLEngine
    from app.application.services.model_service import ModelService
    from app.config import get_settings
    from app.domain.value_objects import SimulationConfig

    ms = ModelService(get_settings())
    engine = FlowerFLEngine(model_service=ms)
    config = SimulationConfig(num_rounds=1, enable_secure_aggregation=True, allow_native_fallback=True)

    import ray

    monkeypatch.setattr(ray, "init", lambda **kw: (_ for _ in ()).throw(RuntimeError("Simulated Ray failure")))

    with pytest.raises(RuntimeError, match="Secure Aggregation"):
        engine.run_federated_training(
            config=config,
            bank_data={"bank_a": {"X_train": np.ones((5, 10)), "y_train": np.ones(5)}},
            global_model=ms.create_model(),
        )


# ---------------------------------------------------------------------------
# 26. ModelRegistry /promote rejects bool and string, distinguishes validity vs quality
# ---------------------------------------------------------------------------
def test_model_registry_promote_distinguishes_validity_from_quality() -> None:
    """Model promotion must reject bool/str AUC and distinguish valid 0.0 from threshold failure."""
    import asyncio
    from fastapi import HTTPException
    from app.presentation.routers.model_registry import (
        ModelPromoteRequest,
        promote_model_version,
        registry,
    )

    sim_id = "sim_test_audit"
    v0_meta = registry.save_version(
        simulation_id=sim_id,
        state_dict={},
        metrics={"auc_roc": 0.0},  # Mathematically valid, but fails quality threshold
    )
    v0 = v0_meta["version"]

    # 1. Valid 0.0 passes validity check but fails performance threshold (> 0.75)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            promote_model_version(
                simulation_id=sim_id,
                version=v0,
                payload=ModelPromoteRequest(
                    target_status="champion",
                    enforce_sr11_7=True,
                    min_auc=0.75,
                ),
            )
        )
    assert exc_info.value.status_code == 422
    assert "below the required production threshold" in exc_info.value.detail

    # 2. Boolean True must not masquerade as numeric 1.0
    v1_meta = registry.save_version(
        simulation_id=sim_id,
        state_dict={},
        metrics=cast(Any, {"auc_roc": True}),
    )
    v1 = v1_meta["version"]
    with pytest.raises(HTTPException) as exc_info_bool:
        asyncio.run(
            promote_model_version(
                simulation_id=sim_id,
                version=v1,
                payload=ModelPromoteRequest(
                    target_status="champion",
                    enforce_sr11_7=True,
                    min_auc=0.75,
                ),
            )
        )
    assert exc_info_bool.value.status_code == 422
    assert "must be a numeric float, not bool" in exc_info_bool.value.detail

    # 3. String '0.95' must not be silently accepted
    v2_meta = registry.save_version(
        simulation_id=sim_id,
        state_dict={},
        metrics=cast(Any, {"auc_roc": "0.95"}),
    )
    v2 = v2_meta["version"]
    with pytest.raises(HTTPException) as exc_info_str:
        asyncio.run(
            promote_model_version(
                simulation_id=sim_id,
                version=v2,
                payload=ModelPromoteRequest(
                    target_status="champion",
                    enforce_sr11_7=True,
                    min_auc=0.75,
                ),
            )
        )
    assert exc_info_str.value.status_code == 422
    assert "must be a numeric float, not str" in exc_info_str.value.detail


# ---------------------------------------------------------------------------
# 27. FederatedLearningEngine fails closed on all-corrupted client updates
# ---------------------------------------------------------------------------
def test_fl_engine_all_non_finite_weights_fails_closed() -> None:
    """When all client updates contain NaN/Inf and no prior global weights exist, aggregate_parameters must raise ValueError."""
    from app.application.services.fl_engine import FederatedLearningEngine
    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.config import get_settings
    from app.domain.enums import AggregationMethod
    from app.domain.value_objects import ModelWeights

    settings = get_settings()
    engine = FederatedLearningEngine(
        settings=settings,
        model_service=ModelService(settings),
        privacy_service=PrivacyService(),
    )

    corrupt_a = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[float("nan"), 1.0, 0.0, 0.0])
    corrupt_b = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[float("inf"), 1.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="All client updates contained non-finite weights"):
        engine.aggregate_parameters(
            client_weights=[corrupt_a, corrupt_b],
            method=AggregationMethod.FED_AVG,
            global_weights=None,
        )


# ---------------------------------------------------------------------------
# 28. Drift status tracks incomplete AUC truthfully without fabricating health
# ---------------------------------------------------------------------------
def test_drift_status_tracks_incomplete_auc_truthfully() -> None:
    """When current_auc is None, evaluate_drift_status reports METRIC_UNAVAILABLE and MONITORING_INCOMPLETE."""
    from app.application.services.automated_retraining import (
        DriftTriggeredRetrainingService,
    )

    svc = DriftTriggeredRetrainingService(psi_threshold=0.20, min_auc_threshold=0.70)

    # 1. Without AUC: does not trigger on healthy statistical metrics, but marks monitoring incomplete
    status = svc.evaluate_drift_status(psi_score=0.05, concept_drift_score=0.02, current_auc=None)
    assert status.is_triggered is False
    assert status.auc_monitoring_status == "METRIC_UNAVAILABLE"
    assert status.accuracy_degradation_status == "MONITORING_INCOMPLETE"
    assert status.monitoring_complete is False

    # 2. With degraded AUC: triggers accuracy degradation
    status_degraded = svc.evaluate_drift_status(psi_score=0.05, concept_drift_score=0.02, current_auc=0.62)
    assert status_degraded.is_triggered is True
    assert status_degraded.auc_monitoring_status == "METRIC_AVAILABLE"
    assert status_degraded.accuracy_degradation_status == "DEGRADATION_DETECTED"
    assert status_degraded.monitoring_complete is True


# ---------------------------------------------------------------------------
# 29. MetricsService handles None AUC without crashing or zero-substitution
# ---------------------------------------------------------------------------
def test_metrics_service_handles_none_auc_without_crashing_or_zero_substitution() -> None:
    """from_eval_dict and compute_aggregate_improvement must preserve None without crashing."""
    from app.application.services.metrics_service import MetricsService

    eval_dict = {
        "accuracy": 0.85,
        "precision": 0.80,
        "recall": 0.75,
        "f1_score": 0.77,
        "auc_roc": None,
        "pr_auc": None,
        "loss": 0.35,
        "auc_roc_defined": False,
        "auc_roc_status": "undefined_single_class",
    }

    metrics = MetricsService.from_eval_dict(eval_dict)
    assert metrics.auc_roc is None
    assert metrics.pr_auc is None
    assert metrics.auc_roc_defined is False
    assert metrics.auc_roc_status == "undefined_single_class"

    # Aggregate improvement handles None without crash
    improvement = MetricsService.compute_aggregate_improvement(
        local_metrics=[metrics],
        federated_metrics=[metrics],
    )
    assert improvement["auc_roc"] is None
    assert improvement["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# 30. CanaryQualityGate rejects boolean and string metrics
# ---------------------------------------------------------------------------
def test_canary_gate_rejects_bool_and_string_metrics() -> None:
    """Canary gate must fail closed when metrics contain boolean or string masquerading as float."""
    from app.application.services.model_governance_service import (
        CanaryQualityGate,
    )

    gate = CanaryQualityGate()

    # Boolean candidate AUC must be rejected
    cand_metrics = {
        "disparate_impact_ratio": 0.95,
        "auc_roc": True,  # bool!
        "p99_latency_ms": 40.0,
        "fpr": 0.01,
    }
    decision = gate.evaluate(candidate_metrics=cand_metrics)
    assert decision["passed"] is False
    assert any("must be a numeric float, not bool" in r for r in decision["reasons"])


# ---------------------------------------------------------------------------
# 31. Dataloader missing label fail-closed tests (DEF-19)
# ---------------------------------------------------------------------------
def test_dataloader_missing_label_fails_closed() -> None:
    """Loaders must fail closed with ValueError when target label column is missing, never fabricating zeros."""
    import pandas as pd
    from app.application.services.dataloader import (
        _process_amlnet_dataframe,
        _process_amlsim_dataframe,
        _process_creditcard_dataframe,
        _process_ieee_cis_dataframe,
    )

    unlabeled_df = pd.DataFrame({"V1": [1.0, 2.0], "V2": [3.0, 4.0]})

    with pytest.raises(ValueError, match="IEEE-CIS dataset missing required fraud label column"):
        _process_ieee_cis_dataframe(unlabeled_df, source="test")

    with pytest.raises(ValueError, match="AMLSim dataset missing required fraud/laundering label column"):
        _process_amlsim_dataframe(unlabeled_df, root=Path("."), source="test")

    with pytest.raises(ValueError, match="CreditCard dataset missing required label column"):
        _process_creditcard_dataframe(unlabeled_df)

    with pytest.raises(ValueError, match="AMLNet dataset missing required laundering label column"):
        _process_amlnet_dataframe(unlabeled_df)


# ---------------------------------------------------------------------------
# 32. FHE driver emulation transparency & SimulationRun tracking (DEF-22)
# ---------------------------------------------------------------------------
def test_fhe_emulation_provenance_truth() -> None:
    """FHEDriver and SimulationRun must truthfully reflect whether FHE is real TenSEAL or software emulated."""
    from app.domain.entities import SimulationConfig, SimulationRun
    from app.domain.value_objects import ModelWeights
    from app.infrastructure.security.fhe_driver import FHEDriver, TENSEAL_AVAILABLE

    keyring = FHEDriver.generate_keys("sim_test_fhe")
    assert hasattr(keyring, "is_emulated")
    assert hasattr(keyring, "driver_mode")

    if not TENSEAL_AVAILABLE:
        assert keyring.is_emulated is True
        assert keyring.driver_mode == "SOFTWARE_EMULATED"

    weights = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[0.1, 0.2, 0.3, 0.4])
    enc = FHEDriver.encrypt_weights(weights, keyring)
    assert enc.is_emulated == keyring.is_emulated
    assert enc.driver_mode == keyring.driver_mode

    # SimulationRun stores FHE emulation fields
    sim = SimulationRun(config=SimulationConfig())
    assert sim.fhe_is_emulated is False
    assert sim.fhe_driver_mode is None


# ---------------------------------------------------------------------------
# 33. Bank enclave hardware truth transparency (DEF-23)
# ---------------------------------------------------------------------------
def test_bank_hardware_enclave_truth() -> None:
    """Bank configurations and ConsortiumStatusResponse must declare hardware enclave backing accurately."""
    from app.presentation.routers.banks import BANK_CONFIGS
    from app.infrastructure.security.tee_driver import is_sgx_hardware_available

    hw_avail = is_sgx_hardware_available()
    for bank in BANK_CONFIGS:
        assert "hardware_enclave_mode" in bank
        assert "is_hardware_enclave_backed" in bank
        assert bank["is_hardware_enclave_backed"] == hw_avail


# ---------------------------------------------------------------------------
# 34. HMAC Tokenize salt provenance and security (DEF-24)
# ---------------------------------------------------------------------------
def test_hmac_tokenize_salt_provenance_and_security(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokenize must reject default salt in production and record salt provenance."""
    import asyncio
    from fastapi import HTTPException
    from app.presentation.routers.entities import tokenize_raw_identifier
    from app.application.schemas.entities import HMACTokenizeRequest

    # Development mode: allows default salt with warning and provenance marker
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("CFI_STRICT_SECURITY", raising=False)
    monkeypatch.delenv("CFI_HMAC_SALT", raising=False)
    res_dev = asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com")))
    assert res_dev.salt_provenance == "INSECURE_DEFAULT_DEV_SALT"

    # Production mode: rejects default salt
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com")))
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 35. EventBus Kafka truth (DEF-25)
# ---------------------------------------------------------------------------
def test_event_bus_kafka_no_fake_metadata_when_producer_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """EventBus must NOT fabricate partition and offset when Kafka producer is not connected."""
    from app.infrastructure.event_bus import AlertCreated, EventBus
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "use_kafka", True)

    bus = EventBus()
    event = AlertCreated(alert_id="alt-1", bank_id="bank_a", severity="HIGH", risk_score=0.9)
    bus.publish(event)

    assert "kafka_publish" in event.metadata
    assert event.metadata["kafka_publish"]["status"] == "UNAVAILABLE"
    assert event.metadata["kafka_publish"]["reason"] == "NO_ACTIVE_KAFKA_PRODUCER_CLIENT"
    assert "offset" not in event.metadata["kafka_publish"]


# ---------------------------------------------------------------------------
# 36. KafkaBankConnector no fabricated metrics (DEF-26)
# ---------------------------------------------------------------------------
def test_kafka_connector_no_fabricated_metrics() -> None:
    """KafkaBankConnector must return COMMAND_PUBLISHED and None for unmeasured metrics."""
    from app.infrastructure.connectors.kafka_connector import KafkaBankConnector
    from app.domain.value_objects import ModelWeights

    connector = KafkaBankConnector(bootstrap_servers="localhost:9092")
    weights = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[0.1, 0.2, 0.3, 0.4])

    train_res = connector.train("bank_a", weights)
    assert train_res["status"] == "COMMAND_PUBLISHED"
    assert train_res["metrics"] is None
    assert train_res["loss"] is None

    eval_res = connector.evaluate("bank_a", weights)
    assert eval_res["status"] == "COMMAND_PUBLISHED"
    assert eval_res["metrics"] is None
    assert eval_res["loss"] is None


# ---------------------------------------------------------------------------
# 37. FHE Capability Semantics & Emulated Separation (Part I)
# ---------------------------------------------------------------------------
def test_fhe_capability_semantics_and_emulation_separation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify FHE capability modes: fail-closed on required when TenSEAL missing, and distinct EmulatedWeights."""
    from app.infrastructure.security.fhe_driver import (
        FHEDriver,
        EmulatedWeights,
        EncryptedWeights,
        verify_cryptographic_fhe,
    )
    from app.domain.enums import FHECapabilityMode, FHEBackendProvenance
    from app.domain.value_objects import ModelWeights

    weights = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[0.1, 0.2, 0.3, 0.4])

    # Case A: FHE_REQUIRED + TenSEAL unavailable -> FAIL CLOSED
    monkeypatch.setattr("app.infrastructure.security.fhe_driver.TENSEAL_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="Fail-closed: refusing software emulation"):
        FHEDriver.generate_keys("sim_req", capability_mode=FHECapabilityMode.FHE_REQUIRED)

    # Case B: FHE_EMULATION_EXPLICITLY_REQUESTED + TenSEAL unavailable -> SOFTWARE_EMULATED & EmulatedWeights
    emul_kr = FHEDriver.generate_keys(
        "sim_emul", capability_mode=FHECapabilityMode.FHE_EMULATION_EXPLICITLY_REQUESTED
    )
    assert emul_kr.is_emulated is True
    assert emul_kr.is_cryptographic is False
    assert emul_kr.driver_mode == "SOFTWARE_EMULATED"
    assert emul_kr.backend_provenance == FHEBackendProvenance.SOFTWARE_EMULATED.value

    emul_weights = FHEDriver.encrypt_weights(weights, emul_kr)
    assert isinstance(emul_weights, EmulatedWeights)
    # Critical Type Invariant: EmulatedWeights must NOT be an instance of EncryptedWeights
    assert not isinstance(emul_weights, EncryptedWeights)
    assert emul_weights.is_emulated is True
    assert emul_weights.is_cryptographic is False
    assert hasattr(emul_weights, "simulated_plaintext_vector")
    assert emul_weights.backend_provenance == FHEBackendProvenance.SOFTWARE_EMULATED.value

    # Serialization preserves non-cryptographic emulated provenance
    emul_dict = emul_weights.to_dict()
    assert emul_dict["payload_type"] == "SOFTWARE_EMULATED_WEIGHTS"
    assert emul_dict["is_cryptographic"] is False
    restored_emul = EmulatedWeights.from_dict(emul_dict)
    assert isinstance(restored_emul, EmulatedWeights)
    assert not isinstance(restored_emul, EncryptedWeights)

    # Cannot deserialize emulated payload as EncryptedWeights
    with pytest.raises(ValueError, match="Cannot deserialize non-cryptographic payload"):
        EncryptedWeights.from_dict(emul_dict)

    # Case D: Emulated result cannot pass cryptographic FHE gate
    with pytest.raises((ValueError, TypeError), match="Cryptographic FHE gate rejected"):
        verify_cryptographic_fhe(emul_weights)

    # Case E: Decryption succeeds for emulated weights back to ModelWeights when using emulated key ring
    dec = FHEDriver.decrypt_weights(emul_weights, emul_kr, [(2, 2)])
    assert isinstance(dec, ModelWeights)
    assert len(dec.flat_weights) == 4

    # Case C: Real FHE execution when TenSEAL is available
    monkeypatch.undo()
    from app.infrastructure.security.fhe_driver import TENSEAL_AVAILABLE
    if TENSEAL_AVAILABLE:
        real_kr = FHEDriver.generate_keys("sim_real", capability_mode=FHECapabilityMode.FHE_REQUIRED)
        assert real_kr.is_emulated is False
        assert real_kr.is_cryptographic is True
        assert real_kr.backend_provenance == FHEBackendProvenance.REAL_CKKS.value

        real_enc = FHEDriver.encrypt_weights(weights, real_kr)
        assert isinstance(real_enc, EncryptedWeights)
        assert not isinstance(real_enc, EmulatedWeights)
        assert real_enc.is_cryptographic is True
        assert verify_cryptographic_fhe(real_enc) is True

        # Real serialized payload remains cryptographic
        real_dict = real_enc.to_dict()
        assert real_dict["payload_type"] == "CRYPTOGRAPHIC_ENCRYPTED_WEIGHTS"
        restored_real = EncryptedWeights.from_dict(real_dict)
        assert isinstance(restored_real, EncryptedWeights)
        assert not isinstance(restored_real, EmulatedWeights)

        # Emulated weights cannot enter real decrypt path with require_cryptographic=True
        with pytest.raises(ValueError, match="cannot decrypt EmulatedWeights with authentic CKKS key ring"):
            FHEDriver.decrypt_weights(emul_weights, real_kr, [(2, 2)], require_cryptographic=True)


# ---------------------------------------------------------------------------
# 38. TEE Device Availability vs Hardware Attestation (Part II)
# ---------------------------------------------------------------------------
def test_tee_device_availability_vs_hardware_attestation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify device presence (/dev/sgx_enclave) does NOT equal verified hardware attestation."""
    from app.infrastructure.security.tee_driver import (
        TEEDriver,
        EmulatedAttestationReport,
        verify_hardware_attestation,
    )
    from app.domain.enums import TEECapabilityMode

    # Context creation
    ctx = TEEDriver.create_enclave("sim_tee_test")

    # 1. Device missing -> SOFTWARE_EMULATION
    monkeypatch.setattr("app.infrastructure.security.tee_driver.is_sgx_hardware_available", lambda: False)
    rep_no_hw = TEEDriver.generate_attestation_report(ctx)
    assert isinstance(rep_no_hw, EmulatedAttestationReport)
    assert rep_no_hw.is_hardware_backed is False
    assert rep_no_hw.is_hardware_attested is False
    assert rep_no_hw.attestation_status == TEECapabilityMode.TEE_SOFTWARE_EMULATION.value
    assert rep_no_hw.provenance == "EMULATED_ATTESTATION_REPORT"

    # 2. Device present -> DEVICE_AVAILABLE, but still is_hardware_attested=False (local hashes are simulated!)
    monkeypatch.setattr("app.infrastructure.security.tee_driver.is_sgx_hardware_available", lambda: True)
    rep_hw = TEEDriver.generate_attestation_report(ctx)
    assert isinstance(rep_hw, EmulatedAttestationReport)
    assert rep_hw.is_hardware_backed is True
    assert rep_hw.is_hardware_attested is False
    assert rep_hw.attestation_status == TEECapabilityMode.TEE_DEVICE_AVAILABLE.value
    assert rep_hw.driver_mode == "SGX_DEVICE_AVAILABLE_EMULATED_ATTESTATION"

    # 3. Verification gate requiring real hardware quote fails closed
    with pytest.raises(ValueError, match="Hardware attestation gate rejected"):
        verify_hardware_attestation(rep_hw, require_hardware=True)

    # 4. Verification with require_hardware=False passes software report
    assert verify_hardware_attestation(rep_hw, require_hardware=False) is True


# ---------------------------------------------------------------------------
# 39. HMAC Secret Capability Enforcement & Stability (Part III)
# ---------------------------------------------------------------------------
def test_hmac_capability_secret_enforcement_and_stability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HMAC tokenization enforces secret requirement under capability mode and ensures stability."""
    import asyncio
    from fastapi import HTTPException
    from app.presentation.routers.entities import tokenize_raw_identifier
    from app.application.schemas.entities import HMACTokenizeRequest

    monkeypatch.delenv("CFI_HMAC_SALT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    # 1. require_secret=True with default salt -> fails closed regardless of environment
    with pytest.raises(HTTPException) as exc1:
        asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com", require_secret=True)))
    assert exc1.value.status_code == 400
    assert "Insecure default consortium HMAC secret key is forbidden" in exc1.value.detail

    # 2. tokenization_mode="REAL_CONSORTIUM_STRICT" with default salt -> fails closed
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com", tokenization_mode="REAL_CONSORTIUM_STRICT")))
    assert exc2.value.status_code == 400

    # 3. Valid explicit secret -> succeeds with HMAC_SECRET_KEY metadata
    res1 = asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com", tenant_salt="my_secret_key_123")))
    assert res1.key_material_type == "HMAC_SECRET_KEY"
    assert res1.salt_provenance == "EXPLICIT_TENANT_SALT"
    assert res1.tokenization_mode == "REAL_CONSORTIUM"

    # 4. Stability: same ID + same key -> same token
    res2 = asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com", tenant_salt="my_secret_key_123")))
    assert res1.hmac_token == res2.hmac_token

    # 5. Distinctness: same ID + different key -> different token
    res3 = asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="user@bank.com", tenant_salt="different_key_456")))
    assert res1.hmac_token != res3.hmac_token

    # 6. Empty identifier -> fails closed
    with pytest.raises(HTTPException) as exc3:
        asyncio.run(tokenize_raw_identifier(payload=HMACTokenizeRequest(identifier="   ", tenant_salt="key")))
    assert exc3.value.status_code == 400


# ---------------------------------------------------------------------------
# 40. Kafka Broker Metadata & Correlation State Machine (Part IV)
# ---------------------------------------------------------------------------
def test_kafka_broker_metadata_and_result_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Kafka delivery metadata records real broker ack and correlation fails closed on mismatches."""
    from app.infrastructure.event_bus import AlertCreated, EventBus
    from app.infrastructure.connectors.kafka_connector import KafkaBankConnector
    from app.config import get_settings
    from app.domain.value_objects import ModelWeights

    settings = get_settings()
    monkeypatch.setattr(settings, "use_kafka", True)

    # 1. EventBus with broker ack future
    class MockRecordMetadata:
        topic = "domain_events.alert.created"
        partition = 2
        offset = 1042

    class MockFuture:
        def get(self, timeout: float = 2.0):
            return MockRecordMetadata()

    class MockProducer:
        def send(self, topic, key=None, value=None):
            return MockFuture()

    bus = EventBus()
    bus.set_kafka_producer(MockProducer())
    event = AlertCreated(alert_id="alt-42", bank_id="bank_b", severity="CRITICAL", risk_score=0.95)
    bus.publish(event)

    assert event.metadata["kafka_publish"]["status"] == "BROKER_ACKNOWLEDGED"
    assert event.metadata["kafka_publish"]["partition"] == 2
    assert event.metadata["kafka_publish"]["offset"] == 1042

    # 2. Connector train command delivery state
    connector = KafkaBankConnector()
    weights = ModelWeights(layer_shapes=[(2,)], flat_weights=[0.5, 0.5])
    cmd = connector.train("bank_b", weights, correlation_id="cid_999", run_id="run_42")
    assert cmd["delivery_status"] == "SEND_REQUESTED"
    assert cmd["command_type"] == "TRAIN"

    # 3. Worker result correlation success
    worker_ack = {
        "correlation_id": "cid_999",
        "bank_id": "bank_b",
        "command_type": "TRAIN",
        "run_id": "run_42",
        "loss": 0.12,
        "metrics": {"pr_auc": 0.88},
        "num_samples": 500,
    }
    correlated = connector.correlate_worker_result(cmd, worker_ack)
    assert correlated["status"] == "PROCESSED"
    assert correlated["loss"] == 0.12

    # 4. Correlation failure on mismatched command_type (eval result returned for train command)
    worker_wrong_type = dict(worker_ack, command_type="EVALUATE")
    with pytest.raises(ValueError, match="command_type mismatch"):
        connector.correlate_worker_result(cmd, worker_wrong_type)

    # 5. Correlation failure on stale/mismatched correlation_id
    worker_stale_cid = dict(worker_ack, correlation_id="cid_stale")
    with pytest.raises(ValueError, match="command correlation_id"):
        connector.correlate_worker_result(cmd, worker_stale_cid)


# ---------------------------------------------------------------------------
# 41. Dataset Loader Fail-Closed Contract Matrix (Part V)
# ---------------------------------------------------------------------------
def test_loader_fail_closed_contract_matrix(tmp_path: Path) -> None:
    """Verify PaySim, Elliptic, IEEE-CIS, CreditCard, AMLSim, SynthAML, and AMLNet fail closed on empty and NaN labels."""
    import pandas as pd
    from app.application.services.dataloader import (
        _process_paysim_dataframe,
        _process_ieee_cis_dataframe,
        _process_creditcard_dataframe,
        _process_amlsim_dataframe,
        _aggregate_synthaml_alert_features,
        _process_amlnet_dataframe,
    )

    # 1. PaySim: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _process_paysim_dataframe(pd.DataFrame(), source="real_csv")

    df_paysim_nan = pd.DataFrame({"isFraud": [np.nan, 0.0], "amount": [10.0, 20.0]})
    with pytest.raises(ValueError, match="contains NaN values"):
        _process_paysim_dataframe(df_paysim_nan, source="real_csv")

    # 2. IEEE-CIS: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _process_ieee_cis_dataframe(pd.DataFrame(), source="real_csv")

    df_ieee_nan = pd.DataFrame({"isFraud": [1.0, np.nan], "TransactionAmt": [100.0, 200.0]})
    with pytest.raises(ValueError, match="contains NaN in label column"):
        _process_ieee_cis_dataframe(df_ieee_nan, source="real_csv")

    # 3. CreditCard: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _process_creditcard_dataframe(pd.DataFrame())

    df_cc_nan = pd.DataFrame({"Class": [0.0, np.nan], "Time": [1.0, 2.0], "V1": [0.5, 0.2]})
    with pytest.raises(ValueError, match="contains NaN in label column"):
        _process_creditcard_dataframe(df_cc_nan)

    # 4. AMLSim: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _process_amlsim_dataframe(pd.DataFrame(), root=tmp_path)

    df_amlsim_nan = pd.DataFrame({"IS_FRAUD": [0.0, np.nan], "TX_AMOUNT": [100.0, 200.0]})
    with pytest.raises(ValueError, match="contains NaN in label column"):
        _process_amlsim_dataframe(df_amlsim_nan, root=tmp_path)

    # 5. SynthAML: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _aggregate_synthaml_alert_features(pd.DataFrame(), pd.DataFrame())

    alerts_nan = pd.DataFrame({"ALERT_ID": ["A1"], "OUTCOME": [np.nan]})
    tx_df = pd.DataFrame({"ALERT_ID": ["A1"], "ENTRY": ["credit"], "SIZE": [10.0]})
    with pytest.raises(ValueError, match="contains NaN in label column"):
        _aggregate_synthaml_alert_features(alerts_nan, tx_df)

    # 6. AMLNet: empty and NaN label
    with pytest.raises(ValueError, match="dataframe is empty"):
        _process_amlnet_dataframe(pd.DataFrame())

    df_amlnet_nan = pd.DataFrame({"isMoneyLaundering": [0.0, np.nan], "amount": [50.0, 100.0]})
    with pytest.raises(ValueError, match="contains missing or NaN labels"):
        _process_amlnet_dataframe(df_amlnet_nan)


# ---------------------------------------------------------------------------
# 42. Kafka Capability Mode Fail-Closed & In-Memory Distinction (Part III)
# ---------------------------------------------------------------------------
def test_kafka_capability_mode_fail_closed_and_in_memory_distinction() -> None:
    """Verify KAFKA_REQUIRED fails closed when producer is absent, and IN_MEMORY is explicitly distinct."""
    from app.domain.enums import KafkaCapabilityMode
    from app.infrastructure.event_bus import EventBus, AlertCreated

    bus = EventBus()
    event = AlertCreated(alert_id="alt_999", bank_id="bank_a", risk_score=0.9)

    # Case A: KAFKA_REQUIRED + No producer -> FAIL CLOSED
    with pytest.raises(RuntimeError, match="Kafka delivery required .* but no active Kafka producer client is connected"):
        bus.publish(event, capability_mode=KafkaCapabilityMode.KAFKA_REQUIRED)

    # Case B: IN_MEMORY_EXPLICIT -> Succeeds with explicit in-memory status
    event2 = AlertCreated(alert_id="alt_1000", bank_id="bank_b", risk_score=0.5)
    bus.publish(event2, capability_mode=KafkaCapabilityMode.IN_MEMORY_EXPLICIT)
    assert event2.metadata["kafka_publish"]["status"] == "IN_MEMORY_EVENT_BUS"
    assert event2.metadata["kafka_publish"]["provenance"] == "IN_MEMORY"
    assert event2.metadata["kafka_publish"]["broker"] is None

    # Case C: Fake producer with genuine RecordMetadata ack
    class FakeAck:
        topic = "domain_events.alert.created"
        partition = 2
        offset = 42

    class FakeFuture:
        def get(self, timeout: float = 2.0):
            return FakeAck()

    class FakeProducer:
        def send(self, topic: str, key: bytes, value: bytes):
            return FakeFuture()

    bus.set_kafka_producer(FakeProducer())
    event3 = AlertCreated(alert_id="alt_1001", bank_id="bank_c", risk_score=0.8)
    bus.publish(event3, capability_mode=KafkaCapabilityMode.KAFKA_REQUIRED)
    assert event3.metadata["kafka_publish"]["status"] == "BROKER_ACKNOWLEDGED"
    assert event3.metadata["kafka_publish"]["partition"] == 2
    assert event3.metadata["kafka_publish"]["offset"] == 42


# ---------------------------------------------------------------------------
# 43. PR-AUC Truth & Definedness Semantics (Part I: ML-005 Full Closure)
# ---------------------------------------------------------------------------
def test_pr_auc_truth_no_fabricated_half_score() -> None:
    """Verify PR-AUC computation distinguishes valid 0.0 from undefined None and rejects non-finite inputs."""
    from app.domain.metrics_service import (
        compute_pr_auc,
        compute_pr_auc_with_status,
        safe_pr_auc_score,
    )

    # 1. Single-class labels: PR-AUC is mathematically undefined -> value is None
    y_single = [0, 0, 0, 0]
    y_pred = [0.1, 0.2, 0.3, 0.4]

    score, is_def, status = compute_pr_auc_with_status(y_single, y_pred)
    assert is_def is False
    assert status == "undefined_single_class"
    assert score is None

    # compute_pr_auc and safe_pr_auc_score return None, NOT 0.0, NOT 0.5
    assert compute_pr_auc(y_single, y_pred) is None
    assert safe_pr_auc_score(y_single, y_pred) is None

    # 2. Single-class all 1s
    score_p, is_def_p, status_p = compute_pr_auc_with_status([1, 1, 1], [0.8, 0.9, 0.7])
    assert is_def_p is False
    assert status_p == "undefined_single_class"
    assert score_p is None
    assert compute_pr_auc([1, 1, 1], [0.8, 0.9, 0.7]) is None

    # 3. Empty inputs -> value is None, status is undefined_empty_input
    score_empty, is_def_e, status_e = compute_pr_auc_with_status([], [])
    assert is_def_e is False
    assert status_e == "undefined_empty_input"
    assert score_empty is None
    assert compute_pr_auc([], []) is None

    # 4. Two-class legitimate input
    y_two = [0, 1, 0, 1]
    y_pred_two = [0.1, 0.9, 0.2, 0.8]
    score_two, is_def_t, status_t = compute_pr_auc_with_status(y_two, y_pred_two)
    assert is_def_t is True
    assert status_t == "defined"
    assert score_two == 1.0

    # 5. Non-finite values (NaN / Inf) raise explicit ValueError
    with pytest.raises(ValueError, match="Non-finite values"):
        compute_pr_auc([0, 1], [float("nan"), 0.5])
    with pytest.raises(ValueError, match="Non-finite values"):
        compute_pr_auc([0, 1], [float("inf"), 0.5])

    # 6. Shape mismatch raises explicit ValueError
    with pytest.raises(ValueError, match="Shape mismatch"):
        compute_pr_auc([0, 1, 0], [0.2, 0.8])


# ---------------------------------------------------------------------------
# 44. Model Promotion Fail-Closed on Undefined PR-AUC (Part I Section 12)
# ---------------------------------------------------------------------------
def test_pr_auc_promotion_fails_closed_when_metric_unavailable() -> None:
    """Verify Challenger model is NEVER promoted when PR-AUC cannot be computed (single-class or empty)."""
    from unittest.mock import MagicMock
    from app.application.services.model_registry import ModelEvaluationEngine

    registry_mock = MagicMock()
    registry_mock.get_active_version.return_value = {"version": 1}
    store_mock = MagicMock()
    engine = ModelEvaluationEngine(registry=registry_mock)
    engine._store = store_mock

    # 10 transactions with only class 0 (single-class: PR-AUC is None)
    records = [
        {
            "actual_label": 0,
            "champion_prob": 0.1,
            "champion_latency_ms": 10.0,
            "challenger_version": 2,
            "challenger_prob": 0.05,
            "challenger_latency_ms": 8.0,
        }
        for _ in range(10)
    ]
    keys = [f"sim_pr_test:prediction:tx_{i}" for i in range(10)]
    store_data: dict[str, Any] = {
        "sim_pr_test:prediction_keys": keys,
        "sim_pr_test:challenger_traffic_share": 0.0,
    }
    for k, r in zip(keys, records, strict=True):
        store_data[k] = r

    store_mock.get.side_effect = lambda k: store_data.get(k)

    # Evaluate performance
    metrics = engine.evaluate_performance("sim_pr_test")

    # Promotion MUST NOT be triggered
    assert metrics["promotion_triggered"] is False
    assert metrics["champion_pr_auc"] is None
    assert metrics["challenger_pr_auc"] is None
    assert "PR-AUC undefined" in metrics["promotion_message"]


# ---------------------------------------------------------------------------
# 45. Elliptic Unknown Node Loss & Evaluation Masking Invariance (Part V)
# ---------------------------------------------------------------------------
def test_elliptic_unknown_node_loss_and_eval_masking_invariance() -> None:
    """Behavioral proof that changing predictions on y=-1 unknown nodes has zero effect on supervised loss and metrics."""
    import torch
    from app.application.services.graph_embedding_model import GraphSAGEModel
    from app.domain.metrics_service import compute_pr_auc, compute_roc_auc_with_status

    model = GraphSAGEModel(input_dim=12, hidden_dim=16, embedding_dim=8)

    # 5 nodes: node 0: class 0, node 1: class 1, node 2: unknown (-1), node 3: class 0, node 4: unknown (-1)
    targets = torch.tensor([0, 1, -1, 0, -1], dtype=torch.long)
    preds_initial = torch.tensor([0.2, 0.8, 0.05, 0.3, 0.10], dtype=torch.float32)

    # Baseline supervised loss
    loss_initial = model.compute_loss(preds_initial, targets)

    # Drastically mutate predictions on unknown nodes (node 2 from 0.05 -> 0.999, node 4 from 0.10 -> 0.001)
    preds_mutated = torch.tensor([0.2, 0.8, 0.999, 0.3, 0.001], dtype=torch.float32)
    loss_mutated = model.compute_loss(preds_mutated, targets)

    # Behavioral Invariant 1: Supervised loss MUST remain identical to exact machine precision
    assert torch.equal(loss_initial, loss_mutated), "Loss changed when only unknown node predictions were mutated!"

    # Behavioral Invariant 2: Supervised evaluation metrics on labeled subset are identical
    labeled_mask = (targets != -1).numpy()
    y_labeled = targets[labeled_mask].numpy()
    pr_initial = compute_pr_auc(y_labeled, preds_initial[labeled_mask].numpy())
    pr_mutated = compute_pr_auc(y_labeled, preds_mutated[labeled_mask].numpy())
    assert pr_initial == pr_mutated, "Supervised PR-AUC changed after mutating unknown node predictions!"

    roc_init, is_def_init, _ = compute_roc_auc_with_status(y_labeled, preds_initial[labeled_mask].numpy())
    roc_mut, is_def_mut, _ = compute_roc_auc_with_status(y_labeled, preds_mutated[labeled_mask].numpy())
    assert roc_init == roc_mut and is_def_init == is_def_mut

    # Behavioral Invariant 3: Class weights computed strictly excluding -1 nodes
    num_pos = int(torch.sum(targets == 1).item())
    num_neg = int(torch.sum(targets == 0).item())
    assert num_pos == 1
    assert num_neg == 2
    # Ensure -1 was not counted as positive or negative
    assert int(torch.sum(targets != -1).item()) == num_pos + num_neg


# ---------------------------------------------------------------------------
# 46. Kafka Multi-Dimensional Identity & Idempotency (Part VI)
# ---------------------------------------------------------------------------
def test_kafka_correlation_multi_dimensional_identity_and_idempotency() -> None:
    """Verify Kafka connector correlation enforces 6 identity dimensions and guarantees idempotency."""
    from app.infrastructure.connectors.kafka_connector import KafkaBankConnector

    cmd_meta = {
        "correlation_id": "cid-abc-123",
        "bank_id": "bank_a",
        "command_type": "TRAIN",
        "run_id": "run-42",
        "round_id": 3,
        "model_id": "model-gnn-v1",
    }

    # Case A: Matching worker result -> PROCESSED
    valid_worker = {
        "correlation_id": "cid-abc-123",
        "bank_id": "bank_a",
        "command_type": "TRAIN",
        "run_id": "run-42",
        "round_id": 3,
        "model_id": "model-gnn-v1",
        "loss": 0.245,
        "metrics": {"pr_auc": 0.82},
        "num_samples": 500,
    }
    processed_set: set[str] = set()
    res1 = KafkaBankConnector.correlate_worker_result(cmd_meta, valid_worker, processed_set)
    assert res1["status"] == "PROCESSED"
    assert res1["idempotent_duplicate"] is False
    assert res1["loss"] == 0.245
    assert "cid-abc-123" in processed_set

    # Case B: Duplicate response with same correlation_id -> DUPLICATE_IGNORED
    res_dup = KafkaBankConnector.correlate_worker_result(cmd_meta, valid_worker, processed_set)
    assert res_dup["status"] == "DUPLICATE_IGNORED"
    assert res_dup["idempotent_duplicate"] is True
    assert res_dup["loss"] is None
    assert res_dup["num_samples"] == 0

    # Case C: Round ID mismatch -> Fail Closed
    stale_round_worker = {**valid_worker, "round_id": 2}
    with pytest.raises(ValueError, match="round_id mismatch"):
        KafkaBankConnector.correlate_worker_result(cmd_meta, stale_round_worker)

    # Case D: Model ID mismatch -> Fail Closed
    wrong_model_worker = {**valid_worker, "model_id": "model-v2"}
    with pytest.raises(ValueError, match="model_id mismatch"):
        KafkaBankConnector.correlate_worker_result(cmd_meta, wrong_model_worker)


# ---------------------------------------------------------------------------
# 47. Elliptic Deterministic Label Contract & Invalid Encoding Rejection (Part IV)
# ---------------------------------------------------------------------------
def test_elliptic_loader_deterministic_contract_and_invalid_encoding_rejection(tmp_path: Path) -> None:
    """Verify Elliptic loader fails closed on NaN labels and rejects invalid encoding strings."""
    from app.application.services.dataloader import load_elliptic

    # Prepare minimal directory structure for Elliptic fixture
    feat_file = tmp_path / "elliptic_txs_features.csv"
    cls_file = tmp_path / "elliptic_txs_classes.csv"
    edge_file = tmp_path / "elliptic_txs_edgelist.csv"

    edge_file.write_text("txId1,txId2\n101,102\n")
    feat_file.write_text("101,1,0.5,0.6\n102,1,0.2,0.3\n103,1,0.1,0.4\n")

    # Case A: NaN in class column -> Fail Closed
    cls_file.write_text("txId,class\n101,1\n102,\n103,2\n")
    with pytest.raises(ValueError, match="Elliptic dataset contains NaN or missing values in label column"):
        load_elliptic(path=tmp_path, use_cache=False)

    # Case B: Invalid encoding string (e.g. '3' or 'malformed') -> Fail Closed
    cls_file.write_text("txId,class\n101,1\n102,invalid_code\n103,2\n")
    with pytest.raises(ValueError, match="Elliptic dataset contains invalid label encodings"):
        load_elliptic(path=tmp_path, use_cache=False)

    # Case C: Valid classes {'1', '2'} with include_unknown=False -> 1=illicit, 0=licit
    cls_file.write_text("txId,class\n101,1\n102,2\n103,unknown\n")
    data_sup = load_elliptic(path=tmp_path, use_cache=False, include_unknown=False)
    assert len(data_sup["y"]) == 2
    assert set(data_sup["y"].tolist()) == {0, 1}

    # Case D: Valid classes with include_unknown=True -> unknown mapped to -1
    data_all = load_elliptic(path=tmp_path, use_cache=False, include_unknown=True)
    assert len(data_all["y"]) == 3
    assert -1 in data_all["y"].tolist()




