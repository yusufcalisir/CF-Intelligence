"""Unit tests for the metrics service."""

import pytest

from app.application.services.metrics_service import MetricsService
from app.domain.value_objects import EvaluationMetrics


@pytest.fixture
def metrics_service() -> MetricsService:
    return MetricsService()


def _make_metrics(accuracy: float, f1: float, auc: float) -> EvaluationMetrics:
    return EvaluationMetrics(
        accuracy=accuracy,
        precision=f1,
        recall=f1,
        f1_score=f1,
        auc_roc=auc,
        loss=0.3,
        confusion_matrix=[[90, 5], [3, 2]],
        roc_fpr=[0.0, 0.5, 1.0],
        roc_tpr=[0.0, 0.8, 1.0],
        roc_thresholds=[1.0, 0.5, 0.0],
    )


class TestMetricsService:
    def test_from_eval_dict(self) -> None:
        eval_dict = {
            "accuracy": 0.95,
            "precision": 0.90,
            "recall": 0.85,
            "f1_score": 0.87,
            "auc_roc": 0.92,
            "loss": 0.15,
            "confusion_matrix": [[950, 20], [15, 15]],
            "roc_fpr": [0.0, 0.5, 1.0],
            "roc_tpr": [0.0, 0.9, 1.0],
            "roc_thresholds": [1.0, 0.5, 0.0],
        }
        metrics = MetricsService.from_eval_dict(eval_dict)
        assert metrics.accuracy == 0.95
        assert metrics.f1_score == 0.87

    def test_from_eval_dict_with_feature_importance(self) -> None:
        eval_dict = {
            "accuracy": 0.9,
            "precision": 0.8,
            "recall": 0.7,
            "f1_score": 0.75,
            "auc_roc": 0.85,
            "loss": 0.2,
            "confusion_matrix": [[80, 10], [5, 5]],
            "roc_fpr": [0.0, 1.0],
            "roc_tpr": [0.0, 1.0],
            "roc_thresholds": [1.0, 0.0],
        }
        feat_imp = {"amount": 0.9, "velocity": 0.7}
        metrics = MetricsService.from_eval_dict(eval_dict, feat_imp)
        assert metrics.feature_importance == {"amount": 0.9, "velocity": 0.7}

    def test_aggregate_improvement_positive(self) -> None:
        local = [_make_metrics(0.80, 0.60, 0.75)]
        federated = [_make_metrics(0.90, 0.75, 0.85)]

        improvement = MetricsService.compute_aggregate_improvement(local, federated)
        assert improvement["accuracy"] == pytest.approx(0.10, abs=1e-4)
        assert improvement["f1_score"] == pytest.approx(0.15, abs=1e-4)

    def test_aggregate_improvement_averages_across_banks(self) -> None:
        local = [
            _make_metrics(0.80, 0.60, 0.70),
            _make_metrics(0.85, 0.70, 0.80),
        ]
        federated = [
            _make_metrics(0.90, 0.75, 0.85),
            _make_metrics(0.90, 0.75, 0.85),
        ]

        improvement = MetricsService.compute_aggregate_improvement(local, federated)
        # (0.10 + 0.05) / 2 = 0.075
        assert improvement["accuracy"] == pytest.approx(0.075, abs=1e-4)

    def test_empty_metrics_returns_empty(self) -> None:
        result = MetricsService.compute_aggregate_improvement([], [])
        assert result == {}

    def test_metrics_to_dict_roundtrip(self) -> None:
        metrics = _make_metrics(0.95, 0.85, 0.90)
        d = MetricsService.metrics_to_dict(metrics)
        assert d["accuracy"] == 0.95
        assert d["f1_score"] == 0.85
        assert len(d["confusion_matrix"]) == 2


def test_prometheus_telemetry_exposition() -> None:
    """Verifies all 6 required cfi_* Prometheus metrics are correctly rendered in text format."""
    from app.infrastructure.telemetry import telemetry_registry

    telemetry_registry.record_inference_latency(45.0, decision="ALLOW")
    telemetry_registry.set_active_bank_nodes(3)
    telemetry_registry.record_federated_round_duration(12.5)
    telemetry_registry.record_dp_epsilon_consumed(1.2, bank_id="bank_alpha")
    telemetry_registry.record_gradient_rejection(reason="byzantine")
    telemetry_registry.set_champion_model_auc(0.895)

    prom_text = telemetry_registry.get_prometheus_metrics_text()

    required_metrics = [
        "cfi_inference_latency_ms",
        "cfi_active_bank_nodes",
        "cfi_federated_round_duration_seconds",
        "cfi_dp_epsilon_consumed_total",
        "cfi_gradient_rejections_total",
        "cfi_champion_model_auc",
    ]

    for metric in required_metrics:
        assert metric in prom_text, f"Metric '{metric}' missing from Prometheus exposition text"
