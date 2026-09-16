"""Compliance & Security Audit API Router.

Exposes SOC 2 Type II evidence, Federal Reserve SR 11-7 model risk management audits,
EEOC 80% Rule algorithmic fairness evaluations, and Canary quality gates.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.model_governance_service import ModelGovernanceService
from app.application.services.security_compliance import SecurityComplianceEngine

logger = logging.getLogger(__name__)

# Legacy and Canonical Routers for dual-prefix support (/v1/compliance and /api/v1/compliance)
router = APIRouter(prefix="/v1/compliance", tags=["Security & Compliance"])
api_router = APIRouter(prefix="/api/v1/compliance", tags=["Security & Compliance"])

compliance_engine = SecurityComplianceEngine()
governance_service = ModelGovernanceService()


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class FairnessAuditRequest(BaseModel):
    """Request model for algorithmic fairness and non-discrimination audits."""

    y_pred_probs: list[float] = Field(
        ..., min_length=1, description="List of predicted model scores / probabilities"
    )
    sensitive_attributes: list[int] = Field(
        ...,
        min_length=1,
        description="Binary demographic attribute flags (1=protected group, 0=reference group)",
    )
    y_true: list[int] | None = Field(
        None, description="Optional ground truth labels for TPR/FPR fairness parity analysis"
    )
    threshold: float = Field(0.50, ge=0.0, le=1.0, description="Classification decision threshold")


class CanaryGateRequest(BaseModel):
    """Request model for evaluating candidate model promotion via CanaryQualityGate."""

    candidate_metrics: dict[str, Any] = Field(
        ...,
        description="Candidate model performance metrics (auc_roc, disparate_impact_ratio, p99_latency_ms, fpr)",
    )
    champion_metrics: dict[str, Any] | None = Field(
        None, description="Optional champion baseline metrics for comparative delta validation"
    )


class SARValidateRequest(BaseModel):
    """Request model for validating arbitrary XML payload against FinCEN SAR 2.0 schema."""

    xml_content: str = Field(..., min_length=10, description="Raw FinCEN SAR XML content")


class SARGenerateRequest(BaseModel):
    """Request model for compiling and filing an official FinCEN SAR report."""

    case_id: str = Field(..., description="Confirmed fraud case ID to compile SAR filing for")
    institution_name: str | None = Field(None, description="Optional reporting financial institution name")
    tin_type: str | None = Field(None, description="TIN Type (EIN/SSN)")
    narrative_override: str | None = Field(None, description="Optional custom narrative summary")


# ---------------------------------------------------------------------------
# Route Handlers
# ---------------------------------------------------------------------------


def get_soc2_evidence():
    return compliance_engine.generate_soc2_evidence_report()


def post_soc2_evidence():
    return compliance_engine.generate_soc2_evidence_report()


def get_sr11_7_audit():
    return governance_service.audit_conceptual_soundness()


def get_sr11_7_schedule(year: int = 2026):
    return governance_service.get_validation_schedule(reference_year=year)


def evaluate_fairness(payload: FairnessAuditRequest):
    try:
        return governance_service.audit_fairness(
            y_pred_probs=payload.y_pred_probs,
            sensitive_attributes=payload.sensitive_attributes,
            y_true=payload.y_true,
            threshold=payload.threshold,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def evaluate_canary_gate(payload: CanaryGateRequest):
    return governance_service.evaluate_canary_quality_gate(
        candidate_metrics=payload.candidate_metrics,
        champion_metrics=payload.champion_metrics,
    )


def list_sar_filings(limit: int = 50):
    from app.application.services.regulatory_reporter import RegulatoryReporterService

    filings = RegulatoryReporterService.list_filings(limit=limit)
    return [f.to_dict() for f in filings]


def validate_sar_xml(payload: SARValidateRequest):
    from app.application.services.regulatory_reporter import (
        RegulatoryReporterService,
        SARValidationError,
    )

    try:
        return RegulatoryReporterService.validate_sar_xml_payload(payload.xml_content)
    except SARValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def generate_sar_filing(payload: SARGenerateRequest):
    from app.application.services.case_service import CaseManagementService
    from app.application.services.regulatory_reporter import (
        RegulatoryReporterService,
        SARValidationError,
    )

    case = CaseManagementService().get_case(payload.case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{payload.case_id}' not found.",
        )

    try:
        from app.application.services.alert_service import AlertIntelligenceService

        alert_service = AlertIntelligenceService()
        alerts = [
            a
            for aid in (case.alert_ids or [])
            if (a := alert_service.get_alert(aid)) is not None
        ]

        record = RegulatoryReporterService.generate_and_store_sar_filing(
            case_id=payload.case_id,
            case_obj=case,
            alerts=alerts,
            institution_name=payload.institution_name or "Consortium AML Joint Investigation Unit",
            tin_type=payload.tin_type or "EIN",
            narrative_override=payload.narrative_override,
        )
        return record.to_dict()
    except SARValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def get_sar_filing_by_id(filing_id: str):
    from app.application.services.regulatory_reporter import RegulatoryReporterService

    record, content = RegulatoryReporterService.get_filing(filing_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SAR filing '{filing_id}' not found.",
        )
    res = record.to_dict()
    res["xml_content"] = content
    return res


# ---------------------------------------------------------------------------
# Bind Handlers with unique operation_ids to avoid OpenAPI collisions
# ---------------------------------------------------------------------------

for prefix_tag, r in [("v1", router), ("api_v1", api_router)]:
    r.add_api_route(
        "/soc2-evidence",
        get_soc2_evidence,
        methods=["GET"],
        summary="SOC 2 Evidence Collection Report",
        operation_id=f"{prefix_tag}_get_soc2_evidence",
    )
    r.add_api_route(
        "/soc2-evidence",
        post_soc2_evidence,
        methods=["POST"],
        summary="SOC 2 Evidence Collection Report (POST)",
        operation_id=f"{prefix_tag}_post_soc2_evidence",
    )
    r.add_api_route(
        "/sr11-7/audit",
        get_sr11_7_audit,
        methods=["GET"],
        summary="SR 11-7 Conceptual Soundness Audit Report",
        operation_id=f"{prefix_tag}_get_sr11_7_audit",
    )
    r.add_api_route(
        "/sr11-7/schedule",
        get_sr11_7_schedule,
        methods=["GET"],
        summary="SR 11-7 4-Quarter Independent Validation Schedule",
        operation_id=f"{prefix_tag}_get_sr11_7_schedule",
    )
    r.add_api_route(
        "/fairness/evaluate",
        evaluate_fairness,
        methods=["POST"],
        summary="Algorithmic Fairness & Disparate Impact Audit",
        operation_id=f"{prefix_tag}_evaluate_fairness",
    )
    r.add_api_route(
        "/sr11-7/canary-gate",
        evaluate_canary_gate,
        methods=["POST"],
        summary="Canary Quality Gate Promotion Evaluation",
        operation_id=f"{prefix_tag}_evaluate_canary_gate",
    )
    r.add_api_route(
        "/sar/filings",
        list_sar_filings,
        methods=["GET"],
        summary="List Regulatory FinCEN SAR Submissions",
        operation_id=f"{prefix_tag}_list_sar_filings",
    )
    r.add_api_route(
        "/sar/validate",
        validate_sar_xml,
        methods=["POST"],
        summary="Validate FinCEN SAR 2.0 XML Schema",
        operation_id=f"{prefix_tag}_validate_sar_xml",
    )
    r.add_api_route(
        "/sar/generate",
        generate_sar_filing,
        methods=["POST"],
        summary="Generate and Store FinCEN SAR 2.0 Filing",
        operation_id=f"{prefix_tag}_generate_sar_filing",
    )
    r.add_api_route(
        "/sar/filings/{filing_id}",
        get_sar_filing_by_id,
        methods=["GET"],
        summary="Retrieve SAR Filing by ID with Hash Verification",
        operation_id=f"{prefix_tag}_get_sar_filing_by_id",
    )

