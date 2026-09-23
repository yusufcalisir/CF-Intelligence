"""EU AI Act & SR 11-7 Regulatory Model Validation Dossier API Router.

Provides REST endpoints for querying, validating, exporting, and signing off on
automated regulatory AI model validation dossiers:
- GET  /api/v1/regulatory/dossier/summary    — Executive compliance summary & metrics
- GET  /api/v1/regulatory/dossier/eu-ai-act  — EU AI Act Articles 9–15 compliance matrix
- GET  /api/v1/regulatory/dossier/sr11-7     — Federal Reserve SR 11-7 3-Pillars audit report
- GET  /api/v1/regulatory/dossier/benchmarks — Baseline model comparison table
- GET  /api/v1/regulatory/dossier/signoffs   — Supervisory sign-off audit register
- POST /api/v1/regulatory/dossier/signoff    — Cryptographic dual-control supervisory sign-off
- GET  /api/v1/regulatory/dossier/export     — Export dossier in Markdown or JSON
- GET  /api/v1/regulatory/dossier/health     — Router health probe

Dual-routing supported under /api/v1/regulatory/dossier and /regulatory/dossier.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.application.services.regulatory_dossier_generator import (
    RegulatoryDossierGenerator,
    get_regulatory_dossier_generator,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regulatory/dossier", tags=["regulatory-dossier"])
api_router = APIRouter(prefix="/api/v1/regulatory/dossier", tags=["regulatory-dossier"])


# ---------------------------------------------------------------------------
# Pydantic v2 Request / Response Schemas
# ---------------------------------------------------------------------------


class SupervisorySignoffRequest(BaseModel):
    """Request payload for recording a supervisory sign-off."""

    officer_name: str = Field(..., min_length=2, max_length=120, description="Full name of the authorized officer")
    role: str = Field(..., min_length=2, max_length=80, description="Role title (e.g. CHIEF_RISK_OFFICER)")
    notes: str = Field(default="", max_length=1000, description="Audit notes or supervisory findings")


class SupervisorySignoffResponse(BaseModel):
    """Response payload for a recorded supervisory sign-off."""

    signoff_id: str
    officer_name: str
    role: str
    timestamp: str
    sha256_attestation_hash: str
    notes: str


class ModelBenchmarkResponse(BaseModel):
    """Benchmark performance comparison schema."""

    model_name: str
    architecture: str
    pr_auc: float
    roc_auc: float
    f1_score: float
    p99_latency_ms: float
    byzantine_tolerance: str
    differential_privacy_eps: float | None = None
    zero_raw_pii_enforced: bool
    disparate_impact_ratio: float


class DossierSummaryResponse(BaseModel):
    """High-level executive summary response schema."""

    dossier_id: str
    generated_at: str
    model_id: str
    model_version: str
    system_classification: str
    governing_standards: list[str]
    overall_compliance_score: float
    eu_ai_act_status: str
    sr11_7_status: str
    primary_model_metrics: dict[str, Any]
    drift_monitoring_limits: dict[str, Any]
    supervisory_signoffs_count: int
    sha256_dossier_seal: str


class EUAIActMatrixResponse(BaseModel):
    """EU AI Act Articles 9-15 compliance matrix schema."""

    framework: str
    classification: str
    overall_status: str
    compliance_rate_pct: float
    articles: list[dict[str, Any]]


class SR117MatrixResponse(BaseModel):
    """Federal Reserve SR 11-7 Model Risk Management matrix schema."""

    framework: str
    scope: str
    overall_status: str
    pillars: list[dict[str, Any]]


class DossierHealthResponse(BaseModel):
    """Health probe response schema."""

    status: str
    service: str
    active_model_id: str
    total_signoffs: int


# ---------------------------------------------------------------------------
# Router Route Registration
# ---------------------------------------------------------------------------


def _svc() -> RegulatoryDossierGenerator:
    return get_regulatory_dossier_generator()


def _register_routes(r: APIRouter) -> None:

    @r.get(
        "/summary",
        response_model=DossierSummaryResponse,
        summary="Get executive regulatory dossier summary and key compliance metrics",
    )
    def get_summary(
        model_id: str = Query(default="fedgnn-champion-v4", description="Model identifier for the dossier"),
    ) -> DossierSummaryResponse:
        return DossierSummaryResponse(**_svc().get_dossier_summary(model_id=model_id))

    @r.get(
        "/eu-ai-act",
        response_model=EUAIActMatrixResponse,
        summary="Get EU AI Act (Regulation (EU) 2024/1689) High-Risk AI compliance checklist",
    )
    def get_eu_ai_act() -> EUAIActMatrixResponse:
        return EUAIActMatrixResponse(**_svc().get_eu_ai_act_matrix())

    @r.get(
        "/sr11-7",
        response_model=SR117MatrixResponse,
        summary="Get Federal Reserve SR 11-7 3-Pillars Model Risk Management matrix",
    )
    def get_sr11_7() -> SR117MatrixResponse:
        return SR117MatrixResponse(**_svc().get_sr11_7_matrix())

    @r.get(
        "/benchmarks",
        response_model=list[ModelBenchmarkResponse],
        summary="Get benchmark comparison between FedGNN and classical machine learning baselines",
    )
    def get_benchmarks() -> list[ModelBenchmarkResponse]:
        return [ModelBenchmarkResponse(**b) for b in _svc().get_benchmark_comparison()]

    @r.get(
        "/signoffs",
        response_model=list[SupervisorySignoffResponse],
        summary="List all recorded cryptographic supervisory sign-offs",
    )
    def list_signoffs() -> list[SupervisorySignoffResponse]:
        return [SupervisorySignoffResponse(**s) for s in _svc().list_supervisory_signoffs()]

    @r.post(
        "/signoff",
        response_model=SupervisorySignoffResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Add a dual-control cryptographic supervisory sign-off to the dossier",
    )
    def add_signoff(payload: SupervisorySignoffRequest) -> SupervisorySignoffResponse:
        try:
            record = _svc().add_supervisory_signoff(
                officer_name=payload.officer_name,
                role=payload.role,
                notes=payload.notes,
            )
            return SupervisorySignoffResponse(**record)
        except Exception as exc:
            logger.exception("Failed to record supervisory sign-off")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to record sign-off: {exc}",
            ) from exc

    @r.get(
        "/export",
        summary="Export regulator-ready technical audit dossier in Markdown or structured JSON",
    )
    def export_dossier(
        model_id: str = Query(default="fedgnn-champion-v4", description="Model identifier"),
        export_format: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$", description="Export format: markdown or json"),
    ) -> Any:
        if export_format == "json":
            return _svc().export_dossier_json(model_id=model_id)

        md_content = _svc().export_dossier_markdown(model_id=model_id)
        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f"attachment; filename=regulatory_dossier_{model_id}.md",
            },
        )

    @r.get(
        "/health",
        response_model=DossierHealthResponse,
        summary="Health status check for Regulatory Dossier service",
    )
    def health_check() -> DossierHealthResponse:
        return DossierHealthResponse(
            status="healthy",
            service="RegulatoryDossierGenerator",
            active_model_id="fedgnn-champion-v4",
            total_signoffs=len(_svc().list_supervisory_signoffs()),
        )


_register_routes(router)
_register_routes(api_router)
