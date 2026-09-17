"""Compliance & Security Audit API Router.

Exposes SOC 2 Type II evidence, Federal Reserve SR 11-7 model risk management audits,
EEOC 80% Rule algorithmic fairness evaluations, FinCEN SAR 2.0 e-filings, and GDPR Art. 17 erasures.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.application.schemas.compliance import (
    CanaryGateRequest,
    CanaryGateResponse,
    ErasureAuditRecordResponse,
    ErasureChainVerificationResponse,
    FairnessAuditRequest,
    FairnessAuditResponse,
    GDPRErasureRequest,
    RetentionPolicyRequest,
    RetentionPolicyResponse,
    RetentionPurgeRequest,
    SARFilingDetailResponse,
    SARFilingRecordResponse,
    SARGenerateRequest,
    SARValidateRequest,
    SARValidationResponse,
    SOC2EvidenceReportResponse,
    SR117AuditReportResponse,
    SR117ScheduleResponse,
)
from app.application.services.model_governance_service import ModelGovernanceService
from app.application.services.security_compliance import SecurityComplianceEngine

logger = logging.getLogger(__name__)

# Legacy and Canonical Routers for dual-prefix support (/v1/compliance and /api/v1/compliance)
router = APIRouter(prefix="/v1/compliance", tags=["Security & Compliance"])
api_router = APIRouter(prefix="/api/v1/compliance", tags=["Security & Compliance"])

compliance_engine = SecurityComplianceEngine()
governance_service = ModelGovernanceService()


# ---------------------------------------------------------------------------
# Route Handlers
# ---------------------------------------------------------------------------


def get_soc2_evidence() -> SOC2EvidenceReportResponse:
    return SOC2EvidenceReportResponse.model_validate(
        compliance_engine.generate_soc2_evidence_report()
    )


def post_soc2_evidence() -> SOC2EvidenceReportResponse:
    return SOC2EvidenceReportResponse.model_validate(
        compliance_engine.generate_soc2_evidence_report()
    )


def get_sr11_7_audit() -> SR117AuditReportResponse:
    return SR117AuditReportResponse.model_validate(
        governance_service.audit_conceptual_soundness()
    )


def get_sr11_7_schedule(year: int = 2026) -> SR117ScheduleResponse:
    return SR117ScheduleResponse.model_validate(
        governance_service.get_validation_schedule(reference_year=year)
    )


def evaluate_fairness(payload: FairnessAuditRequest) -> FairnessAuditResponse:
    try:
        raw = governance_service.audit_fairness(
            y_pred_probs=payload.y_pred_probs,
            sensitive_attributes=payload.sensitive_attributes,
            y_true=payload.y_true,
            threshold=payload.threshold,
        )
        return FairnessAuditResponse.model_validate(raw)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def evaluate_canary_gate(payload: CanaryGateRequest) -> CanaryGateResponse:
    raw = governance_service.evaluate_canary_quality_gate(
        candidate_metrics=payload.candidate_metrics,
        champion_metrics=payload.champion_metrics,
    )
    return CanaryGateResponse.model_validate(raw)


def list_sar_filings(limit: int = 50) -> list[SARFilingRecordResponse]:
    from app.application.services.regulatory_reporter import RegulatoryReporterService

    filings = RegulatoryReporterService.list_filings(limit=limit)
    return [SARFilingRecordResponse.model_validate(f.to_dict()) for f in filings]


def validate_sar_xml(payload: SARValidateRequest) -> SARValidationResponse:
    from app.application.services.regulatory_reporter import (
        RegulatoryReporterService,
        SARValidationError,
    )

    try:
        raw = RegulatoryReporterService.validate_sar_xml_payload(payload.xml_content)
        return SARValidationResponse.model_validate(raw)
    except SARValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def generate_sar_filing(payload: SARGenerateRequest) -> SARFilingRecordResponse:
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
        return SARFilingRecordResponse.model_validate(record.to_dict())
    except SARValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def get_sar_filing_by_id(filing_id: str) -> SARFilingDetailResponse:
    from app.application.services.regulatory_reporter import RegulatoryReporterService

    record, content = RegulatoryReporterService.get_filing(filing_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SAR filing '{filing_id}' not found.",
        )
    res = record.to_dict()
    res["xml_content"] = content
    return SARFilingDetailResponse.model_validate(res)


def get_retention_policies(tenant_id: str = "bank_alpha") -> list[RetentionPolicyResponse]:
    from app.application.services.retention_engine import AutomatedRetentionEngine

    engine = AutomatedRetentionEngine.get_instance()
    policies = engine.get_tenant_policies(tenant_id=tenant_id)
    return [
        RetentionPolicyResponse(
            tenant_id=tenant_id,
            category=p.category.value,
            ttl_days=p.ttl_days,
            erasure_method=p.erasure_method.value,
        )
        for p in policies
    ]


def configure_retention_policy(payload: RetentionPolicyRequest) -> RetentionPolicyResponse:
    from app.application.services.retention_engine import AutomatedRetentionEngine
    from app.domain.retention_policy import DataCategory, ErasureMethod

    engine = AutomatedRetentionEngine.get_instance()
    try:
        cat = DataCategory(payload.category)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category '{payload.category}'. Allowed: {[c.value for c in DataCategory]}",
        ) from err

    method = ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION
    if payload.erasure_method in ErasureMethod._value2member_map_:
        method = ErasureMethod(payload.erasure_method)

    try:
        policy = engine.configure_tenant_policy(
            tenant_id=payload.tenant_id,
            category=cat,
            ttl_days=payload.ttl_days,
            erasure_method=method,
        )
        return RetentionPolicyResponse(
            tenant_id=payload.tenant_id,
            category=policy.category.value,
            ttl_days=policy.ttl_days,
            erasure_method=policy.erasure_method.value,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


def purge_retention_records(payload: RetentionPurgeRequest) -> list[ErasureAuditRecordResponse]:
    from app.application.services.retention_engine import AutomatedRetentionEngine
    from app.domain.retention_policy import RetentionErasureError

    engine = AutomatedRetentionEngine.get_instance()
    try:
        records = engine.purge_expired_records(tenant_id=payload.tenant_id)
        return [ErasureAuditRecordResponse.model_validate(r.to_dict()) for r in records]
    except RetentionErasureError as err:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err)) from err


def execute_gdpr_erasure(payload: GDPRErasureRequest) -> ErasureAuditRecordResponse:
    from app.application.services.retention_engine import AutomatedRetentionEngine
    from app.domain.retention_policy import DataCategory, RetentionErasureError

    engine = AutomatedRetentionEngine.get_instance()
    cat = DataCategory.CUSTOMER_ENTITIES
    if payload.category and payload.category in DataCategory._value2member_map_:
        cat = DataCategory(payload.category)

    try:
        record = engine.execute_gdpr_right_to_be_forgotten(
            tenant_id=payload.tenant_id,
            entity_id_hash=payload.entity_id_hash,
            category=cat,
        )
        return ErasureAuditRecordResponse.model_validate(record.to_dict())
    except RetentionErasureError as err:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err)) from err


def get_erasure_audit_trail(tenant_id: str = "bank_alpha") -> list[ErasureAuditRecordResponse]:
    from app.application.services.retention_engine import AutomatedRetentionEngine

    engine = AutomatedRetentionEngine.get_instance()
    trail = engine.get_erasure_audit_trail(tenant_id=tenant_id)
    return [ErasureAuditRecordResponse.model_validate(r.to_dict()) for r in trail]


def verify_erasure_audit_trail(tenant_id: str = "bank_alpha") -> ErasureChainVerificationResponse:
    from app.application.services.retention_engine import AutomatedRetentionEngine

    engine = AutomatedRetentionEngine.get_instance()
    raw = engine.verify_erasure_chain_integrity(tenant_id=tenant_id)
    return ErasureChainVerificationResponse.model_validate(raw)


# ---------------------------------------------------------------------------
# Bind Handlers with unique operation_ids to avoid OpenAPI collisions
# ---------------------------------------------------------------------------

for prefix_tag, r in [("v1", router), ("api_v1", api_router)]:
    r.add_api_route(
        "/soc2-evidence",
        get_soc2_evidence,
        methods=["GET"],
        response_model=SOC2EvidenceReportResponse,
        summary="SOC 2 Evidence Collection Report",
        operation_id=f"{prefix_tag}_get_soc2_evidence",
    )
    r.add_api_route(
        "/soc2-evidence",
        post_soc2_evidence,
        methods=["POST"],
        response_model=SOC2EvidenceReportResponse,
        summary="SOC 2 Evidence Collection Report (POST)",
        operation_id=f"{prefix_tag}_post_soc2_evidence",
    )
    r.add_api_route(
        "/sr11-7/audit",
        get_sr11_7_audit,
        methods=["GET"],
        response_model=SR117AuditReportResponse,
        summary="SR 11-7 Conceptual Soundness Audit Report",
        operation_id=f"{prefix_tag}_get_sr11_7_audit",
    )
    r.add_api_route(
        "/sr11-7/schedule",
        get_sr11_7_schedule,
        methods=["GET"],
        response_model=SR117ScheduleResponse,
        summary="SR 11-7 4-Quarter Independent Validation Schedule",
        operation_id=f"{prefix_tag}_get_sr11_7_schedule",
    )
    r.add_api_route(
        "/fairness/evaluate",
        evaluate_fairness,
        methods=["POST"],
        response_model=FairnessAuditResponse,
        summary="Algorithmic Fairness & Disparate Impact Audit",
        operation_id=f"{prefix_tag}_evaluate_fairness",
    )
    r.add_api_route(
        "/sr11-7/canary-gate",
        evaluate_canary_gate,
        methods=["POST"],
        response_model=CanaryGateResponse,
        summary="Canary Quality Gate Promotion Evaluation",
        operation_id=f"{prefix_tag}_evaluate_canary_gate",
    )
    r.add_api_route(
        "/sar/filings",
        list_sar_filings,
        methods=["GET"],
        response_model=list[SARFilingRecordResponse],
        summary="List Regulatory FinCEN SAR Submissions",
        operation_id=f"{prefix_tag}_list_sar_filings",
    )
    r.add_api_route(
        "/sar/validate",
        validate_sar_xml,
        methods=["POST"],
        response_model=SARValidationResponse,
        summary="Validate FinCEN SAR 2.0 XML Schema",
        operation_id=f"{prefix_tag}_validate_sar_xml",
    )
    r.add_api_route(
        "/sar/generate",
        generate_sar_filing,
        methods=["POST"],
        response_model=SARFilingRecordResponse,
        summary="Generate and Store FinCEN SAR 2.0 Filing",
        operation_id=f"{prefix_tag}_generate_sar_filing",
    )
    r.add_api_route(
        "/sar/filings/{filing_id}",
        get_sar_filing_by_id,
        methods=["GET"],
        response_model=SARFilingDetailResponse,
        summary="Retrieve SAR Filing by ID with Hash Verification",
        operation_id=f"{prefix_tag}_get_sar_filing_by_id",
    )
    r.add_api_route(
        "/retention/policies",
        get_retention_policies,
        methods=["GET"],
        response_model=list[RetentionPolicyResponse],
        summary="List Configured Tenant Retention Policies",
        operation_id=f"{prefix_tag}_get_retention_policies",
    )
    r.add_api_route(
        "/retention/policies",
        configure_retention_policy,
        methods=["POST"],
        response_model=RetentionPolicyResponse,
        summary="Configure Tenant Data Retention TTL Policy",
        operation_id=f"{prefix_tag}_configure_retention_policy",
    )
    r.add_api_route(
        "/retention/purge",
        purge_retention_records,
        methods=["POST"],
        response_model=list[ErasureAuditRecordResponse],
        summary="Trigger Automated TTL Retention Purging",
        operation_id=f"{prefix_tag}_purge_retention_records",
    )
    r.add_api_route(
        "/gdpr/erasure",
        execute_gdpr_erasure,
        methods=["POST"],
        response_model=ErasureAuditRecordResponse,
        summary="Execute GDPR Article 17 Right-to-be-Forgotten Cryptographic Erasure",
        operation_id=f"{prefix_tag}_execute_gdpr_erasure",
    )
    r.add_api_route(
        "/retention/audit-trail",
        get_erasure_audit_trail,
        methods=["GET"],
        response_model=list[ErasureAuditRecordResponse],
        summary="Retrieve Cryptographic Erasure Audit Trail",
        operation_id=f"{prefix_tag}_get_erasure_audit_trail",
    )
    r.add_api_route(
        "/retention/audit-trail/verify",
        verify_erasure_audit_trail,
        methods=["GET"],
        response_model=ErasureChainVerificationResponse,
        summary="Verify Cryptographic Hash Chain Integrity of Erasure Ledger",
        operation_id=f"{prefix_tag}_verify_erasure_audit_trail",
    )
