"""Unit tests for Design Partner Pilot Service and API Router."""

import pandas as pd
import pytest

from app.application.services.design_partner_service import DesignPartnerPilotService


def test_hmac_sha256_type_salted_hashing():
    pilot = DesignPartnerPilotService(hmac_secret_salt=b"test-salt-123")
    hash1 = pilot.hash_pii_identifier("12345678901", entity_type="TCKN")
    hash2 = pilot.hash_pii_identifier("12345678901", entity_type="TCKN")
    hash_diff_type = pilot.hash_pii_identifier("12345678901", entity_type="IBAN")

    assert hash1 == hash2
    assert hash1 != hash_diff_type
    assert len(hash1) == 64  # SHA-256 hex string


def test_scan_for_raw_pii_detects_violations():
    pilot = DesignPartnerPilotService()

    clean_df = pd.DataFrame({
        "tx_id": ["tx_1", "tx_2"],
        "amount": [100.50, 450.00],
        "hashed_account": ["a1b2c3d4e5f6", "f6e5d4c3b2a1"],
    })
    res_clean = pilot.scan_for_raw_pii(clean_df)
    assert res_clean.clean is True
    assert len(res_clean.violations_detected) == 0

    dirty_df = pd.DataFrame({
        "tx_id": ["tx_1", "tx_2"],
        "credit_card": ["4532-1234-5678-9012", "5412 3456 7890 1234"],
        "email": ["fraudster@example.com", "innocent@bank.com"],
    })
    res_dirty = pilot.scan_for_raw_pii(dirty_df)
    assert res_dirty.clean is False
    assert len(res_dirty.violations_detected) >= 2


def test_pilot_readiness_checklist_generation():
    pilot = DesignPartnerPilotService()
    checklist = pilot.generate_pilot_readiness_checklist(partner_name="Fintech Alpha", jurisdiction="EU")

    assert checklist.partner_name == "Fintech Alpha"
    assert checklist.overall_readiness_score > 90.0
    assert checklist.status == "APPROVED_FOR_PILOT"
    assert len(checklist.compliance_items) >= 5


@pytest.mark.real_data
def test_evaluate_reference_benchmark_paysim_and_ieee():
    pilot = DesignPartnerPilotService()
    res = pilot.evaluate_reference_benchmark(dataset_name="paysim", n_samples=3000)

    assert "performance_comparison" in res
    assert "distribution_fidelity" in res
    assert "multi_threshold_confusion_matrices" in res
    assert "bank_partitions" in res
    assert res["performance_comparison"]["federated_advantage"]["net_daily_economic_benefit_dollars"] > 0


@pytest.mark.real_data
def test_evaluate_reference_benchmark_scales_with_daily_volume():
    """Verify volume scaling linearity and economic benefit scaling across 50k, 100k, 500k volumes."""
    pilot = DesignPartnerPilotService()
    res_50k = pilot.evaluate_reference_benchmark(dataset_name="paysim", n_samples=3000, daily_volume=50_000)
    res_100k = pilot.evaluate_reference_benchmark(dataset_name="paysim", n_samples=3000, daily_volume=100_000)
    res_500k = pilot.evaluate_reference_benchmark(dataset_name="paysim", n_samples=3000, daily_volume=500_000)

    fp_50k = res_50k["performance_comparison"]["federated_learning"]["cost_report"]["false_positive_alerts_daily"]
    fp_100k = res_100k["performance_comparison"]["federated_learning"]["cost_report"]["false_positive_alerts_daily"]
    fp_500k = res_500k["performance_comparison"]["federated_learning"]["cost_report"]["false_positive_alerts_daily"]

    # Independent oracle: daily FP is linear in volume with bounded integer truncation discrepancy <= 10
    assert abs(fp_500k - fp_50k * 10) <= 10
    assert abs(fp_100k - fp_50k * 2) <= 2
    assert fp_500k > fp_50k * 9

    benefit_50k = res_50k["performance_comparison"]["federated_advantage"]["net_daily_economic_benefit_dollars"]
    benefit_100k = res_100k["performance_comparison"]["federated_advantage"]["net_daily_economic_benefit_dollars"]
    benefit_500k = res_500k["performance_comparison"]["federated_advantage"]["net_daily_economic_benefit_dollars"]
    assert benefit_50k > 0
    assert benefit_100k > benefit_50k
    assert benefit_500k > benefit_100k


@pytest.mark.real_data
def test_evaluate_reference_benchmark_scales_with_sample_size():
    pilot = DesignPartnerPilotService()
    res_2k = pilot.evaluate_reference_benchmark(dataset_name="ieee_cis", n_samples=2000, daily_volume=100_000)
    res_4k = pilot.evaluate_reference_benchmark(dataset_name="ieee_cis", n_samples=4000, daily_volume=100_000)

    assert res_2k["total_transactions_evaluated"] == 2000
    assert res_4k["total_transactions_evaluated"] == 4000


def _make_schema_valid_benchmark_payload(dataset: str, n_samples: int, daily_volume: int) -> dict:
    return {
        "dataset_name": dataset,
        "source_type": "BENCHMARK_REFERENCE",
        "total_transactions_evaluated": n_samples,
        "actual_fraud_count": max(1, n_samples // 100),
        "actual_fraud_rate_percent": 1.0,
        "evaluation_provenance": {
            "model_type": "PYTORCH_FEDERATED_INFERENCE",
            "probability_synthesis": "NONE_GENUINE_INFERENCE",
            "is_synthetic_beta": False,
            "input_features": 13,
            "samples_evaluated": n_samples,
        },
        "performance_comparison": {
            "federated_learning": {
                "roc_auc": 0.88,
                "pr_auc": 0.75,
                "recall_at_01_fpr": 0.65,
                "cost_report": {
                    "baseline_total_cost_dollars": 50000.0,
                    "fl_total_cost_dollars": 20000.0,
                    "total_saved_dollars": 30000.0,
                    "operational_fte_hours_saved": 150.0,
                    "roi_multiple": 2.5,
                    "false_positive_alerts_daily": 100,
                },
            },
            "isolated_local_model": {
                "roc_auc": 0.72,
                "pr_auc": 0.51,
                "recall_at_01_fpr": 0.38,
                "cost_report": {
                    "baseline_total_cost_dollars": 50000.0,
                    "fl_total_cost_dollars": 35000.0,
                    "total_saved_dollars": 15000.0,
                    "operational_fte_hours_saved": 75.0,
                    "roi_multiple": 1.5,
                    "false_positive_alerts_daily": 250,
                },
            },
            "federated_advantage": {
                "pr_auc_gain": 0.24,
                "recall_at_01_fpr_gain": 0.27,
                "daily_fraud_loss_saved_dollars": 15000.0,
                "daily_investigation_saved_dollars": 15000.0,
                "net_daily_economic_benefit_dollars": 30000.0,
            },
        },
        "multi_threshold_confusion_matrices": [],
        "distribution_fidelity": {
            "wasserstein_distance": 0.045,
            "ks_statistic": 0.082,
            "ks_pvalue": 0.95,
            "js_divergence": 0.012,
            "drift_detected": False,
            "fidelity_score": 0.94,
            "metrics": {},
        },
        "bank_partitions": [
            {"bank_id": "bank_a", "samples": n_samples // 3, "fraud_count": 5, "fraud_ratio": 0.01},
            {"bank_id": "bank_b", "samples": n_samples // 3, "fraud_count": 5, "fraud_ratio": 0.01},
            {"bank_id": "bank_c", "samples": n_samples - 2 * (n_samples // 3), "fraud_count": 5, "fraud_ratio": 0.01},
        ],
    }


def test_evaluate_reference_benchmark_fastapi_endpoints(monkeypatch: pytest.MonkeyPatch):
    import fastapi
    from fastapi.testclient import TestClient

    from app.presentation.routers import design_partner
    from app.presentation.routers.design_partner import api_router, router

    monkeypatch.setattr(
        design_partner._pilot_service,
        "evaluate_reference_benchmark",
        lambda dataset_name, n_samples=2500, daily_volume=100000: _make_schema_valid_benchmark_payload(
            dataset_name, n_samples, daily_volume
        ),
    )

    test_app = fastapi.FastAPI()
    test_app.include_router(router)
    test_app.include_router(api_router)
    client = TestClient(test_app)

    # Test canonical /api/v1 prefix
    resp1 = client.get(
        "/api/v1/design-partner/evaluate-benchmark",
        params={"dataset": "elliptic", "n_samples": 2500, "daily_volume": 250000},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["dataset_name"] == "elliptic"
    assert data1["total_transactions_evaluated"] == 2500
    perf = data1["performance_comparison"]
    adv = perf["federated_advantage"]
    expected_net = round(
        adv["daily_fraud_loss_saved_dollars"] + adv["daily_investigation_saved_dollars"],
        2,
    )
    assert isinstance(adv["net_daily_economic_benefit_dollars"], float)
    assert adv["net_daily_economic_benefit_dollars"] == pytest.approx(expected_net, abs=0.011)

    # Test short /v1 prefix
    resp2 = client.get(
        "/v1/design-partner/evaluate-benchmark",
        params={"dataset": "creditcard", "n_samples": 2000, "daily_volume": 500000},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["dataset_name"] == "creditcard"
    assert data2["total_transactions_evaluated"] == 2000


@pytest.mark.real_data
def test_evaluate_reference_benchmark_fastapi_endpoints_authentic_datasets():
    import fastapi
    from fastapi.testclient import TestClient

    from app.presentation.routers.design_partner import api_router, router

    test_app = fastapi.FastAPI()
    test_app.include_router(router)
    test_app.include_router(api_router)
    client = TestClient(test_app)

    resp = client.get(
        "/api/v1/design-partner/evaluate-benchmark",
        params={"dataset": "elliptic", "n_samples": 2500, "daily_volume": 250000},
    )
    assert resp.status_code == 200
    assert resp.json()["dataset_name"] == "elliptic"
