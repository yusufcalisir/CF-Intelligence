"""Unit and Integration Tests for EU AI Act & SR 11-7 Regulatory Dossier Generator.

Covers:
- Dossier summary compilation & metrics integrity
- EU AI Act Articles 9–15 compliance matrix
- Federal Reserve SR 11-7 3-Pillars Model Risk Management matrix
- Classical baseline benchmark comparison (FedGNN vs XGBoost, Random Forest, MLP, Logistic Regression)
- Dual-control supervisory sign-off recording & cryptographic SHA-256 seal attestation
- Markdown & JSON export serialization
- REST presentation router endpoints & dual-prefix routing parity (/api/v1/regulatory/dossier and /regulatory/dossier)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.regulatory_dossier_generator import (
    RegulatoryDossierGenerator,
)
from app.main import app


@pytest.fixture
def dossier_service() -> RegulatoryDossierGenerator:
    """Fixture providing a fresh RegulatoryDossierGenerator instance."""
    return RegulatoryDossierGenerator()


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Service Unit Tests
# ---------------------------------------------------------------------------


class TestRegulatoryDossierService:
    """Tests core business logic of RegulatoryDossierGenerator."""

    def test_get_dossier_summary_structure(self, dossier_service: RegulatoryDossierGenerator) -> None:
        summary = dossier_service.get_dossier_summary("fedgnn-v4")
        assert summary["model_id"] == "fedgnn-v4"
        assert summary["dossier_id"] == "DOSSIER-FEDGNN-V4-2026"
        assert summary["overall_compliance_score"] >= 95.0
        assert summary["eu_ai_act_status"] == "COMPLIANT"
        assert summary["sr11_7_status"] == "COMPLIANT"
        assert len(summary["governing_standards"]) >= 4

        metrics = summary["primary_model_metrics"]
        assert metrics["pr_auc"] >= 0.85
        assert metrics["roc_auc"] >= 0.90
        assert metrics["f1_score"] >= 0.80
        assert metrics["p99_latency_ms"] <= 100.0
        assert 0.80 <= metrics["disparate_impact_ratio"] <= 1.25
        assert metrics["differential_privacy_epsilon"] == 1.0
        assert metrics["differential_privacy_delta"] == 1e-5
        assert metrics["byzantine_fault_tolerance_pct"] >= 33.0
        assert metrics["zero_raw_pii_enforced"] is True

        limits = summary["drift_monitoring_limits"]
        assert limits["feature_drift_ks_p_value_critical"] == 0.01
        assert limits["concept_drift_psi_critical"] == 0.25
        assert limits["covariance_frobenius_critical"] == 3.00
        assert limits["rollback_sla_seconds"] <= 5.0

    def test_dossier_sha256_seal_consistency(self, dossier_service: RegulatoryDossierGenerator) -> None:
        summary1 = dossier_service.get_dossier_summary("fedgnn-test")
        summary2 = dossier_service.get_dossier_summary("fedgnn-test")
        assert summary1["sha256_dossier_seal"] == summary2["sha256_dossier_seal"]
        assert len(summary1["sha256_dossier_seal"]) == 64

    def test_eu_ai_act_matrix_all_compliant(self, dossier_service: RegulatoryDossierGenerator) -> None:
        matrix = dossier_service.get_eu_ai_act_matrix()
        assert matrix["overall_status"] == "COMPLIANT"
        assert matrix["compliance_rate_pct"] == 100.0
        assert len(matrix["articles"]) == 7

        article_numbers = [a["article"] for a in matrix["articles"]]
        expected_articles = [
            "Article 9",
            "Article 10",
            "Article 11 & Annex IV",
            "Article 12",
            "Article 13",
            "Article 14",
            "Article 15",
        ]
        assert article_numbers == expected_articles

        for article in matrix["articles"]:
            assert article["status"] == "COMPLIANT"
            assert len(article["requirement"]) > 10
            assert len(article["evidence"]) > 10

    def test_sr11_7_matrix_all_compliant(self, dossier_service: RegulatoryDossierGenerator) -> None:
        matrix = dossier_service.get_sr11_7_matrix()
        assert matrix["overall_status"] == "COMPLIANT"
        assert len(matrix["pillars"]) == 3

        p1, p2, p3 = matrix["pillars"]
        assert p1["pillar"] == "Pillar I"
        assert "topology_rationale" in p1["details"]
        assert "privacy_guarantees" in p1["details"]

        assert p2["pillar"] == "Pillar II"
        assert "first_line" in p2["details"]
        assert "second_line" in p2["details"]
        assert "third_line" in p2["details"]
        assert "fairness_audit" in p2["details"]

        assert p3["pillar"] == "Pillar III"
        assert "input_feature_drift" in p3["details"]
        assert "concept_output_drift" in p3["details"]
        assert "rollback_sla" in p3["details"]

    def test_benchmark_comparison_metrics(self, dossier_service: RegulatoryDossierGenerator) -> None:
        benchmarks = dossier_service.get_benchmark_comparison()
        assert len(benchmarks) == 5

        fedgnn = next(b for b in benchmarks if "FedGNN" in b["model_name"])
        xgboost = next(b for b in benchmarks if "XGBoost" in b["model_name"])
        rf = next(b for b in benchmarks if "Random Forest" in b["model_name"])

        # FedGNN out-performs single-institution classical models in PR-AUC
        assert fedgnn["pr_auc"] > xgboost["pr_auc"]
        assert fedgnn["pr_auc"] > rf["pr_auc"]
        assert fedgnn["roc_auc"] > xgboost["roc_auc"]

        # Privacy and Byzantine robustness invariants
        assert fedgnn["differential_privacy_eps"] == 1.0
        assert fedgnn["zero_raw_pii_enforced"] is True
        assert "33.3%" in fedgnn["byzantine_tolerance"]
        assert xgboost["zero_raw_pii_enforced"] is False

    def test_supervisory_signoff_addition(self, dossier_service: RegulatoryDossierGenerator) -> None:
        initial_count = len(dossier_service.list_supervisory_signoffs())
        record = dossier_service.add_supervisory_signoff(
            officer_name="Sarah Jenkins",
            role="LEAD_REGULATORY_COMPLIANCE_DIRECTOR",
            notes="Full conformity certified following EBA AML guidelines audit.",
        )

        assert record["officer_name"] == "Sarah Jenkins"
        assert record["role"] == "LEAD_REGULATORY_COMPLIANCE_DIRECTOR"
        assert len(record["sha256_attestation_hash"]) == 64
        assert len(dossier_service.list_supervisory_signoffs()) == initial_count + 1

    def test_supervisory_signoff_thread_safety(self, dossier_service: RegulatoryDossierGenerator) -> None:
        initial_count = len(dossier_service.list_supervisory_signoffs())

        def _add(idx: int) -> dict[str, Any]:
            return dossier_service.add_supervisory_signoff(
                officer_name=f"Officer {idx}",
                role="AUDITOR",
                notes=f"Audit batch {idx}",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_add, range(20)))

        assert len(results) == 20
        assert len(dossier_service.list_supervisory_signoffs()) == initial_count + 20


# ---------------------------------------------------------------------------
# Export Verification Tests
# ---------------------------------------------------------------------------


class TestRegulatoryDossierExport:
    """Tests Markdown and JSON dossier export serialization."""

    def test_export_markdown_structure(self, dossier_service: RegulatoryDossierGenerator) -> None:
        md = dossier_service.export_dossier_markdown("fedgnn-champion-v4")
        assert "# REGULATORY MODEL VALIDATION DOSSIER: FEDGNN-CHAMPION-V4" in md
        assert "## 1. Executive Summary & Regulatory Certification" in md
        assert "## 2. EU AI Act Compliance Matrix (Regulation (EU) 2024/1689)" in md
        assert "## 3. Federal Reserve SR 11-7 / OCC 2011-12 Model Risk Management" in md
        assert "## 4. Empirical Model Benchmarking Comparison" in md
        assert "## 5. Dual-Control Supervisory Sign-Off Register" in md
        assert "CONFIDENTIAL — PROPRIETARY REGULATORY VALIDATION DOSSIER" in md

        # Verify KaTeX math compatibility (Rule 4: no unescaped underscores in \text{})
        text_underscore_matches = re.findall(r"\\text\{[^}]*_[^}]*\}", md)
        assert len(text_underscore_matches) == 0, f"KaTeX invalid math found: {text_underscore_matches}"

    def test_export_json_structure(self, dossier_service: RegulatoryDossierGenerator) -> None:
        data = dossier_service.export_dossier_json("fedgnn-champion-v4")
        assert "summary" in data
        assert "eu_ai_act_compliance" in data
        assert "sr11_7_compliance" in data
        assert "benchmarks" in data
        assert "supervisory_signoffs" in data
        assert data["dossier_schema_version"] == "2026.1"


# ---------------------------------------------------------------------------
# Router Presentation Integration Tests
# ---------------------------------------------------------------------------


class TestRegulatoryDossierRouter:
    """Tests FastAPI HTTP REST endpoints and dual-routing parity."""

    def test_get_summary_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["model_id"] == "fedgnn-champion-v4"
        assert payload["eu_ai_act_status"] == "COMPLIANT"
        assert payload["overall_compliance_score"] >= 95.0

    def test_get_eu_ai_act_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/eu-ai-act")
        assert response.status_code == 200
        payload = response.json()
        assert payload["framework"] == "Regulation (EU) 2024/1689 (EU AI Act)"
        assert payload["compliance_rate_pct"] == 100.0
        assert len(payload["articles"]) == 7

    def test_get_sr11_7_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/sr11-7")
        assert response.status_code == 200
        payload = response.json()
        assert payload["overall_status"] == "COMPLIANT"
        assert len(payload["pillars"]) == 3

    def test_get_benchmarks_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/benchmarks")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 5
        assert any(b["model_name"].startswith("FedGNN") for b in payload)

    def test_get_signoffs_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/signoffs")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) >= 2
        assert any(s["role"] == "CHIEF_RISK_OFFICER" for s in payload)

    def test_post_signoff_endpoint(self, test_client: TestClient) -> None:
        body = {
            "officer_name": "Auditor General von Clausewitz",
            "role": "SUPERVISORY_AUDITOR",
            "notes": "ECB Joint Supervisory Mechanism (JSM) formal verification complete.",
        }
        response = test_client.post("/api/v1/regulatory/dossier/signoff", json=body)
        assert response.status_code == 201
        payload = response.json()
        assert payload["officer_name"] == body["officer_name"]
        assert payload["role"] == body["role"]
        assert len(payload["sha256_attestation_hash"]) == 64

    def test_post_signoff_validation_error(self, test_client: TestClient) -> None:
        # Invalid payload: officer_name too short
        body = {
            "officer_name": "A",
            "role": "AUDITOR",
            "notes": "Short name",
        }
        response = test_client.post("/api/v1/regulatory/dossier/signoff", json=body)
        assert response.status_code == 422

    def test_export_markdown_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/export?format=markdown")
        assert response.status_code == 200
        assert "text/markdown" in response.headers.get("content-type", "")
        assert "attachment; filename=" in response.headers.get("content-disposition", "")
        assert "# REGULATORY MODEL VALIDATION DOSSIER" in response.text

    def test_export_json_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/export?format=json")
        assert response.status_code == 200
        payload = response.json()
        assert "summary" in payload
        assert "eu_ai_act_compliance" in payload
        assert "sr11_7_compliance" in payload

    def test_export_invalid_format_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/export?format=xml")
        assert response.status_code == 422

    def test_health_endpoint(self, test_client: TestClient) -> None:
        response = test_client.get("/api/v1/regulatory/dossier/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "healthy"
        assert payload["service"] == "RegulatoryDossierGenerator"

    def test_dual_routing_parity(self, test_client: TestClient) -> None:
        resp_v1 = test_client.get("/api/v1/regulatory/dossier/summary?model_id=fedgnn-test")
        resp_root = test_client.get("/regulatory/dossier/summary?model_id=fedgnn-test")

        assert resp_v1.status_code == 200
        assert resp_root.status_code == 200
        assert resp_v1.json()["dossier_id"] == resp_root.json()["dossier_id"]
        assert resp_v1.json()["sha256_dossier_seal"] == resp_root.json()["sha256_dossier_seal"]
