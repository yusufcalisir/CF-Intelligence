"""European FIU & UNODC goAML / AMLA Regulatory Reporting API Router (Phase 109).

Endpoints:
- POST /api/v1/regulatory/reports                 — Create report draft (STR, SAR, TTR, AIF)
- GET  /api/v1/regulatory/reports                 — List reports (filtered by type/status)
- GET  /api/v1/regulatory/reports/{report_id}     — Get comprehensive report details
- POST /api/v1/regulatory/reports/{report_id}/submit   — Submit report for supervisory sign-off
- POST /api/v1/regulatory/reports/{report_id}/signoff  — Dual-control supervisory sign-off
- GET  /api/v1/regulatory/reports/{report_id}/xml      — Export UNODC goAML 4.0 XML
- GET  /api/v1/regulatory/reports/{report_id}/amla-json — Export EU AMLA Single Rulebook JSON
- POST /api/v1/regulatory/reports/{report_id}/validate  — Validate against official goAML schema
- GET  /api/v1/regulatory/reports/{report_id}/envelope  — Get cryptographic digital envelope
- POST /api/v1/regulatory/reports/{report_id}/transmit  — Transmit approved filing to European FIU
- GET  /api/v1/regulatory/reports/{report_id}/audit-trail — Audit trail & SHA-256 chain verification
- GET  /api/v1/regulatory/metrics                 — Aggregate European filing metrics

Dual-routing supported under /api/v1/regulatory and /v1/regulatory.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.application.schemas.regulatory_schemas import (
    CreateRegulatoryReportRequest,
    DigitalEnvelopeResponse,
    RegulatoryMetricsResponse,
    RegulatoryReportDetailResponse,
    RegulatoryReportSummaryResponse,
    SchemaValidationResponse,
    SupervisorySignoffRequest,
    TransmissionReceiptResponse,
    TransmitReportRequest,
)
from app.application.services.fiu_regulatory_service import (
    DualControlViolationError,
    FIURegulatoryService,
    InvalidReportStateError,
    RegulatoryValidationError,
    ReportNotApprovedError,
    ReportNotFoundError,
    get_fiu_regulatory_service,
)
from app.domain.enums import RegulatoryReportStatus, RegulatoryReportType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/regulatory", tags=["european-fiu-goaml"])
api_router = APIRouter(prefix="/v1/regulatory", tags=["european-fiu-goaml"])


def _svc() -> FIURegulatoryService:
    return get_fiu_regulatory_service()


def _register_routes(r: APIRouter) -> None:

    @r.post(
        "/reports",
        response_model=RegulatoryReportDetailResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create a new European FIU / UNODC goAML regulatory report draft",
    )
    def create_report(req: CreateRegulatoryReportRequest) -> RegulatoryReportDetailResponse:
        try:
            return _svc().create_report(req)
        except RegulatoryValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Unexpected error creating regulatory report")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    @r.get(
        "/reports",
        response_model=list[RegulatoryReportSummaryResponse],
        summary="List regulatory reports with optional type/status filters",
    )
    def list_reports(
        report_type: RegulatoryReportType | None = Query(default=None, description="Filter by report type"),
        report_status: RegulatoryReportStatus | None = Query(default=None, alias="status", description="Filter by status"),
    ) -> list[RegulatoryReportSummaryResponse]:
        return _svc().list_reports(report_type=report_type, status=report_status)

    @r.get(
        "/reports/{report_id}",
        response_model=RegulatoryReportDetailResponse,
        summary="Get comprehensive report details including audit trail",
    )
    def get_report(report_id: str) -> RegulatoryReportDetailResponse:
        try:
            return _svc().get_report(report_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.post(
        "/reports/{report_id}/submit",
        response_model=RegulatoryReportDetailResponse,
        summary="Submit DRAFT report for dual-control supervisory sign-off",
    )
    def submit_report(
        report_id: str,
        user_id: str = Query(..., min_length=2, description="Officer ID requesting submission"),
    ) -> RegulatoryReportDetailResponse:
        try:
            return _svc().submit_for_approval(report_id, user_id=user_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except InvalidReportStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @r.post(
        "/reports/{report_id}/signoff",
        response_model=RegulatoryReportDetailResponse,
        summary="Dual-control supervisory sign-off (Approve/Reject). Self-approval is strictly forbidden.",
    )
    def supervisory_signoff(
        report_id: str,
        req: SupervisorySignoffRequest,
    ) -> RegulatoryReportDetailResponse:
        try:
            return _svc().supervisory_signoff(report_id, req)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except DualControlViolationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except InvalidReportStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @r.get(
        "/reports/{report_id}/xml",
        summary="Export UNODC goAML 4.0 XML format",
    )
    def export_goaml_xml(report_id: str) -> Response:
        try:
            xml_data = _svc().generate_goaml_4_xml(report_id)
            return Response(content=xml_data, media_type="application/xml")
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.get(
        "/reports/{report_id}/amla-json",
        response_model=dict[str, Any],
        summary="Export EU AMLA Single Rulebook interchange JSON format",
    )
    def export_amla_json(report_id: str) -> dict[str, Any]:
        try:
            return _svc().generate_amla_json(report_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.post(
        "/reports/{report_id}/validate",
        response_model=SchemaValidationResponse,
        summary="Validate report XML payload against official UNODC goAML 4.0 schema",
    )
    def validate_report(report_id: str) -> SchemaValidationResponse:
        try:
            xml_data = _svc().generate_goaml_4_xml(report_id)
            return _svc().validate_schema(xml_data)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.get(
        "/reports/{report_id}/envelope",
        response_model=DigitalEnvelopeResponse,
        summary="Get cryptographic digital envelope (canonical SHA-256 + HMAC seal)",
    )
    def get_digital_envelope(report_id: str) -> DigitalEnvelopeResponse:
        try:
            return _svc().get_digital_envelope(report_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.post(
        "/reports/{report_id}/transmit",
        response_model=TransmissionReceiptResponse,
        summary="Transmit approved filing to European FIU / AMLA Hub",
    )
    def transmit_report(
        report_id: str,
        req: TransmitReportRequest,
        actor_id: str = Query(default="compliance_officer_1", description="ID of transmitting operator"),
    ) -> TransmissionReceiptResponse:
        try:
            return _svc().transmit_to_fiu(report_id, req, actor_id=actor_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ReportNotApprovedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @r.get(
        "/reports/{report_id}/audit-trail",
        summary="Retrieve complete SHA-256 hash-chained audit trail and integrity verification status",
    )
    def get_audit_trail(report_id: str) -> dict[str, Any]:
        try:
            report = _svc().get_report(report_id)
            chain_valid = _svc().verify_audit_chain(report_id)
            return {
                "report_id": report_id,
                "chain_integrity_valid": chain_valid,
                "entry_count": len(report.audit_trail),
                "audit_trail": [entry.model_dump(mode="json") for entry in report.audit_trail],
            }
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @r.get(
        "/metrics",
        response_model=RegulatoryMetricsResponse,
        summary="Retrieve aggregate European FIU / AMLA filing operational metrics",
    )
    def get_regulatory_metrics() -> RegulatoryMetricsResponse:
        data = _svc().get_metrics()
        return RegulatoryMetricsResponse(
            total_reports=data["total_reports"],
            draft_reports=data["draft_reports"],
            pending_approval_reports=data["pending_approval_reports"],
            approved_reports=data["approved_reports"],
            rejected_reports=data["rejected_reports"],
            transmitted_reports=data["transmitted_reports"],
            reports_by_type=data["reports_by_type"],
            total_suspicious_volume_eur=data["total_suspicious_volume_eur"],
            dual_control_enforcement_rate=data["dual_control_enforcement_rate"],
            schema_compliance_rate=data["schema_compliance_rate"],
        )


_register_routes(router)
_register_routes(api_router)
