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
    created_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(UTC))

    def __post_init__(self) -> None:
        self.consortium_id = self.consortium_id.lower().strip()
        self.name = self.name.strip()
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
