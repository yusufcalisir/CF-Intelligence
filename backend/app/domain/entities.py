"""Domain entities.

Core business objects with identity. These represent the primary concepts
in the federated learning simulation domain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import (
    BankTier,
    ClientStatus,
    DatasetMode,
    DatasetProvenance,
    SimulationStatus,
)
from app.domain.value_objects import BankDataProfile, EvaluationMetrics, SimulationConfig


@dataclass
class Bank:
    """A participating financial institution in the federated learning simulation.

    Each bank owns independent transaction data with distinct statistical
    properties (Non-IID). Banks never share raw data — only model parameters.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    tier: BankTier = BankTier.MEDIUM
    fraud_ratio: float = 0.01
    num_transactions: int = 10000
    status: ClientStatus = ClientStatus.ACTIVE
    data_profile: BankDataProfile | None = None
    local_metrics: EvaluationMetrics | None = None
    federated_metrics: EvaluationMetrics | None = None
    contribution_score: float = 0.0
    quarantined: bool = False

    @property
    def improvement(self) -> dict[str, float | None] | None:
        """Calculate metric improvements from local to federated model."""
        if not self.local_metrics or not self.federated_metrics:
            return None
        auc_delta: float | None = None
        if self.federated_metrics.auc_roc is not None and self.local_metrics.auc_roc is not None:
            auc_delta = self.federated_metrics.auc_roc - self.local_metrics.auc_roc

        return {
            "accuracy": self.federated_metrics.accuracy - self.local_metrics.accuracy,
            "precision": self.federated_metrics.precision - self.local_metrics.precision,
            "recall": self.federated_metrics.recall - self.local_metrics.recall,
            "f1_score": self.federated_metrics.f1_score - self.local_metrics.f1_score,
            "auc_roc": auc_delta,
        }


@dataclass
class SimulationRun:
    """A complete end-to-end federated learning simulation.

    Tracks lifecycle from data generation through training to evaluation.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: SimulationStatus = SimulationStatus.PENDING
    config: SimulationConfig = field(default_factory=SimulationConfig)
    banks: list[Bank] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_round: int = 0
    total_rounds: int = 10
    error_message: str | None = None
    rounds: list[TrainingRound] = field(default_factory=list)
    rounds_run: int = 0

    # Hardware/Cryptographic Isolation telemetry
    tee_mrenclave: str | None = None
    tee_mrsigner: str | None = None
    tee_attestation_signature: str | None = None
    fhe_poly_degree: int | None = None
    fhe_noise_bound: float | None = None
    fhe_key_id: str | None = None
    fhe_is_emulated: bool = False
    fhe_driver_mode: str | None = None

    # Streaming GNN Telemetry
    streaming_gnn_node_count: int = 0
    streaming_gnn_edge_count: int = 0
    streaming_gnn_loss_history: list[float] = field(default_factory=list)

    # Web3 & CBDC Smart Contract Settlement
    settlement_tx_hash: str | None = None
    settlement_block_number: int | None = None
    settlement_status: str | None = None
    on_chain_payouts: list[dict[str, Any]] = field(default_factory=list)

    # Dataset Mode & Provenance Telemetry (unresolved until data is loaded)
    dataset_mode: DatasetMode | str | None = None
    dataset_provenance: DatasetProvenance | str | None = None

    @property
    def is_provenance_resolved(self) -> bool:
        """Returns True only when both dataset mode and provenance have been authoritatively resolved."""
        return self.dataset_mode is not None and self.dataset_provenance is not None

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration of the simulation."""
        if not self.started_at or not self.completed_at:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def progress_pct(self) -> float:
        """Completion percentage based on current round."""
        if self.total_rounds == 0:
            return 0.0
        return min(100.0, (self.current_round / self.total_rounds) * 100)


@dataclass
class TrainingRound:
    """A single round of federated training.

    In each round, participating banks train locally and send model
    updates to the aggregator. Some banks may drop out.
    """

    round_number: int
    simulation_id: str
    participating_bank_ids: list[str] = field(default_factory=list)
    dropped_bank_ids: list[str] = field(default_factory=list)
    global_loss: float = 0.0
    per_bank_loss: dict[str, float] = field(default_factory=dict)
    per_bank_samples: dict[str, int] = field(default_factory=dict)
    aggregation_time_ms: float = 0.0
    round_duration_ms: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    canary_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class BankRegistration:
    """Enterprise registration record of a participating bank node."""

    bank_id: str
    legal_name: str
    jurisdiction: str
    contact_email: str
    data_residency_region: str
    status: Any = "pending_verification"
    cert_fingerprint: str | None = None
    vault_key_path: str | None = None
    schema_provisioned: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    activated_at: datetime | None = None
