"""Consortium Governance Service handling multi-bank alliances, weighted voting quorums, and proposal state machines."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import os
import threading
import uuid
from datetime import UTC
from typing import Any

from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumSecurityError,
    ConsortiumStatus,
    MemberRole,
    MembershipProposal,
    ProposalAction,
    ProposalStatus,
)
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

logger = logging.getLogger(__name__)


def compute_operational_grant_scope_digest(
    grant_id: str,
    revision: int,
    participating_banks: list[str],
    allowed_purposes: list[str],
    institution_jurisdictions: dict[str, str],
    allowed_jurisdictions: list[str],
    allow_cross_border: bool,
    allow_institution_identity_sharing: bool,
) -> str:
    """Computes deterministic, canonical SHA-256 digest of an operational grant scope."""
    canonical = {
        "grant_id": grant_id.strip(),
        "revision": revision,
        "participating_banks": sorted(list(set(b.lower().strip() for b in participating_banks))),
        "allowed_purposes": sorted(list(set(p.strip() for p in allowed_purposes))),
        "institution_jurisdictions": {
            k.lower().strip(): v.strip()
            for k, v in sorted(institution_jurisdictions.items())
        },
        "allowed_jurisdictions": sorted(list(set(j.strip() for j in allowed_jurisdictions))),
        "allow_cross_border": allow_cross_border,
        "allow_institution_identity_sharing": allow_institution_identity_sharing,
    }
    canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_pairwise_grant_scope_digest(
    grant_id: str,
    consortium_id: str,
    institution_a: str,
    institution_b: str,
    revision: int,
    allowed_purposes: list[str],
    institution_jurisdictions: dict[str, str],
    allowed_jurisdictions: list[str],
    allow_cross_border: bool,
    allow_institution_identity_sharing: bool,
) -> str:
    """Computes deterministic, canonical SHA-256 digest of a pairwise operational grant scope."""
    part_banks = sorted([institution_a.lower().strip(), institution_b.lower().strip()])
    canonical = {
        "grant_id": grant_id.strip(),
        "consortium_id": consortium_id.lower().strip(),
        "institution_a": part_banks[0],
        "institution_b": part_banks[1],
        "revision": revision,
        "participating_banks": part_banks,
        "allowed_purposes": sorted(list(set(p.strip() for p in allowed_purposes))),
        "institution_jurisdictions": {
            k.lower().strip(): v.strip()
            for k, v in sorted(institution_jurisdictions.items())
        },
        "allowed_jurisdictions": sorted(list(set(j.strip() for j in allowed_jurisdictions))),
        "allow_cross_border": bool(allow_cross_border),
        "allow_institution_identity_sharing": bool(allow_institution_identity_sharing),
    }
    canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class ConsortiumGovernanceService:
    """Manages consortium creation, weighted voting proposals, dynamic state machine, and member lifecycle."""

    def __init__(
        self,
        store: DurableConsortiumStore | None = None,
        institutional_registry: Any | None = None,
    ) -> None:
        self._store = store or DurableConsortiumStore.get_instance()
        self._lock = threading.RLock()
        self._institutional_registry = institutional_registry

    @property
    def store(self) -> DurableConsortiumStore:
        """Authoritative durable storage accessor."""
        return self._store

    @property
    def institutional_registry(self) -> Any | None:
        """Accessor for institutional verification key registry."""
        return self._institutional_registry

    @institutional_registry.setter
    def institutional_registry(self, registry: Any | None) -> None:
        self._institutional_registry = registry

    @property
    def _consortia(self) -> dict[str, Consortium]:
        """Backward-compatible read access to authoritative consortia dictionary."""
        self._store.reload()
        return self._store._consortia

    @property
    def _proposals(self) -> dict[str, MembershipProposal]:
        """Backward-compatible read access to authoritative proposals dictionary."""
        self._store.reload()
        return self._store._proposals

    def _verify_service_actor_bank(
        self,
        expected_bank: str,
        consortium_id: str | None = None,
        target_bank_id: str | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
        action_name: str = "consortium_mutation",
    ) -> dict[str, Any]:
        """Validate that direct service mutations enforce authentic institutional authority.

        Rejects unauthenticated calls, caller-supplied actor identity without authentication,
        and cross-bank institutional impersonation.
        Returns authentic attribution metadata.
        """
        if trusted_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action=action_name,
                expected_service_name="consortium_coordinator",
                expected_resource_type="consortium",
                expected_resource_id=consortium_id,
                expected_bank_id=expected_bank,
                expected_target_bank_id=target_bank_id,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise PermissionError(
                    f"Internal orchestration authority rejected for {action_name}: {ctx_reason}"
                )
            svc_name = getattr(trusted_context, "service_name", "") or (
                trusted_context.get("service_name", "") if isinstance(trusted_context, dict) else ""
            )
            if svc_name != "consortium_coordinator":
                raise PermissionError(
                    f"Internal service '{svc_name}' is not authorized for {action_name}."
                )
            return {
                "actor_id": f"service:{svc_name}",
                "actor_type": "trusted_internal_service",
                "actor_institution": expected_bank,
                "effective_role": ["consortium_coordinator"],
                "attribution_limitation": None,
            }

        if actor and not auth_token:
            raise PermissionError(
                f"Caller-supplied actor identity ('{actor}') cannot establish authority without valid authentication."
            )

        if not auth_token:
            raise PermissionError(
                f"Authentication required: Direct service operation '{action_name}' requires a valid Bearer auth_token or trusted internal context."
            )

        from app.infrastructure.security.auth_service import AuthenticationService

        auth_svc = AuthenticationService.get_instance()
        clean_expected = expected_bank.lower().strip()

        is_valid, claims, err = auth_svc.verify_access_token(auth_token)
        if not is_valid or not claims:
            raise PermissionError(f"Direct service authentication failed: {err}")
        username = (claims.get("username") or claims.get("sub") or "").strip()
        if actor and actor.lower().strip() != username.lower():
            raise PermissionError(
                f"Actor '{actor}' does not match authenticated token principal '{username}'."
            )
        user_rec = auth_svc.get_user(username)
        if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
            raise PermissionError(f"Direct service actor '{username}' is not a valid authoritative principal.")
        user_bank = (getattr(user_rec, "bank_id", "") or "").lower().strip()
        if user_bank != clean_expected:
            raise PermissionError(
                f"Cross-bank direct service operation rejected: Principal '{username}' from institution '{user_bank}' "
                f"cannot perform governance actions on behalf of institution '{expected_bank}'."
            )
        user_roles = {r.lower().strip() for r in getattr(user_rec, "roles", [])}
        governance_roles = {"admin", "ml_lead", "compliance"}
        if not (user_roles & governance_roles):
            raise PermissionError(
                f"Principal '{username}' lacks required governance role for {action_name}."
            )
        return {
            "actor_id": f"user:{username}@{user_bank}",
            "actor_type": "authenticated_operator",
            "actor_institution": user_bank,
            "effective_role": list(user_roles),
            "attribution_limitation": None,
        }

    def create_consortium(
        self,
        consortium_id: str,
        name: str,
        founder_bank_id: str,
        quorum_ratio: float = 0.51,
        max_epsilon: float = 5.0,
        min_members_n: int = 2,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> Consortium:
        """Initializes a new multi-bank consortium with founder member."""
        clean_id = consortium_id.lower().strip()
        clean_name = name.strip()
        clean_founder = founder_bank_id.lower().strip()

        if not clean_id:
            raise ValueError("consortium_id cannot be empty or whitespace.")
        if not clean_name:
            raise ValueError("consortium name cannot be empty or whitespace.")
        if not clean_founder:
            raise ValueError("founder_bank_id cannot be empty or whitespace.")
        if not (0.0 < quorum_ratio <= 1.0) or not math.isfinite(quorum_ratio):
            raise ValueError(f"quorum_ratio must be in (0.0, 1.0], got {quorum_ratio}")
        if max_epsilon <= 0.0 or not math.isfinite(max_epsilon):
            raise ValueError(f"max_epsilon must be positive finite number, got {max_epsilon}")
        if min_members_n < 1:
            raise ValueError(f"min_members_n must be >= 1, got {min_members_n}")

        attr = self._verify_service_actor_bank(
            clean_founder,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="create_consortium",
        )

        is_prod = getattr(self._store, "_is_prod", False)

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> Consortium:
            if clean_id in consortia:
                if is_prod:
                    raise ValueError(f"Consortium '{clean_id}' already exists in production.")
                return consortia[clean_id]

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_CREATED",
                actor_id=attr["actor_id"],
                action=f"create_consortium:{clean_id}",
                resource_id=f"consortium:{clean_id}",
                details={
                    "consortium_id": clean_id,
                    "name": clean_name,
                    "founder_bank_id": clean_founder,
                    "quorum_ratio": quorum_ratio,
                    "max_epsilon": max_epsilon,
                    "min_members_n": min_members_n,
                    "actor_type": attr["actor_type"],
                    "actor_institution": attr["actor_institution"],
                },
            )

            founder = ConsortiumMember(
                bank_id=clean_founder,
                role=MemberRole.FOUNDER,
                voting_power=1.0,
            )
            consortium = Consortium(
                consortium_id=clean_id,
                name=clean_name,
                quorum_ratio=quorum_ratio,
                min_members_n=min_members_n,
                max_epsilon=max_epsilon,
                status=ConsortiumStatus.ACTIVE,
                members={clean_founder: founder},
            )

            consortia[clean_id] = consortium
            logger.info(
                "Created consortium '%s' (%s) with founder '%s'",
                clean_name,
                clean_id,
                clean_founder,
            )
            return consortium

        return self._store.atomic_mutate(_mutate)

    def register_operational_data_sharing_grant(
        self,
        consortium_id: str,
        grant_id: str,
        authority: str,
        participating_banks: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        institution_jurisdictions: dict[str, str] | None = None,
        allowed_jurisdictions: list[str] | None = None,
        allow_cross_border: bool = False,
        allow_institution_identity_sharing: bool = False,
        revision: int = 1,
        approving_bank_id: str | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> dict[str, Any]:
        """Registers or updates an authoritative operational data-sharing grant under verified governance authority."""
        clean_id = consortium_id.lower().strip()
        if not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'register_operational_data_sharing_grant' "
                "requires a valid Bearer auth_token or trusted internal context."
            )

        if auth_token and not approving_bank_id:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise PermissionError(f"Direct service authentication failed: {err}")
            approving_bank_id = str(claims.get("bank_id", "")).lower().strip()

        clean_approver = (approving_bank_id or "").lower().strip()
        attr = self._verify_service_actor_bank(
            expected_bank=clean_approver,
            consortium_id=clean_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="register_operational_data_sharing_grant",
        )

        purposes = sorted(list(set(p.strip() for p in (allowed_purposes or ["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"]))))
        jurisdictions = institution_jurisdictions or {}
        default_allowed = list(dict.fromkeys(list(jurisdictions.values()) + ["eu-central-1", "us-east-1", "apac-singapore-1"]))
        part_banks = [b.lower().strip() for b in (participating_banks or list(jurisdictions.keys()) or [clean_approver])]
        if clean_approver not in part_banks:
            part_banks.append(clean_approver)
        part_banks = sorted(list(set(part_banks)))
        clean_jurisdictions = {k.lower().strip(): v.strip() for k, v in sorted(jurisdictions.items())}
        clean_allowed_jur = sorted(list(set(j.strip() for j in (allowed_jurisdictions or default_allowed))))

        scope_digest = compute_operational_grant_scope_digest(
            grant_id=grant_id,
            revision=revision,
            participating_banks=part_banks,
            allowed_purposes=purposes,
            institution_jurisdictions=clean_jurisdictions,
            allowed_jurisdictions=clean_allowed_jur,
            allow_cross_border=allow_cross_border,
            allow_institution_identity_sharing=allow_institution_identity_sharing,
        )

        approval_record = {
            "actor_id": attr["actor_id"],
            "actor_institution": clean_approver,
            "approved_at": datetime.datetime.now(UTC).isoformat(),
            "roles": attr["effective_role"],
            "scope_digest": scope_digest,
            "grant_revision": revision,
        }

        grant_dict: dict[str, Any] = {
            "grant_id": grant_id.strip(),
            "authority": authority.strip(),
            "revision": revision,
            "scope_digest": scope_digest,
            "is_active": True,
            "participating_banks": part_banks,
            "approvals": {clean_approver: approval_record},
            "revoked_by_institutions": [],
            "revocation_history": [],
            "allowed_purposes": purposes,
            "institution_jurisdictions": clean_jurisdictions,
            "allowed_jurisdictions": clean_allowed_jur,
            "allow_cross_border": allow_cross_border,
            "allow_institution_identity_sharing": allow_institution_identity_sharing,
            "registered_at": datetime.datetime.now(UTC).isoformat(),
            "approved_by": attr["actor_id"],
            "approving_bank_id": clean_approver,
            "history": [],
        }

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_id not in consortia:
                raise ValueError(f"Consortium '{clean_id}' does not exist.")
            c = consortia[clean_id]
            if clean_approver not in c.members:
                raise PermissionError(
                    f"Approving bank '{clean_approver}' is not an active member of consortium '{clean_id}'."
                )
            if not hasattr(c, "metadata") or c.metadata is None:
                c.metadata = {}

            existing = c.metadata.get("operational_data_sharing_grant")
            history: list[dict[str, Any]] = []
            if existing and isinstance(existing, dict):
                prev_rev = int(existing.get("revision", 1))
                prev_scope_digest = existing.get("scope_digest")
                if not prev_scope_digest:
                    prev_scope_digest = compute_operational_grant_scope_digest(
                        grant_id=existing.get("grant_id", grant_id),
                        revision=prev_rev,
                        participating_banks=existing.get("participating_banks", []),
                        allowed_purposes=existing.get("allowed_purposes", []),
                        institution_jurisdictions=existing.get("institution_jurisdictions", {}),
                        allowed_jurisdictions=existing.get("allowed_jurisdictions", []),
                        allow_cross_border=existing.get("allow_cross_border", False),
                        allow_institution_identity_sharing=existing.get("allow_institution_identity_sharing", False),
                    )
                    existing["scope_digest"] = prev_scope_digest

                # Scope is immutable per approval revision
                if revision == prev_rev and existing.get("grant_id") == grant_id.strip():
                    if scope_digest != prev_scope_digest:
                        raise ValueError(
                            f"Operational data sharing grant scope is immutable per revision. "
                            f"Modifications to participants, purposes, jurisdictions, or cross-border settings "
                            f"require an incremented revision. Current active revision is {prev_rev}."
                        )
                    approvals = dict(existing.get("approvals", {}))
                    approvals[clean_approver] = approval_record
                    existing["approvals"] = approvals
                    revoked = list(existing.get("revoked_by_institutions", []))
                    if clean_approver in revoked:
                        revoked.remove(clean_approver)
                    existing["revoked_by_institutions"] = revoked
                    existing["is_active"] = True
                    c.metadata["operational_data_sharing_grant"] = existing
                    return existing

                if revision <= prev_rev:
                    raise ValueError(
                        f"Operational grant revision must be strictly monotonically increasing. "
                        f"Current revision is {prev_rev}, attempted revision is {revision}."
                    )
                # Material changes or bumped revision: prior approvals from earlier revisions are stale
                # and do NOT carry forward into the new revision.
                history = list(existing.get("history", []))
                prior_snapshot = {k: v for k, v in existing.items() if k != "history"}
                history.append(prior_snapshot)
                grant_dict["history"] = history
                grant_dict["approvals"] = {clean_approver: approval_record}
                c.metadata["operational_data_sharing_grant"] = grant_dict
                return grant_dict

            grant_dict["history"] = history
            c.metadata["operational_data_sharing_grant"] = grant_dict
            return grant_dict

        return self._store.atomic_mutate(_mutate)

    def approve_operational_data_sharing_grant(
        self,
        consortium_id: str,
        grant_id: str,
        approving_bank_id: str,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> dict[str, Any]:
        """Records authenticated bilateral approval from a participating institution bound to canonical scope."""
        clean_id = consortium_id.lower().strip()
        clean_bank = approving_bank_id.lower().strip()
        attr = self._verify_service_actor_bank(
            expected_bank=clean_bank,
            consortium_id=clean_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="approve_operational_data_sharing_grant",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_id not in consortia:
                raise ValueError(f"Consortium '{clean_id}' does not exist.")
            c = consortia[clean_id]
            if clean_bank not in c.members:
                raise PermissionError(
                    f"Approving bank '{clean_bank}' is not an active member of consortium '{clean_id}'."
                )
            existing = (c.metadata or {}).get("operational_data_sharing_grant")
            if not existing or not isinstance(existing, dict):
                raise ValueError(f"Operational data-sharing grant '{grant_id}' does not exist in consortium '{clean_id}'.")
            if existing.get("grant_id") != grant_id.strip():
                raise ValueError(f"Grant ID mismatch: active grant is '{existing.get('grant_id')}', not '{grant_id}'.")

            rev = int(existing.get("revision", 1))
            current_scope_digest = existing.get("scope_digest")
            if not current_scope_digest:
                current_scope_digest = compute_operational_grant_scope_digest(
                    grant_id=existing.get("grant_id", grant_id),
                    revision=rev,
                    participating_banks=existing.get("participating_banks", []),
                    allowed_purposes=existing.get("allowed_purposes", []),
                    institution_jurisdictions=existing.get("institution_jurisdictions", {}),
                    allowed_jurisdictions=existing.get("allowed_jurisdictions", []),
                    allow_cross_border=existing.get("allow_cross_border", False),
                    allow_institution_identity_sharing=existing.get("allow_institution_identity_sharing", False),
                )
                existing["scope_digest"] = current_scope_digest

            approval_record = {
                "actor_id": attr["actor_id"],
                "actor_institution": clean_bank,
                "approved_at": datetime.datetime.now(UTC).isoformat(),
                "roles": attr["effective_role"],
                "scope_digest": current_scope_digest,
                "grant_revision": rev,
            }

            approvals = dict(existing.get("approvals", {}))
            approvals[clean_bank] = approval_record
            existing["approvals"] = approvals

            part_banks = set(existing.get("participating_banks", []))
            part_banks.add(clean_bank)
            existing["participating_banks"] = sorted(list(part_banks))

            revoked = list(existing.get("revoked_by_institutions", []))
            if clean_bank in revoked:
                revoked.remove(clean_bank)
            existing["revoked_by_institutions"] = revoked
            existing["is_active"] = True
            c.metadata["operational_data_sharing_grant"] = existing
            return existing

        return self._store.atomic_mutate(_mutate)

    def revoke_operational_data_sharing_grant(
        self,
        consortium_id: str,
        grant_id: str,
        revoking_bank_id: str | None = None,
        reason: str = "operational_governance_revocation",
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> dict[str, Any]:
        """Revokes an existing operational data-sharing grant under authenticated governance authority."""
        clean_id = consortium_id.lower().strip()
        if not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'revoke_operational_data_sharing_grant' "
                "requires a valid Bearer auth_token or trusted internal context."
            )

        if auth_token and not revoking_bank_id:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise PermissionError(f"Direct service authentication failed: {err}")
            revoking_bank_id = str(claims.get("bank_id", "")).lower().strip()

        clean_revoker = (revoking_bank_id or "").lower().strip()
        attr = self._verify_service_actor_bank(
            expected_bank=clean_revoker,
            consortium_id=clean_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="revoke_operational_data_sharing_grant",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_id not in consortia:
                raise ValueError(f"Consortium '{clean_id}' does not exist.")
            c = consortia[clean_id]
            if clean_revoker not in c.members:
                raise PermissionError(
                    f"Revoking bank '{clean_revoker}' is not an active member of consortium '{clean_id}'."
                )
            if not hasattr(c, "metadata") or not c.metadata:
                raise ValueError(f"No operational data-sharing grants found for consortium '{clean_id}'.")

            existing = c.metadata.get("operational_data_sharing_grant")
            if not existing or existing.get("grant_id") != grant_id.strip():
                raise ValueError(f"Grant '{grant_id}' does not exist in consortium '{clean_id}'.")

            history = list(existing.get("history", []))
            prior_snapshot = {k: v for k, v in existing.items() if k != "history"}
            history.append(prior_snapshot)

            revoked_list = list(existing.get("revoked_by_institutions", []))
            if clean_revoker not in revoked_list:
                revoked_list.append(clean_revoker)
            existing["revoked_by_institutions"] = revoked_list

            revocations = list(existing.get("revocation_history", []))
            revocations.append({
                "revoked_by_actor": attr["actor_id"],
                "revoked_by_institution": clean_revoker,
                "reason": reason.strip(),
                "revoked_at": datetime.datetime.now(UTC).isoformat(),
            })
            existing["revocation_history"] = revocations

            existing["is_active"] = False
            existing["revoked_at"] = datetime.datetime.now(UTC).isoformat()
            existing["revocation_reason"] = reason.strip()
            existing["revoked_by"] = attr["actor_id"]
            existing["history"] = history
            c.metadata["operational_data_sharing_grant"] = existing
            return existing

        return self._store.atomic_mutate(_mutate)

    def register_pairwise_operational_grant(
        self,
        consortium_id: str,
        grant_id: str,
        institution_a: str,
        institution_b: str,
        authority: str,
        allowed_purposes: list[str] | None = None,
        institution_jurisdictions: dict[str, str] | None = None,
        allowed_jurisdictions: list[str] | None = None,
        allow_cross_border: bool = False,
        allow_institution_identity_sharing: bool = False,
        revision: int = 1,
        approving_bank_id: str | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
        approval_signature: str | None = None,
    ) -> dict[str, Any]:
        """Registers or updates an authoritative pairwise operational data-sharing grant between two institutions.

        Enforces:
        - Authenticated caller authority bound to one of the two participating institutions.
        - Strict rejection of third-party, operator, or other bank approvals on their behalf.
        - Exactly two distinct active member institutions.
        - Scope immutability per revision.
        - Incremented revision resets bilateral approvals (stale approvals do not carry forward).
        - Optional Ed25519 signature verification via InstitutionalVerificationRegistry.
        """
        clean_c_id = consortium_id.lower().strip()
        clean_grant_id = grant_id.strip()
        clean_a = institution_a.lower().strip()
        clean_b = institution_b.lower().strip()
        clean_auth = authority.strip()

        if not clean_c_id:
            raise ValueError("consortium_id cannot be empty.")
        if not clean_grant_id:
            raise ValueError("grant_id cannot be empty.")
        if not clean_a or not clean_b:
            raise ValueError("Both institution_a and institution_b must be non-empty.")
        if clean_a == clean_b:
            raise ValueError(
                f"Bilateral operational grant requires two distinct member institutions, got same '{clean_a}'."
            )
        if not clean_auth:
            raise ValueError("Grant authority cannot be empty.")
        if revision < 1:
            raise ValueError(f"Grant revision must be >= 1, got {revision}.")

        if not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'register_pairwise_operational_grant' "
                "requires a valid Bearer auth_token or trusted internal context."
            )

        if auth_token and not approving_bank_id:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise PermissionError(f"Direct service authentication failed: {err}")
            approving_bank_id = str(claims.get("bank_id", "")).lower().strip()

        clean_approver = (approving_bank_id or "").lower().strip()
        if not clean_approver:
            raise PermissionError("Approving institution identity could not be established.")

        if clean_approver not in (clean_a, clean_b):
            raise PermissionError(
                f"Cross-bank authorization rejected: Approving bank '{clean_approver}' is not a participant "
                f"of pairwise grant between '{clean_a}' and '{clean_b}'. Third-party institutions cannot approve on their behalf."
            )

        attr = self._verify_service_actor_bank(
            expected_bank=clean_approver,
            consortium_id=clean_c_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="register_pairwise_operational_grant",
        )

        purposes = sorted(
            list(
                set(
                    p.strip()
                    for p in (
                        allowed_purposes
                        or ["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"]
                    )
                )
            )
        )
        jurisdictions = institution_jurisdictions or {}
        default_allowed = list(
            dict.fromkeys(
                list(jurisdictions.values())
                + ["eu-central-1", "us-east-1", "apac-singapore-1"]
            )
        )
        clean_jurisdictions = {
            k.lower().strip(): v.strip() for k, v in sorted(jurisdictions.items())
        }
        clean_allowed_jur = sorted(
            list(set(j.strip() for j in (allowed_jurisdictions or default_allowed)))
        )

        scope_digest = compute_pairwise_grant_scope_digest(
            grant_id=clean_grant_id,
            consortium_id=clean_c_id,
            institution_a=clean_a,
            institution_b=clean_b,
            revision=revision,
            allowed_purposes=purposes,
            institution_jurisdictions=clean_jurisdictions,
            allowed_jurisdictions=clean_allowed_jur,
            allow_cross_border=allow_cross_border,
            allow_institution_identity_sharing=allow_institution_identity_sharing,
        )

        if approval_signature:
            from app.domain.institutional_identity import (
                InstitutionalVerificationRegistry,
                canonical_pairwise_grant_approval_bytes,
            )

            reg = self._institutional_registry or InstitutionalVerificationRegistry()
            challenge = canonical_pairwise_grant_approval_bytes(
                consortium_id=clean_c_id,
                grant_id=clean_grant_id,
                revision=revision,
                scope_digest=scope_digest,
                approving_bank_id=clean_approver,
                approver_id=attr["actor_id"],
            )
            if not reg.verify_signature(clean_approver, approval_signature, challenge):
                raise ConsortiumSecurityError(
                    f"Cryptographic signature verification failed for pairwise grant registration by institution '{clean_approver}'."
                )

        approval_record = {
            "actor_id": attr["actor_id"],
            "actor_institution": clean_approver,
            "approved_at": datetime.datetime.now(UTC).isoformat(),
            "roles": attr["effective_role"],
            "scope_digest": scope_digest,
            "grant_revision": revision,
            "signature": approval_signature,
        }

        part_banks = sorted([clean_a, clean_b])
        grant_dict: dict[str, Any] = {
            "grant_id": clean_grant_id,
            "consortium_id": clean_c_id,
            "institution_a": part_banks[0],
            "institution_b": part_banks[1],
            "authority": clean_auth,
            "revision": revision,
            "scope_digest": scope_digest,
            "is_active": True,
            "participating_banks": part_banks,
            "allowed_purposes": purposes,
            "institution_jurisdictions": clean_jurisdictions,
            "allowed_jurisdictions": clean_allowed_jur,
            "allow_cross_border": allow_cross_border,
            "allow_institution_identity_sharing": allow_institution_identity_sharing,
            "approvals": {clean_approver: approval_record},
            "revoked_by_institutions": [],
            "revocation_history": [],
            "registered_at": datetime.datetime.now(UTC).isoformat(),
            "history": [],
        }

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_c_id not in consortia:
                raise ValueError(f"Consortium '{clean_c_id}' does not exist.")
            c = consortia[clean_c_id]
            if c.status != ConsortiumStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Consortium '{clean_c_id}' is not in ACTIVE status ({c.status.value})."
                )
            if clean_a not in c.members or clean_b not in c.members:
                missing = [b for b in (clean_a, clean_b) if b not in c.members]
                raise PermissionError(
                    f"Cannot register pairwise grant: institution(s) {missing} are not active members of consortium '{clean_c_id}'."
                )
            if clean_a in c.removed_members or clean_b in c.removed_members:
                evicted = [b for b in (clean_a, clean_b) if b in c.removed_members]
                raise PermissionError(
                    f"Cannot register pairwise grant: institution(s) {evicted} have been evicted from consortium '{clean_c_id}'."
                )

            if not hasattr(c, "metadata") or c.metadata is None:
                c.metadata = {}

            pairwise_grants = c.metadata.setdefault("pairwise_operational_grants", {})
            existing = pairwise_grants.get(clean_grant_id)

            if existing and isinstance(existing, dict):
                existing_parts = set(existing.get("participating_banks", []))
                if existing_parts != set(part_banks):
                    raise ValueError(
                        f"Pairwise grant '{clean_grant_id}' is bound to participants {sorted(existing_parts)}. "
                        f"Re-registering under different institutions {part_banks} is prohibited."
                    )

                prev_rev = int(existing.get("revision", 1))
                prev_scope_digest = existing.get("scope_digest")
                if not prev_scope_digest:
                    prev_scope_digest = compute_pairwise_grant_scope_digest(
                        grant_id=existing.get("grant_id", clean_grant_id),
                        consortium_id=clean_c_id,
                        institution_a=existing.get("institution_a", clean_a),
                        institution_b=existing.get("institution_b", clean_b),
                        revision=prev_rev,
                        allowed_purposes=existing.get("allowed_purposes", []),
                        institution_jurisdictions=existing.get("institution_jurisdictions", {}),
                        allowed_jurisdictions=existing.get("allowed_jurisdictions", []),
                        allow_cross_border=existing.get("allow_cross_border", False),
                        allow_institution_identity_sharing=existing.get(
                            "allow_institution_identity_sharing", False
                        ),
                    )
                    existing["scope_digest"] = prev_scope_digest

                if revision == prev_rev:
                    if scope_digest != prev_scope_digest:
                        raise ValueError(
                            f"Pairwise operational grant scope is immutable per revision. "
                            f"Modifications to participants, purposes, jurisdictions, or cross-border settings "
                            f"require an incremented revision. Current active revision is {prev_rev}."
                        )
                    approvals = dict(existing.get("approvals", {}))
                    approvals[clean_approver] = approval_record
                    existing["approvals"] = approvals
                    revoked = list(existing.get("revoked_by_institutions", []))
                    if clean_approver in revoked:
                        revoked.remove(clean_approver)
                    existing["revoked_by_institutions"] = revoked
                    existing["is_active"] = len(existing["revoked_by_institutions"]) == 0
                    pairwise_grants[clean_grant_id] = existing
                    return existing

                if revision <= prev_rev:
                    raise ValueError(
                        f"Pairwise grant revision must be strictly monotonically increasing. "
                        f"Current revision is {prev_rev}, attempted revision is {revision}."
                    )

                history = list(existing.get("history", []))
                prior_snapshot = {k: v for k, v in existing.items() if k != "history"}
                history.append(prior_snapshot)
                grant_dict["history"] = history
                grant_dict["approvals"] = {clean_approver: approval_record}
                pairwise_grants[clean_grant_id] = grant_dict
                return grant_dict

            grant_dict["history"] = []
            pairwise_grants[clean_grant_id] = grant_dict
            return grant_dict

        return self._store.atomic_mutate(_mutate)

    def approve_pairwise_operational_grant(
        self,
        consortium_id: str,
        grant_id: str,
        approving_bank_id: str,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
        approval_signature: str | None = None,
    ) -> dict[str, Any]:
        """Records authenticated bilateral approval from a participating institution for a pairwise grant."""
        clean_c_id = consortium_id.lower().strip()
        clean_grant_id = grant_id.strip()
        clean_bank = approving_bank_id.lower().strip()

        if not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'approve_pairwise_operational_grant' "
                "requires a valid Bearer auth_token or trusted internal context."
            )

        attr = self._verify_service_actor_bank(
            expected_bank=clean_bank,
            consortium_id=clean_c_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="approve_pairwise_operational_grant",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_c_id not in consortia:
                raise ValueError(f"Consortium '{clean_c_id}' does not exist.")
            c = consortia[clean_c_id]
            if c.status != ConsortiumStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Consortium '{clean_c_id}' is not in ACTIVE status ({c.status.value})."
                )
            if clean_bank not in c.members:
                raise PermissionError(
                    f"Approving bank '{clean_bank}' is not an active member of consortium '{clean_c_id}'."
                )
            if clean_bank in c.removed_members:
                raise PermissionError(
                    f"Approving bank '{clean_bank}' has been evicted from consortium '{clean_c_id}'."
                )

            pairwise_grants = (c.metadata or {}).get("pairwise_operational_grants", {})
            existing = pairwise_grants.get(clean_grant_id)
            if not existing or not isinstance(existing, dict):
                raise ValueError(
                    f"Pairwise operational grant '{clean_grant_id}' does not exist in consortium '{clean_c_id}'."
                )

            part_banks = existing.get("participating_banks", [])
            if clean_bank not in part_banks:
                raise PermissionError(
                    f"Institution '{clean_bank}' is not a bilateral participant of pairwise grant '{clean_grant_id}' "
                    f"({part_banks}). Third-party approval is strictly prohibited."
                )

            counterpart = next((b for b in part_banks if b != clean_bank), None)
            if counterpart and (
                counterpart not in c.members or counterpart in c.removed_members
            ):
                raise PermissionError(
                    f"Counterpart institution '{counterpart}' is not an active member of consortium '{clean_c_id}'."
                )

            rev = int(existing.get("revision", 1))
            current_scope_digest = existing.get("scope_digest")
            if not current_scope_digest:
                current_scope_digest = compute_pairwise_grant_scope_digest(
                    grant_id=existing.get("grant_id", clean_grant_id),
                    consortium_id=clean_c_id,
                    institution_a=existing.get("institution_a", part_banks[0]),
                    institution_b=existing.get(
                        "institution_b",
                        part_banks[1] if len(part_banks) > 1 else part_banks[0],
                    ),
                    revision=rev,
                    allowed_purposes=existing.get("allowed_purposes", []),
                    institution_jurisdictions=existing.get("institution_jurisdictions", {}),
                    allowed_jurisdictions=existing.get("allowed_jurisdictions", []),
                    allow_cross_border=existing.get("allow_cross_border", False),
                    allow_institution_identity_sharing=existing.get(
                        "allow_institution_identity_sharing", False
                    ),
                )
                existing["scope_digest"] = current_scope_digest

            if approval_signature:
                from app.domain.institutional_identity import (
                    InstitutionalVerificationRegistry,
                    canonical_pairwise_grant_approval_bytes,
                )

                reg = self._institutional_registry or InstitutionalVerificationRegistry()
                challenge = canonical_pairwise_grant_approval_bytes(
                    consortium_id=clean_c_id,
                    grant_id=clean_grant_id,
                    revision=rev,
                    scope_digest=current_scope_digest,
                    approving_bank_id=clean_bank,
                    approver_id=attr["actor_id"],
                )
                if not reg.verify_signature(clean_bank, approval_signature, challenge):
                    raise ConsortiumSecurityError(
                        f"Cryptographic signature verification failed for pairwise grant approval by institution '{clean_bank}'."
                    )

            approval_record = {
                "actor_id": attr["actor_id"],
                "actor_institution": clean_bank,
                "approved_at": datetime.datetime.now(UTC).isoformat(),
                "roles": attr["effective_role"],
                "scope_digest": current_scope_digest,
                "grant_revision": rev,
                "signature": approval_signature,
            }

            approvals = dict(existing.get("approvals", {}))
            approvals[clean_bank] = approval_record
            existing["approvals"] = approvals

            revoked = list(existing.get("revoked_by_institutions", []))
            if clean_bank in revoked:
                revoked.remove(clean_bank)
            existing["revoked_by_institutions"] = revoked
            existing["is_active"] = len(existing["revoked_by_institutions"]) == 0
            pairwise_grants[clean_grant_id] = existing
            return existing

        return self._store.atomic_mutate(_mutate)

    def revoke_pairwise_operational_grant(
        self,
        consortium_id: str,
        grant_id: str,
        revoking_bank_id: str | None = None,
        reason: str = "pairwise_governance_revocation",
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> dict[str, Any]:
        """Revokes an existing pairwise operational data-sharing grant under authenticated governance authority."""
        clean_c_id = consortium_id.lower().strip()
        clean_grant_id = grant_id.strip()

        if not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'revoke_pairwise_operational_grant' "
                "requires a valid Bearer auth_token or trusted internal context."
            )

        if auth_token and not revoking_bank_id:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise PermissionError(f"Direct service authentication failed: {err}")
            revoking_bank_id = str(claims.get("bank_id", "")).lower().strip()

        clean_revoker = (revoking_bank_id or "").lower().strip()
        attr = self._verify_service_actor_bank(
            expected_bank=clean_revoker,
            consortium_id=clean_c_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="revoke_pairwise_operational_grant",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, Any]) -> dict[str, Any]:
            if clean_c_id not in consortia:
                raise ValueError(f"Consortium '{clean_c_id}' does not exist.")
            c = consortia[clean_c_id]
            pairwise_grants = (c.metadata or {}).get("pairwise_operational_grants", {})
            existing = pairwise_grants.get(clean_grant_id)
            if not existing or not isinstance(existing, dict):
                raise ValueError(
                    f"Pairwise grant '{clean_grant_id}' does not exist in consortium '{clean_c_id}'."
                )

            part_banks = existing.get("participating_banks", [])
            if clean_revoker not in part_banks and clean_revoker != c.founder_bank_id:
                raise PermissionError(
                    f"Revoking bank '{clean_revoker}' is not a participant of pairwise grant '{clean_grant_id}'."
                )

            history = list(existing.get("history", []))
            prior_snapshot = {k: v for k, v in existing.items() if k != "history"}
            history.append(prior_snapshot)

            revoked_list = list(existing.get("revoked_by_institutions", []))
            if clean_revoker not in revoked_list:
                revoked_list.append(clean_revoker)
            existing["revoked_by_institutions"] = sorted(revoked_list)

            revocations = list(existing.get("revocation_history", []))
            revocations.append({
                "revoked_by_actor": attr["actor_id"],
                "revoked_by_institution": clean_revoker,
                "reason": reason.strip(),
                "revoked_at": datetime.datetime.now(UTC).isoformat(),
            })
            existing["revocation_history"] = revocations
            existing["is_active"] = False
            existing["revoked_at"] = datetime.datetime.now(UTC).isoformat()
            existing["revocation_reason"] = reason.strip()
            existing["revoked_by"] = attr["actor_id"]
            existing["history"] = history

            pairwise_grants[clean_grant_id] = existing
            return existing

        return self._store.atomic_mutate(_mutate)

    def get_pairwise_operational_grant(
        self, consortium_id: str, grant_id: str
    ) -> dict[str, Any] | None:
        """Retrieves a specific pairwise operational grant by ID."""
        clean_c_id = consortium_id.lower().strip()
        clean_grant_id = grant_id.strip()
        c = self._store.get_consortium(clean_c_id)
        if not c:
            return None
        return c.get_pairwise_operational_grant(clean_grant_id)

    def list_pairwise_operational_grants(
        self, consortium_id: str, bank_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lists pairwise operational grants in a consortium, optionally filtered by participating institution."""
        clean_c_id = consortium_id.lower().strip()
        c = self._store.get_consortium(clean_c_id)
        if not c:
            return []
        grants = list(c.pairwise_operational_grants.values())
        if bank_id:
            clean_bank = bank_id.lower().strip()
            return [
                g
                for g in grants
                if isinstance(g, dict) and clean_bank in g.get("participating_banks", [])
            ]
        return grants

    def resolve_pairwise_authorization(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        purpose: str,
        requesting_jurisdiction: str | None = None,
        counterpart_jurisdiction: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Evaluates whether data sharing between two institutions is authorized.

        Enforces:
        - Active consortium status and active membership of both institutions.
        - Fails closed if either institution has been evicted/removed.
        - Exactly two distinct institutions.
        - Authoritative pairwise grant resolution (or legacy grant fallback).
        - Mandatory bilateral approvals bound to current revision and scope digest.
        - Fails closed for revoked grants or grants revoked by either institution.
        - Permitted purpose validation.
        - Jurisdiction compliance and cross-border sovereignty treaties.
        """
        clean_c_id = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_counterpart = counterpart_bank_id.lower().strip()
        clean_purpose = purpose.strip()

        if not clean_req or not clean_counterpart:
            return False, "Requesting and counterpart institution IDs must be non-empty.", None
        if clean_req == clean_counterpart:
            return (
                False,
                f"Pairwise authorization requires two distinct institutions, got '{clean_req}'.",
                None,
            )

        c = self._store.get_consortium(clean_c_id)
        if not c:
            return False, f"Consortium '{clean_c_id}' does not exist.", None
        if c.status != ConsortiumStatus.ACTIVE:
            return (
                False,
                f"Consortium '{clean_c_id}' is in non-active status '{c.status.value}'. Operational queries are prohibited.",
                None,
            )

        # Check membership and evictions
        if clean_req in c.removed_members:
            return (
                False,
                f"Requesting institution '{clean_req}' has been evicted from consortium '{clean_c_id}'.",
                None,
            )
        if clean_counterpart in c.removed_members:
            return (
                False,
                f"Counterpart institution '{clean_counterpart}' has been evicted from consortium '{clean_c_id}'.",
                None,
            )
        if clean_req not in c.members:
            return (
                False,
                f"Requesting institution '{clean_req}' is not an active member of consortium '{clean_c_id}'.",
                None,
            )
        if clean_counterpart not in c.members:
            return (
                False,
                f"Counterpart institution '{clean_counterpart}' is not an active member of consortium '{clean_c_id}'.",
                None,
            )

        # Find matching pairwise grant
        grant = c.find_pairwise_operational_grant(clean_req, clean_counterpart)
        if not grant:
            legacy = (c.metadata or {}).get("operational_data_sharing_grant")
            if legacy and isinstance(legacy, dict):
                legacy_parts = {
                    b.lower().strip()
                    for b in legacy.get("participating_banks", [])
                    if str(b).strip()
                }
                if len(legacy_parts) == 2 and legacy_parts == {clean_req, clean_counterpart}:
                    grant = legacy

            if not grant:
                return (
                    False,
                    f"No authorized operational data-sharing grant exists between institutions '{clean_req}' and '{clean_counterpart}' in consortium '{clean_c_id}'.",
                    None,
                )

        grant_id = grant.get("grant_id", "unknown")
        if not grant.get("is_active", True):
            return False, f"Operational grant '{grant_id}' is inactive or revoked.", grant

        revoked_institutions = set(grant.get("revoked_by_institutions", []))
        if clean_req in revoked_institutions:
            return (
                False,
                f"Operational consent under grant '{grant_id}' was revoked by requesting institution '{clean_req}'.",
                grant,
            )
        if clean_counterpart in revoked_institutions:
            return (
                False,
                f"Operational consent under grant '{grant_id}' was revoked by counterpart institution '{clean_counterpart}'.",
                grant,
            )

        current_rev = int(grant.get("revision", 1))
        if expected_revision is not None and expected_revision != current_rev:
            return (
                False,
                f"Grant revision mismatch for '{grant_id}': expected revision {expected_revision}, active is {current_rev}.",
                grant,
            )

        current_scope_digest = grant.get("scope_digest")
        approvals = grant.get("approvals", {})

        req_approval = approvals.get(clean_req)
        if not req_approval or not isinstance(req_approval, dict):
            return (
                False,
                f"Requesting institution '{clean_req}' has not approved operational grant '{grant_id}'.",
                grant,
            )
        if req_approval.get("grant_revision") != current_rev:
            return (
                False,
                f"Approval by requesting institution '{clean_req}' is stale for operational grant '{grant_id}' (approved revision {req_approval.get('grant_revision')}, current is {current_rev}).",
                grant,
            )
        if current_scope_digest and req_approval.get("scope_digest") != current_scope_digest:
            return (
                False,
                f"Approval by requesting institution '{clean_req}' is stale for operational grant '{grant_id}' (approved digest '{req_approval.get('scope_digest')}', current is '{current_scope_digest}').",
                grant,
            )

        counterpart_approval = approvals.get(clean_counterpart)
        if not counterpart_approval or not isinstance(counterpart_approval, dict):
            return (
                False,
                f"Counterpart institution '{clean_counterpart}' has not approved operational grant '{grant_id}'. Unilateral approval does not constitute bilateral consent.",
                grant,
            )
        if counterpart_approval.get("grant_revision") != current_rev:
            return (
                False,
                f"Approval by counterpart institution '{clean_counterpart}' is stale for operational grant '{grant_id}' (approved revision {counterpart_approval.get('grant_revision')}, current is {current_rev}).",
                grant,
            )
        if current_scope_digest and counterpart_approval.get("scope_digest") != current_scope_digest:
            return (
                False,
                f"Approval by counterpart institution '{clean_counterpart}' is stale for operational grant '{grant_id}' (approved digest '{counterpart_approval.get('scope_digest')}', current is '{current_scope_digest}').",
                grant,
            )

        # Purpose check
        allowed_purposes = grant.get("allowed_purposes", [])
        if clean_purpose not in allowed_purposes:
            return (
                False,
                f"Purpose '{clean_purpose}' is not authorized under operational grant '{grant_id}'. Permitted: {allowed_purposes}.",
                grant,
            )

        # Jurisdiction & Sovereignty
        inst_jurisdictions = grant.get("institution_jurisdictions", {})
        allowed_jurisdictions = grant.get("allowed_jurisdictions", [])
        allow_cross_border = bool(grant.get("allow_cross_border", False))

        req_jur = requesting_jurisdiction or inst_jurisdictions.get(clean_req)
        counterpart_jur = counterpart_jurisdiction or inst_jurisdictions.get(clean_counterpart)

        if not req_jur:
            return (
                False,
                f"Authoritative jurisdiction for requesting institution '{clean_req}' is not registered and was not supplied. Denied by default.",
                grant,
            )
        if req_jur not in allowed_jurisdictions:
            return (
                False,
                f"Requesting institution operates in unapproved jurisdiction '{req_jur}' (Allowed: {allowed_jurisdictions}).",
                grant,
            )

        if not counterpart_jur:
            return (
                False,
                f"Authoritative jurisdiction for counterpart institution '{clean_counterpart}' is not registered and was not supplied. Denied by default.",
                grant,
            )
        if counterpart_jur not in allowed_jurisdictions:
            return (
                False,
                f"Counterpart institution operates in unapproved jurisdiction '{counterpart_jur}' (Allowed: {allowed_jurisdictions}).",
                grant,
            )

        if req_jur != counterpart_jur and not allow_cross_border:
            return (
                False,
                f"Cross-border intelligence disclosure between '{req_jur}' and '{counterpart_jur}' is prohibited without explicit bilateral cross-border consent.",
                grant,
            )

        return True, "Pairwise authorization verified.", grant

    def check_pairwise_authorization(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        purpose: str,
        requesting_jurisdiction: str | None = None,
        counterpart_jurisdiction: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Fails closed with ConsortiumSecurityError if pairwise authorization is not verified."""
        is_auth, reason, grant = self.resolve_pairwise_authorization(
            consortium_id=consortium_id,
            requesting_bank_id=requesting_bank_id,
            counterpart_bank_id=counterpart_bank_id,
            purpose=purpose,
            requesting_jurisdiction=requesting_jurisdiction,
            counterpart_jurisdiction=counterpart_jurisdiction,
            expected_revision=expected_revision,
        )
        if not is_auth:
            raise ConsortiumSecurityError(reason)
        assert grant is not None
        return grant

    def propose_membership_change(
        self,
        consortium_id: str,
        creator_bank_id: str,
        target_bank_id: str,
        action: ProposalAction = ProposalAction.ADD_MEMBER,
        ttl_seconds: int = 86400,
        metadata: dict[str, Any] | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Creates a membership voting proposal (e.g. ADD_MEMBER or REMOVE_MEMBER)."""
        clean_id = consortium_id.lower().strip()
        clean_creator = creator_bank_id.lower().strip()
        clean_target = target_bank_id.lower().strip()

        attr = self._verify_service_actor_bank(
            clean_creator,
            consortium_id=clean_id,
            target_bank_id=clean_target,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="propose_membership_change",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
            if clean_creator not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_creator}' is not a member of consortium '{clean_id}'."
                )

            creator_member = consortium.members[clean_creator]
            if not creator_member.can_vote:
                raise ValueError(
                    f"Bank '{clean_creator}' has role {creator_member.role.value} or 0 voting power and cannot sponsor proposals."
                )

            # Defensive domain invariants for proposal actions
            if action == ProposalAction.ADD_MEMBER and clean_target in consortium.members:
                raise ValueError(
                    f"Bank '{clean_target}' is already a member of consortium '{clean_id}'."
                )
            if action == ProposalAction.REMOVE_MEMBER and clean_target not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_target}' is not a member of consortium '{clean_id}'."
                )

            proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
            proposal = MembershipProposal(
                proposal_id=proposal_id,
                consortium_id=clean_id,
                creator_bank_id=clean_creator,
                target_bank_id=clean_target,
                action=action,
                required_quorum_ratio=consortium.quorum_ratio,
                ttl_seconds=ttl_seconds,
                metadata=metadata or {},
            )

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_PROPOSAL_CREATED",
                actor_id=attr["actor_id"],
                action=f"propose_membership_change:{proposal_id}:{action.value}",
                resource_id=f"consortium:{clean_id}:proposal:{proposal_id}",
                details={
                    "proposal_id": proposal_id,
                    "consortium_id": clean_id,
                    "creator_bank_id": clean_creator,
                    "target_bank_id": clean_target,
                    "action": action.value,
                    "actor_type": attr["actor_type"],
                    "actor_institution": attr["actor_institution"],
                },
            )

            # Creator automatically votes FOR the proposal
            proposal.votes_for.add(clean_creator)
            proposals[proposal_id] = proposal

            logger.info(
                "Opened governance proposal %s in consortium %s for bank %s (action=%s)",
                proposal_id,
                clean_id,
                clean_target,
                action.value,
            )
            self._evaluate_proposal_quorum_in_memory(consortium, proposal)
            return proposal

        return self._store.atomic_mutate(_mutate)

    def propose_policy_update(
        self,
        consortium_id: str,
        creator_bank_id: str,
        policy_updates: dict[str, Any],
        ttl_seconds: int = 86400,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Creates a policy modification voting proposal (e.g. updating quorum ratio, max epsilon, min members)."""
        clean_id = consortium_id.lower().strip()
        clean_creator = creator_bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_creator,
            consortium_id=clean_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="propose_policy_update",
        )

        c = self._store.get_consortium(clean_id)
        if c is None:
            raise KeyError(f"Consortium '{clean_id}' does not exist.")

        if not policy_updates:
            raise ValueError("policy_updates cannot be empty.")

        # Validate proposed policy fields
        valid_keys = {"new_quorum_ratio", "new_max_epsilon", "new_min_members_n", "new_status"}
        unknown_keys = set(policy_updates.keys()) - valid_keys
        if unknown_keys:
            raise ValueError(f"Unsupported policy update keys: {sorted(unknown_keys)}")

        if "new_quorum_ratio" in policy_updates:
            q = float(policy_updates["new_quorum_ratio"])
            if not (0.0 < q <= 1.0) or not math.isfinite(q):
                raise ValueError(f"new_quorum_ratio must be in (0.0, 1.0], got {q}")

        if "new_max_epsilon" in policy_updates:
            eps = float(policy_updates["new_max_epsilon"])
            if eps <= 0.0 or not math.isfinite(eps):
                raise ValueError(f"new_max_epsilon must be positive finite number, got {eps}")

        if "new_min_members_n" in policy_updates:
            n = int(policy_updates["new_min_members_n"])
            if n < 1:
                raise ValueError(f"new_min_members_n must be >= 1, got {n}")

        return self.propose_membership_change(
            consortium_id=clean_id,
            creator_bank_id=clean_creator,
            target_bank_id="CONSORTIUM_POLICY",
            action=ProposalAction.UPDATE_POLICY,
            ttl_seconds=ttl_seconds,
            metadata=policy_updates,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
        )

    def cast_vote(
        self,
        proposal_id: str,
        bank_id: str,
        approve: bool,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Casts a vote FOR or AGAINST an active proposal with weighted voting power."""
        clean_prop_id = proposal_id.strip()
        clean_bank_id = bank_id.lower().strip()

        prop = self._store.get_proposal(clean_prop_id)
        prop_consortium_id = prop.consortium_id if prop else None
        attr = self._verify_service_actor_bank(
            clean_bank_id,
            consortium_id=prop_consortium_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="cast_vote",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_prop_id not in proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = proposals[clean_prop_id]
            if proposal.consortium_id not in consortia:
                raise KeyError(f"Consortium '{proposal.consortium_id}' does not exist.")
            consortium = consortia[proposal.consortium_id]

            if clean_bank_id not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_bank_id}' is not a member of consortium '{proposal.consortium_id}'."
                )

            member = consortium.members[clean_bank_id]
            if not member.can_vote:
                raise ValueError(
                    f"Bank '{clean_bank_id}' has role {member.role.value} with voting power {member.voting_power} and cannot cast votes."
                )

            # Lazy expiration check
            if proposal.is_expired():
                proposal.status = ProposalStatus.EXPIRED
                raise ValueError(f"Proposal '{clean_prop_id}' has EXPIRED and is closed for voting.")

            if proposal.status != ProposalStatus.PENDING:
                raise ValueError(f"Proposal '{clean_prop_id}' is already {proposal.status.value}.")

            if approve:
                proposal.votes_for.add(clean_bank_id)
                proposal.votes_against.discard(clean_bank_id)
            else:
                proposal.votes_against.add(clean_bank_id)
                proposal.votes_for.discard(clean_bank_id)

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_VOTE_CAST",
                actor_id=attr["actor_id"],
                action=f"cast_vote:{clean_prop_id}:{'FOR' if approve else 'AGAINST'}",
                resource_id=f"consortium:{proposal.consortium_id}:proposal:{clean_prop_id}",
                details={
                    "proposal_id": clean_prop_id,
                    "consortium_id": proposal.consortium_id,
                    "bank_id": clean_bank_id,
                    "approve": approve,
                    "voting_power": member.voting_power,
                    "actor_type": attr["actor_type"],
                    "actor_institution": attr["actor_institution"],
                },
            )

            logger.info(
                "Bank %s voted %s on proposal %s (voting_power=%.2f)",
                clean_bank_id,
                "FOR" if approve else "AGAINST",
                clean_prop_id,
                member.voting_power,
            )
            self._evaluate_proposal_quorum_in_memory(consortium, proposal)
            return proposal

        return self._store.atomic_mutate(_mutate)

    def cancel_proposal(
        self,
        proposal_id: str,
        creator_bank_id: str,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Allows the sponsoring bank to withdraw/cancel a pending proposal before resolution."""
        clean_prop_id = proposal_id.strip()
        clean_creator = creator_bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_creator,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="cancel_proposal",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_prop_id not in proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = proposals[clean_prop_id]
            if proposal.status != ProposalStatus.PENDING:
                raise ValueError(
                    f"Cannot cancel proposal '{clean_prop_id}' in state {proposal.status.value}."
                )

            if proposal.creator_bank_id != clean_creator:
                raise ValueError(
                    f"Only the sponsoring bank '{proposal.creator_bank_id}' can cancel proposal '{clean_prop_id}'."
                )

            proposal.status = ProposalStatus.CANCELLED
            proposal.cancelled_at = datetime.datetime.now(UTC)
            logger.info(
                "Proposal %s was CANCELLED by sponsoring bank %s",
                clean_prop_id,
                clean_creator,
            )
            return proposal

        return self._store.atomic_mutate(_mutate)

    def set_member_voting_power(
        self,
        consortium_id: str,
        bank_id: str,
        voting_power: float,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> ConsortiumMember:
        """Updates a member institution's voting weight in the consortium."""
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        if not math.isfinite(voting_power) or voting_power < 0.0:
            raise ValueError(f"voting_power must be non-negative finite number, got {voting_power}")

        is_prod = getattr(self._store, "_is_prod", False)
        if is_prod and not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'set_member_voting_power' requires a valid Bearer auth_token or trusted internal context."
            )

        if trusted_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action="set_member_voting_power",
                expected_service_name="consortium_coordinator",
                expected_resource_type="consortium",
                expected_resource_id=clean_id,
                expected_target_bank_id=clean_bank,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise PermissionError(f"Internal orchestration authority rejected for set_member_voting_power: {ctx_reason}")
            if getattr(trusted_context, "service_name", "") != "consortium_coordinator":
                raise PermissionError(
                    f"Internal service '{getattr(trusted_context, 'service_name', '')}' is not authorized for set_member_voting_power."
                )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
            if clean_bank not in consortium.members:
                raise ValueError(f"Bank '{clean_bank}' is not a member of consortium '{clean_id}'.")

            if is_prod and auth_token:
                founder_id = consortium.founder_bank_id
                if not founder_id:
                    raise PermissionError(
                        f"Consortium '{clean_id}' has no designated founder member; direct mutation rejected."
                    )
                self._verify_service_actor_bank(
                    founder_id,
                    auth_token=auth_token,
                    actor=actor,
                    trusted_context=None,
                    action_name="set_member_voting_power",
                )

            consortium.members[clean_bank].voting_power = voting_power
            logger.info(
                "Updated voting power for bank %s in consortium %s to %.2f",
                clean_bank,
                clean_id,
                voting_power,
            )

            # Re-evaluate all pending proposals under the new voting power distribution
            for prop in proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum_in_memory(consortium, prop)

            return consortium.members[clean_bank]

        return self._store.atomic_mutate(_mutate)

    def set_member_role(
        self,
        consortium_id: str,
        bank_id: str,
        role: MemberRole,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> ConsortiumMember:
        """Updates a member institution's governance role in the consortium."""
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        is_prod = getattr(self._store, "_is_prod", False)
        if is_prod and not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'set_member_role' requires a valid Bearer auth_token or trusted internal context."
            )

        if trusted_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action="set_member_role",
                expected_service_name="consortium_coordinator",
                expected_resource_type="consortium",
                expected_resource_id=clean_id,
                expected_target_bank_id=clean_bank,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise PermissionError(f"Internal orchestration authority rejected for set_member_role: {ctx_reason}")
            if getattr(trusted_context, "service_name", "") != "consortium_coordinator":
                raise PermissionError(
                    f"Internal service '{getattr(trusted_context, 'service_name', '')}' is not authorized for set_member_role."
                )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
            if clean_bank not in consortium.members:
                raise ValueError(f"Bank '{clean_bank}' is not a member of consortium '{clean_id}'.")

            if is_prod and auth_token:
                founder_id = consortium.founder_bank_id
                if not founder_id:
                    raise PermissionError(
                        f"Consortium '{clean_id}' has no designated founder member; direct mutation rejected."
                    )
                self._verify_service_actor_bank(
                    founder_id,
                    auth_token=auth_token,
                    actor=actor,
                    trusted_context=None,
                    action_name="set_member_role",
                )

            target_role = MemberRole(role.upper().strip()) if isinstance(role, str) else role
            consortium.members[clean_bank].role = target_role
            logger.info(
                "Updated role for bank %s in consortium %s to %s",
                clean_bank,
                clean_id,
                target_role.value,
            )

            # Re-evaluate all pending proposals
            for prop in proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum_in_memory(consortium, prop)

            return consortium.members[clean_bank]

        return self._store.atomic_mutate(_mutate)

    def check_expired_proposals(self, consortium_id: str | None = None) -> list[str]:
        """Sweeps proposals and marks expired ones whose voting window has closed."""
        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> list[str]:
            expired_ids: list[str] = []
            now = datetime.datetime.now(UTC)

            for prop in proposals.values():
                if consortium_id and prop.consortium_id != consortium_id.lower().strip():
                    continue
                if prop.status == ProposalStatus.PENDING and prop.is_expired(now):
                    prop.status = ProposalStatus.EXPIRED
                    expired_ids.append(prop.proposal_id)
                    logger.info("Proposal %s expired after TTL window.", prop.proposal_id)

            return expired_ids

        return self._store.atomic_mutate(_mutate)

    def _evaluate_proposal_quorum_in_memory(self, consortium: Consortium, proposal: MembershipProposal) -> None:
        """Evaluates weighted voting quorum ratio and executes proposal action if passed (called within mutation lock)."""
        if proposal.is_expired():
            proposal.status = ProposalStatus.EXPIRED
            return

        eligible_members = [m for m in consortium.members.values() if m.can_vote]

        total_voting_power = sum(m.voting_power for m in eligible_members)
        if total_voting_power <= 0.0:
            total_voting_power = max(float(len(eligible_members)), 1.0)

        power_for = sum(
            consortium.members[b].voting_power
            for b in proposal.votes_for
            if b in consortium.members and consortium.members[b].can_vote
        )
        power_against = sum(
            consortium.members[b].voting_power
            for b in proposal.votes_against
            if b in consortium.members and consortium.members[b].can_vote
        )

        ratio_for = power_for / total_voting_power
        ratio_against = power_against / total_voting_power

        if ratio_for >= proposal.required_quorum_ratio:
            proposal.status = ProposalStatus.APPROVED
            self._execute_proposal_action_in_memory(consortium, proposal)
            logger.info(
                "Proposal %s APPROVED (weighted ratio: %.3f >= %.3f)",
                proposal.proposal_id,
                ratio_for,
                proposal.required_quorum_ratio,
            )
        elif ratio_against > (1.0 - proposal.required_quorum_ratio):
            proposal.status = ProposalStatus.REJECTED
            proposal.rejection_reason = (
                f"Against votes (ratio {ratio_against:.3f}) prevent reaching required quorum ratio {proposal.required_quorum_ratio:.3f}"
            )
            logger.info("Proposal %s REJECTED: %s", proposal.proposal_id, proposal.rejection_reason)

    def _execute_proposal_action_in_memory(self, consortium: Consortium, proposal: MembershipProposal) -> None:
        """Applies approved proposal actions to consortium state (called within mutation lock)."""
        target = proposal.target_bank_id
        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

        if proposal.action == ProposalAction.ADD_MEMBER and target not in consortium.members:
            assigned_role = proposal.metadata.get("role", MemberRole.FULL_MEMBER)
            if isinstance(assigned_role, str):
                assigned_role = MemberRole(assigned_role)
            assigned_power = float(proposal.metadata.get("voting_power", 1.0))

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_MEMBER_ADMITTED",
                actor_id=f"governance:proposal:{proposal.proposal_id}",
                action=f"admit_member:{target}",
                resource_id=f"consortium:{consortium.consortium_id}:member:{target}",
                details={
                    "consortium_id": consortium.consortium_id,
                    "target_bank_id": target,
                    "proposal_id": proposal.proposal_id,
                    "assigned_role": assigned_role.value if hasattr(assigned_role, "value") else str(assigned_role),
                    "voting_power": assigned_power,
                    "approved_by_votes": sorted(proposal.votes_for),
                },
            )

            consortium.members[target] = ConsortiumMember(
                bank_id=target,
                role=assigned_role,
                voting_power=assigned_power,
            )
            consortium.removed_members.pop(target, None)
            logger.info("Bank %s joined consortium %s (role=%s, power=%.2f)", target, consortium.consortium_id, assigned_role.value, assigned_power)

        elif proposal.action == ProposalAction.REMOVE_MEMBER and target in consortium.members:
            reason_text = f"Expelled via approved governance proposal {proposal.proposal_id}"

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_MEMBER_EXPELLED",
                actor_id=f"governance:proposal:{proposal.proposal_id}",
                action=f"expel_member:{target}",
                resource_id=f"consortium:{consortium.consortium_id}:member:{target}",
                details={
                    "consortium_id": consortium.consortium_id,
                    "target_bank_id": target,
                    "proposal_id": proposal.proposal_id,
                    "reason": reason_text,
                    "approved_by_votes": sorted(proposal.votes_for),
                    "opposed_by_votes": sorted(proposal.votes_against),
                },
            )

            del consortium.members[target]
            consortium.removed_members[target] = {
                "bank_id": target,
                "removed_at": datetime.datetime.now(UTC).isoformat(),
                "proposal_id": proposal.proposal_id,
                "reason": reason_text,
            }
            logger.info("Bank %s evicted from consortium %s", target, consortium.consortium_id)

        elif proposal.action == ProposalAction.UPDATE_POLICY:
            meta = proposal.metadata
            if "new_quorum_ratio" in meta:
                consortium.quorum_ratio = float(meta["new_quorum_ratio"])
                logger.info("Consortium %s quorum ratio updated to %.2f", consortium.consortium_id, consortium.quorum_ratio)
            if "new_max_epsilon" in meta:
                consortium.max_epsilon = float(meta["new_max_epsilon"])
                logger.info("Consortium %s max epsilon updated to %.2f", consortium.consortium_id, consortium.max_epsilon)
            if "new_min_members_n" in meta:
                consortium.min_members_n = int(meta["new_min_members_n"])
                logger.info("Consortium %s min members updated to %d", consortium.consortium_id, consortium.min_members_n)
            if "new_status" in meta:
                new_st = meta["new_status"]
                if isinstance(new_st, str):
                    new_st = ConsortiumStatus(new_st)
                consortium.status = new_st
                logger.info("Consortium %s status updated to %s", consortium.consortium_id, consortium.status.value)

            ImmutableAuditChain.get_instance().append_event(
                event_type="CONSORTIUM_POLICY_UPDATED",
                actor_id=f"governance:proposal:{proposal.proposal_id}",
                action=f"update_policy:{consortium.consortium_id}",
                resource_id=f"consortium:{consortium.consortium_id}",
                details={
                    "consortium_id": consortium.consortium_id,
                    "proposal_id": proposal.proposal_id,
                    "policy_updates": proposal.metadata,
                    "approved_by_votes": sorted(proposal.votes_for),
                },
            )

    def get_consortium(self, consortium_id: str) -> Consortium | None:
        """Retrieves consortium by ID after validating disk freshness."""
        res = self._store.get_consortium(consortium_id)
        if res is None and consortium_id == "cfi-consortium":
            is_prod = getattr(self._store, "_is_prod", False)
            if not is_prod:
                return init_default_consortium(self)
        return res

    def get_proposal(self, proposal_id: str) -> MembershipProposal | None:
        """Retrieves a proposal by ID and updates expiration if elapsed."""
        proposal = self._store.get_proposal(proposal_id)
        if proposal and proposal.status == ProposalStatus.PENDING and proposal.is_expired():
            def _expire(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> None:
                if proposal_id in proposals and proposals[proposal_id].status == ProposalStatus.PENDING:
                    proposals[proposal_id].status = ProposalStatus.EXPIRED
            self._store.atomic_mutate(_expire)
            proposal.status = ProposalStatus.EXPIRED
        return proposal

    def list_proposals(
        self, consortium_id: str, status: ProposalStatus | None = None
    ) -> list[MembershipProposal]:
        """Lists proposals for a given consortium, optionally filtered by status."""
        return self._store.list_proposals(consortium_id, status=status)

    def list_members(self, consortium_id: str) -> list[ConsortiumMember]:
        """Lists all registered members of a consortium."""
        c = self._store.get_consortium(consortium_id)
        if not c:
            return []
        return list(c.members.values())

    def add_member(
        self,
        consortium_id: str,
        bank_id: str,
        role: MemberRole = MemberRole.FULL_MEMBER,
        voting_power: float = 1.0,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> ConsortiumMember:
        """Registers a member institution into a consortium.

        Guarantees:
        - In production, requires verified coordinator context or legitimate founder provisioning.
        - Ordinary institutional credentials cannot unilaterally bypass proposal voting.
        - Rejects unverified internal context objects.
        - Rejects direct readmission of an expelled member without governance approval.
        """
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        is_prod = getattr(self._store, "_is_prod", False)

        if trusted_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action="add_member",
                expected_service_name="consortium_coordinator",
                expected_resource_type="consortium",
                expected_resource_id=clean_id,
                expected_target_bank_id=clean_bank,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise PermissionError(f"Internal orchestration authority rejected for add_member: {ctx_reason}")
            if getattr(trusted_context, "service_name", "") != "consortium_coordinator":
                raise PermissionError(
                    f"Internal service '{getattr(trusted_context, 'service_name', '')}' is not authorized for add_member."
                )
        elif is_prod:
            if not auth_token:
                raise PermissionError(
                    "Authentication required: Direct service operation 'add_member' requires a valid Bearer auth_token or trusted internal context."
                )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]

            # In production without trusted coordinator context, verify if caller has founder provisioning authority
            if is_prod and trusted_context is None and auth_token:
                from app.infrastructure.security.auth_service import AuthenticationService

                auth_svc = AuthenticationService.get_instance()
                is_valid, claims, err = auth_svc.verify_access_token(auth_token)
                if not is_valid or not claims:
                    raise PermissionError(f"Direct service authentication failed: {err}")
                username = (claims.get("username") or claims.get("sub") or "").strip()
                if actor and actor.lower().strip() != username.lower():
                    raise PermissionError(f"Actor '{actor}' does not match authenticated token principal '{username}'.")
                user_rec = auth_svc.get_user(username)
                if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
                    raise PermissionError(f"Direct service actor '{username}' is not a valid authoritative principal.")
                user_bank = (getattr(user_rec, "bank_id", "") or "").lower().strip()
                user_roles = {r.lower().strip() for r in getattr(user_rec, "roles", [])}
                if not (user_roles & {"admin", "ml_lead", "compliance"}):
                    raise PermissionError(f"Principal '{username}' lacks required governance role for add_member.")

                # If the caller bank is not the founder, ordinary members cannot unilaterally bypass quorum
                if user_bank != consortium.founder_bank_id:
                    raise PermissionError(
                        "Direct admission rejected: Ordinary institutional credentials cannot bypass "
                        "consortium membership proposal and voting quorum requirements."
                    )

            # Prevent silent readmission of an expelled member
            if clean_bank in consortium.removed_members:
                raise PermissionError(
                    f"Direct admission rejected: Institution '{clean_bank}' was previously expelled from consortium '{clean_id}'. "
                    f"Re-admission requires a formal governance proposal and voting quorum."
                )

            member = ConsortiumMember(bank_id=clean_bank, role=role, voting_power=voting_power)
            consortium.members[clean_bank] = member
            return member

        return self._store.atomic_mutate(_mutate)

    def reset(self) -> None:
        """Resets service state for clean test isolation."""
        with self._lock:
            self._store.clear()
            is_prod = getattr(self._store, "_is_prod", False)
            if not is_prod:
                init_default_consortium(self)


def init_default_consortium(
    service: ConsortiumGovernanceService | None = None,
    allow_bootstrap: bool = False,
) -> Consortium:
    """Initializes canonical consortium with founding and participant institutions.

    Guarantees:
    - In production, requires explicit bootstrap authorization (allow_bootstrap=True or CFI_ALLOW_CONSORTIUM_BOOTSTRAP="1").
    - Never silently fabricates institutional memberships in fresh production environments.
    - Never recreates or overwrites an established consortium.
    - Never readmits previously removed/expelled members upon worker startup.
    """
    svc = service or consortium_governance_service
    is_prod = getattr(svc._store, "_is_prod", False)

    existing = svc._store.get_consortium("cfi-consortium")
    if existing is not None:
        return existing

    explicit_bootstrap_allowed = (
        allow_bootstrap
        or os.environ.get("CFI_ALLOW_CONSORTIUM_BOOTSTRAP") == "1"
    )
    if is_prod and not explicit_bootstrap_allowed:
        raise ConsortiumSecurityError(
            "Production consortium bootstrap rejected: unprovisioned production environment "
            "cannot automatically fabricate default institutional memberships. Explicit bootstrap "
            "authorization (CFI_ALLOW_CONSORTIUM_BOOTSTRAP=1) is required."
        )

    from app.application.services.model_registry import create_trusted_internal_context

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="create_consortium",
        resource_type="consortium",
        resource_id="cfi-consortium",
        bank_id="bank_a",
    )
    c = svc.create_consortium(
        consortium_id="cfi-consortium",
        name="Cross-Bank Federated Intelligence Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.51,
        max_epsilon=5.0,
        min_members_n=2,
        trusted_context=ctx,
    )
    ctx_add_b = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id="cfi-consortium",
        target_bank_id="bank_b",
    )
    svc.add_member("cfi-consortium", "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0, trusted_context=ctx_add_b)
    ctx_add_c = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id="cfi-consortium",
        target_bank_id="bank_c",
    )
    svc.add_member("cfi-consortium", "bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0, trusted_context=ctx_add_c)
    return c


# Global singleton instance
consortium_governance_service = ConsortiumGovernanceService()
if not getattr(consortium_governance_service._store, "_is_prod", False):
    init_default_consortium(consortium_governance_service)


