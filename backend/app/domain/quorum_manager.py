"""Domain-level Dynamic Quorum Timeout Manager.

Monitors training round submission progress across bank nodes.
Automatically triggers round aggregation as soon as minimum quorum (e.g. >= 60%)
of registered bank nodes submit updates within the target window (e.g. 300 seconds).
"""

from __future__ import annotations

import datetime
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class QuorumState(str, Enum):  # noqa: UP042
    """Quorum status for a federated learning training round."""

    WAITING = "WAITING"
    QUORUM_REACHED = "QUORUM_REACHED"
    TIMEOUT_EXPIRED = "TIMEOUT_EXPIRED"


@dataclass
class RoundQuorumStatus:
    """Status details for a round's quorum progress."""

    round_number: int
    registered_nodes_count: int
    submitted_nodes_count: int
    quorum_threshold_pct: float
    current_quorum_pct: float
    state: QuorumState
    start_time: str
    target_window_seconds: int
    time_remaining_seconds: float


@dataclass
class DynamicQuorumManager:
    """Manages dynamic quorum monitoring, threshold verification, and auto-aggregation triggers."""

    quorum_threshold_pct: float = 0.60  # Minimum 60% of nodes must submit
    target_window_seconds: int = 300  # 5-minute target window
    registered_nodes: set[str] = field(default_factory=set)
    submitted_nodes: set[str] = field(default_factory=set)
    round_start_time: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register_nodes(self, node_ids: list[str]) -> None:
        """Registers participating bank nodes for the active training round (thread-safe)."""
        with self._lock:
            self.registered_nodes = set(node_ids)
            self.submitted_nodes.clear()
            self.round_start_time = datetime.datetime.now(datetime.UTC)
            logger.info(
                "Registered %d nodes for dynamic quorum monitoring (threshold=%.0f%%)",
                len(self.registered_nodes),
                self.quorum_threshold_pct * 100,
            )

    def record_node_submission(self, node_id: str) -> QuorumState:
        """Records a gradient/weight submission from a bank node and checks quorum condition (thread-safe)."""
        with self._lock:
            if node_id in self.registered_nodes:
                self.submitted_nodes.add(node_id)

        return self.evaluate_quorum_status().state

    def evaluate_quorum_status(self, round_number: int = 1) -> RoundQuorumStatus:
        """Evaluates current submission progress against quorum threshold (>= 60%) and timeout window."""
        with self._lock:
            total_registered = max(1, len(self.registered_nodes))
            total_submitted = len(self.submitted_nodes)
            current_pct = round(total_submitted / total_registered, 4)

            now = datetime.datetime.now(datetime.UTC)
            elapsed = (now - self.round_start_time).total_seconds()
            time_remaining = max(0.0, self.target_window_seconds - elapsed)

            if current_pct >= self.quorum_threshold_pct:
                state = QuorumState.QUORUM_REACHED
            elif elapsed >= self.target_window_seconds:
                state = QuorumState.TIMEOUT_EXPIRED
            else:
                state = QuorumState.WAITING

            start_iso = self.round_start_time.isoformat()

        logger.debug(
            "Quorum eval: round %d, submitted %d/%d (%.1f%%, threshold=%.1f%%, state=%s)",
            round_number,
            total_submitted,
            total_registered,
            current_pct * 100,
            self.quorum_threshold_pct * 100,
            state.value,
        )

        return RoundQuorumStatus(
            round_number=round_number,
            registered_nodes_count=total_registered,
            submitted_nodes_count=total_submitted,
            quorum_threshold_pct=self.quorum_threshold_pct,
            current_quorum_pct=current_pct,
            state=state,
            start_time=start_iso,
            target_window_seconds=self.target_window_seconds,
            time_remaining_seconds=round(time_remaining, 2),
        )
