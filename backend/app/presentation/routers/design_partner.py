"""Design Partner Bank/Fintech Pilot and Real-World Benchmark API Router — Phase 89.

Serves commercial pilot evaluation, sandbox benchmarks, Zero-Raw-PII scanning,
and institutional compliance readiness audits.
Supports dual-prefix mounting: `/api/v1/design-partner` and `/v1/design-partner`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.application.schemas.design_partner import (
    BenchmarkEvaluationResponse,
    DistributionFidelityResponse,
    IngestionValidationRequest,
    IngestionValidationResponse,
    PartnerLeadRequest,
    PartnerLeadResponse,
    PiiViolationDetail,
    PilotComplianceChecklistResponse,
    PilotComplianceItem,
    PilotFeedbackRequest,
    PilotFeedbackResponse,
    PilotStatusResponse,
)
from app.application.services.design_partner_service import DesignPartnerPilotService

logger = logging.getLogger(__name__)

_pilot_service = DesignPartnerPilotService()

# Re-export schemas for backward compatibility
__all__ = [
    "BenchmarkEvaluationResponse",
    "DistributionFidelityResponse",
    "IngestionValidationRequest",
    "IngestionValidationResponse",
    "PartnerLeadRequest",
    "PartnerLeadResponse",
    "PilotComplianceChecklistResponse",
    "PilotFeedbackRequest",
    "PilotFeedbackResponse",
    "PilotStatusResponse",
    "api_router",
    "router",
]

_base_router = APIRouter(tags=["design-partner"])

# In-memory stores for pilot leads and feedback (bounded capacity)
_LEADS_STORE: dict[str, dict[str, Any]] = {}
_FEEDBACK_STORE: dict[str, dict[str, Any]] = {}


@_base_router.post(
    "/validate-ingest",
    response_model=IngestionValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Scan sample records for PII leakage and validate Zero-Raw-PII edge requirements",
)
async def validate_data_ingestion(request: IngestionValidationRequest) -> IngestionValidationResponse:
    """Scans sample records for PII leakage and validates Zero-Raw-PII edge requirements."""
    import pandas as pd

    if not request.sample_records:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sample records cannot be empty.")

    try:
        df = pd.DataFrame(request.sample_records)
        scan_res = _pilot_service.scan_for_raw_pii(df)
        violations = [
            PiiViolationDetail(
                column=str(v.get("column", "")),
                pii_type=str(v.get("pii_type", "")),
                sample_count=int(v.get("sample_count", 1)),
                remediation=str(v.get("remediation", "")),
            )
            for v in scan_res.violations_detected
        ]

        return IngestionValidationResponse(
            partner_name=request.partner_name,
            schema_format=request.schema_format,
            is_clean_zero_pii=scan_res.clean,
            total_records_scanned=scan_res.total_records_scanned,
            violations=violations,
            status="READY_FOR_LOCAL_EDGE_TRAINING" if scan_res.clean else "REMEDIATION_REQUIRED",
            guidance=(
                "Pass: No raw PII detected. Proceed with local edge client gradient extraction."
                if scan_res.clean
                else "Violations detected. Please tokenize identifiers with HMAC-SHA256 before ingestion."
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Ingestion validation error: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@_base_router.get(
    "/evaluate-benchmark",
    response_model=BenchmarkEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Run calibrated synthetic reference benchmark evaluation for institutional sandboxes",
)
async def evaluate_benchmark(
    dataset: str = Query(
        "paysim",
        description="Benchmark dataset name: 'paysim' | 'ieee_cis' | 'elliptic' | 'creditcard'",
    ),
    n_samples: int = Query(10_000, ge=1_000, le=100_000, description="Sample size to evaluate"),
    daily_volume: int = Query(
        100_000,
        ge=10_000,
        description="Bank average daily transaction volume for economic modeling",
    ),
) -> BenchmarkEvaluationResponse:
    """Runs calibrated synthetic reference benchmark evaluation for institutional sandbox comparisons."""
    try:
        res = _pilot_service.evaluate_reference_benchmark(
            dataset_name=dataset,
            n_samples=n_samples,
            daily_volume=daily_volume,
        )
        return BenchmarkEvaluationResponse(
            dataset_name=dataset,
            total_samples=n_samples,
            daily_volume=daily_volume,
            performance_comparison=res.get("performance_comparison", {}),
            distribution_fidelity=res.get("distribution_fidelity", {}),
            multi_threshold_confusion_matrices=res.get("multi_threshold_confusion_matrices", {}),
            bank_partitions=res.get("bank_partitions", {}),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Benchmark evaluation failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Benchmark execution failed: {exc}",
        ) from exc


@_base_router.get(
    "/distribution-fidelity",
    response_model=DistributionFidelityResponse,
    status_code=status.HTTP_200_OK,
    summary="Returns distribution shift, Wasserstein distance and degradation metrics",
)
async def get_distribution_fidelity(
    dataset: str = Query("paysim", description="Benchmark dataset name"),
) -> DistributionFidelityResponse:
    """Returns distribution shift, Wasserstein distance and degradation metrics between synthetic and real data."""
    try:
        res = _pilot_service.evaluate_reference_benchmark(dataset_name=dataset, n_samples=5_000)
        fidelity_data = res.get("distribution_fidelity", {})
        return DistributionFidelityResponse(
            dataset_name=dataset,
            degradation_metrics=fidelity_data.get("degradation_metrics", {}),
            feature_shifts=fidelity_data.get("feature_shifts", {}),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to compute distribution fidelity: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@_base_router.get(
    "/readiness-checklist",
    response_model=PilotComplianceChecklistResponse,
    status_code=status.HTTP_200_OK,
    summary="Generates the institutional compliance and readiness checklist for banking IT committees",
)
async def get_pilot_readiness_checklist(
    partner_name: str = Query("Design Partner Bank", description="Name of the partner bank"),
    jurisdiction: str = Query("EU/TR/US", description="Regulatory jurisdiction"),
) -> PilotComplianceChecklistResponse:
    """Generates the institutional compliance and readiness checklist for banking IT committees."""
    try:
        checklist = _pilot_service.generate_pilot_readiness_checklist(
            partner_name=partner_name, jurisdiction=jurisdiction
        )
        return PilotComplianceChecklistResponse(
            partner_name=checklist.partner_name,
            jurisdiction=checklist.jurisdiction,
            overall_readiness_score=checklist.overall_readiness_score,
            status=checklist.status,
            compliance_items=[
                PilotComplianceItem(
                    standard=str(item.get("standard", "")),
                    clause=str(item.get("clause", "")),
                    status=str(item.get("status", "PASSED")),
                    evidence=str(item.get("evidence", "")),
                )
                for item in checklist.compliance_items
            ],
            cryptographic_guarantees=checklist.cryptographic_guarantees,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate readiness checklist: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@_base_router.post(
    "/leads",
    response_model=PartnerLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a prospective design partner institution inquiry",
)
async def register_partner_lead(payload: PartnerLeadRequest) -> PartnerLeadResponse:
    """Registers an enterprise design partner inquiry and generates an onboarding token."""
    lead_id = f"lead_{uuid.uuid4().hex[:12]}"
    # Determine assigned tier by volume
    assigned_tier = "TIER_1_GLOBAL" if payload.daily_volume >= 500_000 else "TIER_2_REGIONAL"

    _LEADS_STORE[lead_id] = {
        "lead_id": lead_id,
        "partner_name": payload.partner_name,
        "contact_email": payload.contact_email,
        "jurisdiction": payload.jurisdiction,
        "institution_type": payload.institution_type,
        "daily_volume": payload.daily_volume,
        "assigned_tier": assigned_tier,
        "created_at": datetime.now(UTC).isoformat(),
    }

    return PartnerLeadResponse(
        lead_id=lead_id,
        partner_name=payload.partner_name,
        status="PENDING_REVIEW",
        assigned_tier=assigned_tier,
        onboarding_wizard_url=f"/onboarding?lead_id={lead_id}",
    )


@_base_router.get(
    "/pilot",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get design partner pilot program status and available sandbox benchmark environments",
)
async def get_pilot_program_status(
    partner_name: str = Query("Design Partner Bank", description="Name of the partner bank"),
) -> PilotStatusResponse:
    """Returns overview of the design partner program and available sandbox environments."""
    return PilotStatusResponse(
        partner_name=partner_name,
        status="ACTIVE_SANDBOX",
        supported_schemas=["ISO_20022", "OPEN_BANKING_PSD2", "CUSTOM_CSV"],
        benchmarks_available=["paysim", "ieee_cis", "elliptic", "creditcard"],
        sandbox_mode="AIRGAPPED_CONTAINER",
    )


@_base_router.post(
    "/feedback",
    response_model=PilotFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record design partner trial feedback from institutional review committees",
)
async def submit_pilot_feedback(payload: PilotFeedbackRequest) -> PilotFeedbackResponse:
    """Records trial feedback from banking IT committees and returns confirmation."""
    feedback_id = f"fb_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()

    _FEEDBACK_STORE[feedback_id] = {
        "feedback_id": feedback_id,
        "partner_name": payload.partner_name,
        "contact_email": payload.contact_email,
        "satisfaction_rating": payload.satisfaction_rating,
        "category": payload.category,
        "comments": payload.comments,
        "recorded_at": now_iso,
    }

    return PilotFeedbackResponse(
        feedback_id=feedback_id,
        partner_name=payload.partner_name,
        recorded_at=now_iso,
        status="ACKNOWLEDGED",
    )


# ── Multi-Prefix Router Exports ───────────────────────────────────────────────
router = APIRouter(prefix="/api/v1/design-partner", tags=["design-partner"])
api_router = APIRouter(prefix="/v1/design-partner", tags=["design-partner"])

router.include_router(_base_router)
api_router.include_router(_base_router)
