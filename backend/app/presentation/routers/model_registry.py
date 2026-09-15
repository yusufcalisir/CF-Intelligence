"""Model Registry, Rollback, and Zero-Downtime Deployment API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.application.services.zero_downtime_deployer import ZeroDowntimeDeploymentManager
from app.presentation.routers.simulation import _simulation_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])

# Shared registry and deployer instances
registry = ModelRegistry()
_eval_engine = ModelEvaluationEngine(registry)
deployer = ZeroDowntimeDeploymentManager()


# ============================================================================
# Pydantic Request Models
# ============================================================================


class ModelSignOffRequest(BaseModel):
    role: str = Field(..., description="Role of the signer ('compliance' or 'ml_engineer')")
    user: str = Field(..., description="Name/identifier of the user signing off")
    signature: str = Field(..., description="Cryptographic signature string")
    fairness_score: float = Field(1.0, description="Evaluated model fairness score")
    bias_metric: float = Field(0.0, description="Evaluated model bias metric")
    drift_divergence: float = Field(0.0, description="Evaluated dataset drift divergence")


class ModelPromoteRequest(BaseModel):
    target_status: str = Field("champion", description="Target status: 'champion' or 'challenger'")


class UpgradeInitiateRequest(BaseModel):
    target_version: str = Field(..., description="Target version tag (e.g. 'v2.1.0')")
    compatibility_window_hours: int = Field(
        48, ge=1, le=168, description="Dual-version compatibility window in hours"
    )
    initial_connections: int = Field(100, ge=0, description="Initial active connections count")


class DrainConnectionsRequest(BaseModel):
    batch_size: int = Field(50, ge=1, description="Number of connections to drain in this batch")


class RollingUpdateRequest(BaseModel):
    instance_ids: list[str] = Field(..., description="List of cluster instance IDs to update")


class UpgradeAbortRequest(BaseModel):
    reason: str = Field(
        "Upgrade aborted due to health check failure",
        description="Reason for aborting deployment session",
    )


def _session_to_dict(session: Any) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "target_version": session.target_version,
        "stage": session.stage.value,
        "active_connections_count": session.active_connections_count,
        "drained_connections_count": session.drained_connections_count,
        "updated_instances": session.updated_instances,
        "started_at": session.started_at.isoformat(),
        "abort_reason": session.abort_reason,
    }


# ============================================================================
# Zero-Downtime Rolling Deployment Endpoints (Registered FIRST to avoid /{simulation_id} collision)
# ============================================================================


@router.post("/deployment/initiate")
async def initiate_deployment(payload: UpgradeInitiateRequest) -> dict[str, Any]:
    """Initiates a zero-downtime rolling upgrade session."""
    try:
        session = deployer.initiate_upgrade(
            target_version=payload.target_version,
            compatibility_window_hours=payload.compatibility_window_hours,
            initial_connections=payload.initial_connections,
        )
        return _session_to_dict(session)
    except Exception as e:
        logger.error("Failed to initiate deployment upgrade: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate upgrade: {e}",
        )


@router.get("/deployment/active")
async def get_active_deployment() -> dict[str, Any]:
    """Retrieves the currently active zero-downtime deployment session."""
    session = deployer.get_active_session()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active deployment session found.",
        )
    return _session_to_dict(session)


@router.get("/deployment/status")
async def get_deployment_status() -> dict[str, Any]:
    """Retrieves current platform version, upgrade window, and session counts."""
    window = deployer.get_upgrade_window()
    return {
        "current_version": deployer.current_version,
        "upgrade_window": {
            "current_version": window.current_version,
            "target_version": window.target_version,
            "compatibility_window_hours": window.compatibility_window_hours,
        }
        if window
        else None,
        "total_sessions": len(deployer.list_sessions()),
        "has_active_session": deployer.get_active_session() is not None,
    }


@router.post("/deployment/{session_id}/drain")
async def drain_deployment_connections(
    session_id: str, payload: DrainConnectionsRequest
) -> dict[str, Any]:
    """Gracefully drains active client connections without dropping requests."""
    try:
        active_remaining, drained_total = deployer.drain_client_connections(
            session_id=session_id,
            batch_size=payload.batch_size,
        )
        session = deployer.get_session(session_id)
        return {
            "session_id": session_id,
            "stage": session.stage.value if session else "UNKNOWN",
            "active_connections_count": active_remaining,
            "drained_connections_count": drained_total,
        }
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed to drain connections for %s: %s", session_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to drain connections: {e}",
        )


@router.post("/deployment/{session_id}/rolling-update")
async def execute_deployment_rolling_update(
    session_id: str, payload: RollingUpdateRequest
) -> dict[str, Any]:
    """Executes rolling instance updates across cluster instances."""
    try:
        session = deployer.execute_rolling_instance_update(
            session_id=session_id,
            instance_ids=payload.instance_ids,
        )
        return _session_to_dict(session)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed rolling update for %s: %s", session_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed rolling update: {e}",
        )


@router.post("/deployment/{session_id}/finalize")
async def finalize_deployment(session_id: str) -> dict[str, Any]:
    """Finalizes deployment session and promotes target version to current."""
    try:
        session = deployer.finalize_upgrade(session_id)
        return _session_to_dict(session)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed to finalize upgrade for %s: %s", session_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to finalize upgrade: {e}",
        )


@router.post("/deployment/{session_id}/abort")
async def abort_deployment(session_id: str, payload: UpgradeAbortRequest) -> dict[str, Any]:
    """Aborts deployment session on health check failure, restoring connections."""
    try:
        session = deployer.abort_upgrade(session_id, reason=payload.reason)
        return _session_to_dict(session)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed to abort upgrade for %s: %s", session_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to abort upgrade: {e}",
        )


@router.get("/deployment/{session_id}")
async def get_deployment_session(session_id: str) -> dict[str, Any]:
    """Retrieves deployment session by session_id."""
    session = deployer.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment session '{session_id}' not found.",
        )
    return _session_to_dict(session)


# ============================================================================
# Model Registry Version & Evaluation Endpoints
# ============================================================================


@router.get("/{simulation_id}/versions")
async def list_model_versions(simulation_id: str) -> list[dict[str, Any]]:
    """List all model versions tracked in the registry for this simulation."""
    try:
        return registry.list_versions(simulation_id)
    except Exception as e:
        logger.error("Failed to list versions for %s: %s", simulation_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list model versions: {e}",
        )


@router.get("/{simulation_id}/active")
@router.get("/{simulation_id}/champion")
async def get_active_model_version(simulation_id: str) -> dict[str, Any]:
    """Retrieve metadata of the currently active champion model version."""
    entry = registry.get_active_version(simulation_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active champion model found for simulation '{simulation_id}'.",
        )
    return entry


@router.get("/{simulation_id}/versions/{version}")
async def get_model_version(simulation_id: str, version: int) -> dict[str, Any]:
    """Retrieve metadata of a specific model version from the registry."""
    entry = registry.get_version_metadata(simulation_id, version)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version} not found in registry for simulation '{simulation_id}'.",
        )
    return entry


@router.post("/{simulation_id}/versions/{version}/promote")
async def promote_model_version(
    simulation_id: str, version: int, payload: ModelPromoteRequest
) -> dict[str, Any]:
    """Promote a specific model version to champion or challenger."""
    try:
        updated_entry = registry.promote_version(
            simulation_id=simulation_id,
            version=version,
            target_status=payload.target_status,
        )
        _simulation_events.push_list(
            simulation_id,
            {
                "event_type": "promotion",
                "data": {
                    "version": version,
                    "target_status": payload.target_status,
                    "message": f"Global model version {version} promoted to {payload.target_status}",
                    "timestamp": updated_entry.get("created_at"),
                },
            },
        )
        return updated_entry
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed to promote model for %s: %s", simulation_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute model promotion: {e}",
        )


@router.post("/{simulation_id}/rollback/{version}")
async def rollback_model_version(simulation_id: str, version: int) -> dict[str, Any]:
    """Rollback/promote a specific model version as active."""
    try:
        updated_entry = registry.rollback(simulation_id, version)

        # Notify the UI about the rollback event
        _simulation_events.push_list(
            simulation_id,
            {
                "event_type": "rollback",
                "data": {
                    "version": version,
                    "message": f"Global model rolled back to version {version}",
                    "timestamp": updated_entry.get("created_at"),
                },
            },
        )
        return updated_entry
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("Failed to rollback model for %s: %s", simulation_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute model rollback: {e}",
        )


@router.get("/{simulation_id}/canary")
async def get_canary_history(simulation_id: str) -> list[dict[str, Any]]:
    """Retrieve the canary evaluation decisions history from simulation events."""
    events = _simulation_events.get_list(simulation_id)
    canary_history = []
    for event in events:
        if event.get("event_type") == "round_complete":
            data = event.get("data", {})
            canary_info = data.get("canary_info")
            if canary_info:
                canary_history.append(
                    {
                        "round": data.get("round"),
                        "version": canary_info.get("version"),
                        "candidate_auc": canary_info.get("candidate_auc"),
                        "promoted_auc": canary_info.get("promoted_auc"),
                        "is_promoted": canary_info.get("is_promoted"),
                        "reason": canary_info.get("reason"),
                    }
                )
    return canary_history


@router.post("/{simulation_id}/versions/{version}/signoff")
async def sign_off_model(
    simulation_id: str, version: int, payload: ModelSignOffRequest
) -> dict[str, Any]:
    """Approve and sign off on a new global model's metrics."""
    try:
        updated_entry = registry.sign_off(
            simulation_id=simulation_id,
            version=version,
            role=payload.role,
            user=payload.user,
            signature=payload.signature,
            fairness_score=payload.fairness_score,
            bias_metric=payload.bias_metric,
            drift_divergence=payload.drift_divergence,
        )
        return updated_entry
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Failed to sign off on model version: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit model sign-off: {e}",
        )


@router.get("/{simulation_id}/shadow/metrics")
async def get_shadow_metrics(simulation_id: str) -> dict[str, Any]:
    """Get real-time shadowing deployment and evaluation metrics."""
    try:
        return _eval_engine.get_shadow_metrics(simulation_id)
    except Exception as e:
        logger.error("Failed to retrieve shadow metrics: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load shadow metrics: {e}",
        )
