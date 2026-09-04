"""Design Partner Bank/Fintech Pilot and Real-World Benchmark API Router."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.application.services.design_partner_service import DesignPartnerPilotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/design-partner", tags=["design-partner"])

_pilot_service = DesignPartnerPilotService()


class IngestionValidationRequest(BaseModel):
    partner_name: str = Field(..., description="Name of the design partner bank or fintech")
    schema_format: str = Field(
        "ISO_20022", description="ISO_20022 | OPEN_BANKING_PSD2 | CUSTOM_CSV"
    )
    sample_records: list[dict[str, Any]] = Field(
        ..., description="Sample un-hashed transaction records to scan for PII"
    )


@router.post("/validate-ingest", status_code=status.HTTP_200_OK)
async def validate_data_ingestion(request: IngestionValidationRequest) -> dict[str, Any]:
    """Scans sample records for PII leakage and validates Zero-Raw-PII edge requirements."""
    import pandas as pd

    if not request.sample_records:
        raise HTTPException(status_code=400, detail="Sample records cannot be empty.")

    try:
        df = pd.DataFrame(request.sample_records)
        scan_res = _pilot_service.scan_for_raw_pii(df)
        return {
            "partner_name": request.partner_name,
            "schema_format": request.schema_format,
            "is_clean_zero_pii": scan_res.clean,
            "total_records_scanned": scan_res.total_records_scanned,
            "violations": scan_res.violations_detected,
            "status": "READY_FOR_LOCAL_EDGE_TRAINING" if scan_res.clean else "REMEDIATION_REQUIRED",
            "guidance": (
                "Pass: No raw PII detected. Proceed with local edge client gradient extraction."
                if scan_res.clean
                else "Violations detected. Please tokenize identifiers with HMAC-SHA256 before ingestion."
            ),
        }
    except Exception as exc:
        logger.error("Ingestion validation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/evaluate-benchmark", status_code=status.HTTP_200_OK)
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
) -> dict[str, Any]:
    """Runs calibrated synthetic reference benchmark evaluation for institutional sandbox comparisons."""
    try:
        res = _pilot_service.evaluate_reference_benchmark(
            dataset_name=dataset,
            n_samples=n_samples,
            daily_volume=daily_volume,
        )
        return res
    except Exception as exc:
        logger.error("Benchmark evaluation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Benchmark execution failed: {exc}") from exc


@router.get("/distribution-fidelity", status_code=status.HTTP_200_OK)
async def get_distribution_fidelity(
    dataset: str = Query("paysim", description="Benchmark dataset name"),
) -> dict[str, Any]:
    """Returns distribution shift, Wasserstein distance and degradation metrics between synthetic and real data."""
    try:
        res = _pilot_service.evaluate_reference_benchmark(dataset_name=dataset, n_samples=5_000)
        return res.get("distribution_fidelity", {})
    except Exception as exc:
        logger.error("Failed to compute distribution fidelity: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/readiness-checklist", status_code=status.HTTP_200_OK)
async def get_pilot_readiness_checklist(
    partner_name: str = Query("Design Partner Bank", description="Name of the partner bank"),
    jurisdiction: str = Query("EU/TR/US", description="Regulatory jurisdiction"),
) -> dict[str, Any]:
    """Generates the institutional compliance and readiness checklist for banking IT committees."""
    try:
        checklist = _pilot_service.generate_pilot_readiness_checklist(
            partner_name=partner_name, jurisdiction=jurisdiction
        )
        from dataclasses import asdict

        return asdict(checklist)
    except Exception as exc:
        logger.error("Failed to generate readiness checklist: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
