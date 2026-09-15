# ruff: noqa: UP042
"""Domain models for Zero-Downtime Platform Upgrade & Client Compatibility."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DeploymentStage(str, Enum):
    """Stage enum for zero-downtime rolling upgrades."""

    IDLE = "IDLE"
    DRAINING_CONNECTIONS = "DRAINING_CONNECTIONS"
    ROLLING_UPGRADE = "ROLLING_UPGRADE"
    DUAL_VERSION_ACTIVE = "DUAL_VERSION_ACTIVE"
    UPGRADE_COMPLETED = "UPGRADE_COMPLETED"
    ABORTED = "ABORTED"


@dataclass
class DeploymentSession:
    """Dataclass representing an active zero-downtime deployment session."""

    session_id: str
    target_version: str
    stage: DeploymentStage = DeploymentStage.IDLE
    active_connections_count: int = 100
    drained_connections_count: int = 0
    updated_instances: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    abort_reason: str | None = None


@dataclass
class UpgradeWindow:
    """Dataclass tracking dual-version backward compatibility parameters."""

    current_version: str
    target_version: str
    compatibility_window_hours: int = 48
    deprecated_versions: list[str] = field(default_factory=list)
