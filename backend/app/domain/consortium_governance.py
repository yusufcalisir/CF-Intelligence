# ruff: noqa: UP042
"""Domain models for Federated Consortium Governance & Membership Protocol."""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConsortiumStatus(str, Enum):
    """Status enum for a federated multi-bank consortium."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class ProposalStatus(str, Enum):
    """Status enum for membership or policy voting proposals."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class MemberRole(str, Enum):
    """Role enum for a member institution in a consortium."""

    FOUNDER = "FOUNDER"
    FULL_MEMBER = "FULL_MEMBER"
    OBSERVER = "OBSERVER"


class ProposalAction(str, Enum):
    """Action type for a governance proposal."""

    ADD_MEMBER = "ADD_MEMBER"
    REMOVE_MEMBER = "REMOVE_MEMBER"
    UPDATE_POLICY = "UPDATE_POLICY"


class ConsortiumSecurityError(Exception):
    """Raised when consortium governance integrity or authorization boundary fails."""


class CounterpartyWorkerUnavailableError(ConsortiumSecurityError):
    """Raised when an institutional counterpart worker node is unreachable, offline, or refuses connection."""


class CounterpartyTimeoutError(ConsortiumSecurityError, TimeoutError):
    """Raised when communication with counterpart worker daemon times out."""


class CounterpartyTransportError(ConsortiumSecurityError):
    """Raised when network transport service or intermediate gateway is unavailable or broken."""


class CrossBankIdempotencyConflictError(ConsortiumSecurityError):
    """Raised when an idempotency key is reused with a conflicting payload, principal, or indeterminate state."""


@dataclass
class ConsortiumMember:
    """Represents a member institution participating in a consortium."""

    bank_id: str
    role: MemberRole = MemberRole.FULL_MEMBER
    voting_power: float = 1.0
    joined_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(UTC))

    def __post_init__(self) -> None:
        self.bank_id = self.bank_id.lower().strip()
        if not self.bank_id:
            raise ValueError("ConsortiumMember bank_id cannot be empty or whitespace.")
        if isinstance(self.role, str):
            self.role = MemberRole(self.role)
        if isinstance(self.joined_at, str):
            self.joined_at = datetime.datetime.fromisoformat(self.joined_at)
            if self.joined_at.tzinfo is None:
                self.joined_at = self.joined_at.replace(tzinfo=UTC)
        if not math.isfinite(self.voting_power) or self.voting_power < 0.0:
            raise ValueError(
                f"ConsortiumMember voting_power must be non-negative finite number, got {self.voting_power}"
            )

    @property
    def can_vote(self) -> bool:
        """Returns True if member has non-zero voting rights and is not an observer."""
        return self.role != MemberRole.OBSERVER and self.voting_power > 0.0


@dataclass
class MembershipProposal:
    """Represents a voting proposal for consortium membership or policy changes."""

    proposal_id: str
    consortium_id: str
    creator_bank_id: str
    target_bank_id: str
    action: ProposalAction
    required_quorum_ratio: float = 0.51  # 51% majority required by default
    votes_for: set[str] = field(default_factory=set)
    votes_against: set[str] = field(default_factory=set)
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(UTC))
    ttl_seconds: int = 86400  # Default 24-hour voting window
    expires_at: datetime.datetime | None = None
    cancelled_at: datetime.datetime | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.proposal_id = self.proposal_id.strip()
        self.consortium_id = self.consortium_id.lower().strip()
        self.creator_bank_id = self.creator_bank_id.lower().strip()
        self.target_bank_id = self.target_bank_id.lower().strip()

        if isinstance(self.action, str):
            self.action = ProposalAction(self.action)
        if isinstance(self.status, str):
            self.status = ProposalStatus(self.status)

        if isinstance(self.created_at, str):
            self.created_at = datetime.datetime.fromisoformat(self.created_at)
            if self.created_at.tzinfo is None:
                self.created_at = self.created_at.replace(tzinfo=UTC)
        if isinstance(self.expires_at, str):
            self.expires_at = datetime.datetime.fromisoformat(self.expires_at)
            if self.expires_at.tzinfo is None:
                self.expires_at = self.expires_at.replace(tzinfo=UTC)
        if isinstance(self.cancelled_at, str):
            self.cancelled_at = datetime.datetime.fromisoformat(self.cancelled_at)
            if self.cancelled_at.tzinfo is None:
                self.cancelled_at = self.cancelled_at.replace(tzinfo=UTC)

        if isinstance(self.votes_for, (list, tuple)):
            self.votes_for = set(self.votes_for)
        if isinstance(self.votes_against, (list, tuple)):
            self.votes_against = set(self.votes_against)

        if not self.proposal_id:
            raise ValueError("MembershipProposal proposal_id cannot be empty.")
        if not self.consortium_id:
            raise ValueError("MembershipProposal consortium_id cannot be empty.")
        if not self.creator_bank_id:
            raise ValueError("MembershipProposal creator_bank_id cannot be empty.")
        if not self.target_bank_id:
            raise ValueError("MembershipProposal target_bank_id cannot be empty.")

        if not math.isfinite(self.required_quorum_ratio) or not (0.0 < self.required_quorum_ratio <= 1.0):
            raise ValueError(
                f"required_quorum_ratio must be strictly between 0.0 and 1.0, got {self.required_quorum_ratio}"
            )
        if self.ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive integer, got {self.ttl_seconds}")

        if self.expires_at is None:
            self.expires_at = self.created_at + datetime.timedelta(seconds=self.ttl_seconds)

    def is_expired(self, current_time: datetime.datetime | None = None) -> bool:
        """Determines if the voting window has elapsed."""
        now = current_time or datetime.datetime.now(UTC)
        return bool(self.expires_at and now >= self.expires_at)


@dataclass
class Consortium:
    """Domain model representing a multi-bank federated consortium."""

    consortium_id: str
    name: str
    quorum_ratio: float = 0.51  # Quorum ratio (e.g. 0.51 for 51%, 0.66 for 2/3)
    min_members_n: int = 2
    max_epsilon: float = 5.0
    status: ConsortiumStatus = ConsortiumStatus.DRAFT
    members: dict[str, ConsortiumMember] = field(default_factory=dict)
    removed_members: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.consortium_id = self.consortium_id.lower().strip()
        self.name = self.name.strip()
        if isinstance(self.status, str):
            self.status = ConsortiumStatus(self.status)
        if isinstance(self.created_at, str):
            self.created_at = datetime.datetime.fromisoformat(self.created_at)
            if self.created_at.tzinfo is None:
                self.created_at = self.created_at.replace(tzinfo=UTC)

        if not self.consortium_id:
            raise ValueError("Consortium consortium_id cannot be empty.")
        if not self.name:
            raise ValueError("Consortium name cannot be empty.")
        if not math.isfinite(self.quorum_ratio) or not (0.0 < self.quorum_ratio <= 1.0):
            raise ValueError(
                f"Consortium quorum_ratio must be in (0.0, 1.0], got {self.quorum_ratio}"
            )
        if self.min_members_n < 1:
            raise ValueError(f"min_members_n must be >= 1, got {self.min_members_n}")
        if not math.isfinite(self.max_epsilon) or self.max_epsilon <= 0.0:
            raise ValueError(f"max_epsilon must be positive finite number, got {self.max_epsilon}")

    @property
    def founder_bank_id(self) -> str | None:
        """Returns the bank_id of the founder member if present."""
        for b_id, m in self.members.items():
            if m.role == MemberRole.FOUNDER:
                return b_id
        return None

    @property
    def pairwise_operational_grants(self) -> dict[str, dict[str, Any]]:
        """Access all bilateral operational grants registered in consortium metadata."""
        return (self.metadata or {}).get("pairwise_operational_grants", {})

    @property
    def operational_data_sharing_grants(self) -> dict[str, dict[str, Any]]:
        """Unified dictionary of all operational grants (both pairwise and legacy) for backward compatibility."""
        grants: dict[str, dict[str, Any]] = dict((self.metadata or {}).get("pairwise_operational_grants", {}))
        legacy = (self.metadata or {}).get("operational_data_sharing_grant")
        if legacy and isinstance(legacy, dict) and "grant_id" in legacy:
            grants[legacy["grant_id"]] = legacy
        return grants

    def get_pairwise_operational_grant(self, grant_id: str) -> dict[str, Any] | None:
        """Retrieve a specific pairwise operational grant by stable ID."""
        clean = grant_id.strip()
        pairwise = self.pairwise_operational_grants
        if clean in pairwise:
            return pairwise[clean]
        return None

    def find_pairwise_operational_grant(
        self, bank_a: str, bank_b: str
    ) -> dict[str, Any] | None:
        """Find the pairwise operational grant governing data sharing between two institutions."""
        clean_a = bank_a.lower().strip()
        clean_b = bank_b.lower().strip()
        target_pair = {clean_a, clean_b}
        found_grant: dict[str, Any] | None = None
        for grant in self.pairwise_operational_grants.values():
            if not isinstance(grant, dict):
                continue
            inst_a = str(grant.get("institution_a", "")).lower().strip()
            inst_b = str(grant.get("institution_b", "")).lower().strip()
            part = {
                b.lower().strip()
                for b in grant.get("participating_banks", [])
                if str(b).strip()
            } or ({inst_a, inst_b} - {""})
            if part == target_pair:
                if grant.get("is_active", True) and not grant.get("revoked_by_institutions"):
                    return grant
                if found_grant is None:
                    found_grant = grant
        return found_grant


@dataclass
class PairwiseOperationalGrant:
    """Domain model representing an authoritative bilateral operational data-sharing grant between two institutions."""

    grant_id: str
    consortium_id: str
    institution_a: str
    institution_b: str
    authority: str
    revision: int = 1
    scope_digest: str = ""
    is_active: bool = True
    participating_banks: list[str] = field(default_factory=list)
    allowed_purposes: list[str] = field(
        default_factory=lambda: ["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"]
    )
    institution_jurisdictions: dict[str, str] = field(default_factory=dict)
    allowed_jurisdictions: list[str] = field(
        default_factory=lambda: ["eu-central-1", "us-east-1", "apac-singapore-1"]
    )
    allow_cross_border: bool = False
    allow_institution_identity_sharing: bool = False
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    revoked_by_institutions: list[str] = field(default_factory=list)
    revocation_history: list[dict[str, Any]] = field(default_factory=list)
    registered_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.grant_id = self.grant_id.strip()
        self.consortium_id = self.consortium_id.lower().strip()
        self.institution_a = self.institution_a.lower().strip()
        self.institution_b = self.institution_b.lower().strip()
        self.authority = self.authority.strip()

        if not self.grant_id:
            raise ValueError("PairwiseOperationalGrant grant_id cannot be empty.")
        if not self.consortium_id:
            raise ValueError("PairwiseOperationalGrant consortium_id cannot be empty.")
        if not self.institution_a:
            raise ValueError("PairwiseOperationalGrant institution_a cannot be empty.")
        if not self.institution_b:
            raise ValueError("PairwiseOperationalGrant institution_b cannot be empty.")
        if self.institution_a == self.institution_b:
            raise ValueError(
                f"Bilateral operational grant requires two distinct member institutions, got same '{self.institution_a}'."
            )
        if not self.authority:
            raise ValueError("PairwiseOperationalGrant authority cannot be empty.")
        if self.revision < 1:
            raise ValueError(f"PairwiseOperationalGrant revision must be >= 1, got {self.revision}")

        self.participating_banks = sorted([self.institution_a, self.institution_b])
        self.allowed_purposes = sorted(list(set(p.strip() for p in self.allowed_purposes)))
        self.institution_jurisdictions = {
            k.lower().strip(): v.strip()
            for k, v in sorted(self.institution_jurisdictions.items())
        }
        self.allowed_jurisdictions = sorted(list(set(j.strip() for j in self.allowed_jurisdictions)))
        self.revoked_by_institutions = sorted(
            list(set(b.lower().strip() for b in self.revoked_by_institutions))
        )

    def involves(self, bank_id: str) -> bool:
        """Returns True if the given institution is one of the two bilateral participants."""
        clean = bank_id.lower().strip()
        return clean in (self.institution_a, self.institution_b)

    def counterpart_of(self, bank_id: str) -> str:
        """Returns the counterpart institution for the given participant."""
        clean = bank_id.lower().strip()
        if clean == self.institution_a:
            return self.institution_b
        if clean == self.institution_b:
            return self.institution_a
        raise ValueError(
            f"Institution '{bank_id}' is not a participant of pairwise grant '{self.grant_id}'."
        )

    @property
    def is_fully_approved(self) -> bool:
        """True only if both institutions have approved the exact current revision and scope digest."""
        if not self.is_active or bool(self.revoked_by_institutions):
            return False
        for bank_id in (self.institution_a, self.institution_b):
            rec = self.approvals.get(bank_id)
            if not rec or not isinstance(rec, dict):
                return False
            if rec.get("grant_revision") != self.revision:
                return False
            if self.scope_digest and rec.get("scope_digest") != self.scope_digest:
                return False
        return True

    @property
    def is_revoked(self) -> bool:
        """True if the grant is marked inactive or has been revoked by either institution."""
        return not self.is_active or bool(self.revoked_by_institutions)

    def to_dict(self) -> dict[str, Any]:
        """Convert domain model to canonical dictionary representation."""
        return {
            "grant_id": self.grant_id,
            "consortium_id": self.consortium_id,
            "institution_a": self.institution_a,
            "institution_b": self.institution_b,
            "authority": self.authority,
            "revision": self.revision,
            "scope_digest": self.scope_digest,
            "is_active": self.is_active,
            "participating_banks": self.participating_banks,
            "allowed_purposes": self.allowed_purposes,
            "institution_jurisdictions": self.institution_jurisdictions,
            "allowed_jurisdictions": self.allowed_jurisdictions,
            "allow_cross_border": self.allow_cross_border,
            "allow_institution_identity_sharing": self.allow_institution_identity_sharing,
            "approvals": self.approvals,
            "revoked_by_institutions": self.revoked_by_institutions,
            "revocation_history": self.revocation_history,
            "registered_at": self.registered_at,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PairwiseOperationalGrant:
        """Construct domain model from dictionary representation."""
        part_banks = d.get("participating_banks", [])
        inst_a = d.get("institution_a") or (part_banks[0] if len(part_banks) > 0 else "")
        inst_b = d.get("institution_b") or (part_banks[1] if len(part_banks) > 1 else "")
        return cls(
            grant_id=d["grant_id"],
            consortium_id=d["consortium_id"],
            institution_a=inst_a,
            institution_b=inst_b,
            authority=d.get("authority", "authoritative_consortium_governance"),
            revision=int(d.get("revision", 1)),
            scope_digest=d.get("scope_digest", ""),
            is_active=bool(d.get("is_active", True)),
            participating_banks=part_banks,
            allowed_purposes=d.get(
                "allowed_purposes",
                ["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
            ),
            institution_jurisdictions=d.get("institution_jurisdictions", {}),
            allowed_jurisdictions=d.get(
                "allowed_jurisdictions",
                ["eu-central-1", "us-east-1", "apac-singapore-1"],
            ),
            allow_cross_border=bool(d.get("allow_cross_border", False)),
            allow_institution_identity_sharing=bool(
                d.get("allow_institution_identity_sharing", False)
            ),
            approvals=d.get("approvals", {}),
            revoked_by_institutions=d.get("revoked_by_institutions", []),
            revocation_history=d.get("revocation_history", []),
            registered_at=d.get("registered_at", datetime.datetime.now(UTC).isoformat()),
            history=d.get("history", []),
        )
