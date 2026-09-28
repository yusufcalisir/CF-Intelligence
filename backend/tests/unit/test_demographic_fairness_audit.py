"""Unit tests for demographic attribute availability audit and algorithmic fairness governance."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from experiments.fairness.demographic_audit import (
    DatasetDemographicAudit,
    DemographicAuditReport,
    FairnessAuditor,
    ProxyFairnessMetrics,
    run_demographic_fairness_audit,
)


class TestDatasetDemographicScanning:
    """Verifies scanning logic across benchmark datasets and synthetic columns."""

    def test_all_seven_standard_datasets_audited(self) -> None:
        audits = FairnessAuditor.audit_all_standard_datasets()
        assert len(audits) == 7
        dataset_ids = [d.dataset_id for d in audits]
        expected_ids = ["paysim", "ieee_cis", "credit_card", "elliptic", "amlsim", "synthaml", "amlnet"]
        for expected in expected_ids:
            assert expected in dataset_ids

    def test_zero_protected_demographic_attributes_in_all_datasets(self) -> None:
        audits = FairnessAuditor.audit_all_standard_datasets()
        for audit in audits:
            assert audit.protected_attribute_count == 0, (
                f"Dataset {audit.dataset_id} unexpectedly matched protected attributes: "
                f"{audit.protected_attributes_present}"
            )
            assert audit.demographic_coverage_pct == 0.0
            assert "GDPR" in audit.regulatory_privacy_basis or "PCI-DSS" in audit.regulatory_privacy_basis or "Anonymization" in audit.regulatory_privacy_basis or "Blockchain" in audit.regulatory_privacy_basis or "Synthetic" in audit.regulatory_privacy_basis or "AUSTRAC" in audit.regulatory_privacy_basis

    def test_synthetic_protected_attribute_injection_flagged(self) -> None:
        tainted_columns = ["transaction_id", "amount", "customer_age", "gender_code", "ethnicity_group"]
        audit = FairnessAuditor.audit_dataset_columns(
            dataset_id="test_tainted",
            name="Tainted Test Dataset",
            columns=tainted_columns,
        )
        assert audit.protected_attribute_count == 3
        assert audit.protected_attributes_present["age"] is True
        assert audit.protected_attributes_present["gender"] is True
        assert audit.protected_attributes_present["race_ethnicity"] is True
        assert audit.protected_attributes_present["religion"] is False
        assert audit.demographic_coverage_pct == pytest.approx(0.30)


class TestProxyFairnessMetricCalculations:
    """Verifies mathematical correctness of DIR, EOD, DPD, AOD and Four-Fifths rule."""

    def test_known_fairness_metrics_calculation(self) -> None:
        # Group 0: Privileged, Group 1: Unprivileged
        # Privileged: 10 samples (y_true: 5 pos, 5 neg; y_pred: 4 pos (3 TP, 1 FP))
        # Unprivileged: 10 samples (y_true: 5 pos, 5 neg; y_pred: 4 pos (4 TP, 0 FP))
        y_true = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0,  1, 1, 1, 1, 1, 0, 0, 0, 0, 0])
        y_pred = np.array([1, 1, 1, 0, 0, 1, 0, 0, 0, 0,  1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
        groups = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  1, 1, 1, 1, 1, 1, 1, 1, 1, 1])

        metrics = FairnessAuditor.compute_fairness_metrics(
            y_true=y_true,
            y_pred=y_pred,
            group_membership=groups,
            privileged_val=0,
            unprivileged_val=1,
            proxy_dimension="test_proxy",
            privileged_label="Privileged",
            unprivileged_label="Unprivileged",
        )

        # Selection rates:
        # priv_sel = 4 / 10 = 0.4
        # unpriv_sel = 4 / 10 = 0.4
        assert metrics.privileged_selection_rate == pytest.approx(0.40)
        assert metrics.unprivileged_selection_rate == pytest.approx(0.40)
        # DIR: 0.4 / 0.4 = 1.0
        assert metrics.disparate_impact_ratio == pytest.approx(1.0)
        assert metrics.demographic_parity_difference == pytest.approx(0.0)
        assert metrics.four_fifths_rule_passed is True

        # TPRs:
        # priv_tpr = 3 / 5 = 0.60
        # unpriv_tpr = 4 / 5 = 0.80
        # EOD = unpriv_tpr - priv_tpr = 0.80 - 0.60 = +0.20
        assert metrics.equal_opportunity_difference == pytest.approx(0.20)

        # FPRs:
        # priv_fpr = 1 / 5 = 0.20
        # unpriv_fpr = 0 / 5 = 0.00
        # AOD = 0.5 * ((0.0 - 0.2) + (0.8 - 0.6)) = 0.5 * (-0.2 + 0.2) = 0.0
        assert metrics.average_odds_difference == pytest.approx(0.0)

    def test_four_fifths_rule_rejection_boundary(self) -> None:
        # Adverse disparate impact: unpriv_sel = 0.10, priv_sel = 0.40 => DIR = 0.25 (< 0.80)
        y_true = np.zeros(20, dtype=int)
        y_pred = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0,  1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        groups = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  1, 1, 1, 1, 1, 1, 1, 1, 1, 1])

        metrics = FairnessAuditor.compute_fairness_metrics(
            y_true=y_true,
            y_pred=y_pred,
            group_membership=groups,
            privileged_val=0,
            unprivileged_val=1,
            proxy_dimension="adverse_test",
            privileged_label="Priv",
            unprivileged_label="Unpriv",
        )
        assert metrics.disparate_impact_ratio == pytest.approx(0.25)
        assert metrics.four_fifths_rule_passed is False

    def test_zero_privileged_selection_graceful_handling(self) -> None:
        y_true = np.zeros(10, dtype=int)
        y_pred = np.zeros(10, dtype=int)
        groups = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])

        metrics = FairnessAuditor.compute_fairness_metrics(
            y_true=y_true,
            y_pred=y_pred,
            group_membership=groups,
            privileged_val=0,
            unprivileged_val=1,
            proxy_dimension="zero_test",
            privileged_label="Priv",
            unprivileged_label="Unpriv",
        )
        assert metrics.privileged_selection_rate == 0.0
        assert metrics.unprivileged_selection_rate == 0.0
        assert metrics.disparate_impact_ratio == 1.0
        assert metrics.four_fifths_rule_passed is True


class TestDemographicAuditPipelineAndArtifacts:
    """Verifies end-to-end report compilation, disclaimers, and artifact serialization."""

    def test_run_audit_report_contents(self) -> None:
        report = run_demographic_fairness_audit(
            sample_size=10000,
            seed=42,
            save_artifact=False,
        )
        assert report.total_datasets_audited == 7
        assert report.datasets_with_demographics == 0
        assert len(report.proxy_fairness_evaluations) == 3
        assert "FEDERAL RESERVE SR 11-7" in report.formal_sr11_7_disclaimer
        assert "EQUAL CREDIT OPPORTUNITY ACT" in report.formal_ecoa_disclaimer
        assert "EU AI ACT" in report.formal_eu_ai_act_disclaimer
        assert report.audit_conclusion == "FORMALLY_COMPLIANT_ZERO_DEMOGRAPHIC_PII_PROTECTED"

        for p in report.proxy_fairness_evaluations:
            assert p.four_fifths_rule_passed is True
            assert 0.80 <= p.disparate_impact_ratio <= 1.25

    def test_markdown_report_formatting(self) -> None:
        report = run_demographic_fairness_audit(
            sample_size=1000,
            seed=42,
            save_artifact=False,
        )
        md = report.to_markdown()
        assert "# Demographic Attribute Availability Assessment" in md
        assert "## 1. Demographic Attribute Availability Across Benchmark Datasets" in md
        assert "## 2. Operational Proxy Fairness Evaluation" in md
        assert "## 3. Authoritative Model Governance & Regulatory Disclaimers" in md
        assert "### 3.1 Federal Reserve SR 11-7" in md

    def test_custom_output_artifact_saving(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "test_demo_audit.json"
            report = run_demographic_fairness_audit(
                sample_size=500,
                seed=42,
                output_path=target,
                save_artifact=True,
            )
            assert target.exists()
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["total_datasets_audited"] == 7
            assert data["datasets_with_demographics"] == 0
            assert len(data["datasets_audited"]) == 7
            assert len(data["proxy_fairness_evaluations"]) == 3

    def test_golden_artifact_validation(self) -> None:
        golden_path = (
            Path(__file__).resolve().parents[3]
            / "benchmarks"
            / "results"
            / "raw"
            / "demographic_fairness_audit.json"
        )
        assert golden_path.exists(), f"Golden artifact missing at {golden_path}"
        with open(golden_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["total_datasets_audited"] == 7
        assert data["datasets_with_demographics"] == 0
        assert data["audit_conclusion"] == "FORMALLY_COMPLIANT_ZERO_DEMOGRAPHIC_PII_PROTECTED"
        assert len(data["proxy_fairness_evaluations"]) == 3
        for proxy in data["proxy_fairness_evaluations"]:
            assert proxy["four_fifths_rule_passed"] is True
