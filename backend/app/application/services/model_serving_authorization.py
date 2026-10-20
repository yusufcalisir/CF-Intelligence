"""Centralized Model Serving Authorization Boundary.

Enforces authoritative institutional and consortium model serving authorization
AFTER authentication, ensuring:
- Bank A cannot execute Bank B's private model.
- Consortium models require active, currently valid institutional membership.
- Revoked consortium members cannot initiate new inference requests.
- Omitted simulation_id resolves via authoritative tenant-appropriate policy without unscoped foreign scanning.
- Unbound, ambiguous, or corrupted model ownership fails closed.
- Strict privileged role checking using exact canonical role definitions.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException, Request, status

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
    validate_simulation_id,
)
from app.config import get_settings
from app.domain.consortium_governance import ConsortiumStatus
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

logger = logging.getLogger(__name__)


def resolve_effective_caller_tenant(
    request: Request | None = None,
    caller_tenant: str | None = None,
    requested_bank_id: str | None = None,
    body_bank_id: str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    action_name: str = "inference",
) -> tuple[AuthenticatedPrincipal | None, str]:
    """Authoritatively binds and validates the institutional tenant identity for an inference request.

    Enforces runtime truth:
    - An authenticated Bank A principal remains Bank A regardless of body.bank_id.
    - Missing body.bank_id resolves to the authenticated, authorized institutional tenant.
    - No production fallback to 'serving_client' or 'global' for private scoring.
    - Conflicting body.bank_id, header tenant, or authenticated principal identity fails HTTP 403.
    - A verified principal with no institutional tenant cannot gain private-bank access by selecting one in the request.
    - Exact cross-bank privileged roles (super_admin, cross_bank_investigator, compliance_auditor)
      may specify a target bank_id or use their own.
    """
    if principal is None and request is not None:
        principal = getattr(getattr(request, "state", None), "principal", None)

    req_bank = requested_bank_id or body_bank_id

    settings = get_settings()
    is_production = getattr(settings, "app_env", "development").lower() in ("production", "prod")

    # If principal is absent or unauthenticated
    if principal is None or not principal.is_authenticated:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for production inference scoring.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Development fallback only
        if req_bank:
            return principal, req_bank.strip().lower()
        if caller_tenant:
            return principal, caller_tenant.strip().lower()
        return principal, "bank_a"

    # Privileged cross-bank authority
    if principal.is_privileged:
        if req_bank and req_bank.strip():
            return principal, req_bank.strip().lower()
        if caller_tenant and caller_tenant.strip():
            return principal, caller_tenant.strip().lower()
        if principal.tenant_id and principal.tenant_id.strip():
            return principal, principal.tenant_id.strip().lower()
        return principal, "global"

    # Ordinary authenticated caller
    auth_tenant = (principal.tenant_id or "").strip().lower()
    if not auth_tenant:
        # Verified principal with no institutional tenant cannot assume one from the body
        if req_bank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Broken Access Control: Authenticated principal '{principal.principal_id}' has no institutional "
                    f"tenant binding and cannot assume private-bank tenant '{req_bank}'."
                ),
            )
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated principal lacks institutional tenant binding required for scoring.",
            )
        return principal, "bank_a"

    # Caller has verified tenant identity
    if req_bank and req_bank.strip():
        norm_body = req_bank.strip().lower().replace("-", "_")
        norm_auth = auth_tenant.replace("-", "_")
        if norm_body != norm_auth:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Broken Access Control: Cross-tenant access forbidden. Authenticated tenant '{auth_tenant}' "
                    f"cannot submit requests on behalf of bank '{req_bank}'."
                ),
            )

    if caller_tenant and caller_tenant.strip():
        norm_caller = caller_tenant.strip().lower().replace("-", "_")
        norm_auth = auth_tenant.replace("-", "_")
        if norm_caller != norm_auth:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Broken Access Control: Conflicting tenant selector. Header tenant '{caller_tenant}' "
                    f"mismatches authenticated tenant '{auth_tenant}'."
                ),
            )

    return principal, auth_tenant


def authorize_model_serving(
    principal: AuthenticatedPrincipal | None,
    caller_tenant: str | None = None,
    simulation_id: str | None = None,
    requested_simulation_id: str | None = None,
    effective_tenant: str | None = None,
    registry: ModelRegistry | None = None,
    consortium_service: Any | None = None,
    action_name: str = "inference",
) -> tuple[str, dict[str, Any]]:
    """Verifies that the caller possesses authoritative permission to serve/execute the target model.

    Returns:
        tuple[str, dict[str, Any]]: (Authorized simulation ID, simulation ownership record).

    Raises:
        HTTPException:
            401 - Missing authentication in production.
            403 - Cross-bank access forbidden, non-member consortium access, revoked member, or unbound ownership.
            404 - Simulation not found or no authorized champion model exists for tenant.
            422 - Ambiguous simulation selection when multiple authorized models exist.
            503 - Ownership metadata corrupted or consortium service unavailable.
    """
    settings = get_settings()
    is_production = getattr(settings, "app_env", "development").lower() in ("production", "prod")

    eff_tenant = caller_tenant or effective_tenant
    target_sim_id = simulation_id or requested_simulation_id

    reg = registry or ModelRegistry()

    # 1. Validate Authentication & Institutional Identity
    if principal is None or not principal.is_authenticated:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for production model serving.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        norm_tenant = (eff_tenant or "bank_a").lower().strip()
    else:
        if principal.is_privileged:
            norm_tenant = (eff_tenant or principal.tenant_id or "global").lower().strip()
        else:
            if not eff_tenant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Model serving authorization requires an authenticated institutional tenant.",
                )
            norm_tenant = eff_tenant.lower().strip()

    is_privileged = bool(principal and principal.is_privileged)

    # 2. When simulation_id is explicitly supplied:
    if target_sim_id and target_sim_id.strip():
        try:
            clean_sim_id = validate_simulation_id(target_sim_id.strip())
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invalid simulation identifier: {err}",
            ) from err

        sim_dir = reg._get_sim_dir(clean_sim_id, create=False)
        if not os.path.isdir(sim_dir):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Simulation '{clean_sim_id}' not found.",
            )

        try:
            ownership = reg.get_simulation_ownership(clean_sim_id)
        except ModelIntegrityError as exc:
            logger.error("Simulation ownership verification failed for %s: %s", clean_sim_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Serving model rejected: ownership verification failed for simulation '{clean_sim_id}': {exc}",
            ) from exc

        # Unbound ownership fails closed in production
        if ownership.get("is_unbound"):
            if is_production or not is_privileged:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Broken Access Control: Model serving rejected because simulation '{clean_sim_id}' "
                        f"has unbound or ambiguous institutional ownership."
                    ),
                )
            return clean_sim_id, ownership

        owning_bank = ownership.get("owning_bank_id")
        consortium_id = ownership.get("consortium_id")

        if is_privileged:
            return clean_sim_id, ownership

        norm_owner = owning_bank.lower().strip() if owning_bank else None

        # Exclusive institution ownership remains exclusive when caller is owner
        if norm_owner and norm_owner == norm_tenant:
            return clean_sim_id, ownership

        # If ownership binds a consortium, evaluate active authorized membership
        if consortium_id:
            c = DurableConsortiumStore.get_instance().get_consortium(consortium_id)
            if c is None or c.status != ConsortiumStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Broken Access Control: Consortium '{consortium_id}' is not active or not found.",
                )

            if norm_tenant in c.removed_members:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Broken Access Control: Institutional membership for tenant '{norm_tenant}' in "
                        f"consortium '{consortium_id}' has been revoked; new inference requests are rejected."
                    ),
                )

            if norm_tenant not in c.members:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Broken Access Control: Tenant '{norm_tenant}' is not an active member of "
                        f"consortium '{consortium_id}'."
                    ),
                )

            return clean_sim_id, ownership

        # If model has an owning bank and no consortium sharing authorization exists
        if norm_owner and norm_owner != norm_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Broken Access Control: Tenant '{norm_tenant}' is not authorized to execute private "
                    f"champion model owned by bank '{owning_bank}'."
                ),
            )

        # Missing authoritative ownership
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Broken Access Control: Simulation '{clean_sim_id}' lacks authoritative ownership.",
        )

    # 3. When simulation_id is omitted:
    # Resolve through authoritative, tenant-appropriate serving policy before loading any champion
    raw_default = os.environ.get("CFI_DEFAULT_SIMULATION_ID") or getattr(settings, "default_simulation_id", "") or ""
    configured_default = str(raw_default).strip()

    if configured_default:
        try:
            return authorize_model_serving(
                principal=principal,
                effective_tenant=norm_tenant,
                requested_simulation_id=configured_default,
                registry=reg,
                consortium_service=consortium_service,
                action_name=action_name,
            )
        except HTTPException:
            # Configured default is not authorized for this caller; do not leak or silently fallback
            pass

    # Search for an unambiguous active champion model owned by or authorized for norm_tenant
    if not os.path.isdir(reg.registry_root):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model registry directory not found.",
        )

    authorized_candidates: list[str] = []
    candidates_ownership: dict[str, dict[str, Any]] = {}
    c_store = None
    for entry_name in sorted(os.listdir(reg.registry_root)):
        try:
            clean_s_id = validate_simulation_id(entry_name)
        except ValueError:
            continue
        sim_dir_path = os.path.join(reg.registry_root, clean_s_id)
        if not os.path.isdir(sim_dir_path):
            continue

        try:
            own = reg.get_simulation_ownership(clean_s_id)
        except Exception:
            continue

        if own.get("is_unbound") and is_production:
            continue

        o_bank = (own.get("owning_bank_id") or "").lower().strip()
        c_id = own.get("consortium_id")

        is_authorized = False
        if is_privileged or (o_bank and o_bank == norm_tenant):
            is_authorized = True
        elif c_id:
            try:
                if c_store is None:
                    c_store = DurableConsortiumStore.get_instance()
                c = c_store.get_consortium(c_id)
            except Exception:
                c = None
            if c and c.status == ConsortiumStatus.ACTIVE and norm_tenant in c.members and norm_tenant not in c.removed_members:
                is_authorized = True
        elif not is_production and own.get("is_unbound") and not o_bank and not c_id:
            is_authorized = True

        if is_authorized:
            ident = reg.get_active_champion_identity(simulation_id=clean_s_id)
            if ident:
                authorized_candidates.append(clean_s_id)
                candidates_ownership[clean_s_id] = own

    if len(authorized_candidates) == 1:
        chosen = authorized_candidates[0]
        return chosen, candidates_ownership[chosen]

    if len(authorized_candidates) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No authorized active champion model found for institution '{norm_tenant}'.",
        )

    # Multiple candidates exist and none unambiguously selected
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Ambiguous model selection: multiple authorized champion models exist for institution '{norm_tenant}' "
            f"({authorized_candidates}). Explicit 'simulation_id' is required."
        ),
    )
