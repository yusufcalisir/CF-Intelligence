"""Feedback and retraining ground-truth endpoints.

Exposes REST APIs for ingesting analyst determinations, monitoring local tenant feedback
buffers, sampling prioritized retraining batches, and computing Differential-Privacy-protected
gradient updates for continuous federated learning.
Supports dual routing: /api/v1/feedback and /v1/feedback.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.application.schemas.feedback import (
    AnalystFeedbackIngestRequest,
    AnalystFeedbackIngestResponse,
    ClearFeedbackBufferResponse,
    DPGradientRequest,
    DPGradientResponse,
    FeedbackStatsResponse,
    RetrainingBatchRequest,
    RetrainingBatchResponse,
)
from app.application.services.label_feedback_pipeline import (
    LocalLabelFeedbackPipeline,
)
from app.config import get_settings
from app.dependencies import TenantDep
from app.domain.label_privacy_guard import LabelPrivacyViolationError
from app.infrastructure.security.principal_auth import (
    AuthenticatedPrincipal,
    resolve_authenticated_principal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])
api_router = APIRouter(prefix="/v1/feedback", tags=["feedback"])

_pipeline = LocalLabelFeedbackPipeline()

FEEDBACK_INGEST_ROLES: frozenset[str] = frozenset({
    "analyst",
    "senior_analyst",
    "lead_analyst",
    "investigator",
    "bank_admin",
    "admin",
    "operator",
    "super_admin",
})

FEEDBACK_READ_ROLES: frozenset[str] = frozenset({
    "analyst",
    "senior_analyst",
    "lead_analyst",
    "investigator",
    "bank_admin",
    "admin",
    "operator",
    "compliance_auditor",
    "cross_bank_investigator",
    "auditor",
    "super_admin",
})

FEEDBACK_SAMPLE_ROLES: frozenset[str] = frozenset({
    "analyst",
    "senior_analyst",
    "lead_analyst",
    "ml_lead",
    "ml_engineer",
    "bank_admin",
    "admin",
    "operator",
    "super_admin",
})

FEEDBACK_CONSUME_ROLES: frozenset[str] = frozenset({
    "admin",
    "bank_admin",
    "operator",
    "lead_analyst",
    "super_admin",
})

FEEDBACK_GRADIENT_ROLES: frozenset[str] = frozenset({
    "admin",
    "bank_admin",
    "operator",
    "lead_analyst",
    "ml_lead",
    "ml_engineer",
    "super_admin",
})

FEEDBACK_CLEAR_ROLES: frozenset[str] = frozenset({
    "admin",
    "bank_admin",
    "operator",
    "lead_analyst",
    "super_admin",
})

# Backward-compatibility aliases
FEEDBACK_OPERATOR_ROLES: frozenset[str] = FEEDBACK_GRADIENT_ROLES
FEEDBACK_DESTRUCTIVE_ROLES: frozenset[str] = FEEDBACK_CLEAR_ROLES


def _authorize_feedback_request(
    request: Request,
    target_tenant_id: str | None,
    caller_tenant: str | None = None,
    required_roles: frozenset[str] | None = None,
    action_name: str = "feedback",
) -> tuple[AuthenticatedPrincipal, str]:
    """Verify authentication, institutional tenant binding, and role permissions for feedback APIs."""
    settings = get_settings()
    is_prod = str(getattr(settings, "app_env", "")).lower() in ("production", "prod", "staging")

    principal, auth_err, auth_status = resolve_authenticated_principal(request)
    if auth_err:
        raise HTTPException(status_code=auth_status, detail=auth_err)

    if is_prod and not principal.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for production feedback endpoints.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Resolve effective caller tenant
    effective_caller = caller_tenant or principal.tenant_id
    if not effective_caller and principal.is_authenticated and not principal.is_privileged:
        # Verified principal with no tenant bound
        pass

    target_clean = (target_tenant_id or "").strip()
    if not target_clean:
        if effective_caller:
            target_clean = effective_caller
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant identifier is required for feedback operations.",
            )

    try:
        validated_tenant = LocalLabelFeedbackPipeline.validate_tenant_id(target_clean)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Cross-tenant authorization check
    if principal.is_authenticated and not principal.is_privileged:
        if principal.tenant_id:
            norm_principal_tenant = principal.tenant_id.lower().replace("-", "_")
            if norm_principal_tenant != validated_tenant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Cross-tenant access forbidden. Principal '{principal.principal_id}' of "
                        f"tenant '{principal.tenant_id}' cannot access feedback for tenant '{validated_tenant}'."
                    ),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal '{principal.principal_id}' has no institutional tenant binding.",
            )

    # Role-based action check:
    # Cross-bank resource access (is_privileged) and action permission (required_roles) are separate decisions.
    # Privileged principal (e.g. read-only compliance_auditor) must NOT bypass required action roles.
    if required_roles and principal.is_authenticated:
        principal_roles = set(principal.roles or [])
        if not principal_roles.intersection(required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Forbidden: Action '{action_name}' requires one of "
                    f"{sorted(list(required_roles))}, but principal '{principal.principal_id}' "
                    f"has roles {sorted(list(principal_roles))}."
                ),
            )

    return principal, validated_tenant


# ── Ingestion & Submission ──────────────────────────────────────────────────

async def _ingest_handler(
    payload: AnalystFeedbackIngestRequest,
    request: Request,
    caller_tenant: str | None = None,
) -> AnalystFeedbackIngestResponse:
    """Core handler ingesting an analyst determination into local tenant feedback buffer."""
    _, authorized_tenant = _authorize_feedback_request(
        request=request,
        target_tenant_id=payload.tenant_id,
        caller_tenant=caller_tenant,
        required_roles=FEEDBACK_INGEST_ROLES,
        action_name="ingest_feedback",
    )
    try:
        item = _pipeline.ingest_analyst_determination(
            tenant_id=authorized_tenant,
            transaction_id_hash=payload.transaction_id_hash,
            determination=payload.determination,
            alert_id=payload.alert_id,
            priority=payload.priority,
            weight=payload.weight,
            feature_vector=payload.feature_vector,
            notes=payload.notes,
            raw_attributes=payload.raw_attributes,
            metadata=payload.metadata,
        )
        return AnalystFeedbackIngestResponse(status="success", item=item.to_dict())
    except LabelPrivacyViolationError as exc:
        logger.warning("Zero-PII violation rejected in feedback ingestion: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to ingest feedback: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest feedback: {exc}",
        )


@router.post(
    "/ingest",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
@api_router.post(
    "/ingest",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_feedback(
    payload: AnalystFeedbackIngestRequest,
    request: Request,
    caller_tenant: TenantDep = None,
) -> AnalystFeedbackIngestResponse:
    """Ingest an analyst determination into the tenant's local label feedback buffer."""
    return await _ingest_handler(payload, request, caller_tenant)


@router.post(
    "/submit",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
@api_router.post(
    "/submit",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    payload: AnalystFeedbackIngestRequest,
    request: Request,
    caller_tenant: TenantDep = None,
) -> AnalystFeedbackIngestResponse:
    """Confirm ground truth fraud/clean labels and push to retraining priority queue (API_REGISTRY.md alias)."""
    return await _ingest_handler(payload, request, caller_tenant)


# ── Statistics & Monitoring ─────────────────────────────────────────────────

def _get_stats_handler(
    request: Request,
    tenant_id: str | None,
    caller_tenant: str | None = None,
) -> FeedbackStatsResponse:
    """Core handler retrieving summary metrics for a tenant's feedback store."""
    _, authorized_tenant = _authorize_feedback_request(
        request=request,
        target_tenant_id=tenant_id,
        caller_tenant=caller_tenant,
        required_roles=FEEDBACK_READ_ROLES,
        action_name="get_feedback_stats",
    )
    stats = _pipeline.get_buffer_stats(tenant_id=authorized_tenant)
    return FeedbackStatsResponse(**stats)


@router.get(
    "/stats",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/stats",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_feedback_stats_query(
    request: Request,
    tenant_id: str | None = Query(default=None, description="Target bank tenant ID"),
    caller_tenant: TenantDep = None,
) -> FeedbackStatsResponse:
    """Return label distribution and ground truth buffer occupancy (API_REGISTRY.md endpoint)."""
    return _get_stats_handler(request, tenant_id, caller_tenant)


@router.get(
    "/stats/{tenant_id}",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/stats/{tenant_id}",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_feedback_stats(
    tenant_id: str,
    request: Request,
    caller_tenant: TenantDep = None,
) -> FeedbackStatsResponse:
    """Retrieve summary metrics and class balance for a tenant's feedback store by path."""
    return _get_stats_handler(request, tenant_id, caller_tenant)


# ── Retraining Batch Sampling ───────────────────────────────────────────────

@router.post(
    "/retraining-batch",
    response_model=RetrainingBatchResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/retraining-batch",
    response_model=RetrainingBatchResponse,
    status_code=status.HTTP_200_OK,
)
async def sample_retraining_batch(
    payload: RetrainingBatchRequest,
    request: Request,
    caller_tenant: TenantDep = None,
) -> RetrainingBatchResponse:
    """Sample a prioritized, stratified batch of human-verified feedback items for local model retraining."""
    required_roles = FEEDBACK_CONSUME_ROLES if payload.mark_consumed else FEEDBACK_SAMPLE_ROLES
    _, authorized_tenant = _authorize_feedback_request(
        request=request,
        target_tenant_id=payload.tenant_id,
        caller_tenant=caller_tenant,
        required_roles=required_roles,
        action_name="consume_retraining_batch" if payload.mark_consumed else "sample_retraining_batch",
    )
    batch_data = _pipeline.get_priority_retraining_batch(
        tenant_id=authorized_tenant,
        batch_size=payload.batch_size,
        mark_consumed=payload.mark_consumed,
        min_priority=payload.min_priority,
        stratified=payload.stratified,
    )
    return RetrainingBatchResponse(**batch_data)


# ── Differential Privacy Gradients ─────────────────────────────────────────

@router.post(
    "/dp-gradient",
    response_model=DPGradientResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/dp-gradient",
    response_model=DPGradientResponse,
    status_code=status.HTTP_200_OK,
)
async def compute_dp_gradient(
    payload: DPGradientRequest,
    request: Request,
    caller_tenant: TenantDep = None,
) -> DPGradientResponse:
    """Compute Differential-Privacy-protected gradient update for tenant's local model."""
    _, authorized_tenant = _authorize_feedback_request(
        request=request,
        target_tenant_id=payload.tenant_id,
        caller_tenant=caller_tenant,
        required_roles=FEEDBACK_GRADIENT_ROLES,
        action_name="compute_dp_gradient",
    )
    try:
        grad_data = _pipeline.compute_dp_gradient_update(
            tenant_id=authorized_tenant,
            epsilon=payload.epsilon,
            delta=payload.delta,
            clip_norm=payload.clip_norm,
        )
        return DPGradientResponse(**grad_data)
    except LabelPrivacyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to compute DP gradient update: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute DP gradient update: {exc}",
        )


# ── Buffer Management ───────────────────────────────────────────────────────

@router.delete(
    "/buffer/{tenant_id}",
    response_model=ClearFeedbackBufferResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.delete(
    "/buffer/{tenant_id}",
    response_model=ClearFeedbackBufferResponse,
    status_code=status.HTTP_200_OK,
)
async def clear_feedback_buffer(
    tenant_id: str,
    request: Request,
    caller_tenant: TenantDep = None,
) -> ClearFeedbackBufferResponse:
    """Clear all feedback items from the tenant's buffer."""
    _, authorized_tenant = _authorize_feedback_request(
        request=request,
        target_tenant_id=tenant_id,
        caller_tenant=caller_tenant,
        required_roles=FEEDBACK_CLEAR_ROLES,
        action_name="clear_feedback_buffer",
    )
    cleared = _pipeline.clear_buffer(tenant_id=authorized_tenant)
    return ClearFeedbackBufferResponse(status="success", tenant_id=authorized_tenant, cleared_count=cleared)

