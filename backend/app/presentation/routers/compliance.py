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

