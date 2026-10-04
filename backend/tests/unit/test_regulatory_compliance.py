import os

import pytest

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService, _hash_event
from app.domain.enums import AlertSeverity, CasePriority, CaseStatus


@pytest.fixture
def case_service():
    service = CaseManagementService()
    service._cases.clear()
    return service


@pytest.fixture
def alert_service():
    service = AlertIntelligenceService()
    service._alert_store.clear()
    return service


def test_case_status_transitions_and_sar_filing(case_service, alert_service):
    # 1. Create a case
    case = case_service.create_case(
        title="Suspicious transaction ring", priority=CasePriority.P1_CRITICAL
    )
    assert case.status == CaseStatus.OPEN

    # 2. Add an alert
    from app.application.services.alert_service import _alert_to_dict
    from app.domain.investigation_entities import Alert

    alert = Alert(
        bank_id="bank_a",
        transaction_id="tx_1",
        risk_score=950.0,
        severity=AlertSeverity.CRITICAL,
        involved_entity_ids=["CUST_001"],
    )
    alert_service._alert_store.set(alert.id, _alert_to_dict(alert))
    case = case_service.link_alert(case.id, alert.id)

    # 3. Transition OPEN -> INVESTIGATING -> ESCALATED -> SAR_FILED -> CLOSED_CONFIRMED
    case = case_service.change_status(case.id, CaseStatus.INVESTIGATING)
    assert case.status == CaseStatus.INVESTIGATING

    case = case_service.change_status(case.id, CaseStatus.ESCALATED)
    assert case.status == CaseStatus.ESCALATED

    # Transition to SAR_FILED
    case = case_service.change_status(case.id, CaseStatus.SAR_FILED)
    assert case.status == CaseStatus.SAR_FILED

    # Assert XML report was generated and saved
    report_path = f"storage/regulatory_filings/sar_{case.id}.xml"
    assert os.path.exists(report_path)

    with open(report_path, encoding="utf-8") as f:
        xml_content = f.read()
        assert "<EFilingSubmission" in xml_content
        assert "<ActivityID>" in xml_content
        assert "CUST_001" in xml_content

    # Verify report path in timeline event metadata
    last_event = case.timeline[-1]
    assert last_event.event_type == "status_changed"
    assert last_event.metadata.get("sar_report_path") == report_path

    # Transition from SAR_FILED to CLOSED_CONFIRMED
    case = case_service.change_status(
        case.id, CaseStatus.CLOSED_CONFIRMED, supervisor_signature="supervisor_bob"
    )
    assert case.status == CaseStatus.CLOSED_CONFIRMED

    # Clean up file
    if os.path.exists(report_path):
        os.remove(report_path)


def test_cryptographic_audit_log_hash_chain(case_service):
    # Create case
    case = case_service.create_case(title="Chained audit log test")

    # Check genesis event has hash & parent_hash
    assert len(case.timeline) == 1
    genesis_event = case.timeline[0]
    assert genesis_event.metadata.get("parent_hash") == "0" * 64
    assert genesis_event.metadata.get("hash") is not None

    # Add a note
    case_service.add_note(case.id, author="analyst_1", content="Suspicious device login")
    case = case_service.get_case(case.id)
    assert len(case.timeline) == 2

    second_event = case.timeline[1]
    assert second_event.metadata.get("parent_hash") == genesis_event.metadata.get("hash")

    # Verify chain integrity programmatically
    parent_hash = "0" * 64
    for event in case.timeline:
        assert event.metadata.get("parent_hash") == parent_hash
        expected_hash = _hash_event(event, parent_hash)
        assert event.metadata.get("hash") == expected_hash
        parent_hash = event.metadata.get("hash")


def test_invalid_transitions_are_rejected(case_service):
    case = case_service.create_case(title="Invalid transition test")
    # Transitioning directly from OPEN to SAR_FILED should raise ValueError
    with pytest.raises(ValueError):
        case_service.change_status(case.id, CaseStatus.SAR_FILED)
