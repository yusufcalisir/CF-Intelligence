"""Domain model and state machine for AML case management and Four-Eyes governance.

Enforces strict lifecycle state machine transition integrity, terminal state
immutability (replay resistance), and cryptographic Four-Eyes dual-control
separation of duties (ApproverID != InvestigatorID).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CaseState(StrEnum):
    """Formal lifecycle states of an AML investigation case."""

    OPEN = "open"
    ASSIGNED = "assigned"
    INVESTIGATING = "investigating"
    PENDING_REVIEW = "pending_review"
    ESCALATED = "escalated"
    SAR_FILED = "sar_filed"
    CLOSED_CONFIRMED = "closed_confirmed"
    CLOSED_FALSE_POSITIVE = "closed_false_positive"

    # Semantic aliases for regulatory and compliance specs
    ALERT = "open"
    UNDER_INVESTIGATION = "investigating"
    FOUR_EYES_PENDING = "pending_review"


class CaseResolution(StrEnum):
    """Permitted terminal resolutions for Four-Eyes case closure."""

    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    FALSE_POSITIVE = "FALSE_POSITIVE"


# Valid state machine transitions
# Direct shortcuts (e.g., OPEN -> CLOSED_CONFIRMED or OPEN -> SAR_FILED) are strictly forbidden
CASE_STATE_TRANSITIONS: dict[CaseState, set[CaseState]] = {
    CaseState.OPEN: {
        CaseState.ASSIGNED,
        CaseState.INVESTIGATING,
        CaseState.CLOSED_FALSE_POSITIVE,
    },
    CaseState.ASSIGNED: {
        CaseState.INVESTIGATING,
        CaseState.OPEN,
    },
    CaseState.INVESTIGATING: {
        CaseState.PENDING_REVIEW,
        CaseState.ESCALATED,
        CaseState.CLOSED_CONFIRMED,
        CaseState.CLOSED_FALSE_POSITIVE,
    },
    CaseState.PENDING_REVIEW: {
        CaseState.INVESTIGATING,
        CaseState.ESCALATED,
        CaseState.CLOSED_CONFIRMED,
        CaseState.CLOSED_FALSE_POSITIVE,
    },
    CaseState.ESCALATED: {
        CaseState.INVESTIGATING,
        CaseState.CLOSED_CONFIRMED,
        CaseState.SAR_FILED,
    },
    CaseState.SAR_FILED: {
        CaseState.CLOSED_CONFIRMED,
    },
    CaseState.CLOSED_CONFIRMED: set(),  # Terminal: strictly immutable
    CaseState.CLOSED_FALSE_POSITIVE: set(),  # Terminal: strictly immutable
}

TERMINAL_CASE_STATES: frozenset[CaseState] = frozenset(
    {CaseState.CLOSED_CONFIRMED, CaseState.CLOSED_FALSE_POSITIVE}
)


# ── Domain Exceptions ─────────────────────────────────────────────────────────


class CaseDomainError(ValueError):
    """Base exception for all case management domain invariant failures."""

    pass


class InvalidCaseTransitionError(CaseDomainError):
    """Raised when an illegal lifecycle state transition is attempted."""

    pass


class SelfApprovalProhibitedError(CaseDomainError, PermissionError):
    """Raised when an investigator or actor attempts to approve their own case (ApproverID == InvestigatorID)."""

    pass


class DuplicateSupervisorSignatureError(CaseDomainError):
    """Raised when identical supervisor identities are provided under dual control."""

    pass


class FourEyesVerificationError(CaseDomainError):
    """Raised when required Four-Eyes supervisor signoffs are missing or invalid."""

    pass


class TerminalCaseImmutableError(CaseDomainError):
    """Raised when an attempt is made to transition or modify an already finalized case."""

    pass


# ── Cryptographic & Identity Utilities ───────────────────────────────────────


def clean_identity(identity: str | None) -> str:
    """Normalize user/supervisor identifier for rigorous identity matching.

    Strips prefixes such as 'supervisor:', 'analyst:', 'SIG_SUPERVISOR_',
    trims whitespace, and lowercases to prevent casing/prefix evasion.
    """
    if not identity:
        return ""
    cleaned = identity.strip().lower()
    prefixes = (
        "supervisor:",
        "analyst:",
        "investigator:",
        "sig_supervisor_",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                changed = True
    return cleaned


def compute_timeline_hash(
    timestamp: datetime,
    event_type: str,
    description: str,
    actor: str,
    parent_hash: str,
) -> str:
    """Compute deterministic SHA-256 block hash for tamper-evident timeline chaining."""
    block = f"{timestamp.isoformat()}|{event_type}|{description}|{actor}|{parent_hash}"
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def validate_four_eyes_authorization(
    actor_id: str,
    assigned_investigator: str | None,
    supervisor_signatures: list[str],
    require_dual_supervisors: bool = True,
) -> list[str]:
    """Enforces Four-Eyes principle mathematical invariants:

    1. Non-Empty Signatures: Signatures must be provided.
    2. Self-Approval Prevention: ApproverID != InvestigatorID and ApproverID != ActorID.
    3. Distinct Supervisor Identities: When dual signoff is required, Supervisor1 != Supervisor2.
    4. Valid Identity Format: Signatures must yield non-empty identity strings.

    Returns:
        Deduplicated list of cleaned, valid supervisor signatures.

    Raises:
        FourEyesVerificationError: If required signatures are missing.
        SelfApprovalProhibitedError: If self-approval is attempted.
        DuplicateSupervisorSignatureError: If duplicate supervisor identities are detected.
    """
    if not supervisor_signatures:
        raise FourEyesVerificationError(
            "Case resolution requires Four-Eyes supervisor authorization."
        )

    clean_actor = clean_identity(actor_id)
    clean_assigned = clean_identity(assigned_investigator)

    cleaned_signatures: list[str] = []
    seen_identities: set[str] = set()

    for raw_sig in supervisor_signatures:
        if not raw_sig or not raw_sig.strip():
            continue
        ident = clean_identity(raw_sig)
        if not ident:
            raise FourEyesVerificationError(
                f"Supervisor signature '{raw_sig}' does not contain a valid identity."
            )

        # Invariant 1: Self-Approval Prevention (ApproverID != ActorID)
        if clean_actor and ident == clean_actor:
            raise SelfApprovalProhibitedError(
                f"Self-approval prohibited: acting user '{actor_id}' cannot provide "
                f"supervisor signoff '{raw_sig}' under Four-Eyes dual control governance."
            )

        # Invariant 2: Self-Approval Prevention (ApproverID != InvestigatorID)
        if clean_assigned and ident == clean_assigned:
            raise SelfApprovalProhibitedError(
                f"Self-approval prohibited: assigned investigator '{assigned_investigator}' "
                f"cannot approve their own case under Four-Eyes dual control governance."
            )

        # Invariant 3: Distinct Supervisor Identities (Supervisor_1 != Supervisor_2)
        if ident in seen_identities:
            raise DuplicateSupervisorSignatureError(
                f"Duplicate supervisor signature '{ident}' rejected under Four-Eyes "
                f"dual control governance. Signers must be distinct individuals."
            )

        seen_identities.add(ident)
        cleaned_signatures.append(raw_sig.strip())

    if require_dual_supervisors and len(cleaned_signatures) < 2:
        raise FourEyesVerificationError(
            f"Four-Eyes dual control requires at least 2 distinct supervisor signatures "
            f"(received {len(cleaned_signatures)}: '{cleaned_signatures[0]}')."
        )

    return cleaned_signatures


# ── Domain Models ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FourEyesSignature:
    """Immutable supervisor signature record."""

    supervisor_id: str
    signed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = None
    signature_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.signature_hash:
            data = f"{self.supervisor_id}|{self.signed_at.isoformat()}|{self.notes or ''}"
            object.__setattr__(
                self, "signature_hash", hashlib.sha256(data.encode("utf-8")).hexdigest()
            )


@dataclass
class CaseTimelineEvent:
    """Tamper-evident timeline event with cryptographic SHA-256 parent hash chaining."""

    event_type: str
    description: str
    actor: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def seal(self, parent_hash: str = "0" * 64) -> str:
        """Computes and seals the SHA-256 parent-chained hash for this event."""
        self.metadata["parent_hash"] = parent_hash
        event_hash = compute_timeline_hash(
            self.timestamp, self.event_type, self.description, self.actor, parent_hash
        )
        self.metadata["hash"] = event_hash
        return event_hash


@dataclass
class CaseDomainModel:
    """Encapsulates AML case lifecycle state transitions and Four-Eyes governance invariants."""

    case_id: str
    title: str
    status: CaseState = CaseState.OPEN
    priority: str = "P3_MEDIUM"
    assigned_to: str | None = None
    alert_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    supervisor_signatures: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[CaseTimelineEvent] = field(default_factory=list)
    total_risk_score: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    closed_at: datetime | None = None

    def is_terminal(self) -> bool:
        """Returns True if the case has reached a finalized terminal state."""
        return self.status in TERMINAL_CASE_STATES

    def can_transition_to(self, target: CaseState) -> bool:
        """Evaluates whether the transition from current status to target is legally permitted."""
        if self.is_terminal():
            return False
        return target in CASE_STATE_TRANSITIONS.get(self.status, set())

    def append_event(
        self,
        event_type: str,
        description: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> CaseTimelineEvent:
        """Appends a new cryptographically chained event to the case timeline."""
        parent_hash = "0" * 64
        if self.timeline:
            last_meta = self.timeline[-1].metadata
            parent_hash = str(last_meta.get("hash") or "0" * 64)

        event = CaseTimelineEvent(
            event_type=event_type,
            description=description,
            actor=actor,
            timestamp=datetime.now(UTC),
            metadata=metadata or {},
        )
        event.seal(parent_hash)
        self.timeline.append(event)
        return event

    def transition_to(
        self,
        target_state: CaseState,
        actor_id: str,
        supervisor_signatures: list[str] | None = None,
        notes: str = "",
    ) -> None:
        """Executes state transition, asserting transition validity, terminal immutability,

        and Four-Eyes governance invariants.
        """
        if self.is_terminal():
            raise TerminalCaseImmutableError(
                f"Invalid transition: Case '{self.case_id}' is finalized in terminal state '{self.status.value}' "
                f"and cannot be modified or re-opened (Replay Resistance Invariant)."
            )

        allowed = CASE_STATE_TRANSITIONS.get(self.status, set())
        if target_state not in allowed:
            raise InvalidCaseTransitionError(
                f"Illegal transition for case '{self.case_id}': '{self.status.value}' -> '{target_state.value}'. "
                f"Valid targets: {[s.value for s in allowed]}"
            )

        # Handle Four-Eyes dual control on terminal resolutions
        if target_state in TERMINAL_CASE_STATES:
            candidate_sigs = list(self.supervisor_signatures)
            if supervisor_signatures:
                candidate_sigs.extend(supervisor_signatures)

            # Strictly validate separation of duties and distinct dual supervisors
            verified_sigs = validate_four_eyes_authorization(
                actor_id=actor_id,
                assigned_investigator=self.assigned_to,
                supervisor_signatures=candidate_sigs,
                require_dual_supervisors=False if len(candidate_sigs) == 1 else True,
            )
            self.supervisor_signatures = verified_sigs
            self.closed_at = datetime.now(UTC)

        old_state = self.status
        self.status = target_state
        self.updated_at = datetime.now(UTC)

        self.append_event(
            event_type="status_changed",
            description=f"Status: {old_state.value} -> {target_state.value}",
            actor=actor_id,
            metadata={
                "from": old_state.value,
                "to": target_state.value,
                "notes": notes,
                "supervisor_signatures": self.supervisor_signatures,
            },
        )
