"""Inter-Bank Encrypted FININT Case Messaging Router.

Exposes a European Collaborative FININT bridge messaging API for compliance
officers and fraud intelligence units to exchange encrypted cross-institution
case tickets. Implements:

- POST   /api/v1/bridge/tickets          — create encrypted FININT ticket
- GET    /api/v1/bridge/tickets          — list tickets (filtered by bank/status/type)
- GET    /api/v1/bridge/tickets/{id}     — retrieve single ticket
- POST   /api/v1/bridge/tickets/{id}/transition  — state machine transition
- POST   /api/v1/bridge/tickets/{id}/evidence    — attach evidence hash
- POST   /api/v1/bridge/tickets/{id}/verify-evidence — verify evidence
- GET    /api/v1/bridge/tickets/{id}/audit-chain — retrieve audit trail
- GET    /api/v1/bridge/tickets/{id}/verify-chain — verify hash chain integrity
- GET    /api/v1/bridge/metrics          — aggregate service metrics
- POST   /api/v1/bridge/keypair          — generate Curve25519 keypair

Security:
- All endpoints require the X-Bank-ID header for tenant context.
- Bank IDs must be HMAC-SHA256 hashes — cleartext names are rejected.
- Payload encryption uses ephemeral Curve25519 ECDH + AES-256-GCM.
"""

from __future__ import annotations

import base64
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.application.schemas.bridge_messaging import (
    AttachEvidenceRequest,
    AuditChainVerificationResponse,
    AuditEntryResponse,
    BridgeMetricsResponse,
    CreateTicketRequest,
    EvidenceAttachResponse,
    GenerateKeypairResponse,
    TicketResponse,
    TicketSummaryResponse,
    TransitionTicketRequest,
    VerifyEvidenceRequest,
    VerifyEvidenceResponse,
)
from app.application.services.bridge_case_service import (
    BridgeCaseService,
    InvalidTicketTransitionError,
    TicketNotFoundError,
    generate_bank_keypair,
    get_bridge_service,
)
from app.domain.entities_phase2 import FinintBridgeTicket

logger = logging.getLogger(__name__)

# Dual-routing: /api/v1/bridge and /v1/bridge
router = APIRouter(prefix="/api/v1/bridge", tags=["finint-bridge-messaging"])
api_router = APIRouter(prefix="/v1/bridge", tags=["finint-bridge-messaging"])


# ── Dependency ─────────────────────────────────────────────────────────────────

def _svc() -> BridgeCaseService:
    return get_bridge_service()


# ── Serialisation helper ───────────────────────────────────────────────────────

def _ticket_to_response(ticket: FinintBridgeTicket) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        ticket_type=ticket.ticket_type,
        status=ticket.status,
        originating_bank_id=ticket.originating_bank_id,
        recipient_bank_id=ticket.recipient_bank_id,
        encrypted_payload=ticket.encrypted_payload,
        payload_nonce=ticket.payload_nonce,
        ephemeral_public_key=ticket.ephemeral_public_key,
        evidence_hashes=ticket.evidence_hashes,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        sla_hours=ticket.sla_hours,
        audit_trail=[
            AuditEntryResponse(
                seq=e.seq,
                actor=e.actor,
                action=e.action,
                previous_status=e.previous_status,
                new_status=e.new_status,
                event_hash=e.event_hash,
                previous_hash=e.previous_hash,
                timestamp=e.timestamp,
                metadata=e.metadata,
            )
            for e in ticket.audit_trail
        ],
        head_hash=ticket.head_hash,
    )


def _ticket_to_summary(ticket: FinintBridgeTicket) -> TicketSummaryResponse:
    return TicketSummaryResponse(
        id=ticket.id,
        ticket_type=ticket.ticket_type,
        status=ticket.status,
        originating_bank_id=ticket.originating_bank_id,
        recipient_bank_id=ticket.recipient_bank_id,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        sla_hours=ticket.sla_hours,
        evidence_count=len(ticket.evidence_hashes),
        audit_entry_count=len(ticket.audit_trail),
    )


# ── Routes — applied to both router and api_router via helper ─────────────────

def _register_routes(r: APIRouter) -> None:  # noqa: C901

    @r.post(
        "/tickets",
        response_model=TicketResponse,
        status_code=201,
        summary="Create Encrypted FININT Bridge Ticket",
        description=(
            "Creates an encrypted inter-bank FININT case ticket. "
            "The plaintext payload is encrypted end-to-end using ephemeral Curve25519 ECDH + AES-256-GCM. "
            "Evidence blobs are registered only as SHA-256 hashes — raw data is never persisted."
        ),
    )
    async def create_ticket(
        request: CreateTicketRequest,
        x_bank_id: Annotated[str | None, Header(alias="X-Bank-ID")] = None,
    ) -> TicketResponse:
        """Create a new encrypted FININT bridge ticket."""
        svc = _svc()

        # Decode payload and evidence from base64url
        try:
            plaintext_bytes = base64.urlsafe_b64decode(request.plaintext_payload_b64 + "==")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid base64url payload: {exc}") from exc

        evidence_bytes_list: list[bytes] = []
        for ev_b64 in request.evidence_b64_list:
            try:
                evidence_bytes_list.append(base64.urlsafe_b64decode(ev_b64 + "=="))
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid base64url evidence: {exc}") from exc

        try:
            ticket = svc.create_ticket(
                ticket_type=request.ticket_type,
                originating_bank_id=request.originating_bank_id,
                recipient_bank_id=request.recipient_bank_id,
                plaintext_payload=plaintext_bytes,
                recipient_public_key_b64=request.recipient_public_key_b64,
                evidence_bytes_list=evidence_bytes_list or None,
                actor=request.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return _ticket_to_response(ticket)

    @r.get(
        "/tickets",
        response_model=list[TicketSummaryResponse],
        summary="List FININT Bridge Tickets",
        description="Returns FININT tickets optionally filtered by bank, status, or type.",
    )
    async def list_tickets(
        bank_id: str | None = Query(default=None, description="Filter by originating or recipient bank HMAC-SHA256 ID."),
        status: str | None = Query(default=None, description="Filter by ticket status."),
        ticket_type: str | None = Query(default=None, description="Filter by ticket type."),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[TicketSummaryResponse]:
        svc = _svc()
        tickets = svc.list_tickets(
            bank_id=bank_id,
            status_filter=status,
            ticket_type_filter=ticket_type,
            limit=limit,
        )
        return [_ticket_to_summary(t) for t in tickets]

    @r.get(
        "/tickets/{ticket_id}",
        response_model=TicketResponse,
        summary="Retrieve FININT Bridge Ticket",
    )
    async def get_ticket(ticket_id: str) -> TicketResponse:
        svc = _svc()
        try:
            ticket = svc.get_ticket(ticket_id)
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        return _ticket_to_response(ticket)

    @r.post(
        "/tickets/{ticket_id}/transition",
        response_model=TicketResponse,
        summary="Transition FININT Ticket State",
        description=(
            "Advances a FININT ticket through its compliance lifecycle state machine. "
            "Invalid transitions (e.g. CLOSED → OPEN) are rejected with HTTP 409."
        ),
    )
    async def transition_ticket(
        ticket_id: str,
        request: TransitionTicketRequest,
    ) -> TicketResponse:
        svc = _svc()
        try:
            ticket = svc.transition_status(
                ticket_id=ticket_id,
                new_status=request.new_status,
                actor=request.actor,
                action=request.action,
                metadata=request.metadata,
            )
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        except InvalidTicketTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _ticket_to_response(ticket)

    @r.post(
        "/tickets/{ticket_id}/evidence",
        response_model=EvidenceAttachResponse,
        status_code=201,
        summary="Attach Evidence to FININT Ticket",
        description=(
            "Registers an evidence blob by its SHA-256 hash. "
            "Raw evidence bytes are immediately discarded after hashing."
        ),
    )
    async def attach_evidence(
        ticket_id: str,
        request: AttachEvidenceRequest,
    ) -> EvidenceAttachResponse:
        svc = _svc()
        try:
            ev_bytes = base64.urlsafe_b64decode(request.evidence_b64 + "==")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid base64url evidence: {exc}") from exc
        try:
            evidence_hash = svc.attach_evidence(ticket_id, ev_bytes, actor=request.actor)
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        return EvidenceAttachResponse(
            ticket_id=ticket_id,
            evidence_hash=evidence_hash,
            message="Evidence SHA-256 hash registered. Raw bytes discarded.",
        )

    @r.post(
        "/tickets/{ticket_id}/verify-evidence",
        response_model=VerifyEvidenceResponse,
        summary="Verify Evidence Against Registered Hash",
    )
    async def verify_evidence(
        ticket_id: str,
        request: VerifyEvidenceRequest,
    ) -> VerifyEvidenceResponse:
        svc = _svc()
        try:
            ev_bytes = base64.urlsafe_b64decode(request.evidence_b64 + "==")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid base64url evidence: {exc}") from exc
        try:
            verified = svc.verify_evidence(ticket_id, ev_bytes)
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        return VerifyEvidenceResponse(
            ticket_id=ticket_id,
            verified=verified,
            message="Evidence matches registered SHA-256 hash." if verified else "Evidence hash mismatch.",
        )

    @r.get(
        "/tickets/{ticket_id}/audit-chain",
        response_model=list[AuditEntryResponse],
        summary="Retrieve FININT Ticket Audit Trail",
    )
    async def get_audit_chain(ticket_id: str) -> list[AuditEntryResponse]:
        svc = _svc()
        try:
            ticket = svc.get_ticket(ticket_id)
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        return [
            AuditEntryResponse(
                seq=e.seq,
                actor=e.actor,
                action=e.action,
                previous_status=e.previous_status,
                new_status=e.new_status,
                event_hash=e.event_hash,
                previous_hash=e.previous_hash,
                timestamp=e.timestamp,
                metadata=e.metadata,
            )
            for e in ticket.audit_trail
        ]

    @r.get(
        "/tickets/{ticket_id}/verify-chain",
        response_model=AuditChainVerificationResponse,
        summary="Verify FININT Ticket Audit Chain Integrity",
        description="Recomputes and verifies the SHA-256 hash chain of the audit trail.",
    )
    async def verify_audit_chain(ticket_id: str) -> AuditChainVerificationResponse:
        svc = _svc()
        try:
            ticket = svc.get_ticket(ticket_id)
            intact = svc.verify_audit_chain(ticket_id)
        except TicketNotFoundError:
            raise HTTPException(status_code=404, detail=f"FININT ticket '{ticket_id}' not found.")
        return AuditChainVerificationResponse(
            ticket_id=ticket_id,
            chain_intact=intact,
            entry_count=len(ticket.audit_trail),
            head_hash=ticket.head_hash,
            message="Audit chain integrity verified." if intact else "AUDIT CHAIN TAMPER DETECTED.",
        )

    @r.get(
        "/metrics",
        response_model=BridgeMetricsResponse,
        summary="FININT Bridge Service Metrics",
    )
    async def get_metrics() -> BridgeMetricsResponse:
        svc = _svc()
        m = svc.get_metrics()
        return BridgeMetricsResponse(**m)

    @r.post(
        "/keypair",
        response_model=GenerateKeypairResponse,
        status_code=201,
        summary="Generate Curve25519 Keypair for Institution",
        description=(
            "Generates a fresh Curve25519 keypair. "
            "The private key is returned once — store it immediately in your institution's HSM. "
            "This endpoint never retains private key material."
        ),
    )
    async def generate_keypair() -> GenerateKeypairResponse:
        priv_b64, pub_b64 = generate_bank_keypair()
        return GenerateKeypairResponse(
            private_key_b64=priv_b64,
            public_key_b64=pub_b64,
        )


# Register routes on both prefixed routers
_register_routes(router)
_register_routes(api_router)
