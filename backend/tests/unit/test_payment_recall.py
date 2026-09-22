"""Unit tests for SEPA Instant Payment Recall module (Phase 107).

Validates:
- ISO 20022 XML generation: camt.056, pacs.004, camt.029 structural correctness.
- Recall reason code SLA assignments.
- State machine: valid transitions, invalid transitions (409), terminal states.
- Provisional hold: triggered flag, metadata recording.
- Positive resolution: FUNDS_RETURNED and PARTIALLY_RETURNED.
- Negative resolution: UNABLE_TO_RECALL with camt.029.
- Audit chain integrity: hash-chaining, tamper detection.
- Pydantic schema validation: BIC, IBAN, amount, UETR.
- FastAPI endpoint contracts: HTTP 201, 200, 404, 409, 400.
- Dual routing: /api/v1/recalls and /v1/recalls.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.application.services.payment_recall_service import (
    PaymentRecallService,
    InvalidRecallTransitionError,
    RecallCaseNotFoundError,
    _compute_recall_event_hash,
    _mask_iban,
    _validate_amount,
    generate_camt056_xml,
    generate_camt029_xml,
    generate_pacs004_xml,
    get_recall_service,
)
from app.domain.enums import RecallReasonCode, RecallStatus, ResolutionCode

os.environ.setdefault("TESTING", "1")

# ── Constants used across tests ────────────────────────────────────────────────

INSTRUCTING_BIC = "DEUTDEDB"
CREDITOR_BIC = "BNPAFRPP"
DEBTOR_IBAN = "DE89370400440532013000"
CREDITOR_IBAN = "FR7630006000011234567890189"
MSG_ID = "TEST-MSG-2026-001"
INSTR_ID = "INSTR-001"
E2E_ID = "E2E-2026-001"
UETR = "3d4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"
AMOUNT = "49750.00"


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def svc() -> PaymentRecallService:
    return PaymentRecallService()


@pytest.fixture()
def initiated_case(svc: PaymentRecallService):
    return svc.initiate_recall(
        original_msg_id=MSG_ID,
        original_instr_id=INSTR_ID,
        original_end_to_end_id=E2E_ID,
        original_uetr=UETR,
        recall_reason=RecallReasonCode.FRAD,
        amount_eur=AMOUNT,
        instructing_agent_bic=INSTRUCTING_BIC,
        creditor_agent_bic=CREDITOR_BIC,
        debtor_iban=DEBTOR_IBAN,
        creditor_iban=CREDITOR_IBAN,
        actor="officer-001",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# XML generation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestXMLGeneration:

    def test_camt056_is_valid_xml(self, initiated_case):
        xml_str = initiated_case.camt056_xml
        assert xml_str, "camt056_xml must not be empty"
        root = ET.fromstring(xml_str)
        assert root.tag.endswith("Document"), f"Root must be Document, got {root.tag}"

    def test_camt056_contains_recall_reason(self, initiated_case):
        xml = initiated_case.camt056_xml
        assert RecallReasonCode.FRAD in xml

    def test_camt056_contains_msg_id(self, initiated_case):
        assert MSG_ID in initiated_case.camt056_xml

    def test_camt056_contains_amount(self, initiated_case):
        assert AMOUNT in initiated_case.camt056_xml

    def test_camt056_contains_bic(self, initiated_case):
        assert INSTRUCTING_BIC in initiated_case.camt056_xml
        assert CREDITOR_BIC in initiated_case.camt056_xml

    def test_camt056_contains_iban(self, initiated_case):
        assert DEBTOR_IBAN in initiated_case.camt056_xml
        assert CREDITOR_IBAN in initiated_case.camt056_xml

    def test_camt056_contains_uetr(self, initiated_case):
        assert UETR in initiated_case.camt056_xml

    def test_pacs004_generation(self, initiated_case):
        xml = generate_pacs004_xml(
            case=initiated_case,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur=AMOUNT,
        )
        root = ET.fromstring(xml)
        assert root.tag.endswith("Document")
        assert AMOUNT in xml
        assert CREDITOR_BIC in xml
        assert MSG_ID in xml

    def test_pacs004_partial_amount(self, initiated_case):
        partial = "25000.00"
        xml = generate_pacs004_xml(
            case=initiated_case,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur=partial,
        )
        assert partial in xml

    def test_camt029_generation(self, initiated_case):
        xml = generate_camt029_xml(initiated_case, ResolutionCode.NOAS, "Beneficiary account frozen")
        root = ET.fromstring(xml)
        assert root.tag.endswith("Document")
        assert ResolutionCode.NOAS in xml
        assert initiated_case.id in xml

    def test_camt029_truncates_narrative_to_140_chars(self, initiated_case):
        long_narrative = "X" * 200
        xml = generate_camt029_xml(initiated_case, ResolutionCode.LEGL, long_narrative)
        assert "X" * 141 not in xml

    def test_camt056_namespace(self, initiated_case):
        assert "camt.056.001.08" in initiated_case.camt056_xml


# ═══════════════════════════════════════════════════════════════════════════════
# Initiation and SLA tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecallInitiation:

    def test_frad_sla_is_4h(self, initiated_case):
        assert initiated_case.sla_hours == 4

    def test_tech_sla_is_240h(self, svc):
        case = svc.initiate_recall(
            original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
            original_uetr="", recall_reason=RecallReasonCode.TECH, amount_eur="100.00",
            instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
            debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN,
        )
        assert case.sla_hours == 240

    @pytest.mark.parametrize("reason", list(RecallReasonCode))
    def test_all_reason_codes_accepted(self, svc, reason):
        case = svc.initiate_recall(
            original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
            original_uetr="", recall_reason=reason, amount_eur="1.00",
            instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
            debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN,
        )
        assert case.recall_reason == reason

    def test_invalid_reason_raises(self, svc):
        with pytest.raises(ValueError, match="Invalid recall reason"):
            svc.initiate_recall(
                original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
                original_uetr="", recall_reason="BOGUS", amount_eur="1.00",
                instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
                debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN,
            )

    def test_non_eur_currency_raises(self, svc):
        with pytest.raises(ValueError, match="Only EUR supported"):
            svc.initiate_recall(
                original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
                original_uetr="", recall_reason=RecallReasonCode.FRAD, amount_eur="1.00",
                instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
                debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN, currency="USD",
            )

    def test_bic_stored_as_hash(self, initiated_case):
        expected_hash = hashlib.sha256(INSTRUCTING_BIC.encode()).hexdigest()
        assert initiated_case.originating_bank_bic_hash == expected_hash

    def test_initial_audit_trail_has_one_entry(self, initiated_case):
        assert len(initiated_case.audit_trail) == 1
        assert initiated_case.audit_trail[0].action == "RECALL_INITIATED"

    def test_sla_deadline_set_correctly(self, initiated_case):
        assert initiated_case.sla_deadline is not None
        diff = (initiated_case.sla_deadline - initiated_case.created_at).total_seconds()
        # FRAD = 4 hours = 14400 seconds (allow ±5s for test execution)
        assert abs(diff - 14400) < 5

    def test_status_is_initiated(self, initiated_case):
        assert initiated_case.status == RecallStatus.INITIATED

    def test_camt056_xml_populated(self, initiated_case):
        assert len(initiated_case.camt056_xml) > 100


# ═══════════════════════════════════════════════════════════════════════════════
# State machine tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateMachine:

    def test_initiated_to_sent(self, svc, initiated_case):
        case = svc.mark_sent(initiated_case.id)
        assert case.status == RecallStatus.SENT

    def test_sent_to_acknowledged(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        case = svc.acknowledge_by_creditor_agent(initiated_case.id)
        assert case.status == RecallStatus.ACKNOWLEDGED_BY_CREDITOR_AGENT

    def test_initiated_to_cancelled(self, svc, initiated_case):
        case = svc.cancel_recall(initiated_case.id, actor="officer", reason="Mistake")
        assert case.status == RecallStatus.CANCELLED

    def test_cannot_cancel_sent(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        with pytest.raises(InvalidRecallTransitionError):
            svc.cancel_recall(initiated_case.id, actor="officer")

    def test_funds_returned_is_terminal(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        svc.resolve_positive(
            initiated_case.id,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur=AMOUNT,
        )
        with pytest.raises(InvalidRecallTransitionError):
            svc.mark_sent(initiated_case.id)

    def test_unable_to_recall_is_terminal(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        svc.resolve_negative(initiated_case.id, resolution_code=ResolutionCode.NOAS)
        with pytest.raises(InvalidRecallTransitionError):
            svc.mark_sent(initiated_case.id)

    def test_audit_trail_grows_per_transition(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        svc.acknowledge_by_creditor_agent(initiated_case.id)
        case = svc.get_case(initiated_case.id)
        # 1 INITIATED + 1 SENT + 1 ACKNOWLEDGED = 3
        assert len(case.audit_trail) == 3

    def test_not_found_raises_on_transition(self, svc):
        with pytest.raises(RecallCaseNotFoundError):
            svc.mark_sent("nonexistent-uuid")


# ═══════════════════════════════════════════════════════════════════════════════
# Provisional hold tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvisionalHold:

    def test_hold_sets_flag(self, svc, initiated_case):
        case = svc.trigger_provisional_hold(initiated_case.id, actor="system")
        assert case.provisional_hold_triggered is True

    def test_hold_records_timestamp(self, svc, initiated_case):
        case = svc.trigger_provisional_hold(initiated_case.id, actor="system")
        assert case.provisional_hold_triggered_at is not None

    def test_hold_adds_audit_entry(self, svc, initiated_case):
        svc.trigger_provisional_hold(initiated_case.id)
        case = svc.get_case(initiated_case.id)
        actions = [e.action for e in case.audit_trail]
        assert "PROVISIONAL_HOLD_TRIGGERED" in actions

    def test_hold_without_webhook_url_does_not_crash(self, svc, initiated_case):
        # No webhook URL registered — should succeed silently
        case = svc.trigger_provisional_hold(initiated_case.id)
        assert case.provisional_hold_triggered is True

    def test_hold_not_found_raises(self, svc):
        with pytest.raises(RecallCaseNotFoundError):
            svc.trigger_provisional_hold("no-such-id")


# ═══════════════════════════════════════════════════════════════════════════════
# Positive resolution tests (pacs.004)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositiveResolution:

    def _advance_to_sent(self, svc, case):
        svc.mark_sent(case.id)
        return case

    def test_full_return_status(self, svc, initiated_case):
        self._advance_to_sent(svc, initiated_case)
        case = svc.resolve_positive(
            initiated_case.id,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur=AMOUNT,
        )
        assert case.status == RecallStatus.FUNDS_RETURNED

    def test_partial_return_status(self, svc, initiated_case):
        self._advance_to_sent(svc, initiated_case)
        svc.acknowledge_by_creditor_agent(initiated_case.id)
        case = svc.resolve_positive(
            initiated_case.id,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur="25000.00",
        )
        assert case.status == RecallStatus.PARTIALLY_RETURNED
        assert case.returned_amount_eur == "25000.00"

    def test_pacs004_xml_populated_on_return(self, svc, initiated_case):
        self._advance_to_sent(svc, initiated_case)
        case = svc.resolve_positive(
            initiated_case.id,
            creditor_agent_bic=CREDITOR_BIC,
            instructing_agent_bic=INSTRUCTING_BIC,
            returned_amount_eur=AMOUNT,
        )
        assert len(case.pacs004_xml) > 100
        assert "pacs.004.001.09" in case.pacs004_xml

    def test_resolved_at_set(self, svc, initiated_case):
        self._advance_to_sent(svc, initiated_case)
        case = svc.resolve_positive(
            initiated_case.id, CREDITOR_BIC, INSTRUCTING_BIC, AMOUNT
        )
        assert case.resolved_at is not None

    def test_invalid_amount_raises(self, svc, initiated_case):
        self._advance_to_sent(svc, initiated_case)
        with pytest.raises(Exception):
            svc.resolve_positive(initiated_case.id, CREDITOR_BIC, INSTRUCTING_BIC, "-100.00")


# ═══════════════════════════════════════════════════════════════════════════════
# Negative resolution tests (camt.029)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNegativeResolution:

    @pytest.mark.parametrize("code", list(ResolutionCode))
    def test_all_resolution_codes_accepted(self, svc, initiated_case, code):
        svc.mark_sent(initiated_case.id)
        case = svc.resolve_negative(initiated_case.id, resolution_code=code)
        assert case.status == RecallStatus.UNABLE_TO_RECALL
        assert case.resolution_code == code

    def test_camt029_xml_populated(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        case = svc.resolve_negative(initiated_case.id, ResolutionCode.NOOR, "No original transaction found")
        assert "camt.029.001.09" in case.camt029_xml
        assert ResolutionCode.NOOR in case.camt029_xml

    def test_invalid_resolution_code_raises(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        with pytest.raises(ValueError, match="Invalid resolution code"):
            svc.resolve_negative(initiated_case.id, "BOGUS")

    def test_narrative_stored(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        case = svc.resolve_negative(initiated_case.id, ResolutionCode.LEGL, "Legal freeze active")
        assert case.resolution_narrative == "Legal freeze active"

    def test_resolved_at_set(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        case = svc.resolve_negative(initiated_case.id, ResolutionCode.NOAS)
        assert case.resolved_at is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Audit chain integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditChain:

    def test_fresh_case_chain_intact(self, svc, initiated_case):
        assert svc.verify_audit_chain(initiated_case.id) is True

    def test_chain_intact_after_transitions(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        svc.acknowledge_by_creditor_agent(initiated_case.id)
        svc.resolve_negative(initiated_case.id, ResolutionCode.NOAS)
        assert svc.verify_audit_chain(initiated_case.id) is True

    def test_tampered_hash_detected(self, svc, initiated_case):
        svc.mark_sent(initiated_case.id)
        case = svc.get_case(initiated_case.id)
        case.audit_trail[0].event_hash = "0" * 64
        assert svc.verify_audit_chain(initiated_case.id) is False

    def test_not_found_raises(self, svc):
        with pytest.raises(RecallCaseNotFoundError):
            svc.verify_audit_chain("nonexistent")

    def test_hash_computation_deterministic(self):
        ts = datetime(2026, 9, 22, 18, 0, 0, tzinfo=UTC)
        h1 = _compute_recall_event_hash("prev", 0, "actor", "action", ts)
        h2 = _compute_recall_event_hash("prev", 0, "actor", "action", ts)
        assert h1 == h2
        assert len(h1) == 64


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_validate_amount_positive(self):
        from decimal import Decimal
        assert _validate_amount("100.00") == Decimal("100.00")

    def test_validate_amount_rejects_negative(self):
        from app.application.services.payment_recall_service import InvalidAmountError
        with pytest.raises(InvalidAmountError):
            _validate_amount("-1.00")

    def test_validate_amount_rejects_zero(self):
        from app.application.services.payment_recall_service import InvalidAmountError
        with pytest.raises(InvalidAmountError):
            _validate_amount("0.00")

    def test_validate_amount_rejects_too_many_decimals(self):
        from app.application.services.payment_recall_service import InvalidAmountError
        with pytest.raises(InvalidAmountError):
            _validate_amount("1.001")

    def test_mask_iban_returns_prefix_and_hash(self):
        masked = _mask_iban(DEBTOR_IBAN)
        assert masked.startswith("DE89:")
        assert len(masked) > 5

    def test_mask_iban_different_ibans_produce_different_hashes(self):
        m1 = _mask_iban(DEBTOR_IBAN)
        m2 = _mask_iban(CREDITOR_IBAN)
        assert m1 != m2


# ═══════════════════════════════════════════════════════════════════════════════
# Schema validation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaValidation:

    def test_valid_request_parses(self):
        from app.application.schemas.recall_schemas import InitiateRecallRequest
        req = InitiateRecallRequest(
            original_msg_id=MSG_ID,
            original_instr_id=INSTR_ID,
            original_end_to_end_id=E2E_ID,
            original_uetr=UETR,
            recall_reason="FRAD",
            amount_eur=AMOUNT,
            instructing_agent_bic=INSTRUCTING_BIC,
            creditor_agent_bic=CREDITOR_BIC,
            debtor_iban=DEBTOR_IBAN,
            creditor_iban=CREDITOR_IBAN,
        )
        assert req.recall_reason == "FRAD"

    def test_invalid_bic_rejected(self):
        from pydantic import ValidationError
        from app.application.schemas.recall_schemas import InitiateRecallRequest
        with pytest.raises(ValidationError, match="BIC"):
            InitiateRecallRequest(
                original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
                recall_reason="FRAD", amount_eur="1.00",
                instructing_agent_bic="NOTABIC!!",
                creditor_agent_bic=CREDITOR_BIC,
                debtor_iban=DEBTOR_IBAN,
                creditor_iban=CREDITOR_IBAN,
            )

    def test_negative_amount_rejected(self):
        from pydantic import ValidationError
        from app.application.schemas.recall_schemas import InitiateRecallRequest
        with pytest.raises(ValidationError):
            InitiateRecallRequest(
                original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
                recall_reason="FRAD", amount_eur="-100.00",
                instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
                debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN,
            )

    def test_invalid_uetr_rejected(self):
        from pydantic import ValidationError
        from app.application.schemas.recall_schemas import InitiateRecallRequest
        with pytest.raises(ValidationError, match="UUIDv4"):
            InitiateRecallRequest(
                original_msg_id="M1", original_instr_id="I1", original_end_to_end_id="E1",
                original_uetr="not-a-uuid",
                recall_reason="FRAD", amount_eur="1.00",
                instructing_agent_bic=INSTRUCTING_BIC, creditor_agent_bic=CREDITOR_BIC,
                debtor_iban=DEBTOR_IBAN, creditor_iban=CREDITOR_IBAN,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP API endpoint tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def _recall_payload(**overrides) -> dict:
    base = {
        "original_msg_id": MSG_ID,
        "original_instr_id": INSTR_ID,
        "original_end_to_end_id": E2E_ID,
        "original_uetr": UETR,
        "recall_reason": "FRAD",
        "amount_eur": AMOUNT,
        "instructing_agent_bic": INSTRUCTING_BIC,
        "creditor_agent_bic": CREDITOR_BIC,
        "debtor_iban": DEBTOR_IBAN,
        "creditor_iban": CREDITOR_IBAN,
        "actor": "test-officer",
    }
    base.update(overrides)
    return base


class TestRecallAPI:

    def test_initiate_recall_returns_201(self, client):
        resp = client.post("/api/v1/recalls", json=_recall_payload())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "INITIATED"
        assert data["recall_reason"] == "FRAD"
        assert data["sla_hours"] == 4
        assert len(data["camt056_xml"]) > 100

    def test_initiate_recall_invalid_bic_returns_422(self, client):
        resp = client.post("/api/v1/recalls", json=_recall_payload(instructing_agent_bic="BAD!!"))
        assert resp.status_code == 422

    def test_initiate_recall_invalid_amount_returns_422(self, client):
        resp = client.post("/api/v1/recalls", json=_recall_payload(amount_eur="-50.00"))
        assert resp.status_code == 422

    def test_list_recalls_returns_200(self, client):
        client.post("/api/v1/recalls", json=_recall_payload())
        resp = client.get("/api/v1/recalls")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_recall_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/recalls/{case_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == case_id

    def test_get_recall_not_found_returns_404(self, client):
        resp = client.get("/api/v1/recalls/no-such-uuid")
        assert resp.status_code == 404

    def test_mark_sent_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        resp = client.post(f"/api/v1/recalls/{case_id}/send")
        assert resp.status_code == 200
        assert resp.json()["status"] == "SENT"

    def test_acknowledge_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        resp = client.post(f"/api/v1/recalls/{case_id}/acknowledge")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACKNOWLEDGED_BY_CREDITOR_AGENT"

    def test_invalid_transition_returns_409(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        # Cannot acknowledge before sending
        resp = client.post(f"/api/v1/recalls/{case_id}/acknowledge")
        assert resp.status_code == 409

    def test_trigger_hold_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        resp = client.post(f"/api/v1/recalls/{case_id}/hold", json={"actor": "system"})
        assert resp.status_code == 200
        assert resp.json()["hold_triggered"] is True

    def test_positive_resolution_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        resp = client.post(f"/api/v1/recalls/{case_id}/resolve/positive", json={
            "creditor_agent_bic": CREDITOR_BIC,
            "instructing_agent_bic": INSTRUCTING_BIC,
            "returned_amount_eur": AMOUNT,
            "actor": "creditor_agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "FUNDS_RETURNED"
        assert len(resp.json()["pacs004_xml"]) > 100

    def test_partial_return_sets_partially_returned_status(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        client.post(f"/api/v1/recalls/{case_id}/acknowledge")
        resp = client.post(f"/api/v1/recalls/{case_id}/resolve/positive", json={
            "creditor_agent_bic": CREDITOR_BIC,
            "instructing_agent_bic": INSTRUCTING_BIC,
            "returned_amount_eur": "10000.00",
            "actor": "creditor_agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "PARTIALLY_RETURNED"

    def test_negative_resolution_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        resp = client.post(f"/api/v1/recalls/{case_id}/resolve/negative", json={
            "resolution_code": "NOAS",
            "narrative": "Beneficiary account holder has frozen the account.",
            "actor": "creditor_agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "UNABLE_TO_RECALL"
        assert len(resp.json()["camt029_xml"]) > 100

    def test_cancel_recall_returns_200(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        resp = client.post(f"/api/v1/recalls/{case_id}/cancel", json={"reason": "Erroneous submission"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"

    def test_audit_chain_endpoint(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        resp = client.get(f"/api/v1/recalls/{case_id}/audit-chain")
        assert resp.status_code == 200
        chain = resp.json()
        assert len(chain) == 2  # INITIATED + SENT
        assert chain[0]["action"] == "RECALL_INITIATED"

    def test_verify_chain_intact(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/recalls/{case_id}/verify-chain")
        assert resp.status_code == 200
        assert resp.json()["chain_intact"] is True

    def test_metrics_endpoint(self, client):
        resp = client.get("/api/v1/recalls/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cases" in data
        assert "by_status" in data
        assert "by_reason" in data
        assert "provisional_holds_triggered" in data

    def test_list_filter_by_status(self, client):
        create_resp = client.post("/api/v1/recalls", json=_recall_payload())
        case_id = create_resp.json()["id"]
        client.post(f"/api/v1/recalls/{case_id}/send")
        resp = client.get("/api/v1/recalls", params={"status": "SENT"})
        assert resp.status_code == 200
        assert all(c["status"] == "SENT" for c in resp.json())

    def test_list_filter_by_reason(self, client):
        resp = client.get("/api/v1/recalls", params={"reason": "FRAD"})
        assert resp.status_code == 200
        assert all(c["recall_reason"] == "FRAD" for c in resp.json())

    def test_dual_routing_v1_prefix(self, client):
        resp = client.post("/v1/recalls", json=_recall_payload())
        assert resp.status_code == 201
        assert resp.json()["status"] == "INITIATED"
