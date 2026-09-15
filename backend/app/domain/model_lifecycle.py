# ruff: noqa: UP042
"""Domain models for Multi-Stage Production Model State Machine."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ModelState(str, Enum):
    """Production progression states for a registered model checkpoint."""

    STAGING = "STAGING"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal model state transition is attempted."""

    pass


# Allowed state transition mapping
ALLOWED_TRANSITIONS: dict[ModelState, set[ModelState]] = {
    ModelState.STAGING: {ModelState.SHADOW, ModelState.ARCHIVED},
    ModelState.SHADOW: {ModelState.CANARY, ModelState.ARCHIVED},
    ModelState.CANARY: {ModelState.PRODUCTION, ModelState.SHADOW, ModelState.ARCHIVED},
    ModelState.PRODUCTION: {ModelState.ARCHIVED, ModelState.CANARY},
    ModelState.ARCHIVED: {ModelState.PRODUCTION, ModelState.CANARY, ModelState.STAGING},
}


@dataclass
class ModelLifecycleRecord:
    """Record container tracking a model version's production state machine trajectory."""

    model_version: str
    current_state: ModelState = ModelState.STAGING
    compliance_signoff: bool = False
    state_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.state_history:
            self.state_history.append(
                {
                    "from_state": None,
                    "to_state": self.current_state.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor_role": "SYSTEM",
                    "reason": "Initialized in STAGING",
                }
            )


class ModelLifecycleManager:
    """Manages progression of model checkpoints through progressive production stages."""

    def __init__(self) -> None:
        self._models: dict[str, ModelLifecycleRecord] = {}
        self._lock = threading.RLock()

    def register_model(self, model_version: str) -> ModelLifecycleRecord:
        """Initializes a new model checkpoint version in STAGING state."""
        clean_version = model_version.strip()
        with self._lock:
            if clean_version in self._models:
                return self._models[clean_version]

            record = ModelLifecycleRecord(model_version=clean_version)
            self._models[clean_version] = record
            logger.info("Registered model version '%s' in STAGING", clean_version)
            return record

    def transition_state(
        self,
        model_version: str,
        target_state: ModelState,
        actor_role: str = "ML_ENGINEER",
        signoff_approved: bool = False,
        reason: str = "Standard progression",
    ) -> ModelLifecycleRecord:
        """Transitions a model checkpoint to target_state with transition & signoff validation.

        Enforces the Single Champion Invariant: If a model transitions to PRODUCTION,
        any other existing PRODUCTION model is automatically demoted to ARCHIVED.
        """
        clean_version = model_version.strip()
        with self._lock:
            if clean_version not in self._models:
                self.register_model(clean_version)

            record = self._models[clean_version]
            current = record.current_state

            if target_state not in ALLOWED_TRANSITIONS[current]:
                raise InvalidStateTransitionError(
                    f"Illegal state transition for model '{clean_version}' from {current.value} to {target_state.value}. Allowed: {[s.value for s in ALLOWED_TRANSITIONS[current]]}"
                )

            # Enforce compliance signoff for CANARY or PRODUCTION promotions
            if (
                target_state in (ModelState.CANARY, ModelState.PRODUCTION)
                and not signoff_approved
                and not record.compliance_signoff
            ):
                raise InvalidStateTransitionError(
                    f"Compliance sign-off is required to promote model '{clean_version}' to {target_state.value}."
                )

            if signoff_approved:
                record.compliance_signoff = True

            # Single Champion Invariant: Auto-archive previous PRODUCTION model
            if target_state == ModelState.PRODUCTION:
                for other_ver, other_record in self._models.items():
                    if other_ver != clean_version and other_record.current_state == ModelState.PRODUCTION:
                        other_record.current_state = ModelState.ARCHIVED
                        other_record.state_history.append(
                            {
                                "from_state": ModelState.PRODUCTION.value,
                                "to_state": ModelState.ARCHIVED.value,
                                "timestamp": datetime.now(UTC).isoformat(),
                                "actor_role": "SYSTEM",
                                "reason": f"Auto-archived due to promotion of {clean_version} (Single Champion Invariant)",
                            }
                        )
                        logger.info(
                            "Demoted previous champion model '%s' from PRODUCTION to ARCHIVED",
                            other_ver,
                        )

            # Execute transition
            record.current_state = target_state
            record.state_history.append(
                {
                    "from_state": current.value,
                    "to_state": target_state.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor_role": actor_role,
                    "reason": reason,
                }
            )

            logger.info(
                "Model '%s' transitioned from %s to %s by %s",
                clean_version,
                current.value,
                target_state.value,
                actor_role,
            )
            return record

    def get_model_record(self, model_version: str) -> ModelLifecycleRecord | None:
        """Retrieves model lifecycle record by version."""
        with self._lock:
            return self._models.get(model_version.strip())

    def get_production_model(self) -> ModelLifecycleRecord | None:
        """Retrieves the currently active PRODUCTION champion model."""
        with self._lock:
            for record in self._models.values():
                if record.current_state == ModelState.PRODUCTION:
                    return record
            return None

    def rollback_production(
        self,
        actor_role: str = "ADMIN",
        target_version: str | None = None,
        reason: str = "Production rollback",
    ) -> tuple[ModelLifecycleRecord, ModelLifecycleRecord]:
        """Rolls back the active PRODUCTION model and restores an ARCHIVED candidate."""
        with self._lock:
            current_prod = self.get_production_model()
            if not current_prod:
                raise InvalidStateTransitionError("No active PRODUCTION model found for rollback.")

            if target_version:
                restore_record = self.get_model_record(target_version)
                if not restore_record:
                    raise InvalidStateTransitionError(
                        f"Target rollback model '{target_version}' not registered."
                    )
                if restore_record.current_state != ModelState.ARCHIVED:
                    raise InvalidStateTransitionError(
                        f"Target rollback model '{target_version}' must be in ARCHIVED state (current: {restore_record.current_state.value})."
                    )
            else:
                archived_models = [
                    m for m in self._models.values() if m.current_state == ModelState.ARCHIVED
                ]
                if not archived_models:
                    raise InvalidStateTransitionError("No ARCHIVED model available for rollback.")
                restore_record = max(archived_models, key=lambda m: m.created_at)

            # Demote current prod to ARCHIVED
            current_prod.current_state = ModelState.ARCHIVED
            current_prod.state_history.append(
                {
                    "from_state": ModelState.PRODUCTION.value,
                    "to_state": ModelState.ARCHIVED.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor_role": actor_role,
                    "reason": f"Rolled back from PRODUCTION: {reason}",
                }
            )

            # Restore candidate to PRODUCTION
            restore_record.current_state = ModelState.PRODUCTION
            restore_record.state_history.append(
                {
                    "from_state": ModelState.ARCHIVED.value,
                    "to_state": ModelState.PRODUCTION.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor_role": actor_role,
                    "reason": f"Restored to PRODUCTION via rollback: {reason}",
                }
            )

            logger.warning(
                "Rolled back PRODUCTION model '%s' -> ARCHIVED; Restored '%s' -> PRODUCTION (%s)",
                current_prod.model_version,
                restore_record.model_version,
                reason,
            )
            return current_prod, restore_record

    def list_models(self) -> list[ModelLifecycleRecord]:
        """Returns all registered model lifecycle records."""
        with self._lock:
            return list(self._models.values())
