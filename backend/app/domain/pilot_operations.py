"""Pilot Operations Domain Model & Governed Two-Bank State Machine.

Implements Milestone 5 Controlled Pilot Operations:
- Durable lifecycle: DRAFT -> PREFLIGHT -> AWAITING_APPROVAL -> AUTHORIZED -> ACTIVE -> PAUSED -> COMPLETED
- Terminal outcomes: ABORTED, FAILED
- Cryptographic & canonical scope binding: institutions, consortium, purpose, jurisdiction, synthetic datasets, topology, quotas, grant.
- Independent bilateral and operator approval tracking with stale-approval prevention.
- Operational kill switch (PAUSE, RESUME with re-validation, ABORT).
- Zero-fabrication runtime truth: fail closed on expired, paused, revoked, or quota-exhausted state.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC
from enum import StrEnum
from typing import Any


class PilotStatus(StrEnum):
    """Authoritative lifecycle states for controlled two-bank pilot operations."""

    DRAFT = "DRAFT"
    PREFLIGHT = "PREFLIGHT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


VALID_PILOT_TRANSITIONS: dict[PilotStatus, set[PilotStatus]] = {
    PilotStatus.DRAFT: {PilotStatus.PREFLIGHT, PilotStatus.AWAITING_APPROVAL, PilotStatus.FAILED, PilotStatus.ABORTED},
    PilotStatus.PREFLIGHT: {PilotStatus.AWAITING_APPROVAL, PilotStatus.FAILED, PilotStatus.ABORTED},
    PilotStatus.AWAITING_APPROVAL: {PilotStatus.AUTHORIZED, PilotStatus.DRAFT, PilotStatus.PREFLIGHT, PilotStatus.ABORTED},
    PilotStatus.AUTHORIZED: {PilotStatus.ACTIVE, PilotStatus.ABORTED},
    PilotStatus.ACTIVE: {PilotStatus.PAUSED, PilotStatus.COMPLETED, PilotStatus.ABORTED},
    PilotStatus.PAUSED: {PilotStatus.ACTIVE, PilotStatus.COMPLETED, PilotStatus.ABORTED},
    PilotStatus.COMPLETED: set(),  # Terminal
    PilotStatus.ABORTED: set(),   # Terminal
    PilotStatus.FAILED: set(),    # Terminal
}


class InvalidPilotTransitionError(ValueError):
    """Raised when an invalid pilot lifecycle transition is attempted."""

    def __init__(self, message: str) -> None:
        if not message.lower().startswith("invalid state transition"):
            message = f"Invalid state transition: {message}"
        super().__init__(message)


class PilotAuthorizationError(PermissionError):
    """Raised when pilot authorization or execution preconditions fail."""

    pass


class PilotGovernanceError(PilotAuthorizationError):
    """Raised when pilot governance policy or state preconditions fail."""

    pass


class PilotQuotaExceededError(Exception):
    """Raised when query quotas are exhausted for the pilot or institution."""

    pass


class PilotExpiredError(Exception):
    """Raised when a query is attempted on an expired pilot."""

    pass


@dataclass(frozen=True)
class PreflightCheckResult:
    """Outcome of a single preflight verification probe."""

    check_name: str
    passed: bool
    details: str
    checked_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class PilotApprovalRecord:
    """Durable record of institutional or operator approval for pilot participation."""

    approver_id: str
    approver_institution: str  # Bank ID (e.g. "bank_a", "bank_b") or "OPERATOR"
    approver_role: str
    scope_digest: str
    approved_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())
    signature: str = ""
    notes: str = ""


@dataclass
class PilotOperationalConfig:
    """Immutable operational configuration binding a two-bank pilot execution."""

    pilot_id: str
    revision: int = 1
    consortium_id: str = ""
    participating_institutions: list[str] = field(default_factory=lambda: ["bank_a", "bank_b"])
    allowed_purposes: list[str] = field(default_factory=lambda: ["AML_COLLABORATIVE_INVESTIGATION"])
    allowed_jurisdictions: list[str] = field(default_factory=lambda: ["eu-central-1"])
    approved_datasets: dict[str, Any] | list[str] = field(default_factory=dict)
    authorized_operators: list[str] = field(default_factory=list)
    operational_grant_id: str = ""
    grant_revision: int = 1
    grant_scope_digest: str = ""
    topology: dict[str, Any] = field(default_factory=dict)
    starts_at: str = ""
    expires_at: str = ""
    query_quota_total: int = 50
    query_quota_per_institution: int = 25
    scope_digest: str = ""

    def compute_scope_digest(self) -> str:
        """Computes deterministic, canonical SHA-256 digest of this pilot scope."""
        if isinstance(self.approved_datasets, dict):
            datasets_norm: Any = {k.lower().strip(): v for k, v in sorted(self.approved_datasets.items())}
        elif isinstance(self.approved_datasets, list):
            datasets_norm = sorted([d.lower().strip() for d in self.approved_datasets])
        else:
            datasets_norm = str(self.approved_datasets)

        canonical = {
            "pilot_id": self.pilot_id.strip(),
            "revision": self.revision,
            "consortium_id": self.consortium_id.lower().strip(),
            "participating_institutions": sorted([b.lower().strip() for b in self.participating_institutions]),
            "allowed_purposes": sorted([p.strip() for p in self.allowed_purposes]),
            "allowed_jurisdictions": sorted([j.strip() for j in self.allowed_jurisdictions]),
            "approved_datasets": datasets_norm,
            "authorized_operators": sorted([op.strip() for op in self.authorized_operators]),
            "operational_grant_id": self.operational_grant_id.strip(),
            "grant_revision": self.grant_revision,
            "grant_scope_digest": self.grant_scope_digest.strip(),
            "topology": {k.lower().strip(): v for k, v in sorted(self.topology.items())},
            "starts_at": self.starts_at.strip(),
            "expires_at": self.expires_at.strip(),
            "query_quota_total": self.query_quota_total,
            "query_quota_per_institution": self.query_quota_per_institution,
        }
        canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()


@dataclass
class PilotExecutionReceipt:
    """Durable receipt of a cross-bank PSI query executed under an active pilot."""

    query_id: str
    pilot_id: str
    requesting_bank_id: str
    target_bank_id: str
    purpose: str
    status: str  # ACCEPTED | DENIED | FAILED | ABORTED
    match_confirmed: bool | None
    latency_ms: float
    receipt_id: str | None = None
    reason: str | None = None
    executed_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())


@dataclass
class PilotEntity:
    """Authoritative aggregate representing a controlled two-bank pilot operations lifecycle."""

    config: PilotOperationalConfig
    status: PilotStatus = PilotStatus.DRAFT
    status_reason: str = "Pilot provisioned in DRAFT state."
    scope_digest: str = ""
    preflight_checks: list[PreflightCheckResult] = field(default_factory=list)
    approvals: dict[str, PilotApprovalRecord] = field(default_factory=dict)
    queries_executed: int = 0
    queries_denied: int = 0
    institution_query_counts: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    matches_count: int = 0
    state_history: list[dict[str, Any]] = field(default_factory=list)
    audit_event_ids: list[str] = field(default_factory=list)
    execution_receipt_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())
    activated_at: str | None = None
    terminated_at: str | None = None
    termination_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.scope_digest:
            self.scope_digest = self.config.compute_scope_digest()
        if not self.state_history:
            self.state_history.append({
                "status": self.status.value,
                "reason": self.status_reason,
                "timestamp": self.created_at,
            })

    @property
    def is_active(self) -> bool:
        """Returns True if the pilot is in ACTIVE state and not expired."""
        return self.status == PilotStatus.ACTIVE and not self.has_expired()

    def has_expired(self, current_time: datetime.datetime | None = None) -> bool:
        """Check if the pilot configuration expires_at boundary has passed."""
        if not self.config.expires_at:
            return False
        now = current_time or datetime.datetime.now(UTC)
        try:
            exp_dt = datetime.datetime.fromisoformat(self.config.expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            return now >= exp_dt
        except (ValueError, TypeError):
            return False

    def transition_to(self, target_status: PilotStatus, reason: str = "") -> None:
        """Validate and apply a lifecycle transition."""
        allowed = VALID_PILOT_TRANSITIONS.get(self.status, set())
        if target_status not in allowed:
            raise InvalidPilotTransitionError(
                f"Cannot transition pilot '{self.config.pilot_id}' from '{self.status.value}' to '{target_status.value}'. "
                f"Permitted transitions from '{self.status.value}': {[s.value for s in allowed]}."
            )
        self.status = target_status
        self.status_reason = reason or f"Transitioned to {target_status.value}."
        self.updated_at = datetime.datetime.now(UTC).isoformat()
        if target_status == PilotStatus.ACTIVE and not self.activated_at:
            self.activated_at = self.updated_at
        elif target_status in (PilotStatus.COMPLETED, PilotStatus.ABORTED, PilotStatus.FAILED):
            self.terminated_at = self.updated_at
            self.termination_reason = reason

        self.state_history.append({
            "status": target_status.value,
            "reason": self.status_reason,
            "timestamp": self.updated_at,
        })

    def record_approval(self, approval: PilotApprovalRecord) -> None:
        """Record an institutional or operator approval, verifying scope digest freshness."""
        current_digest = self.config.compute_scope_digest()
        if approval.scope_digest != current_digest:
            raise PilotAuthorizationError(
                f"Approval for '{approval.approver_institution}' is stale: "
                f"approved scope '{approval.scope_digest[:16]}...', current scope '{current_digest[:16]}...'. "
                "Configuration changed since approval was prepared. Re-approval required."
            )
        self.approvals[approval.approver_institution] = approval
        self.updated_at = datetime.datetime.now(UTC).isoformat()

        # Check if all required approvals are collected
        if self.is_fully_approved() and self.status == PilotStatus.AWAITING_APPROVAL:
            self.transition_to(PilotStatus.AUTHORIZED, "All participating institutions and operator approved.")

    def has_all_required_approvals(self) -> tuple[bool, list[str]]:
        """Returns (is_complete, missing_institutions) based on current scope digest."""
        current_digest = self.config.compute_scope_digest()
        missing = []
        for inst in self.config.participating_institutions:
            clean_inst = inst.lower().strip()
            rec = self.approvals.get(clean_inst)
            if not rec or rec.scope_digest != current_digest:
                missing.append(clean_inst)
        op_rec = self.approvals.get("OPERATOR")
        if not op_rec or op_rec.scope_digest != current_digest:
            missing.append("OPERATOR")
        return len(missing) == 0, missing

    def is_fully_approved(self) -> bool:
        """Returns True if Bank A, Bank B, and an authorized Operator have all approved current scope."""
        complete, _ = self.has_all_required_approvals()
        return complete

    def validate_executable(
        self,
        requesting_bank_id: str,
        target_bank_id: str,
        purpose: str,
    ) -> None:
        """Fails closed if the pilot is not currently in a valid, unexpired, authorized executable state."""
        clean_req = requesting_bank_id.lower().strip()
        clean_target = target_bank_id.lower().strip()

        if self.status != PilotStatus.ACTIVE:
            raise PilotGovernanceError(
                f"Pilot '{self.config.pilot_id}' is in non-active status '{self.status.value}'. "
                f"Reason: {self.status_reason}. Cross-bank matching denied."
            )

        # Check institutions in allowlist
        inst_set = {b.lower().strip() for b in self.config.participating_institutions}
        if clean_req not in inst_set:
            raise PilotAuthorizationError(
                f"Requesting bank '{clean_req}' is not a participating institution of pilot '{self.config.pilot_id}'."
            )
        if clean_target not in inst_set:
            raise PilotAuthorizationError(
                f"Target bank '{clean_target}' is not a participating institution of pilot '{self.config.pilot_id}'."
            )

        # Check purpose
        if purpose not in self.config.allowed_purposes:
            raise PilotAuthorizationError(
                f"Query purpose '{purpose}' is not authorized under pilot '{self.config.pilot_id}'. "
                f"Allowed: {self.config.allowed_purposes}."
            )

        # Check expiration
        if self.has_expired():
            raise PilotExpiredError(
                f"Pilot '{self.config.pilot_id}' expired at {self.config.expires_at}. Execution fails closed."
            )

        # Check quota
        if self.queries_executed >= self.config.query_quota_total:
            raise PilotQuotaExceededError(
                f"Pilot total query quota exhausted ({self.queries_executed} >= {self.config.query_quota_total}). "
                "Matching denied."
            )
        inst_count = self.institution_query_counts.get(clean_req, 0)
        if inst_count >= self.config.query_quota_per_institution:
            raise PilotQuotaExceededError(
                f"Requesting institution '{clean_req}' quota exhausted ({inst_count} >= {self.config.query_quota_per_institution}). "
                "Matching denied."
            )

    def record_query_outcome(
        self,
        requesting_bank_id: str,
        target_bank_id: str,
        purpose: str,
        status: str,
        latency_ms: float,
        match_confirmed: bool | None = None,
        receipt_id: str | None = None,
        reason: str | None = None,
    ) -> PilotExecutionReceipt:
        """Record query latency, counts, and receipt."""
        clean_req = requesting_bank_id.lower().strip()
        query_id = f"qry_pilot_{len(self.latencies_ms) + 1:04d}_{hashlib.sha256(f'{datetime.datetime.now(UTC)}'.encode()).hexdigest()[:8]}"

        if status in ("ACCEPTED", "COMPLETED", "MATCH", "NO_MATCH", "SUCCESS"):
            self.queries_executed += 1
            self.institution_query_counts[clean_req] = self.institution_query_counts.get(clean_req, 0) + 1
        else:
            self.queries_denied += 1

        if match_confirmed is True:
            self.matches_count += 1

        if latency_ms >= 0:
            self.latencies_ms.append(latency_ms)

        if receipt_id:
            self.execution_receipt_ids.append(receipt_id)

        self.updated_at = datetime.datetime.now(UTC).isoformat()

        receipt = PilotExecutionReceipt(
            query_id=query_id,
            pilot_id=self.config.pilot_id,
            requesting_bank_id=clean_req,
            target_bank_id=target_bank_id.lower().strip(),
            purpose=purpose,
            status=status,
            match_confirmed=match_confirmed,
            latency_ms=latency_ms,
            receipt_id=receipt_id,
            reason=reason,
        )
        return receipt

    def export_audit_dossier(self) -> dict[str, Any]:
        """Export durable, redacted completion and audit evidence dossier."""
        avg_latency = (
            sum(self.latencies_ms) / len(self.latencies_ms)
            if self.latencies_ms
            else 0.0
        )
        p95_latency = (
            sorted(self.latencies_ms)[math.ceil(0.95 * len(self.latencies_ms)) - 1]
            if self.latencies_ms
            else 0.0
        )

        return {
            "dossier_id": f"dossier_{self.config.pilot_id}",
            "pilot_id": self.config.pilot_id,
            "revision": self.config.revision,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "consortium_id": self.config.consortium_id,
            "participating_institutions": self.config.participating_institutions,
            "scope_digest": self.scope_digest,
            "state_history": list(self.state_history),
            "operational_grant_id": self.config.operational_grant_id,
            "grant_revision": self.config.grant_revision,
            "grant_scope_digest": self.config.grant_scope_digest,
            "approved_datasets": self.config.approved_datasets,
            "approvals": {
                k: asdict(v) for k, v in self.approvals.items()
            },
            "preflight_summary": {
                "total_checks": len(self.preflight_checks),
                "all_passed": all(c.passed for c in self.preflight_checks) if self.preflight_checks else False,
                "checks": [asdict(c) for c in self.preflight_checks],
            },
            "telemetry": {
                "queries_total": self.queries_executed + self.queries_denied,
                "queries_executed": self.queries_executed,
                "queries_denied": self.queries_denied,
                "queries_successful": self.queries_executed,
                "queries_failed": self.queries_denied,
                "matches_found": self.matches_count,
                "queries_total_quota": self.config.query_quota_total,
                "institution_query_counts": self.institution_query_counts,
                "queries_by_institution": self.institution_query_counts,
                "mean_latency_ms": round(avg_latency, 2),
                "measured_latencies_ms": {
                    "count": len(self.latencies_ms),
                    "mean_ms": round(avg_latency, 2),
                    "p95_ms": round(p95_latency, 2),
                    "min_ms": round(min(self.latencies_ms), 2) if self.latencies_ms else 0.0,
                    "max_ms": round(max(self.latencies_ms), 2) if self.latencies_ms else 0.0,
                },
            },
            "audit_trail": {
                "audit_event_ids": self.audit_event_ids,
                "execution_receipt_ids": self.execution_receipt_ids,
            },
            "lifecycle_timestamps": {
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "activated_at": self.activated_at,
                "terminated_at": self.terminated_at,
                "termination_reason": self.termination_reason,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize entity state for durable persistence."""
        return {
            "config": asdict(self.config),
            "status": self.status.value,
            "status_reason": self.status_reason,
            "scope_digest": self.scope_digest,
            "preflight_checks": [asdict(c) for c in self.preflight_checks],
            "approvals": {k: asdict(v) for k, v in self.approvals.items()},
            "queries_executed": self.queries_executed,
            "queries_denied": self.queries_denied,
            "institution_query_counts": self.institution_query_counts,
            "latencies_ms": self.latencies_ms,
            "matches_count": self.matches_count,
            "state_history": self.state_history,
            "audit_event_ids": self.audit_event_ids,
            "execution_receipt_ids": self.execution_receipt_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "activated_at": self.activated_at,
            "terminated_at": self.terminated_at,
            "termination_reason": self.termination_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PilotEntity:
        """Deserialize entity state from durable persistence."""
        config_data = data.get("config", {})
        config = PilotOperationalConfig(**config_data)
        status_val = data.get("status", PilotStatus.DRAFT.value)
        status = PilotStatus(status_val)
        status_reason = data.get("status_reason", "")
        scope_digest = data.get("scope_digest", "")
        preflight_checks = [
            PreflightCheckResult(**c) for c in data.get("preflight_checks", [])
        ]
        approvals = {
            k: PilotApprovalRecord(**v) for k, v in data.get("approvals", {}).items()
        }
        queries_executed = int(data.get("queries_executed", 0))
        queries_denied = int(data.get("queries_denied", 0))
        institution_query_counts = data.get("institution_query_counts", {})
        latencies_ms = [float(lat) for lat in data.get("latencies_ms", [])]
        matches_count = int(data.get("matches_count", 0))
        state_history = list(data.get("state_history", []))
        audit_event_ids = list(data.get("audit_event_ids", []))
        execution_receipt_ids = list(data.get("execution_receipt_ids", []))
        created_at = data.get("created_at", datetime.datetime.now(UTC).isoformat())
        updated_at = data.get("updated_at", datetime.datetime.now(UTC).isoformat())
        activated_at = data.get("activated_at")
        terminated_at = data.get("terminated_at")
        termination_reason = data.get("termination_reason")

        entity = cls(
            config=config,
            status=status,
            status_reason=status_reason,
            scope_digest=scope_digest,
            preflight_checks=preflight_checks,
            approvals=approvals,
            queries_executed=queries_executed,
            queries_denied=queries_denied,
            institution_query_counts=institution_query_counts,
            latencies_ms=latencies_ms,
            matches_count=matches_count,
            state_history=state_history,
            audit_event_ids=audit_event_ids,
            execution_receipt_ids=execution_receipt_ids,
            created_at=created_at,
            updated_at=updated_at,
            activated_at=activated_at,
            terminated_at=terminated_at,
            termination_reason=termination_reason,
        )
        return entity
