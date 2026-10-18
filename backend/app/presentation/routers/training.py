"""Training progress endpoints.

Provides access to per-round training data, progress streams, convergence metrics,
and simulation history. Reads from Redis-backed event and result stores with in-process fallbacks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Path, Query, status

from starlette.concurrency import run_in_threadpool

from app.application.schemas.simulation import (
    OperationalFederationRequest,
    OperationalFederationResponse,
    TrainingHistoryItem,
    TrainingHistoryResponse,
    TrainingMetricsSummaryResponse,
    TrainingProgressResponse,
    TrainingRoundResponse,
)
from app.application.services.operational_federation_service import OperationalFederationService
from app.application.services.training_provenance_service import (
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.presentation.routers.model_registry import _authenticate_and_authorize_operator
from app.presentation.routers.simulation import _simulation_events, _simulation_results

logger = logging.getLogger(__name__)

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ConsortiumStatus, MemberRole

router = APIRouter(prefix="/api/v1/training", tags=["training"])
api_router = APIRouter(prefix="/v1/training", tags=["training"])


def _sanitize_client_error(raw_msg: str, default_msg: str) -> str:
    """Strip filesystem paths, connection strings, and internal credentials from client-facing HTTP errors."""
    if not raw_msg:
        return default_msg
    lowered = raw_msg.lower()
    if any(k in lowered for k in ("password", "secret", "token", "private_key", "vault", "postgresql://", "sqlite:///", "c:\\", "c:/", "/tmp/")):
        return default_msg
    return raw_msg[:200]


@router.post("/federation/jobs", response_model=OperationalFederationResponse, status_code=status.HTTP_201_CREATED)
@api_router.post("/federation/jobs", response_model=OperationalFederationResponse, status_code=status.HTTP_201_CREATED)
async def start_operational_federation_job(
    payload: OperationalFederationRequest,
    authorization: str | None = Header(None, description="Bearer <operator_jwt>"),
) -> OperationalFederationResponse:
    """Authorized operational entry point to initiate the canonical distributed federated learning lifecycle.

    Guarantees:
    - Cryptographically authenticates initiating operator via live RBAC (requires admin, ml_lead, or ml_engineer role with clearance level >= 2).
    - Resolves cohort against durable authoritative ConsortiumGovernanceService state; rejects unknown, removed, or observer institutions.
    - Enforces institutional authorization: cross-institution runs require verified elevated consortium admin authority (clearance >= 3).
    - Never infers authority solely from caller-controlled bank labels (e.g. CENTRAL, CONSORTIUM).
    - Resolves authorities, credentials, vault paths, and pre-provisioned bank endpoints strictly server-side.
    - Executes asynchronously via threadpool offloading to avoid blocking the event loop.
    - Never accepts private keys, database URLs, bank credentials, raw datasets, or process handles from clients.
    - Executes under isolated Windows Restricted Token in production via run_production_federation.
    - Strictly reports truthful execution state without fabricated success, dummy receipts, or synthetic metrics.
    - Registers verified candidate model in ModelRegistry with inactive status (never automatically promotes champion).
    """
    operator_user, claims, _ = _authenticate_and_authorize_operator(
        authorization=authorization,
        allowed_roles={"admin", "ml_lead", "ml_engineer"},
        operation_name="operational federated training initiation",
        min_clearance=2,
    )
    logger.info("Operator [%s] authenticated for operational federation job", operator_user.username)

    # 1. Resolve and validate active consortium
    target_consortium_id = (payload.consortium_id or "cfi-consortium").strip().lower()
    consortium_svc = ConsortiumGovernanceService()
    consortium = consortium_svc.get_consortium(target_consortium_id)
    if consortium is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authoritative consortium '{target_consortium_id}' does not exist or has not been provisioned.",
        )
    if consortium.status != ConsortiumStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Consortium '{target_consortium_id}' is not in ACTIVE state (current status: {consortium.status.value}).",
        )

    # 2. Validate participant cohort against authoritative consortium
    raw_participants = payload.participating_banks
    if not raw_participants:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="participating_banks must be a non-empty list of consortium institutions.",
        )

    normalized_banks: list[str] = []
    seen_banks: set[str] = set()
    for b in raw_participants:
        if not isinstance(b, str) or not b.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid participant bank identity in training request: non-empty string required.",
            )
        norm_b = b.strip().upper()
        clean_bank_id = norm_b.lower()
        if norm_b in seen_banks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate participant bank identity '{norm_b}' in training request.",
            )

        if clean_bank_id in consortium.removed_members:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Institution '{norm_b}' was expelled or removed from consortium '{target_consortium_id}' and cannot participate.",
            )

        member = consortium.members.get(clean_bank_id)
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Institution '{norm_b}' is not an authorized member of consortium '{target_consortium_id}'.",
            )

        if member.role == MemberRole.OBSERVER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Institution '{norm_b}' is an OBSERVER in consortium '{target_consortium_id}' and cannot participate in model training.",
            )

        seen_banks.add(norm_b)
        normalized_banks.append(norm_b)

    if len(normalized_banks) < consortium.min_members_n:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Requested cohort size ({len(normalized_banks)}) is below minimum required members ({consortium.min_members_n}) for consortium '{target_consortium_id}'.",
        )

    # 3. Enforce institution-scoped operator authorization bound to target consortium
    op_bank = (getattr(operator_user, "bank_id", "") or claims.get("bank_id", "") or "").strip().upper()
    op_clearance = int(getattr(operator_user, "clearance_level", 1) or claims.get("clearance_level", 1))
    op_roles = {r.lower().strip() for r in (getattr(operator_user, "roles", []) or claims.get("roles", []))}

    if not op_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operator '{operator_user.username}' has no institutional affiliation (bank_id missing) and cannot initiate consortium training.",
        )

    clean_op_bank = op_bank.lower()

    if clean_op_bank in consortium.removed_members:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operator's institution '{op_bank}' was expelled or removed from consortium '{target_consortium_id}' and cannot administer or initiate training.",
        )

    op_member = consortium.members.get(clean_op_bank)
    if op_member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Operator's institution '{op_bank}' is not an authorized member of target consortium '{target_consortium_id}'. "
                "Global administrative role does not grant authority over arbitrary consortia."
            ),
        )

    if op_member.role == MemberRole.OBSERVER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operator's institution '{op_bank}' is an OBSERVER in consortium '{target_consortium_id}' and lacks governance authority to initiate model training.",
        )

    # Verifiable elevated authority check for cross-institution coordination:
    # Requires clearance >= 3 AND an admin / governance role.
    # NEVER infer elevated authority solely from bank labels like "CENTRAL", "CONSORTIUM", or "REGULATOR".
    has_elevated_consortium_admin = op_clearance >= 3 and bool(op_roles & {"admin", "consortium_admin", "security_admin"})

    # Check if run includes institutions other than operator's own institution:
    # Never infer permission to involve another institution simply because initiator includes their own bank.
    requires_cross_institution_authority = any(b != op_bank for b in normalized_banks)

    if requires_cross_institution_authority:
        if not has_elevated_consortium_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Initiating cross-institution federated training for cohort {sorted(normalized_banks)} in consortium '{target_consortium_id}' "
                    f"requires elevated consortium administrator authority (clearance >= 3, admin role). "
                    f"Operator '{operator_user.username}' (institution: '{op_bank}', clearance: {op_clearance}) "
                    "is not authorized for cross-institution coordination."
                ),
            )
    else:
        # Single-institution run: operator must belong to that institution
        if op_bank != normalized_banks[0]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operator from institution '{op_bank}' is not authorized to initiate training for institution '{normalized_banks[0]}'.",
            )

    svc = OperationalFederationService()
    try:
        # Offload synchronous execution to threadpool to avoid blocking async event loop
        result = await run_in_threadpool(
            svc.execute_operational_federation,
            job_id=payload.job_id,
            simulation_id=payload.simulation_id,
            participating_banks=normalized_banks,
            num_rounds=payload.num_rounds,
            expected_unique_samples=payload.expected_unique_samples,
            consortium_id=target_consortium_id,
        )

        exec_status = result.get("status")
        is_verified = bool(result.get("is_verified", False))
        if not exec_status or exec_status != "COMPLETED" or not is_verified:
            logger.error("Federation execution returned incomplete or unverified state: status=%s, verified=%s", exec_status, is_verified)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Federation execution failed or produced unverified execution state.",
            )

        # Invariant: Execution result must carry the verified authorized consortium scope
        res_consortium = (result.get("consortium_id") or "").strip().lower()
        if not res_consortium or res_consortium != target_consortium_id:
            logger.error(
                "Federation completion returned consortium scope mismatch: expected=%s, actual=%s",
                target_consortium_id,
                res_consortium,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Federation execution returned inconsistent consortium scope binding.",
            )

        saved_ver = result.get("saved_version")
        ver_number = result.get("model_version")
        if ver_number is None and isinstance(saved_ver, dict):
            ver_number = saved_ver.get("version")
        if ver_number is None:
            logger.error("Federation completion reported success but model version is missing: %s", result)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Federation execution produced no durable candidate model registration.",
            )

        # Invariant: Durable candidate model registration must be bound to the target consortium
        if isinstance(saved_ver, dict):
            cand_consortium = (saved_ver.get("consortium_id") or "").strip().lower()
            if cand_consortium and cand_consortium != target_consortium_id:
                logger.error(
                    "Candidate model registration consortium scope mismatch: expected=%s, actual=%s",
                    target_consortium_id,
                    cand_consortium,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Model candidate registration violates authoritative consortium scope.",
                )

        artifact_sha = str(result.get("artifact_sha256") or result.get("final_artifact_sha256") or "").strip().lower()
        if len(artifact_sha) != 64 or not all(c in "0123456789abcdef" for c in artifact_sha):
            logger.error("Federation completion reported invalid artifact digest: %s", artifact_sha)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Federation execution produced no verified artifact digest (SHA-256).",
            )

        return OperationalFederationResponse(
            job_id=result["job_id"],
            training_run_id=result.get("training_run_id", ""),
            simulation_id=result.get("simulation_id", payload.simulation_id or ""),
            consortium_id=target_consortium_id,
            status="COMPLETED",
            is_verified=True,
            metrics=result.get("metrics", {}),
            artifact_sha256=artifact_sha,
            model_version=ver_number,
            is_promoted_champion=False,
            coordinator_pid=result.get("coordinator_pid", 0),
            worker_pids=result.get("worker_pids", {}),
        )
    except TrainingProvenanceSecurityError as exc:
        logger.error("Operational federation rejected due to security policy violation: %s", exc)
        safe_msg = _sanitize_client_error(str(exc), "Federation security policy violation.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=safe_msg,
        ) from exc
    except TrainingProvenanceConfigurationError as exc:
        logger.error("Operational federation failed closed due to configuration error: %s", exc)
        safe_msg = _sanitize_client_error(str(exc), "Federation authority configuration error.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=safe_msg,
        ) from exc
    except ValueError as exc:
        logger.warning("Operational federation bad request: %s", exc)
        safe_msg = _sanitize_client_error(str(exc), "Invalid federation request parameters.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_msg,
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Operational federation execution failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Operational federation execution failed unexpectedly.",
        ) from exc


@router.get("/progress", response_model=TrainingProgressResponse)
@api_router.get("/progress", response_model=TrainingProgressResponse)
async def get_latest_training_progress(
    simulation_id: str | None = Query(default=None, description="Optional target simulation run ID"),
) -> TrainingProgressResponse:
    """Retrieve real-time training progress for the specified or most recent simulation."""
    target_id = simulation_id
    if not target_id:
        all_sims = list(_simulation_results.list_values())
        if all_sims:
            target_id = all_sims[-1].get("id")

    if not target_id:
        return TrainingProgressResponse(
            simulation_id=None,
            event_type="unknown",
            data={"message": "No active simulations available."},
        )

    return await get_training_progress(target_id)


@router.get("/metrics", response_model=TrainingMetricsSummaryResponse)
@api_router.get("/metrics", response_model=TrainingMetricsSummaryResponse)
async def get_training_metrics(
    simulation_id: str | None = Query(default=None, description="Optional target simulation run ID"),
) -> TrainingMetricsSummaryResponse:
    """Retrieve training convergence loss and per-bank AUC progression."""
    target_id = simulation_id
    if not target_id:
        all_sims = list(_simulation_results.list_values())
        if all_sims:
            target_id = all_sims[-1].get("id")

    if not target_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active simulations found to aggregate training metrics.",
        )

    sim = _simulation_results.get(target_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{target_id}' not found.",
        )

    events = _simulation_events.get_list(target_id)
    losses: list[float] = []
    aucs: list[float] = []
    per_bank_auc: dict[str, list[float]] = {}
    per_bank_loss: dict[str, list[float]] = {}

    for event in events:
        if event.get("event_type") == "round_complete":
            d = event.get("data", {})
            loss_val = d.get("loss")
            if loss_val is not None:
                losses.append(float(loss_val))
            auc_val = d.get("auc")
            if auc_val is not None:
                aucs.append(float(auc_val))
            for b, val in d.get("per_bank_auc", {}).items():
                if val is not None:
                    per_bank_auc.setdefault(b, []).append(float(val))
            for b, val in d.get("per_bank_loss", {}).items():
                if val is not None:
                    per_bank_loss.setdefault(b, []).append(float(val))

    return TrainingMetricsSummaryResponse(
        simulation_id=target_id,
        total_rounds=sim.get("total_rounds", len(losses)),
        global_losses=losses,
        auc_history=aucs,
        per_bank_auc=per_bank_auc,
        per_bank_loss=per_bank_loss,
    )


@router.get("/history", response_model=TrainingHistoryResponse)
@api_router.get("/history", response_model=TrainingHistoryResponse)
async def get_training_history(
    limit: int = Query(default=10, ge=1, le=100, description="Max historical simulation runs to return"),
) -> TrainingHistoryResponse:
    """Retrieve historical federated training simulation runs."""
    all_sims = list(_simulation_results.list_values())
    runs: list[TrainingHistoryItem] = []
    for sim in all_sims:
        if not isinstance(sim, dict):
            continue
        runs.append(
            TrainingHistoryItem(
                simulation_id=sim.get("id", ""),
                status=sim.get("status", "unknown"),
                current_round=sim.get("current_round", 0),
                total_rounds=sim.get("total_rounds", 10),
                progress_pct=float(sim.get("progress_pct", 0.0)),
                created_at=sim.get("created_at"),
                completed_at=sim.get("completed_at"),
                duration_seconds=sim.get("duration_seconds"),
            )
        )
    runs.sort(key=lambda r: r.created_at or "", reverse=True)
    return TrainingHistoryResponse(
        total_count=len(runs),
        runs=runs[:limit],
    )


@router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@router.get("/rounds/{simulation_id}", response_model=list[TrainingRoundResponse])
@api_router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@api_router.get("/rounds/{simulation_id}", response_model=list[TrainingRoundResponse])
async def get_training_rounds(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> list[TrainingRoundResponse]:
    """Get all training rounds for a simulation.

    Returns round-by-round metrics including loss, participants,
    dropouts, and timing data.
    """
    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )

    events = _simulation_events.get_list(simulation_id)
    rounds: list[TrainingRoundResponse] = []
    for event in events:
        if event.get("event_type") == "round_complete":
            data = event.get("data", {})
            loss_raw = data.get("loss")
            auc_raw = data.get("auc")
            pb_raw = data.get("privacy_budget")
            rounds.append(
                TrainingRoundResponse(
                    round_number=data.get("round", 0),
                    total_rounds=data.get("total", sim.get("total_rounds", 10)),
                    global_loss=float(loss_raw) if loss_raw is not None else None,
                    auc=float(auc_raw) if auc_raw is not None else None,
                    per_bank_auc=data.get("per_bank_auc", {}),
                    per_bank_loss=data.get("per_bank_loss", {}),
                    participating_banks=data.get("participants", []),
                    dropped_banks=data.get("dropped", []),
                    duration_ms=float(data.get("duration_ms", 0.0)),
                    privacy_budget=float(pb_raw) if pb_raw is not None else None,
                    feature_importance=data.get("feature_importance", {}),
                    canary_info=data.get("canary_info", {}),
                )
            )

    return rounds


@router.get("/{simulation_id}/rounds/{round_number}", response_model=TrainingRoundResponse)
@api_router.get("/{simulation_id}/rounds/{round_number}", response_model=TrainingRoundResponse)
async def get_training_round(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
    round_number: int = Path(..., ge=1, description="Training round index"),
) -> TrainingRoundResponse:
    """Get details for a specific training round."""
    rounds = await get_training_rounds(simulation_id)

    for r in rounds:
        if r.round_number == round_number:
            return r

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Round {round_number} not found for simulation {simulation_id}",
    )


@router.get("/{simulation_id}/progress", response_model=TrainingProgressResponse)
@api_router.get("/{simulation_id}/progress", response_model=TrainingProgressResponse)
async def get_training_progress(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> TrainingProgressResponse:
    """Get the latest progress update for a running simulation."""
    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )

    events = _simulation_events.get_list(simulation_id)
    if events:
        last_event = events[-1]
        return TrainingProgressResponse(
            simulation_id=simulation_id,
            event_type=last_event.get("event_type", "unknown"),
            status=sim.get("status"),
            current_round=sim.get("current_round", 0),
            total_rounds=sim.get("total_rounds", 10),
            progress_pct=float(sim.get("progress_pct", 0.0)),
            data=last_event.get("data", {}),
        )

    return TrainingProgressResponse(
        simulation_id=simulation_id,
        event_type="pending",
        status=sim.get("status"),
        current_round=sim.get("current_round", 0),
        total_rounds=sim.get("total_rounds", 10),
        progress_pct=float(sim.get("progress_pct", 0.0)),
        data={"message": "No progress data available yet"},
    )
