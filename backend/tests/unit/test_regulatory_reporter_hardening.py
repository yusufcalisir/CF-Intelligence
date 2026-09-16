"""Hardening Test Suite for FinCEN SAR 2.0 XML Generator & e-Filing Pipeline.

Verifies 22-vector compliance:
- Vector 1: Zero mock fallbacks; non-existent cases raise SARValidationError (no fake case synthesis).
- Vector 1 & 9: Dynamic zero-PII subject privacy hash generation; no static 'hash_subj_998877' fallback.
- Vector 11 & 20: Cryptographic SHA-256 filing hash computation and tamper-evident audit integrity.
- Vector 16: Thread-safe atomic file persistence and concurrent filing generation with RLock.
- Vector 17: Contract parity across cases router, compliance router, and Pydantic schemas.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService, _case_to_dict
from app.application.services.regulatory_reporter import (
    XSD_SCHEMA_PATH,
    RegulatoryReporterService,
    SARValidationError,
)
from app.domain.entities_phase2 import Alert, Case
from app.domain.enums import AlertSeverity, CasePriority, CaseStatus
from app.main import app

client = TestClient(app)


@pytest.fixture
def case_service() -> CaseManagementService:
    service = CaseManagementService()
    service._cases.clear()
    return service


def test_real_case_lookup_and_missing_case_rejection(case_service: CaseManagementService) -> None:
    """Verify that non-existent cases raise SARValidationError rather than synthesizing mock cases."""
    non_existent_id = "CASE-NONEXISTENT-9999"

    with pytest.raises(SARValidationError) as exc_info:
        RegulatoryReporterService.generate_sar_xml(non_existent_id)

    assert f"case '{non_existent_id}' not found" in str(exc_info.value)

    # Now create a real confirmed case and assert successful XML generation
    real_case = Case(
        id="CASE-REAL-001",
        title="Cross-border structuring syndicate",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.97,
        alert_ids=["ALT-REAL-101"],
        assigned_to="lead_examiner",
    )
    case_service._cases.set(real_case.id, _case_to_dict(real_case))

    xml_content = RegulatoryReporterService.generate_sar_xml(real_case.id)
    assert xml_content.startswith("<?xml")
    assert "<ActivityID>CASE-REAL-001</ActivityID>" in xml_content
    assert "<TotalRiskScore>0.97</TotalRiskScore>" in xml_content


def test_dynamic_subject_hash_derivation_zero_mock(case_service: CaseManagementService) -> None:
    """Verify dynamic zero-PII subject privacy hash derivation without static 'hash_subj_998877' fallback."""
    case_no_entities = Case(
        id="CASE-NO-ENTITIES-101",
        title="Unlinked alert investigation",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.88,
        alert_ids=[],
        assigned_to="analyst_bob",
    )
    case_service._cases.set(case_no_entities.id, _case_to_dict(case_no_entities))

    xml_str = RegulatoryReporterService.generate_sar_xml(case_no_entities.id)
    assert "hash_subj_998877" not in xml_str

    expected_derived_prefix = hashlib.sha256(
        f"case_subject:{case_no_entities.id}".encode()
    ).hexdigest()[:32]
    assert expected_derived_prefix in xml_str

    # Test case with real entity ID linked through alert
    alert = Alert(
        id="ALT-WITH-ENTITY-01",
        bank_id="bank_alpha",
        transaction_id="TX-9901",
        risk_score=940.0,
        severity=AlertSeverity.CRITICAL,
        involved_entity_ids=["ENTITY-CORP-GLOBAL-441"],
    )
    case_with_entity = Case(
        id="CASE-WITH-ENTITY-202",
        title="Corporate mule network",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.95,
        alert_ids=[alert.id],
        assigned_to="analyst_alice",
    )
    case_service._cases.set(case_with_entity.id, _case_to_dict(case_with_entity))

    xml_with_entity = RegulatoryReporterService.generate_sar_xml(
        case_with_entity.id, alerts=[alert]
    )
    assert "<EntityPrivacyHash>ENTITY-CORP-GLOBAL-441</EntityPrivacyHash>" in xml_with_entity


def test_case_status_gate_rejects_unconfirmed_cases(case_service: CaseManagementService) -> None:
    """Verify that unconfirmed/open cases are rejected from SAR filing across all lifecycle states."""
    invalid_statuses = [
        CaseStatus.OPEN,
        CaseStatus.INVESTIGATING,
        CaseStatus.CLOSED_FALSE_POSITIVE,
        CaseStatus.PENDING_REVIEW,
    ]

    for st in invalid_statuses:
        case = Case(
            id=f"CASE-INVALID-{st.value}",
            title=f"Test case in status {st.value}",
            status=st,
            priority=CasePriority.P3_MEDIUM,
            total_risk_score=0.50,
        )
        case_service._cases.set(case.id, _case_to_dict(case))

        with pytest.raises(SARValidationError) as exc_info:
            RegulatoryReporterService.generate_sar_xml(case.id)

        assert "is not resolved confirmed fraud" in str(exc_info.value)


def test_formal_fincen_sar_2_xsd_validation(case_service: CaseManagementService) -> None:
    """Verify that formal XSD schema validation against FinCEN_SAR_2.0.xsd executes and detects violations."""
    assert XSD_SCHEMA_PATH.exists(), f"Missing FinCEN schema at {XSD_SCHEMA_PATH}"

    case = Case(
        id="CASE-XSD-001",
        title="Valid XSD Schema Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.91,
        alert_ids=["ALT-XSD-1"],
        assigned_to="auditor",
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    valid_xml = RegulatoryReporterService.generate_sar_xml(case.id)
    # Validate valid XML doesn't raise
    RegulatoryReporterService.validate_sar_xml_structure(valid_xml)

    # Corrupt XML by removing mandatory ReportingInstitution
    corrupted_xml = valid_xml.replace(
        "<ReportingInstitution>", "<!-- stripped ReportingInstitution -->"
    ).replace(
        "</ReportingInstitution>", ""
    )
    with pytest.raises(SARValidationError) as exc:
        RegulatoryReporterService.validate_sar_xml_structure(corrupted_xml)

    assert "reportinginstitution" in str(exc.value).lower() or "missing mandatory" in str(exc.value).lower()


def test_sha256_cryptographic_filing_hash_and_record(case_service: CaseManagementService) -> None:
    """Verify that generate_and_store_sar_filing computes and verifies canonical SHA-256 filing hashes."""
    case = Case(
        id="CASE-HASH-001",
        title="Cryptographic Hash Verification Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.99,
        alert_ids=["ALT-HASH-1"],
        assigned_to="lead_cryptographer",
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    filing = RegulatoryReporterService.generate_and_store_sar_filing(case.id, case_obj=case)

    assert filing.case_id == case.id
    assert filing.status == "FILED"
    assert len(filing.sha256_hash) == 64
    assert filing.submission_id.startswith("sar_")

    # Read persisted file and recompute SHA-256 independently
    with open(filing.xml_path, encoding="utf-8") as f:
        content = f.read()

    independent_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert filing.sha256_hash == independent_sha


def test_atomic_disk_persistence_and_retrieval(case_service: CaseManagementService) -> None:
    """Verify atomic disk persistence under storage/regulatory_filings/ and retrieval via get_filing."""
    case = Case(
        id="CASE-PERSIST-001",
        title="Atomic File Persistence Verification",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.89,
        alert_ids=["ALT-PERSIST-1"],
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    filing = RegulatoryReporterService.generate_and_store_sar_filing(case.id, case_obj=case)
    assert os.path.exists(filing.xml_path)

    # Retrieve by case_id
    rec_by_case, xml_by_case = RegulatoryReporterService.get_filing(case.id)
    assert rec_by_case is not None
    assert xml_by_case is not None
    assert rec_by_case.sha256_hash == filing.sha256_hash

    # Retrieve by filing_id
    rec_by_fid, xml_by_fid = RegulatoryReporterService.get_filing(filing.filing_id)
    assert rec_by_fid is not None
    assert rec_by_fid.filing_id == filing.filing_id
    assert xml_by_fid == xml_by_case


def test_thread_safe_concurrent_filings_and_listing(case_service: CaseManagementService) -> None:
    """Verify thread-safe concurrent filing generation under RLock and list_filings aggregation."""
    cases = []
    for i in range(5):
        c = Case(
            id=f"CASE-CONCURRENT-{i:03d}",
            title=f"Concurrent filing {i}",
            status=CaseStatus.CLOSED_CONFIRMED,
            priority=CasePriority.P2_HIGH,
            total_risk_score=0.85 + (i * 0.02),
            alert_ids=[f"ALT-CONC-{i}"],
        )
        cases.append(c)
        case_service._cases.set(c.id, _case_to_dict(c))

    results = []

    def _worker(c: Case) -> None:
        rec = RegulatoryReporterService.generate_and_store_sar_filing(c.id, case_obj=c)
        results.append(rec)

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(_worker, cases))

    assert len(results) == 5
    distinct_hashes = {r.sha256_hash for r in results}
    assert len(distinct_hashes) == 5, "All filings must produce unique SHA-256 hashes"

    all_filings = RegulatoryReporterService.list_filings(limit=100)
    listed_case_ids = {f.case_id for f in all_filings}
    for c in cases:
        assert c.id in listed_case_ids


def test_custom_narrative_and_institution_overrides(case_service: CaseManagementService) -> None:
    """Verify custom narrative summary and reporting financial institution overrides."""
    case = Case(
        id="CASE-OVERRIDE-001",
        title="Default Title",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.98,
        alert_ids=["ALT-OV-1"],
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    custom_institution = "Global Sovereign Intelligence Consortium"
    custom_narrative = (
        "Part I: Multi-bank Layering Syndicate.\n"
        "Autonomous GNN detected structuring loops across 4 institution nodes."
    )

    xml_str = RegulatoryReporterService.generate_sar_xml(
        case.id,
        case_obj=case,
        institution_name=custom_institution,
        tin_type="EIN",
        narrative_override=custom_narrative,
    )

    assert f"<InstitutionName>{custom_institution}</InstitutionName>" in xml_str
    assert custom_narrative in xml_str


def test_compliance_router_sar_endpoints(case_service: CaseManagementService) -> None:
    """Verify /api/v1/compliance/sar/* endpoints (validate, generate, list, retrieve)."""
    case = Case(
        id="CASE-COMPL-ROUTER-001",
        title="Compliance API Test Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.93,
        alert_ids=["ALT-CR-1"],
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    # 1. Generate via compliance router
    gen_resp = client.post(
        "/api/v1/compliance/sar/generate",
        json={
            "case_id": case.id,
            "institution_name": "Test Bank Consortium",
            "narrative_override": "Compliance Endpoint Investigation Summary",
        },
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["status"] == "FILED"
    assert "sha256_hash" in gen_data
    filing_id = gen_data["filing_id"]

    # 2. List filings
    list_resp = client.get("/api/v1/compliance/sar/filings?limit=20")
    assert list_resp.status_code == 200
    filings = list_resp.json()
    assert isinstance(filings, list)
    assert any(f["case_id"] == case.id for f in filings)

    # 3. Retrieve filing by ID
    get_resp = client.get(f"/api/v1/compliance/sar/filings/{filing_id}")
    assert get_resp.status_code == 200
    filing_detail = get_resp.json()
    assert filing_detail["filing_id"] == filing_id
    assert "xml_content" in filing_detail
    assert "<EFilingSubmission" in filing_detail["xml_content"]

    # 4. Validate valid raw XML
    val_resp = client.post(
        "/api/v1/compliance/sar/validate",
        json={"xml_content": filing_detail["xml_content"]},
    )
    assert val_resp.status_code == 200
    assert val_resp.json()["valid"] is True
    assert val_resp.json()["sha256_hash"] == gen_data["sha256_hash"]

    # 5. Validate invalid raw XML returns 400
    bad_resp = client.post(
        "/api/v1/compliance/sar/validate",
        json={"xml_content": "<InvalidXmlRoot>Missing headers</InvalidXmlRoot>"},
    )
    assert bad_resp.status_code == 400


def test_cases_router_sar_endpoints_contract_parity(case_service: CaseManagementService) -> None:
    """Verify cases router endpoints (/file-sar, /export/fincen-xml, /sar-report) return 404 on missing cases and accurate contracts on real cases."""
    # Non-existent case must return 404 (not synthesized mock XML)
    resp_404 = client.post("/api/v1/cases/CASE-DOES-NOT-EXIST-404/file-sar")
    assert resp_404.status_code == 404

    # Valid confirmed case
    case = Case(
        id="CASE-CONTRACT-777",
        title="API Contract Parity Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P1_CRITICAL,
        total_risk_score=0.94,
        alert_ids=["ALT-777"],
    )
    case_service._cases.set(case.id, _case_to_dict(case))

    # Test /file-sar
    resp_file = client.post(f"/api/v1/cases/{case.id}/file-sar")
    assert resp_file.status_code == 200
    data_file = resp_file.json()
    assert data_file["status"] == "FILED"
    assert "sha256_hash" in data_file
    assert "<EFilingSubmission" in data_file["xml"]

    # Test /export/fincen-xml
    resp_export = client.post(
        "/api/v1/cases/export/fincen-xml",
        json={
            "case_id": case.id,
            "institution_name": "Consortium SIEM Gateway",
            "narrative_override": "SIEM Automated SAR Dispatch",
        },
    )
    assert resp_export.status_code == 200
    data_export = resp_export.json()
    assert data_export["status"] == "FILED"
    assert data_export["sha256_hash"] is not None
    assert "SIEM Automated SAR Dispatch" in data_export["xml"]

    # Test GET /sar-report download
    resp_dl = client.get(f"/api/v1/cases/{case.id}/sar-report")
    assert resp_dl.status_code == 200
    assert "application/xml" in resp_dl.headers.get("content-type", "")
    assert "<EFilingSubmission" in resp_dl.text
