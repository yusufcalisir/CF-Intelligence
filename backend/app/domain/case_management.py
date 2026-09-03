# ruff: noqa: UP042
"""Domain models and state machine for Investigator Case Workbench."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class InvestigatorCaseStatus(str, Enum):
    """Lifecycle status enum for an investigator fraud case."""

    NEW = "NEW"
    ASSIGNED = "ASSIGNED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    ESCALATED = "ESCALATED"
    PENDING_SECOND_SIGNATURE = "PENDING_SECOND_SIGNATURE"
    RESOLVED_CONFIRMED_FRAUD = "RESOLVED_CONFIRMED_FRAUD"
    RESOLVED_FALSE_POSITIVE = "RESOLVED_FALSE_POSITIVE"


class InvalidCaseTransitionError(Exception):
    """Raised when an illegal case lifecycle state transition is attempted."""

    pass


def extract_supervisor_identity(signature: str) -> str:
    """Extracts unique supervisor ID from a signature formatted as 'SIG_SUPERVISOR_<ID>'."""
    if not signature or not signature.startswith("SIG_SUPERVISOR_"):
        return ""
    remainder = signature[len("SIG_SUPERVISOR_") :].strip()
    return remainder.upper()


# Allowed state transition map for case lifecycle
ALLOWED_CASE_TRANSITIONS: dict[InvestigatorCaseStatus, set[InvestigatorCaseStatus]] = {
    InvestigatorCaseStatus.NEW: {
        InvestigatorCaseStatus.ASSIGNED,
        InvestigatorCaseStatus.UNDER_INVESTIGATION,
    },
    InvestigatorCaseStatus.ASSIGNED: {
        InvestigatorCaseStatus.UNDER_INVESTIGATION,
        InvestigatorCaseStatus.ESCALATED,
    },
    InvestigatorCaseStatus.UNDER_INVESTIGATION: {
        InvestigatorCaseStatus.ESCALATED,
        InvestigatorCaseStatus.PENDING_SECOND_SIGNATURE,
        InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE,
    },
    InvestigatorCaseStatus.ESCALATED: {
        InvestigatorCaseStatus.UNDER_INVESTIGATION,
        InvestigatorCaseStatus.PENDING_SECOND_SIGNATURE,
        InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE,
    },
    InvestigatorCaseStatus.PENDING_SECOND_SIGNATURE: {
        InvestigatorCaseStatus.UNDER_INVESTIGATION,
        InvestigatorCaseStatus.ESCALATED,
        InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE,
    },
    InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD: set(),  # Terminal state
    InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE: set(),  # Terminal state
}


@dataclass
class FraudCaseRecord:
    """Domain model representing a fraud investigation case."""

    case_id: str
    title: str
    alert_ids: list[str] = field(default_factory=list)
    status: InvestigatorCaseStatus = InvestigatorCaseStatus.NEW
    assigned_to: str | None = None
    supervisor_signatures: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def supervisor_signature(self) -> str | None:
        """Legacy accessor returning primary supervisor signature if present."""
        return self.supervisor_signatures[0] if self.supervisor_signatures else None

    @supervisor_signature.setter
    def supervisor_signature(self, value: str | None) -> None:
        """Legacy setter appending a signature if not already present."""
        if value and value not in self.supervisor_signatures:
            self.supervisor_signatures.append(value)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append(
                {
                    "from_status": None,
                    "to_status": self.status.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor_id": "SYSTEM",
                    "notes": "Initialized case in NEW status",
                }
            )


class CaseLifecycleStateMachine:
    """Manages 6-stage case lifecycle progression and enforces Four-Eyes supervisor dual signoffs."""

    def transition_case(
        self,
        record: FraudCaseRecord,
        target_status: InvestigatorCaseStatus,
        actor_id: str,
        supervisor_signature: str | None = None,
        supervisor_signatures: list[str] | None = None,
        notes: str = "Status transition",
    ) -> FraudCaseRecord:
        """Transitions case to target_status, validating transitions and Four-Eyes signoffs."""
        current = record.status

        if target_status not in ALLOWED_CASE_TRANSITIONS[current]:
            raise InvalidCaseTransitionError(
                f"Illegal transition for case '{record.case_id}' from {current.value} to {target_status.value}. Allowed: {[s.value for s in ALLOWED_CASE_TRANSITIONS[current]]}"
            )

        # Collect all signatures submitted or previously stored
        candidate_sigs: list[str] = list(record.supervisor_signatures)
        if supervisor_signatures:
            for s in supervisor_signatures:
                if s:
                    candidate_sigs.append(s)
        if supervisor_signature:
            candidate_sigs.append(supervisor_signature)

        # Intermediate workflow state: PENDING_SECOND_SIGNATURE
        if target_status == InvestigatorCaseStatus.PENDING_SECOND_SIGNATURE:
            if not candidate_sigs:
                raise InvalidCaseTransitionError(
                    f"First supervisor signature required to enter PENDING_SECOND_SIGNATURE for case '{record.case_id}'."
                )
            for s in candidate_sigs:
                if not s.startswith("SIG_SUPERVISOR_") or not extract_supervisor_identity(s):
                    raise InvalidCaseTransitionError(
                        f"Invalid supervisor signature format: '{s}'. Must start with 'SIG_SUPERVISOR_<ID>'."
                    )
            record.supervisor_signatures = list(dict.fromkeys(candidate_sigs))

        # Four-Eyes Principle: Resolving a case requires TWO distinct supervisor signatures
        is_resolution = target_status in (
            InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
            InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE,
        )
        if is_resolution:
            if not candidate_sigs:
                raise InvalidCaseTransitionError(
                    f"Four-Eyes supervisor dual-authorization signature required to resolve case '{record.case_id}' to {target_status.value}."
                )

            # Validate each signature format first
            for s in candidate_sigs:
                if not s.startswith("SIG_SUPERVISOR_"):
                    raise InvalidCaseTransitionError(
                        f"Four-Eyes supervisor dual-authorization signature required to resolve case '{record.case_id}' to {target_status.value}."
                    )

            # Check if only 1 signature was provided
            if len(candidate_sigs) < 2:
                raise InvalidCaseTransitionError(
                    f"Four-Eyes dual supervisor authorization requires 2 distinct supervisor signatures (got {len(candidate_sigs)}: '{candidate_sigs[0]}')."
                )

            # Extract unique signer IDs and reject duplicate signers
            valid_identities: dict[str, str] = {}
            for s in candidate_sigs:
                ident = extract_supervisor_identity(s)
                if not ident:
                    raise InvalidCaseTransitionError(
                        f"Supervisor signature '{s}' does not contain a valid supervisor identity."
                    )
                if ident in valid_identities:
                    raise InvalidCaseTransitionError(
                        f"Four-Eyes dual supervisor authorization requires 2 distinct supervisor identities (duplicate signer identity '{ident}' rejected)."
                    )
                valid_identities[ident] = s

            record.supervisor_signatures = list(valid_identities.values())

        record.status = target_status
        record.history.append(
            {
                "from_status": current.value,
                "to_status": target_status.value,
                "timestamp": datetime.now(UTC).isoformat(),
                "actor_id": actor_id,
                "notes": notes,
            }
        )

        logger.info(
            "Case '%s' transitioned from %s to %s by %s (signatures: %s)",
            record.case_id,
            current.value,
            target_status.value,
            actor_id,
            record.supervisor_signatures,
        )
        return record
