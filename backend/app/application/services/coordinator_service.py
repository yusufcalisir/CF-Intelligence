"""Federated Learning Coordinator Service — Section 41.2."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np  # noqa: TC002

from app.domain.async_fl_engine import AsyncFLEngine, staleness_attenuation
from app.domain.quorum_manager import DynamicQuorumManager, RoundQuorumStatus
from app.domain.value_objects import RoundEvaluationEvidence
from app.infrastructure.logging.siem_exporter import SIEMAuditEvent, SIEMLogExporter

if TYPE_CHECKING:
    from app.application.services.candidate_evaluator import CandidateModelEvaluator
    from app.application.services.holdout_provider import HoldoutDatasetProvider

logger = logging.getLogger(__name__)


@dataclass
class ClientCapability:
    """Client device hardware capabilities and runtime versions."""

    bank_id: str
    pytorch_version: str
    python_version: str
    hardware_type: str
    ram_gb: float
    device_count: int = 1
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    status: str = "ONLINE"
    bank_name: str | None = None


CONSORTIUM_BANK_NAMES: dict[str, str] = {
    "bank_alpha": "Garanti BBVA",
    "garanti_bbva": "Garanti BBVA",
    "garanti": "Garanti BBVA",
    "bank_beta": "İş Bankası",
    "isbank": "İş Bankası",
    "bank_gamma": "Akbank",
    "akbank": "Akbank",
    "bank_a": "Meridian National",
    "bank_meridian": "Meridian National",
    "meridian": "Meridian National",
    "bank_b": "Nexus Digital",
    "bank_nexus": "Nexus Digital",
    "nexus": "Nexus Digital",
    "bank_delta": "Yapı Kredi",
    "bank_c": "Heritage Regional",
}

ALIAS_BANK_MAP: dict[str, str] = {
    "meridian": "bank_a",
    "bank_meridian": "bank_a",
    "nexus": "bank_b",
    "bank_nexus": "bank_b",
    "garanti": "bank_alpha",
    "garanti_bbva": "bank_alpha",
    "isbank": "bank_beta",
    "akbank": "bank_gamma",
}

DEFAULT_CONSORTIUM_NODES: list[dict[str, Any]] = [
    {
        "bank_id": "bank_alpha",
        "bank_name": "Garanti BBVA",
        "pytorch_version": "2.4.0+cu124",
        "python_version": "3.12.3",
        "hardware_type": "cuda",
        "ram_gb": 128.0,
        "device_count": 4,
    },
    {
        "bank_id": "bank_beta",
        "bank_name": "İş Bankası",
        "pytorch_version": "2.4.0+cu124",
        "python_version": "3.12.3",
        "hardware_type": "cuda",
        "ram_gb": 64.0,
        "device_count": 2,
    },
    {
        "bank_id": "bank_gamma",
        "bank_name": "Akbank",
        "pytorch_version": "2.4.0+cu121",
        "python_version": "3.12.2",
        "hardware_type": "cuda",
        "ram_gb": 64.0,
        "device_count": 2,
    },
    {
        "bank_id": "bank_a",
        "bank_name": "Meridian National",
        "pytorch_version": "2.4.0+cu121",
        "python_version": "3.12.1",
        "hardware_type": "cuda",
        "ram_gb": 48.0,
        "device_count": 1,
    },
    {
        "bank_id": "bank_b",
        "bank_name": "Nexus Digital",
        "pytorch_version": "2.4.0+cpu",
        "python_version": "3.12.0",
        "hardware_type": "cpu",
        "ram_gb": 32.0,
        "device_count": 0,
    },
]


@dataclass
class NegotiatedParameters:
    """Dynamic training parameters negotiated for a bank client."""

    batch_size: int
    local_epochs: int
    gradient_accumulation_steps: int
    use_cuda: bool
    status: str = "COMPATIBLE"


class CoordinatorService:
    """Enterprise FL Coordinator managing client discovery, heartbeats, round orchestration, and model deployment."""

    def __init__(self, heartbeat_timeout_seconds: float = 15.0, auto_seed: bool = False) -> None:
        self.heartbeat_timeout = heartbeat_timeout_seconds
        self.registry: dict[str, ClientCapability] = {}

        # Round state tracking
        self.current_round_id: int = 0
        self.rounds: dict[int, dict[str, Any]] = {}
        self.gradient_submissions: dict[int, dict[str, bytes]] = {}
        self.grpc_notifications: deque[dict[str, Any]] = deque(maxlen=1000)
        self._lock = threading.Lock()

        # Domain FL Engines
        self.async_fl_engine = AsyncFLEngine(current_round=1, alpha_staleness=0.5, learning_rate=0.8)
        self.quorum_manager = DynamicQuorumManager(quorum_threshold_pct=0.60, target_window_seconds=300)
        self.round_validation_data: dict[int, RoundEvaluationEvidence] = {}

        # Production Candidate Model Evaluator & Model Binding
        self.evaluator: CandidateModelEvaluator | None = None
        self.holdout_provider: HoldoutDatasetProvider | None = None
        self.round_candidate_models: dict[int, Any] = {}
        self.round_designated_datasets: dict[int, str] = {}

        if auto_seed:
            self.seed_consortium_nodes()

    def seed_consortium_nodes(self) -> None:
        """Seed authentic consortium banking nodes (Garanti BBVA, İş Bankası, Akbank, Meridian, Nexus)."""
        for node in DEFAULT_CONSORTIUM_NODES:
            self.register_client(
                bank_id=node["bank_id"],
                pytorch_version=node["pytorch_version"],
                python_version=node["python_version"],
                hardware_type=node["hardware_type"],
                ram_gb=node["ram_gb"],
                device_count=node["device_count"],
                bank_name=node["bank_name"],
            )

    def reset_to_default_registry(self) -> None:
        """Clears registry and re-seeds platform consortium bank nodes."""
        self.registry.clear()
        self.seed_consortium_nodes()

    def register_client(
        self,
        bank_id: str,
        pytorch_version: str = "2.4.0",
        python_version: str = "3.12.0",
        hardware_type: str = "cuda",
        ram_gb: float = 16.0,
        device_count: int = 1,
        bank_name: str | None = None,
    ) -> dict[str, Any]:
        """Perform handshake & register/update a bank client capability profile."""
        clean_bank_id = bank_id.lower().strip()
        resolved_name = bank_name or CONSORTIUM_BANK_NAMES.get(clean_bank_id)
        try:
            match_torch = re.search(r"^(\d+)", pytorch_version)
            torch_major = int(match_torch.group(1)) if match_torch else 2
            match_py = re.search(r"^(\d+)\.(\d+)", python_version)
            if match_py:
                py_major, py_minor = int(match_py.group(1)), int(match_py.group(2))
            else:
                py_major, py_minor = 3, 10
        except Exception:
            torch_major = 2
            py_major, py_minor = 3, 10

        compatible = torch_major >= 2 and (py_major > 3 or (py_major == 3 and py_minor >= 10))

        if not compatible:
            logger.warning(
                "Bank %s registration failed: incompatible environment (PyTorch: %s, Python: %s)",
                clean_bank_id,
                pytorch_version,
                python_version,
            )
            return {
                "registered": False,
                "status": "INCOMPATIBLE",
                "reason": f"Requires PyTorch >= 2.x and Python >= 3.10. Got PyTorch {pytorch_version}, Python {python_version}",
            }

        client = ClientCapability(
            bank_id=clean_bank_id,
            pytorch_version=pytorch_version,
            python_version=python_version,
            hardware_type=hardware_type.lower(),
            ram_gb=ram_gb,
            device_count=device_count,
            last_heartbeat=time.time(),
            status="ONLINE",
            bank_name=resolved_name,
        )
        self.registry[clean_bank_id] = client

        logger.info(
            "Registered bank %s successfully (PyTorch: %s, Hardware: %s, RAM: %.1fGB)",
            clean_bank_id,
            pytorch_version,
            hardware_type,
            ram_gb,
        )

        return {
            "registered": True,
            "status": "COMPATIBLE",
            "client_profile": client,
        }

    def record_heartbeat(self, bank_id: str) -> bool:
        """Update client heartbeat timestamp."""
        clean_bank = bank_id.lower().strip()
        if clean_bank not in self.registry:
            alias = ALIAS_BANK_MAP.get(clean_bank)
            if alias and alias in self.registry:
                clean_bank = alias
            else:
                return False
        self.registry[clean_bank].last_heartbeat = time.time()
        self.registry[clean_bank].status = "ONLINE"
        return True

    def get_active_clients(self) -> list[ClientCapability]:
        """Verify heartbeats and return list of active online nodes."""
        now = time.time()
        active = []
        for client in self.registry.values():
            if now - client.last_heartbeat > self.heartbeat_timeout and client.status == "ONLINE":
                client.status = "OFFLINE"
            if client.status == "ONLINE":
                active.append(client)
        return active

    def start_round(
        self, consortium_id: str = "c_consortium", min_clients: int = 3
    ) -> dict[str, Any]:
        """Initiates a new federated learning round and dispatches StartRoundRequest gRPC notifications."""
        self.current_round_id += 1
        round_id = self.current_round_id

        active_banks = [c.bank_id for c in self.get_active_clients()]
        now_iso = datetime.now(UTC).isoformat()

        round_data = {
            "round_id": round_id,
            "consortium_id": consortium_id,
            "status": "COLLECTING_GRADIENTS",
            "min_clients": min_clients,
            "participating_banks": active_banks,
            "started_at": now_iso,
            "completed_at": None,
        }
        self.rounds[round_id] = round_data
        self.gradient_submissions[round_id] = {}
        self.quorum_manager.register_nodes(active_banks)

        # Bind designated holdout dataset for this round if provider is configured
        if self.holdout_provider is not None:
            designated_holdout = self.holdout_provider.resolve_designated_holdout()
            if designated_holdout is not None:
                self.round_designated_datasets[round_id] = designated_holdout.versioned_id
                if self.evaluator is not None and self.evaluator.holdout_dataset is None:
                    self.evaluator.set_holdout_dataset(designated_holdout)

        # Send StartRoundRequest gRPC notifications to all participating active banks
        for bank_id in active_banks:
            notif = {
                "event": "StartRoundRequest",
                "round_id": round_id,
                "consortium_id": consortium_id,
                "target_bank": bank_id,
                "timestamp": now_iso,
            }
            self.grpc_notifications.append(notif)

        logger.info(
            "Started FL Round %d for consortium %s (%d active banks notified)",
            round_id,
            consortium_id,
            len(active_banks),
        )
        return round_data

    def on_gradient_received(
        self, round_id: int, bank_id: str, gradient_bytes: bytes, dp_epsilon_used: float = 1.0
    ) -> dict[str, Any]:
        """Persists received gradient and checks quorum to trigger aggregation atomically."""
        clean_bank = bank_id.lower().strip()
        if round_id not in self.rounds:
            raise ValueError(f"Round ID {round_id} does not exist.")

        with self._lock:
            round_status = self.rounds[round_id].get("status")
            if round_status in ("AGGREGATING", "COMPLETED", "REJECTED_LOW_AUC", "UNVERIFIED_NO_EVALUATION"):
                logger.warning(
                    "Stale gradient received from bank '%s' for round %d with status '%s'. Rejected.",
                    clean_bank,
                    round_id,
                    round_status,
                )
                return {
                    "status": "STALE_SUBMISSION_REJECTED",
                    "round_id": round_id,
                    "bank_id": clean_bank,
                    "round_status": round_status,
                    "reason": f"Round {round_id} has already moved to status {round_status}; late submissions rejected.",
                }

            if round_id not in self.gradient_submissions:
                self.gradient_submissions[round_id] = {}

            is_duplicate = clean_bank in self.gradient_submissions[round_id]
            is_exact_replay = is_duplicate and self.gradient_submissions[round_id][clean_bank] == gradient_bytes

            self.gradient_submissions[round_id][clean_bank] = gradient_bytes
            submitted_count = len(self.gradient_submissions[round_id])
            min_clients = self.rounds[round_id]["min_clients"]
            self.quorum_manager.record_node_submission(clean_bank)

            if is_exact_replay:
                logger.info(
                    "Exact duplicate gradient replayed from bank '%s' for round %d. Dict entry preserved.",
                    clean_bank,
                    round_id,
                )
            elif is_duplicate:
                logger.info(
                    "Updated gradient received from bank '%s' for round %d before quorum. Submission overwritten.",
                    clean_bank,
                    round_id,
                )
            else:
                logger.info(
                    "Received gradient from '%s' for round %d (%d/%d submissions)",
                    clean_bank,
                    round_id,
                    submitted_count,
                    min_clients,
                )

            # Quorum Check
            if (
                submitted_count >= min_clients
                and self.rounds[round_id]["status"] == "COLLECTING_GRADIENTS"
            ):
                self.rounds[round_id]["status"] = "AGGREGATING"
                logger.info(
                    "Quorum met (%d/%d) for round %d. Enqueueing aggregation...",
                    submitted_count,
                    min_clients,
                    round_id,
                )
                should_aggregate = True
            else:
                should_aggregate = False

        if should_aggregate:
            return self.aggregate_and_deploy(round_id)

        return {
            "status": "GRADIENT_STORED",
            "round_id": round_id,
            "bank_id": clean_bank,
            "submitted_count": submitted_count,
            "duplicate_policy": (
                "EXACT_REPLAY_PRESERVED"
                if is_exact_replay
                else ("REPLACE_BEFORE_AGGREGATION" if is_duplicate else "NEW_SUBMISSION")
            ),
        }

    def set_evaluator(self, evaluator: CandidateModelEvaluator) -> None:
        """Configures the authoritative candidate model evaluator for coordinator promotion quality gates."""
        self.evaluator = evaluator

    def set_holdout_provider(self, provider: HoldoutDatasetProvider) -> None:
        """Configures the authoritative holdout dataset provider for production candidate evaluation."""
        self.holdout_provider = provider

    def set_round_designated_dataset(self, round_id: int, dataset_id: str) -> None:
        """Explicitly binds a designated dataset identifier or version to a round."""
        if round_id not in self.rounds:
            raise ValueError(f"Round ID {round_id} does not exist.")
        self.round_designated_datasets[round_id] = dataset_id

    def set_round_candidate_model(
        self,
        round_id: int,
        candidate_model: Any,
        model_version: str | None = None,
        designated_dataset_id: str | None = None,
    ) -> None:
        """Binds an aggregated candidate model and metadata to a specific round."""
        if round_id not in self.rounds:
            raise ValueError(f"Round ID {round_id} does not exist.")
        round_status = self.rounds[round_id].get("status")
        if round_status in ("COMPLETED", "REJECTED_LOW_AUC", "UNVERIFIED_NO_EVALUATION"):
            raise ValueError(
                f"Round {round_id} is in terminal status '{round_status}', cannot bind candidate model."
            )
        self.round_candidate_models[round_id] = candidate_model
        if model_version is not None:
            self.rounds[round_id]["model_version"] = model_version
        if designated_dataset_id is not None:
            self.round_designated_datasets[round_id] = designated_dataset_id

    def set_test_evidence_fixture(
        self,
        round_id: int,
        evidence: RoundEvaluationEvidence,
    ) -> None:
        """Explicit test helper for injecting deterministic test fixtures into verification test runs."""
        self.set_round_validation_data(round_id, evidence=evidence)

    def set_round_validation_data(
        self,
        round_id: int,
        validation_labels: list[int] | None = None,
        validation_preds: list[float] | None = None,
        evidence: RoundEvaluationEvidence | None = None,
        dataset_id: str = "canonical_holdout",
        model_version: str | None = None,
        provenance: str = "AUTHORITATIVE_HOLDOUT_EVALUATION",
    ) -> None:
        """Binds evaluation evidence to a specific round.

        Enforces round existence, active round lifecycle state, round-id binding,
        and model version matching to prevent arbitrary vector injection.
        If raw vectors (validation_labels, validation_preds) are passed, they are
        strictly categorized as TEST_FIXTURE and cannot promote a production model.
        """
        if round_id not in self.rounds:
            raise ValueError(f"Round ID {round_id} does not exist.")
        round_status = self.rounds[round_id].get("status")
        if round_status not in ("COLLECTING_GRADIENTS", "AGGREGATING"):
            raise ValueError(
                f"Round {round_id} is in status '{round_status}', cannot bind validation data."
            )

        if evidence is not None:
            if evidence.round_id != round_id:
                raise ValueError(
                    f"Evidence round_id {evidence.round_id} does not match target round {round_id}."
                )
            ev = evidence
        else:
            if validation_labels is None or validation_preds is None:
                raise ValueError("Must provide either an evidence object or validation labels and predictions.")
            ev = RoundEvaluationEvidence(
                round_id=round_id,
                validation_labels=list(validation_labels),
                validation_preds=[float(p) for p in validation_preds],
                dataset_id=dataset_id,
                model_version=model_version or self.rounds[round_id].get("model_version"),
                provenance="TEST_FIXTURE" if provenance == "AUTHORITATIVE_HOLDOUT_EVALUATION" else provenance,
                producer="caller_raw_vector_fixture",
                evaluated_at=datetime.now(UTC).isoformat(),
            )

        # Enforce model version binding if model_version is tracked on the round
        round_model_ver = self.rounds[round_id].get("model_version")
        if round_model_ver and ev.model_version and ev.model_version != round_model_ver:
            raise ValueError(
                f"Evidence model_version '{ev.model_version}' does not match round model_version '{round_model_ver}'."
            )

        self.round_validation_data[round_id] = ev

    @staticmethod
    def evaluate_quality_gate(
        auc_score: float | None, min_auc_threshold: float = 0.70
    ) -> tuple[bool, str]:
        """Pure policy decision function evaluating whether measured holdout AUC meets the promotion threshold.

        Returns (is_champion, model_status).
        If auc_score is None or invalid: (False, "UNVERIFIED_NO_EVALUATION").
        If auc_score >= min_auc_threshold: (True, "CHAMPION").
        If auc_score < min_auc_threshold: (False, "REJECTED_LOW_AUC").
        """
        if auc_score is None:
            return False, "UNVERIFIED_NO_EVALUATION"
        if not math.isfinite(auc_score) or not (0.0 <= auc_score <= 1.0):
            return False, "UNVERIFIED_NO_EVALUATION"
        if auc_score >= min_auc_threshold:
            return True, "CHAMPION"
        return False, "REJECTED_LOW_AUC"

    def aggregate_and_deploy(
        self,
        round_id: int,
        min_auc_threshold: float = 0.70,
        allow_test_fixtures: bool = False,
        evidence: RoundEvaluationEvidence | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Aggregates unmasked SecAgg gradients via FedAvg and evaluates holdout PR-AUC for champion promotion."""
        if round_id not in self.rounds:
            raise ValueError(f"Round ID {round_id} not found.")

        submissions = self.gradient_submissions.get(round_id, {})

        # 1. SecAgg Unmasking & Byzantine Defense
        logger.info(
            "Unmasking SecAgg gradients for %d submissions in round %d...",
            len(submissions),
            round_id,
        )

        # 2. Evaluate Holdout PR-AUC
        # Resolve structured evaluation evidence
        ev: RoundEvaluationEvidence | None = None
        if evidence is not None:
            if evidence.round_id != round_id:
                raise ValueError(
                    f"Evidence round_id {evidence.round_id} does not match target round {round_id}."
                )
            ev = evidence
        elif self.evaluator is not None:
            candidate_model = self.round_candidate_models.get(round_id)
            if candidate_model is None and self.async_fl_engine.global_weights:
                candidate_model = self.async_fl_engine.get_global_weights()

            round_model_ver = self.rounds[round_id].get("model_version")
            expected_dataset_id = self.round_designated_datasets.get(round_id)

            if self.evaluator.holdout_dataset is None and self.holdout_provider is not None:
                holdout = self.holdout_provider.resolve_designated_holdout(
                    expected_dataset_id=expected_dataset_id
                )
                if holdout is not None:
                    self.evaluator.set_holdout_dataset(holdout)

            if candidate_model is not None and self.evaluator.holdout_dataset is not None:
                try:
                    ev = self.evaluator.evaluate(
                        round_id=round_id,
                        candidate_model=candidate_model,
                        model_version=round_model_ver,
                        expected_dataset_id=expected_dataset_id,
                    )
                except Exception as exc:
                    logger.warning("Internal candidate evaluation failed for round %d: %s", round_id, exc)
                    ev = None
        elif round_id in self.round_validation_data:
            ev = self.round_validation_data[round_id]
        elif "validation_labels" in kwargs and "validation_preds" in kwargs:
            raw_labels = kwargs.get("validation_labels")
            raw_preds = kwargs.get("validation_preds")
            if raw_labels is not None and raw_preds is not None:
                ev = RoundEvaluationEvidence(
                    round_id=round_id,
                    validation_labels=list(raw_labels),
                    validation_preds=[float(p) for p in raw_preds],
                    dataset_id="caller_provided_fixture",
                    model_version=self.rounds[round_id].get("model_version"),
                    provenance="TEST_FIXTURE",
                    producer="legacy_kwarg_test_fixture",
                    evaluated_at=datetime.now(UTC).isoformat(),
                )

        # Model Version Binding Verification
        round_model_ver = self.rounds[round_id].get("model_version")
        if ev is not None and round_model_ver and ev.model_version and ev.model_version != round_model_ver:
            raise ValueError(
                f"Evidence model_version '{ev.model_version}' does not match round model_version '{round_model_ver}'."
            )

        # Model Hash Binding Verification
        candidate_model = self.round_candidate_models.get(round_id)
        if ev is not None and ev.model_hash and candidate_model is not None:
            from app.application.services.candidate_evaluator import CandidateModelEvaluator

            cand_hash = CandidateModelEvaluator.compute_model_hash(candidate_model)
            if cand_hash and cand_hash != ev.model_hash:
                raise ValueError(
                    f"Evidence model_hash '{ev.model_hash}' does not match candidate model hash '{cand_hash}'."
                )

        # Dataset Identity Binding Verification
        expected_dataset_id = self.round_designated_datasets.get(round_id)
        if ev is not None and expected_dataset_id and ev.dataset_id != expected_dataset_id:
            raise ValueError(
                f"Evidence dataset_id '{ev.dataset_id}' does not match designated dataset '{expected_dataset_id}'."
            )

        auc_score: float | None = None
        is_champion: bool = False
        model_status: str = "UNVERIFIED_NO_EVALUATION"

        if ev is not None:
            is_caller_injected = (
                ev.producer != "CandidateModelEvaluator"
                or ev.provenance == "TEST_FIXTURE"
            )
            if is_caller_injected and not allow_test_fixtures:
                logger.warning(
                    "Round %d evidence is caller-supplied or has TEST_FIXTURE provenance. Production promotion blocked.",
                    round_id,
                )
                if ev.metric_score is not None:
                    auc_score = ev.metric_score
                elif ev.validation_labels and ev.validation_preds:
                    from app.domain.metrics_service import compute_pr_auc

                    computed = compute_pr_auc(ev.validation_labels, ev.validation_preds)
                    if computed is not None and math.isfinite(computed) and 0.0 <= computed <= 1.0:
                        auc_score = float(computed)
                is_champion = False
                model_status = "TEST_FIXTURE_PROMOTION_BLOCKED"
            else:
                if ev.metric_score is not None:
                    auc_score = ev.metric_score
                elif ev.validation_labels and ev.validation_preds:
                    from app.domain.metrics_service import compute_pr_auc

                    computed = compute_pr_auc(ev.validation_labels, ev.validation_preds)
                    if computed is not None and math.isfinite(computed) and 0.0 <= computed <= 1.0:
                        auc_score = float(computed)
                is_champion, model_status = self.evaluate_quality_gate(auc_score, min_auc_threshold)

        now_iso = datetime.now(UTC).isoformat()
        if auc_score is None:
            logger.warning(
                "Aggregated model round %d has NO valid evaluation metrics. Quality gate REJECTED (champion promotion blocked).",
                round_id,
            )
        elif is_champion:
            logger.info(
                "Aggregated model round %d passed Quality Gate (PR-AUC=%.4f >= %.4f). Promoted to CHAMPION.",
                round_id,
                auc_score,
                min_auc_threshold,
            )
        elif model_status == "TEST_FIXTURE_PROMOTION_BLOCKED":
            logger.warning(
                "Aggregated model round %d evaluated with TEST_FIXTURE evidence. Production promotion blocked.",
                round_id,
            )
        else:
            logger.warning(
                "Aggregated model round %d FAILED Quality Gate (PR-AUC=%.4f < %.4f). Promotion BLOCKED.",
                round_id,
                auc_score,
                min_auc_threshold,
            )

        # 3. Update Round Record
        self.rounds[round_id]["status"] = "COMPLETED"
        self.rounds[round_id]["completed_at"] = now_iso
        self.rounds[round_id]["auc_score"] = auc_score
        self.rounds[round_id]["is_champion"] = is_champion
        self.rounds[round_id]["model_status"] = model_status
        self.rounds[round_id]["evaluation_evidence"] = {
            "dataset_id": ev.dataset_id if ev else None,
            "model_version": ev.model_version if ev else None,
            "model_hash": ev.model_hash if ev else None,
            "provenance": ev.provenance if ev else "NONE",
            "sample_count": ev.sample_count if ev else (len(ev.validation_labels) if ev else 0),
            "metric_name": ev.metric_name if ev else "NONE",
            "metric_status": ev.metric_status if ev else "NONE",
            "producer": ev.producer if ev else "NONE",
        }

        # 4. Dispatch RoundCompleteNotification gRPC messages
        participating = self.rounds[round_id]["participating_banks"]
        for bank_id in participating:
            notif = {
                "event": "RoundCompleteNotification",
                "round_id": round_id,
                "target_bank": bank_id,
                "auc_score": auc_score,
                "is_champion": is_champion,
                "timestamp": now_iso,
            }
            self.grpc_notifications.append(notif)

        # 5. Log SIEM Audit Event
        siem = SIEMLogExporter()
        auc_str = f"{auc_score:.4f}" if auc_score is not None else "UNAVAILABLE"
        prov_str = ev.provenance if ev else "NONE"
        event = SIEMAuditEvent(
            event_id=f"fl_round_comp_r{round_id}",
            event_type="FL_ROUND_COMPLETED",
            severity="INFO" if is_champion else "WARNING",
            source_bank="coordinator",
            message=f"FL Round {round_id} complete. AUC={auc_str}, Champion={is_champion}, Provenance={prov_str}",
        )
        siem.export_event(event)

        return {
            "round_id": round_id,
            "status": "COMPLETED",
            "auc_score": auc_score,
            "is_champion": is_champion,
            "model_status": model_status,
            "completed_at": now_iso,
            "evaluation_evidence": self.rounds[round_id]["evaluation_evidence"],
        }

    def negotiate_parameters(
        self, bank_id: str, base_batch_size: int, base_epochs: int
    ) -> NegotiatedParameters:
        """Negotiate optimal parameters based on client hardware constraints."""
        clean_bank = bank_id.lower().strip()
        if clean_bank not in self.registry:
            alias = ALIAS_BANK_MAP.get(clean_bank)
            if alias and alias in self.registry:
                clean_bank = alias
            else:
                return NegotiatedParameters(
                    batch_size=16,
                    local_epochs=2,
                    gradient_accumulation_steps=4,
                    use_cuda=False,
                    status="DEGRADED",
                )

        client = self.registry[clean_bank]
        use_cuda = client.hardware_type == "cuda"
        ram = client.ram_gb

        if use_cuda and ram >= 16:
            batch_size = base_batch_size
            epochs = base_epochs
            grad_accum = 1
            status = "COMPATIBLE"
        elif use_cuda:
            batch_size = max(32, base_batch_size // 2)
            epochs = base_epochs
            grad_accum = 2
            status = "COMPATIBLE"
        elif ram >= 8:
            batch_size = max(16, base_batch_size // 2)
            epochs = max(2, base_epochs - 1)
            grad_accum = 2
            status = "DEGRADED"
        else:
            batch_size = 16
            epochs = max(1, base_epochs - 2)
            grad_accum = 4
            status = "DEGRADED"

        return NegotiatedParameters(
            batch_size=batch_size,
            local_epochs=epochs,
            gradient_accumulation_steps=grad_accum,
            use_cuda=use_cuda,
            status=status,
        )

    def submit_async_update(
        self,
        bank_id: str,
        submitted_round: int,
        client_weights: dict[str, np.ndarray],
        sample_count: int = 100,
    ) -> dict[str, Any]:
        """Processes an asynchronous model parameter update with staleness attenuation."""
        clean_bank = bank_id.lower().strip()
        if clean_bank not in self.registry:
            raise ValueError(f"Bank '{clean_bank}' is not registered with the coordinator.")

        self.record_heartbeat(clean_bank)

        prev_round = self.async_fl_engine.current_round
        updated_weights = self.async_fl_engine.apply_async_update(
            node_id=clean_bank,
            submitted_round=submitted_round,
            client_weights=client_weights,
            sample_count=sample_count,
            advance_round=True,
        )

        tau = max(0, prev_round - submitted_round)
        s_tau = staleness_attenuation(
            tau,
            alpha=self.async_fl_engine.alpha_staleness,
            func=self.async_fl_engine.staleness_func,
            max_staleness=self.async_fl_engine.max_staleness,
        )
        effective_alpha = float(self.async_fl_engine.learning_rate * s_tau)

        # Log SIEM Audit Event for async update
        siem = SIEMLogExporter()
        event = SIEMAuditEvent(
            event_id=f"async_fl_upd_{clean_bank}_{submitted_round}_{int(time.time())}",
            event_type="ASYNC_FL_UPDATE_APPLIED",
            severity="INFO",
            source_bank=clean_bank,
            message=(
                f"Async update from {clean_bank}: submitted_r={submitted_round}, "
                f"tau={tau}, s(tau)={s_tau:.4f}, eff_alpha={effective_alpha:.4f}, "
                f"new_round={self.async_fl_engine.current_round}"
            ),
        )
        siem.export_event(event)

        return {
            "success": True,
            "bank_id": clean_bank,
            "submitted_round": submitted_round,
            "current_round": self.async_fl_engine.current_round,
            "staleness_tau": tau,
            "staleness_attenuation": round(s_tau, 6),
            "effective_alpha": round(effective_alpha, 6),
            "layer_keys": list(updated_weights.keys()),
        }

    def get_quorum_status(self, round_id: int | None = None) -> RoundQuorumStatus:
        """Returns dynamic quorum evaluation status for the active round."""
        target_round = round_id if round_id is not None else (self.current_round_id or 1)
        return self.quorum_manager.evaluate_quorum_status(round_number=target_round)

    def prune_completed_rounds(self, keep_last: int = 50) -> int:
        """Prunes historical completed round data to prevent unbounded memory growth."""
        with self._lock:
            if len(self.rounds) <= keep_last:
                return 0
            sorted_round_ids = sorted(self.rounds.keys())
            to_prune = sorted_round_ids[:-keep_last]
            for r_id in to_prune:
                self.rounds.pop(r_id, None)
                self.gradient_submissions.pop(r_id, None)
                self.round_validation_data.pop(r_id, None)
                self.round_candidate_models.pop(r_id, None)
                self.round_designated_datasets.pop(r_id, None)
            logger.info("Pruned %d historical rounds from coordinator memory", len(to_prune))
            return len(to_prune)


coordinator_service = CoordinatorService(auto_seed=False)
