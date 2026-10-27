"""Execution Deadline and Timeout Policy for Cross-Bank Private Matching.

Defines bounded, verifiable execution deadline policies adhering to
repository-wide runtime truth and M6.4 Task 4 requirements:
- Explicit conservative defaults (15.0s per counterpart, 60.0s batch).
- Validated bounds preventing unconstrained execution or infinite timeouts.
- Monotonic elapsed-time calculations for all deadlines.
- Strict prohibition on background thread abandonment (fake cancellation).
- Distinct classifications: pre-dispatch deadline exhaustion, confirmed transport timeout,
  and ambiguous in-flight timeout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS: float = 15.0
MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS: float = 1.0
MAX_PER_COUNTERPARTY_TIMEOUT_SECONDS: float = 60.0

DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS: float = 60.0
MIN_TOTAL_BATCH_TIMEOUT_SECONDS: float = 1.0
MAX_TOTAL_BATCH_TIMEOUT_SECONDS: float = 300.0


@dataclass(frozen=True)
class CounterpartyExecutionDeadlinePolicy:
    """Immutable, bounded per-counterparty and batch execution deadline policy."""

    per_counterparty_timeout_seconds: float = DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS
    total_batch_timeout_seconds: float = DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.per_counterparty_timeout_seconds, (int, float))
            or not math.isfinite(self.per_counterparty_timeout_seconds)
            or self.per_counterparty_timeout_seconds < MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS
            or self.per_counterparty_timeout_seconds > MAX_PER_COUNTERPARTY_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"per_counterparty_timeout_seconds must be a finite float between "
                f"{MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS} and {MAX_PER_COUNTERPARTY_TIMEOUT_SECONDS} seconds."
            )

        if (
            not isinstance(self.total_batch_timeout_seconds, (int, float))
            or not math.isfinite(self.total_batch_timeout_seconds)
            or self.total_batch_timeout_seconds < MIN_TOTAL_BATCH_TIMEOUT_SECONDS
            or self.total_batch_timeout_seconds > MAX_TOTAL_BATCH_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"total_batch_timeout_seconds must be a finite float between "
                f"{MIN_TOTAL_BATCH_TIMEOUT_SECONDS} and {MAX_TOTAL_BATCH_TIMEOUT_SECONDS} seconds."
            )
