"""SEPA Instant Payment Recall API Router (Phase 107).

Implements the ISO 20022 payment recall workflow endpoints:

- POST   /api/v1/recalls                         — initiate camt.056 recall
- GET    /api/v1/recalls                         — list recall cases
- GET    /api/v1/recalls/{id}                    — retrieve full recall case
- POST   /api/v1/recalls/{id}/send               — mark camt.056 as sent
- POST   /api/v1/recalls/{id}/acknowledge        — record creditor agent ack
- POST   /api/v1/recalls/{id}/hold               — trigger provisional hold
- POST   /api/v1/recalls/{id}/resolve/positive   — pacs.004 funds returned
- POST   /api/v1/recalls/{id}/resolve/negative   — camt.029 unable to recall
- POST   /api/v1/recalls/{id}/cancel             — cancel recall
- GET    /api/v1/recalls/{id}/audit-chain        — retrieve audit trail
- GET    /api/v1/recalls/{id}/verify-chain       — verify hash chain integrity
- GET    /api/v1/recalls/metrics                 — aggregate metrics

All endpoints are also available on /v1/recalls for compatibility.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.application.schemas.recall_schemas import (
    AuditChainVerificationResponse,
    CancelRecallRequest,
    InitiateRecallRequest,
    NegativeResolutionRequest,
    PositiveResolutionRequest,
    ProvisionalHoldResponse,
    RecallAuditEntryResponse,
    RecallCaseResponse,
    RecallCaseSummaryResponse,
    RecallMetricsResponse,
    TriggerHoldRequest,
)
from app.application.services.payment_recall_service import (
    InvalidRecallTransitionError,
    PaymentRecallService,
    RecallCaseNotFoundError,
    get_recall_service,
)
from app.domain.entities_phase2 import RecallCase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/recalls", tags=["sepa-payment-recall"])
api_router = APIRouter(prefix="/v1/recalls", tags=["sepa-payment-recall"])


# ── Dependency ─────────────────────────────────────────────────────────────────

def _svc() -> PaymentRecallService:
    return get_recall_service()


# ── Serialisation helpers ──────────────────────────────────────────────────────

def _audit_entries(case: RecallCase) -> list[RecallAuditEntryResponse]:
    return [
        RecallAuditEntryResponse(
            seq=e.seq, actor=e.actor, action=e.action,
            previous_status=e.previous_status, new_status=e.new_status,
            message_type=e.message_type, event_hash=e.event_hash,
            previous_hash=e.previous_hash, timestamp=e.timestamp,
            metadata=e.metadata,
        )
        for e in case.audit_trail
    ]


def _to_response(case: RecallCase) -> RecallCaseResponse:
    return RecallCaseResponse(
        id=case.id,
        original_msg_id=case.original_msg_id,
        original_instr_id=case.original_instr_id,
        original_end_to_end_id=case.original_end_to_end_id,
        original_uetr=case.original_uetr,
        recall_reason=case.recall_reason,
        originating_bank_bic_hash=case.originating_bank_bic_hash,
        creditor_agent_bic_hash=case.creditor_agent_bic_hash,
        amount_eur=case.amount_eur,
        currency=case.currency,
        status=case.status,
        message_type=case.message_type,
        camt056_xml=case.camt056_xml,
        pacs004_xml=case.pacs004_xml,
        camt029_xml=case.camt029_xml,
        resolution_code=case.resolution_code,
        returned_amount_eur=case.returned_amount_eur,
        resolution_narrative=case.resolution_narrative,
        provisional_hold_triggered=case.provisional_hold_triggered,
        provisional_hold_triggered_at=case.provisional_hold_triggered_at,
        provisional_hold_response_status=case.provisional_hold_response_status,
        sla_hours=case.sla_hours,
        sla_deadline=case.sla_deadline,
        created_at=case.created_at,
        updated_at=case.updated_at,
        resolved_at=case.resolved_at,
        audit_trail=_audit_entries(case),
        head_hash=case.head_hash,
    )


def _to_summary(case: RecallCase) -> RecallCaseSummaryResponse:
    return RecallCaseSummaryResponse(
        id=case.id,
        original_msg_id=case.original_msg_id,
        recall_reason=case.recall_reason,
        status=case.status,
        amount_eur=case.amount_eur,
        currency=case.currency,
        provisional_hold_triggered=case.provisional_hold_triggered,
        sla_hours=case.sla_hours,
        sla_deadline=case.sla_deadline,
        created_at=case.created_at,
        updated_at=case.updated_at,
        resolved_at=case.resolved_at,
        audit_entry_count=len(case.audit_trail),
    )


# ── Route registration helper ─────────────────────────────────────────────────

def _register_routes(r: APIRouter) -> None:  # noqa: C901

    @r.post(
        "",
        response_model=RecallCaseResponse,
        status_code=201,
        summary="Initiate SEPA Payment Recall (camt.056)",
        description=(
            "Initiates a SEPA Instant Credit Transfer recall under the EPC SCT Inst rulebook. "
            "Generates a conformant camt.056.001.08 FIToFIPaymentCancellationRequest XML. "
            "SLA: FRAD → 4 h, all other codes → 10 business days."
        ),
    )
    async def initiate_recall(request: InitiateRecallRequest) -> RecallCaseResponse:
        svc = _svc()
        try:
            case = svc.initiate_recall(
                original_msg_id=request.original_msg_id,
                original_instr_id=request.original_instr_id,
                original_end_to_end_id=request.original_end_to_end_id,
                original_uetr=request.original_uetr,
                recall_reason=request.recall_reason,
                amount_eur=request.amount_eur,
                instructing_agent_bic=request.instructing_agent_bic,
                creditor_agent_bic=request.creditor_agent_bic,
                debtor_iban=request.debtor_iban,
                creditor_iban=request.creditor_iban,
                actor=request.actor,
                provisional_hold_webhook_url=request.provisional_hold_webhook_url,
            )
        except (ValueError, Exception) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_response(case)

    @r.get(
        "",
        response_model=list[RecallCaseSummaryResponse],
        summary="List SEPA Payment Recall Cases",
    )
    async def list_recalls(
        status: str | None = Query(default=None),
        reason: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[RecallCaseSummaryResponse]:
        svc = _svc()
        cases = svc.list_cases(status_filter=status, reason_filter=reason, limit=limit)
        return [_to_summary(c) for c in cases]

    @r.get(
        "/metrics",
        response_model=RecallMetricsResponse,
        summary="SEPA Recall Service Metrics",
    )
    async def get_metrics() -> RecallMetricsResponse:
        svc = _svc()
        return RecallMetricsResponse(**svc.get_metrics())

    @r.get(
        "/{case_id}",
        response_model=RecallCaseResponse,
        summary="Retrieve SEPA Recall Case",
    )
    async def get_recall(case_id: str) -> RecallCaseResponse:
        svc = _svc()
        try:
            return _to_response(svc.get_case(case_id))
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")

    @r.post(
        "/{case_id}/send",
        response_model=RecallCaseResponse,
        summary="Mark camt.056 as Sent to Creditor Agent",
    )
    async def mark_sent(case_id: str) -> RecallCaseResponse:
        svc = _svc()
        try:
            return _to_response(svc.mark_sent(case_id))
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        except InvalidRecallTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @r.post(
        "/{case_id}/acknowledge",
        response_model=RecallCaseResponse,
        summary="Record Creditor Agent Acknowledgement",
    )
    async def acknowledge(case_id: str) -> RecallCaseResponse:
        svc = _svc()
        try:
            return _to_response(svc.acknowledge_by_creditor_agent(case_id))
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        except InvalidRecallTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @r.post(
        "/{case_id}/hold",
        response_model=ProvisionalHoldResponse,
        status_code=200,
        summary="Trigger Provisional Hold Webhook",
        description=(
            "Sends a sub-second POST to the registered core banking webhook URL "
            "to provisionally freeze the beneficiary account pending recall resolution."
        ),
    )
    async def trigger_hold(case_id: str, request: TriggerHoldRequest) -> ProvisionalHoldResponse:
        svc = _svc()
        try:
            case = svc.trigger_provisional_hold(case_id, actor=request.actor)
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        return ProvisionalHoldResponse(
            case_id=case_id,
            hold_triggered=case.provisional_hold_triggered,
            webhook_response_status=case.provisional_hold_response_status,
            message="Provisional hold triggered." if case.provisional_hold_triggered else "Hold already triggered.",
        )

    @r.post(
        "/{case_id}/resolve/positive",
        response_model=RecallCaseResponse,
        summary="Positive Recall Resolution — pacs.004 PaymentReturn",
        description=(
            "Records a positive recall resolution: funds returned by creditor agent. "
            "Generates a conformant pacs.004.001.09 PaymentReturn XML. "
            "Updates status to FUNDS_RETURNED or PARTIALLY_RETURNED."
        ),
    )
    async def resolve_positive(case_id: str, request: PositiveResolutionRequest) -> RecallCaseResponse:
        svc = _svc()
        try:
            case = svc.resolve_positive(
                case_id=case_id,
                creditor_agent_bic=request.creditor_agent_bic,
                instructing_agent_bic=request.instructing_agent_bic,
                returned_amount_eur=request.returned_amount_eur,
                actor=request.actor,
            )
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        except InvalidRecallTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, Exception) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_response(case)

    @r.post(
        "/{case_id}/resolve/negative",
        response_model=RecallCaseResponse,
        summary="Negative Recall Resolution — camt.029 ResolutionOfInvestigation",
        description=(
            "Records that recall funds cannot be returned. "
            "Generates a conformant camt.029.001.09 ResolutionOfInvestigation XML. "
            "Updates status to UNABLE_TO_RECALL."
        ),
    )
    async def resolve_negative(case_id: str, request: NegativeResolutionRequest) -> RecallCaseResponse:
        svc = _svc()
        try:
            case = svc.resolve_negative(
                case_id=case_id,
                resolution_code=request.resolution_code,
                narrative=request.narrative,
                actor=request.actor,
            )
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        except InvalidRecallTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_response(case)

    @r.post(
        "/{case_id}/cancel",
        response_model=RecallCaseResponse,
        summary="Cancel Recall Case (INITIATED only)",
    )
    async def cancel_recall(case_id: str, request: CancelRecallRequest) -> RecallCaseResponse:
        svc = _svc()
        try:
            case = svc.cancel_recall(case_id, actor=request.actor, reason=request.reason)
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        except InvalidRecallTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_response(case)

    @r.get(
        "/{case_id}/audit-chain",
        response_model=list[RecallAuditEntryResponse],
        summary="Retrieve SEPA Recall Audit Trail",
    )
    async def get_audit_chain(case_id: str) -> list[RecallAuditEntryResponse]:
        svc = _svc()
        try:
            return _audit_entries(svc.get_case(case_id))
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")

    @r.get(
        "/{case_id}/verify-chain",
        response_model=AuditChainVerificationResponse,
        summary="Verify Recall Audit Chain Integrity",
    )
    async def verify_chain(case_id: str) -> AuditChainVerificationResponse:
        svc = _svc()
        try:
            case = svc.get_case(case_id)
            intact = svc.verify_audit_chain(case_id)
        except RecallCaseNotFoundError:
            raise HTTPException(status_code=404, detail=f"Recall case '{case_id}' not found.")
        return AuditChainVerificationResponse(
            case_id=case_id,
            chain_intact=intact,
            entry_count=len(case.audit_trail),
            head_hash=case.head_hash,
            message="Audit chain integrity verified." if intact else "AUDIT CHAIN TAMPER DETECTED.",
        )


# Register routes on both prefixed routers
_register_routes(router)
_register_routes(api_router)
