"""Circuit Breaker and Recovery Admission Policy for Cross-Bank Private Matching.

Defines bounded, verifiable circuit breaker policies adhering strictly to
.agents/AGENTS.md, runtime truth, and M6.4 Task 5 requirements:
- Scoped to consortium_id, requesting_bank_id, and target_bank_id.
- Explicit states: CLOSED, OPEN, HALF_OPEN.
- Validated bounded failure thresholds and cooldown periods.
- Single-probe recovery admission gate across concurrent service instances.
- Zero process-local shortcuts that can be bypassed by selecting another replica.
- Default behavior preserves existing deployments unless explicitly enabled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from app.domain.consortium_governance import ConsortiumSecurityError

DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 3
MIN_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 1
MAX_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 50

DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 60.0
MIN_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 0.001
MAX_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 3600.0

DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS: float = 30.0
MIN_HALF_OPEN_PROBE_TIMEOUT_SECONDS: float = 0.001
MAX_HALF_OPEN_PROBE_TIMEOUT_SECONDS: float = 300.0


class CircuitState(StrEnum):
    """Authoritative states for cross-bank counterparty circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CounterpartyCircuitBreakerOpenError(ConsortiumSecurityError):
    """Raised when dispatch to a counterpart institution is blocked by an OPEN circuit breaker."""

    def __init__(
        self,
        message: str = "Circuit breaker is OPEN for counterpart institution. Dispatch rejected.",
        target_bank_id: str | None = None,
        consortium_id: str | None = None,
        requesting_bank_id: str | None = None,
        cooldown_remaining_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.target_bank_id = target_bank_id
        self.consortium_id = consortium_id
        self.requesting_bank_id = requesting_bank_id
        self.cooldown_remaining_seconds = cooldown_remaining_seconds


@dataclass(frozen=True)
class CounterpartyCircuitBreakerPolicy:
    """Immutable, bounded per-counterparty circuit breaker and recovery admission policy."""

    enabled: bool = False
    failure_threshold: int = DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    half_open_probe_timeout_seconds: float = DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.failure_threshold, int)
            or isinstance(self.failure_threshold, bool)
            or self.failure_threshold < MIN_CIRCUIT_BREAKER_FAILURE_THRESHOLD
            or self.failure_threshold > MAX_CIRCUIT_BREAKER_FAILURE_THRESHOLD
        ):
            raise ValueError(
                f"failure_threshold must be an integer between "
                f"{MIN_CIRCUIT_BREAKER_FAILURE_THRESHOLD} and {MAX_CIRCUIT_BREAKER_FAILURE_THRESHOLD}."
            )

        if (
            not isinstance(self.cooldown_seconds, (int, float))
            or not math.isfinite(self.cooldown_seconds)
            or self.cooldown_seconds < MIN_CIRCUIT_BREAKER_COOLDOWN_SECONDS
            or self.cooldown_seconds > MAX_CIRCUIT_BREAKER_COOLDOWN_SECONDS
        ):
            raise ValueError(
                f"cooldown_seconds must be a finite float between "
                f"{MIN_CIRCUIT_BREAKER_COOLDOWN_SECONDS} and {MAX_CIRCUIT_BREAKER_COOLDOWN_SECONDS} seconds."
            )

        if (
            not isinstance(self.half_open_probe_timeout_seconds, (int, float))
            or not math.isfinite(self.half_open_probe_timeout_seconds)
            or self.half_open_probe_timeout_seconds < MIN_HALF_OPEN_PROBE_TIMEOUT_SECONDS
            or self.half_open_probe_timeout_seconds > MAX_HALF_OPEN_PROBE_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"half_open_probe_timeout_seconds must be a finite float between "
                f"{MIN_HALF_OPEN_PROBE_TIMEOUT_SECONDS} and {MAX_HALF_OPEN_PROBE_TIMEOUT_SECONDS} seconds."
            )
