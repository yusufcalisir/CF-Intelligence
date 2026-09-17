"""Unit and Integration Test Suite for Regulatory Compliance, FinCEN SAR 2.0, and GDPR Erasure API Routes.

Covers:
1. SOC 2 Type II attestation evidence collection (GET/POST /compliance/soc2-evidence).
2. Federal Reserve SR 11-7 conceptual soundness audit and 4-quarter schedule (GET /compliance/sr11-7/*).
3. EEOC 80% rule disparate impact and algorithmic fairness parity evaluation (POST /compliance/fairness/evaluate).
4. CanaryQualityGate automated candidate model promotion evaluation (POST /compliance/sr11-7/canary-gate).
5. FinCEN SAR 2.0 e-Filing regulatory list, generation, retrieval, and XML XSD validation (GET/POST /compliance/sar/*).
6. Enterprise data retention policy configuration, TTL purge, and GDPR Art. 17 erasure (GET/POST /compliance/retention/*, /compliance/gdpr/*).
7. Cryptographic SHA-256 hash-chain audit ledger verification.
8. Datasets validation preview and consortium enrollment dual-prefix routing (/v1/datasets, /api/v1/datasets).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService, _case_to_dict
from app.application.services.regulatory_reporter import RegulatoryReporterService
from app.domain.entities_phase2 import Case
from app.domain.enums import CasePriority, CaseStatus
from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Provides a shared FastAPI TestClient instance for compliance routes."""
    return TestClient(app)


def test_soc2_evidence_endpoints(client: TestClient) -> None:
    """Verifies SOC 2 Trust Services Criteria evidence collection across both URL prefixes."""
    # 1. Dual prefix GET checks
    res_api = client.get("/api/v1/compliance/soc2-evidence")
    assert res_api.status_code == 200
    data_api = res_api.json()
    assert data_api["compliance_status"] == "COMPLIANT"
    assert data_api["total_controls_audited"] >= 5
    assert "CC6.1" in data_api["controls"]
    assert "CC6.2" in data_api["controls"]
    assert "CC6.3" in data_api["controls"]
    assert "CC7.1" in data_api["controls"]
    assert "CC8.1" in data_api["controls"]
    assert "CC9.1" in data_api["controls"]

    res_core = client.get("/v1/compliance/soc2-evidence")
    assert res_core.status_code == 200
    assert res_core.json()["report_id"] == data_api["report_id"]

    # 2. POST re-computation check
    res_post = client.post("/api/v1/compliance/soc2-evidence")
    assert res_post.status_code == 200
    assert res_post.json()["passed_controls"] >= 5


def test_sr117_audit_endpoints(client: TestClient) -> None:
    """Verifies SR 11-7 Pillar I Conceptual Soundness audit report endpoint."""
    res = client.get("/api/v1/compliance/sr11-7/audit")
    assert res.status_code == 200
    data = res.json()
    assert "Conceptual Soundness" in data["pillar"]
    assert "Federal Reserve SR 11-7" in data["framework"]
    assert data["compliance_score_pct"] >= 90.0
    assert data["overall_status"] == "COMPLIANT"
    assert data["total_clauses"] >= 4
    assert len(data["clauses"]) >= 4

    # Dual prefix check
    res_core = client.get("/v1/compliance/sr11-7/audit")
    assert res_core.status_code == 200


def test_sr117_schedule_endpoints(client: TestClient) -> None:
    """Verifies SR 11-7 4-quarter validation schedule endpoint with parameter filtering."""
    # Default year
    res_default = client.get("/api/v1/compliance/sr11-7/schedule")
    assert res_default.status_code == 200
    data_default = res_default.json()
    assert data_default["reference_year"] == 2026
    assert len(data_default["milestones"]) == 4
    assert data_default["overall_mrm_status"] == "COMPLIANT"

    # Explicit year query
    res_custom = client.get("/v1/compliance/sr11-7/schedule?year=2027")
    assert res_custom.status_code == 200
    assert res_custom.json()["reference_year"] == 2027


def test_fairness_evaluation_endpoint(client: TestClient) -> None:
    """Verifies algorithmic fairness and non-discrimination audits under EEOC 80% rule."""
    # 1. Valid compliant distribution
    payload = {
        "y_pred_probs": [0.90, 0.85, 0.15, 0.10],
        "sensitive_attributes": [1, 1, 0, 0],
        "threshold": 0.50,
    }
    res = client.post("/api/v1/compliance/fairness/evaluate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["threshold"] == 0.50
    assert data["sample_count"] == 4
    assert data["protected_count"] == 2
    assert data["reference_count"] == 2
    assert "disparate_impact_ratio" in data
    assert "demographic_parity_difference" in data
    assert "eeoc_80_percent_rule" in data

    # Dual prefix check
    res_core = client.post("/v1/compliance/fairness/evaluate", json=payload)
    assert res_core.status_code == 200

    # 2. Length mismatch validation error (HTTP 400)
    invalid_payload = {
        "y_pred_probs": [0.90, 0.85],
        "sensitive_attributes": [1],
        "threshold": 0.50,
    }
    res_invalid = client.post("/api/v1/compliance/fairness/evaluate", json=invalid_payload)
    assert res_invalid.status_code == 400
    assert "mismatch" in res_invalid.json()["detail"].lower()


def test_canary_gate_endpoint(client: TestClient) -> None:
    """Verifies CanaryQualityGate candidate model promotion decision endpoint."""
    # 1. High-performing candidate (expected: PROMOTE)
    promote_payload = {
        "candidate_metrics": {
            "auc_roc": 0.96,
            "disparate_impact_ratio": 0.88,
            "p99_latency_ms": 40.0,
            "fpr": 0.01,
        }
    }
    res_promote = client.post("/api/v1/compliance/sr11-7/canary-gate", json=promote_payload)
    assert res_promote.status_code == 200
    data_promote = res_promote.json()
    assert data_promote["passed"] is True
    assert data_promote["decision"] in ("PROMOTE", "APPROVE_CANARY_PROMOTION")

    # Dual prefix check
    res_core = client.post("/v1/compliance/sr11-7/canary-gate", json=promote_payload)
    assert res_core.status_code == 200

    # 2. Substandard candidate (expected: HOLD)
    hold_payload = {
        "candidate_metrics": {
            "auc_roc": 0.55,
            "disparate_impact_ratio": 0.30,
            "p99_latency_ms": 300.0,
            "fpr": 0.25,
        }
    }
    res_hold = client.post("/api/v1/compliance/sr11-7/canary-gate", json=hold_payload)
    assert res_hold.status_code == 200
    data_hold = res_hold.json()
    assert data_hold["passed"] is False
    assert data_hold["decision"] in ("HOLD", "REJECT_CANARY_PROMOTION")
    assert len(data_hold["reasons"]) > 0


def test_sar_filings_and_validation_endpoints(client: TestClient) -> None:
    """Verifies FinCEN SAR 2.0 list and arbitrary XML validation."""
    # 1. List filings
    res_list = client.get("/api/v1/compliance/sar/filings")
    assert res_list.status_code == 200
    assert isinstance(res_list.json(), list)

    res_list_core = client.get("/v1/compliance/sar/filings")
    assert res_list_core.status_code == 200

    # 2. Generate compliant SAR XML using real service helper to test schema validation
    case = Case(
        id="CASE-SAR-ROUTE-001",
        title="Valid XSD Schema SAR Route Test",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.98,
        alert_ids=[],
        assigned_to="compliance_officer_1",
    )
    case_service = CaseManagementService()
    case_service._cases.set(case.id, _case_to_dict(case))

    valid_xml = RegulatoryReporterService.generate_sar_xml(case.id)

    res_val = client.post("/api/v1/compliance/sar/validate", json={"xml_content": valid_xml})
    assert res_val.status_code == 200
    data_val = res_val.json()
    assert data_val["valid"] is True
    assert data_val["root_element"] == "EFilingSubmission"
    assert data_val["schema_version"] == "FinCEN_SAR_2.0"
    assert len(data_val["sha256_hash"]) == 64

    # Dual prefix check
    res_val_core = client.post("/v1/compliance/sar/validate", json={"xml_content": valid_xml})
    assert res_val_core.status_code == 200

    # 3. Invalid XML syntax (HTTP 400)
    res_bad_syntax = client.post(
        "/api/v1/compliance/sar/validate",
        json={"xml_content": "<EFilingSubmission><UnclosedTag></EFilingSubmission>"},
    )
    assert res_bad_syntax.status_code == 400
    assert "syntax error" in res_bad_syntax.json()["detail"].lower()

    # 4. Incorrect root tag (HTTP 400)
    res_bad_root = client.post(
        "/api/v1/compliance/sar/validate",
        json={"xml_content": "<InvalidRoot><Data>Value</Data></InvalidRoot>"},
    )
    assert res_bad_root.status_code == 400
    assert "invalid root element" in res_bad_root.json()["detail"].lower()


def test_sar_generate_and_not_found_errors(client: TestClient) -> None:
    """Verifies SAR generation for confirmed case and RFC-compliant HTTP 404 for missing entities."""
    # 1. Successful generation for confirmed case
    case = Case(
        id="CASE-SAR-CONFIRMED-99",
        title="Confirmed AML Structuring Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.95,
        alert_ids=[],
        assigned_to="aml_investigator_42",
    )
    case_service = CaseManagementService()
    case_service._cases.set(case.id, _case_to_dict(case))

    res_gen = client.post(
        "/api/v1/compliance/sar/generate",
        json={"case_id": case.id, "institution_name": "Test International Bank"},
    )
    assert res_gen.status_code == 200
    gen_data = res_gen.json()
    assert gen_data["case_id"] == case.id
    assert gen_data["status"] == "FILED"
    filing_id = gen_data["filing_id"]

    # 2. Retrieve generated filing detail
    res_get_filing = client.get(f"/api/v1/compliance/sar/filings/{filing_id}")
    assert res_get_filing.status_code == 200
    detail_data = res_get_filing.json()
    assert detail_data["filing_id"] == filing_id
    assert "xml_content" in detail_data
    assert "EFilingSubmission" in detail_data["xml_content"]

    # 3. Nonexistent case SAR generation (HTTP 404)
    res_missing_gen = client.post(
        "/api/v1/compliance/sar/generate",
        json={"case_id": "nonexistent_fraud_case_0000"},
    )
    assert res_missing_gen.status_code == 404
    assert "not found" in res_missing_gen.json()["detail"].lower()

    # 4. Nonexistent filing ID retrieval (HTTP 404)
    res_missing_filing = client.get("/api/v1/compliance/sar/filings/nonexistent_filing_id_0000")
    assert res_missing_filing.status_code == 404
    assert "not found" in res_missing_filing.json()["detail"].lower()


def test_retention_policy_and_purge_endpoints(client: TestClient) -> None:
    """Verifies retention policy management, TTL validation, and purge execution."""
    tenant = "bank_compliance_test"

    # 1. Get policies
    res_get = client.get(f"/api/v1/compliance/retention/policies?tenant_id={tenant}")
    assert res_get.status_code == 200

    # 2. Configure valid policy
    res_post = client.post(
        "/api/v1/compliance/retention/policies",
        json={
            "tenant_id": tenant,
            "category": "TRANSACTION_LOGS",
            "ttl_days": 180,
            "erasure_method": "CRYPTOGRAPHIC_ZEROIZATION",
        },
    )
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert data_post["tenant_id"] == tenant
    assert data_post["category"] == "TRANSACTION_LOGS"
    assert data_post["ttl_days"] == 180

    # Dual prefix check
    res_post_core = client.post(
        "/v1/compliance/retention/policies",
        json={
            "tenant_id": tenant,
            "category": "INFERENCE_AUDITS",
            "ttl_days": 90,
            "erasure_method": "HARD_DELETE",
        },
    )
    assert res_post_core.status_code == 200

    # 3. Invalid category (HTTP 400)
    res_bad_cat = client.post(
        "/api/v1/compliance/retention/policies",
        json={
            "tenant_id": tenant,
            "category": "UNSUPPORTED_CATEGORY",
            "ttl_days": 90,
        },
    )
    assert res_bad_cat.status_code == 400

    # 4. Trigger retention purge
    res_purge = client.post(
        "/api/v1/compliance/retention/purge",
        json={"tenant_id": tenant},
    )
    assert res_purge.status_code == 200
    data_purge = res_purge.json()
    assert isinstance(data_purge, list)


def test_gdpr_erasure_and_audit_chain_endpoints(client: TestClient) -> None:
    """Verifies GDPR Art. 17 right-to-be-forgotten erasure and cryptographic ledger verification."""
    tenant = "bank_gdpr_test"
    target_hash = "f" * 64

    # 1. Execute erasure
    res_erase = client.post(
        "/api/v1/compliance/gdpr/erasure",
        json={
            "tenant_id": tenant,
            "entity_id_hash": target_hash,
            "category": "CUSTOMER_ENTITIES",
        },
    )
    assert res_erase.status_code == 200
    data_erase = res_erase.json()
    assert data_erase["tenant_id"] == tenant
    assert data_erase["status"] == "VERIFIED_ERASED"
    assert len(data_erase["erasure_hash"]) == 64

    # Dual prefix check
    res_erase_core = client.post(
        "/v1/compliance/gdpr/erasure",
        json={
            "tenant_id": tenant,
            "entity_id_hash": "e" * 64,
        },
    )
    assert res_erase_core.status_code == 200

    # 2. Get audit trail
    res_trail = client.get(f"/api/v1/compliance/retention/audit-trail?tenant_id={tenant}")
    assert res_trail.status_code == 200
    trail = res_trail.json()
    assert len(trail) >= 2

    # 3. Cryptographic hash-chain integrity verification
    res_verify = client.get(f"/api/v1/compliance/retention/audit-trail/verify?tenant_id={tenant}")
    assert res_verify.status_code == 200
    data_verify = res_verify.json()
    assert data_verify["valid"] is True
    assert data_verify["total_records"] >= 2
    assert len(data_verify["last_hash"]) == 64


def test_datasets_endpoints_dual_prefix(client: TestClient) -> None:
    """Verifies /v1/datasets and /api/v1/datasets routing and contract behavior."""
    preview_payload = {
        "filename": "transactions_test.csv",
        "file_format": "csv",
        "raw_header": ["tx_id", "amount", "sender_id", "receiver_id", "timestamp"],
        "sample_rows": [
            {"tx_id": "tx_01", "amount": 250.0, "sender_id": "usr_a", "receiver_id": "usr_b", "timestamp": "2026-09-17T12:00:00Z"},
            {"tx_id": "tx_02", "amount": 15000.0, "sender_id": "usr_c", "receiver_id": "usr_d", "timestamp": "2026-09-17T12:05:00Z"},
        ],
    }

    # 1. /v1/datasets/validate-preview
    res_v1 = client.post("/v1/datasets/validate-preview", json=preview_payload)
    assert res_v1.status_code == 200
    data_v1 = res_v1.json()
    assert data_v1["filename"] == "transactions_test.csv"
    assert "preview_id" in data_v1

    # 2. /api/v1/datasets/validate-preview
    res_api = client.post("/api/v1/datasets/validate-preview", json=preview_payload)
    assert res_api.status_code == 200
    assert res_api.json()["filename"] == "transactions_test.csv"

    # 3. /v1/datasets/consortium-enroll
    enroll_payload = {
        "audit_id": "AUDIT-TEST-01",
        "target_bank_id": "bank_alpha",
        "allocation_mode": "replace_partition",
        "trigger_fl_round": False,
    }
    res_enroll_v1 = client.post("/v1/datasets/consortium-enroll", json=enroll_payload)
    assert res_enroll_v1.status_code == 200
    data_enroll = res_enroll_v1.json()
    assert data_enroll["bank_id"] == "bank_alpha"
    assert data_enroll["node_status"] == "ACTIVE_TRAINING"

    # 4. /api/v1/datasets/consortium-enroll
    res_enroll_api = client.post("/api/v1/datasets/consortium-enroll", json=enroll_payload)
    assert res_enroll_api.status_code == 200
    assert res_enroll_api.json()["bank_id"] == "bank_alpha"
