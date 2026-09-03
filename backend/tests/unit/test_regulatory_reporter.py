# ruff: noqa: TC003
"""Automated Unit Test Suite for Regulatory Reporter & EU AI Act Compliance — Section 45.1."""

from __future__ import annotations

import pytest

from app.application.services.regulatory_reporter import (
    RegulatoryReporterService,
    SARValidationError,
)
from app.domain.ai_act_compliance import (
    generate_transparency_report,
    record_human_oversight,
)
from app.domain.entities_phase2 import Case
from app.domain.enums import CasePriority, CaseStatus


def test_sar_xml_passes_xsd_validation() -> None:
    """Generate SAR XML for confirmed fraud case, assert valid FinCEN BSA 2.0 XML structure."""
    case = Case(
        id="case_confirmed_001",
        title="Cross-bank structured money laundering ring",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.96,
        alert_ids=["alert_101", "alert_102"],
        assigned_to="lead_compliance_officer",
    )

    xml_str = RegulatoryReporterService.generate_sar_xml(case.id, case_obj=case)

    assert xml_str.startswith("<?xml")
    assert "<EFilingSubmission" in xml_str
    assert "<ActivityType>SAR</ActivityType>" in xml_str
    assert "<ActivityStatus>CLOSED_CONFIRMED</ActivityStatus>" in xml_str
    assert "Consortium AML Joint Investigation Unit" in xml_str
    assert "<TotalRiskScore>0.96</TotalRiskScore>" in xml_str


def test_sar_xml_fails_when_violating_xsd_schema() -> None:
    """Verify that XML payload missing mandatory XSD schema child element raises SARValidationError."""
    # Build XML with mandatory FinCEN XSD element missing (e.g. missing ReportingInstitution)
    invalid_xml = """<?xml version="1.0" encoding="UTF-8"?>
<EFilingSubmission xmlns="http://www.fincen.gov/spec/bsa">
  <SubmissionHeader>
    <ActivityType>SAR</ActivityType>
    <SubmissionType>New</SubmissionType>
    <CreatedTimestamp>2026-09-03T20:00:00Z</CreatedTimestamp>
  </SubmissionHeader>
  <Activity>
    <ActivityID>case_test_bad</ActivityID>
    <ActivityStatus>CLOSED_CONFIRMED</ActivityStatus>
    <!-- Missing ReportingInstitution element mandated by FinCEN XSD schema -->
    <Subjects>
      <Subject><EntityPrivacyHash>hash_123</EntityPrivacyHash></Subject>
    </Subjects>
    <SuspiciousActivityDetails>
      <TotalRiskScore>0.95</TotalRiskScore>
      <Priority>P1_CRITICAL</Priority>
      <AlertIds><AlertId>alt_1</AlertId></AlertIds>
    </SuspiciousActivityDetails>
    <Narrative><Summary>Test</Summary></Narrative>
  </Activity>
</EFilingSubmission>"""

    with pytest.raises(SARValidationError) as exc_info:
        RegulatoryReporterService.validate_sar_xml_structure(invalid_xml)

    assert "FinCEN SAR 2.0 XSD schema validation failed" in str(exc_info.value) or "missing mandatory" in str(exc_info.value).lower()


def test_sar_rejected_for_unresolved_case() -> None:
    """Call generate_sar_xml for an open / under investigation case, assert SARValidationError is raised."""
    open_case = Case(
        id="case_open_002",
        title="Unconfirmed velocity spike",
        status=CaseStatus.INVESTIGATING,
        priority=CasePriority.P3_MEDIUM,
        total_risk_score=0.45,
        alert_ids=["alert_201"],
        assigned_to="junior_analyst",
    )

    with pytest.raises(SARValidationError) as exc_info:
        RegulatoryReporterService.generate_sar_xml(open_case.id, case_obj=open_case)

    assert "is not resolved confirmed fraud" in str(exc_info.value)


def test_ai_act_pdf_contains_required_fields() -> None:
    """Generate EU AI Act Article 13 transparency report PDF, assert required disclosures are present."""
    model_id = "model_champion_v2.5.0"
    pdf_bytes = generate_transparency_report(model_id)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 200
    assert pdf_bytes.startswith(b"%PDF") or b"%PDF" in pdf_bytes

    raw_str = pdf_bytes.decode("latin1", errors="ignore")
    assert "Article 13" in raw_str
    assert "EU AI Act" in raw_str or "Regulation (EU) 2024/1689" in raw_str
    assert model_id in raw_str


def test_human_oversight_recording() -> None:
    """Record human supervisor oversight decision, assert recorded with EU AI Act Article 14 reference."""
    rec = record_human_oversight(
        case_id="case_confirmed_001",
        supervisor_id="sup_john_doe",
        decision="APPROVED_FOR_SAR_FILING",
    )

    assert rec["case_id"] == "case_confirmed_001"
    assert rec["supervisor_id"] == "sup_john_doe"
    assert rec["decision"] == "APPROVED_FOR_SAR_FILING"
    assert "Article 14" in rec["article_reference"]
