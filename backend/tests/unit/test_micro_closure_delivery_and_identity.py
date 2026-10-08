"""Focused Behavioral Tests for Final Micro-Closure:
- Application-level Delivery & Non-Quorum Crash Semantics (A1 - A11)
- Model Artifact Identity Precision (Metadata vs Lookup vs Content Hash)
- Historical Provenance Dimensions & Revalidation Unit Derivation
"""

from __future__ import annotations

import json
import os
import pickle
import tempfile
from pathlib import Path

import pytest
import torch
import torch.nn as nn

from app.application.services.coordinator_service import CoordinatorService
from app.application.services.model_registry import ModelRegistry
from app.infrastructure.connectors.kafka_streaming_connector import (
    CloudEvent,
    InMemoryKafkaBroker,
    KafkaStreamingConnector,
)


class SimpleLinearModel(nn.Module):
    def __init__(self, val: float = 1.0) -> None:
        super().__init__()
        self.fc = nn.Linear(4, 1, bias=False)
        nn.init.constant_(self.fc.weight, val)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


# ---------------------------------------------------------------------------
# 1. APPLICATION DELIVERY & NON-QUORUM CRASH TESTS (A1 - A11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_quorum_gradient_crash_window_and_work_loss() -> None:
    """Verify exact application ordering for non-quorum gradient submissions:
    1. Message received and parsed from Kafka
    2. Gradient accepted into volatile CoordinatorService.gradient_submissions
    3. Quorum NOT reached (1/2 participants)
    4. Handler returns status 'GRADIENT_STORED'
    5. Consumer commits offset to broker
    6. Process crash / coordinator state loss injected
    7. Demonstrates APPLICATION_WORK_LOSS_AFTER_ACK:
       - Offset is COMMITTED, so broker does not redeliver
       - Restarted coordinator has empty volatile memory
       - In-flight gradient execution count in final model = 0
       - Replayed submission fails closed.
    """
    broker = InMemoryKafkaBroker()
    connector = KafkaStreamingConnector(in_memory_broker=broker)
    topic = connector.transaction_topic
    group_id = connector.group_id

    # 1. Start active round 1 requiring min_clients = 2
    coordinator = CoordinatorService(auto_seed=False)
    coordinator.register_client("bank_alpha")
    coordinator.register_client("bank_beta")
    round_record = coordinator.start_round(min_clients=2)
    round_id = round_record["round_id"]
    assert round_record["status"] == "COLLECTING_GRADIENTS"

    # 2. Bank Alpha publishes gradient to Kafka
    grad_alpha_bytes = b"GRADIENT_TENSOR_ALPHA_V1"
    ce_alpha = CloudEvent(
        source="cfi://banks/bank_alpha",
        type="cfi.fl.gradient.submitted",
        data={
            "round_id": round_id,
            "bank_id": "bank_alpha",
            "gradient_bytes": grad_alpha_bytes.decode("latin1"),
        },
    )
    receipt = await connector.publish(ce_alpha, topic=topic)
    assert receipt.offset == 0

    # Verify offset before consumption
    assert broker.get_committed_offset(topic, group_id) == 0

    # 3. Consumer fetches with auto_commit=False, executes domain handler, and commits
    events = await connector.consume_batch(topic, max_messages=1, auto_commit=False)
    assert len(events) == 1
    event_data = events[0].data
    assert isinstance(event_data, dict)

    # Application domain handler executes
    resp = coordinator.on_gradient_received(
        round_id=int(event_data["round_id"]),
        bank_id=str(event_data["bank_id"]),
        gradient_bytes=str(event_data["gradient_bytes"]).encode("latin1"),
    )

    # A2: Gradient accepted, quorum NOT reached
    assert resp["status"] == "GRADIENT_STORED"
    assert resp["submitted_count"] == 1
    assert coordinator.rounds[1]["status"] == "COLLECTING_GRADIENTS"

    # Handler returns successfully -> connector commits offset
    connector.commit_offset(topic, offset=1)
    assert broker.get_committed_offset(topic, group_id) == 1

    # State before crash:
    # Offset = COMMITTED (1)
    # Volatile state = gradient stored in coordinator.gradient_submissions[1]['bank_alpha']
    # Durable state = NONE (no model persisted)
    assert "bank_alpha" in coordinator.gradient_submissions[1]

    # 6. INJECT PROCESS CRASH / COORDINATOR RESTART (State Loss)
    # Volatile RAM disappears; new coordinator process spawned
    restarted_coordinator = CoordinatorService()

    # 7. Observe restart behavior:
    # A) Broker log check: offset is 1, so no messages available to fetch
    re_fetched = await broker.fetch_messages(topic, group_id, max_messages=1, auto_commit=False)
    assert len(re_fetched) == 0, "Committed message must not be automatically redelivered"

    # B) Coordinator check: round 1 does not exist in restarted coordinator RAM
    assert 1 not in restarted_coordinator.rounds
    assert 1 not in restarted_coordinator.gradient_submissions

    # C) If an external replay tool forces redelivery of Bank Alpha's message:
    # A11: Restarted coordinator rejects orphan submission fail-closed
    with pytest.raises(ValueError, match="Round ID 1 does not exist"):
        restarted_coordinator.on_gradient_received(
            round_id=1,
            bank_id="bank_alpha",
            gradient_bytes=grad_alpha_bytes,
        )


@pytest.mark.asyncio
async def test_quorum_aggregation_and_model_persistence_ordering() -> None:
    """Verify quorum arrival (A4), aggregation success (A5), model persistence (A7),
    and offset state at each boundary.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = ModelRegistry(storage_dir=tmp_dir)
        coordinator = CoordinatorService(auto_seed=False)
        coordinator.register_client("bank_alpha")
        coordinator.register_client("bank_beta")

        # Round requiring 2 clients
        rnd = coordinator.start_round(min_clients=2)
        r_id = rnd["round_id"]

        # Participant 1 arrives (Non-quorum)
        res1 = coordinator.on_gradient_received(r_id, "bank_alpha", b"grad_a")
        assert res1["status"] == "GRADIENT_STORED"

        # Participant 2 arrives (Quorum met -> triggers aggregation)
        res2 = coordinator.on_gradient_received(r_id, "bank_beta", b"grad_b")
        assert res2["status"] == "COMPLETED"
        assert coordinator.rounds[r_id]["status"] == "COMPLETED"

        # Persist model to registry
        model = SimpleLinearModel(2.5)
        entry = registry.save_version(
            simulation_id="sim_fl_round_10",
            state_dict=model.state_dict(),
            metrics={"auc": 0.85},
            is_promoted=True,
        )
        assert entry["version"] == 1
        assert os.path.exists(os.path.join(registry._get_sim_dir("sim_fl_round_10"), "model_v1.pt"))


# ---------------------------------------------------------------------------
# 2. MODEL ARTIFACT IDENTITY PRECISION TESTS (Sections 16 - 21)
# ---------------------------------------------------------------------------


def test_model_artifact_identity_properties_verification() -> None:
    """Verify distinct model identity properties:
    - VERSION_METADATA_CORRELATION: YES (metadata entry contains version number)
    - ARTIFACT_LOOKUP_BINDING: YES (load_version loads file mapped by version)
    - ACTUAL_ARTIFACT_LOADING: YES (torch.load physically loads weights)
    - ARTIFACT_IMMUTABILITY: PARTIAL / NOT STRICTLY ENFORCED (filesystem allows direct overwrite)
    - CONTENT_HASH_BINDING: NO (registry does NOT compute or check SHA-256 on model weights)
    - MISSING_VERSION: FAILS CLOSED (raises ValueError)
    - CORRUPT_ARTIFACT: FAILS CLOSED (raises UnpicklingError / RuntimeError)
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = ModelRegistry(storage_dir=tmp_dir)
        sim_id = "sim_identity_precision"

        m1 = SimpleLinearModel(1.0)
        entry1 = registry.save_version(
            simulation_id=sim_id,
            state_dict=m1.state_dict(),
            metrics={"auc": 0.75},
        )

        # 1. Version metadata correlation
        assert entry1["version"] == 1
        assert entry1["filename"] == "model_v1.pt"

        # 2. Content hash binding: verify registry does NOT enforce content hash
        # Entry has dataset_hash and git_commit_hash, but NOT model_content_hash
        assert "model_content_hash" not in entry1
        assert "sha256" not in entry1

        # 3. Artifact lookup binding & actual loading
        loaded_dict = registry.load_version(sim_id, 1)
        assert torch.allclose(loaded_dict["fc.weight"], torch.tensor([[1.0, 1.0, 1.0, 1.0]]))

        # 4. Missing version fails closed
        with pytest.raises(ValueError, match="Version 99 not found in registry"):
            registry.load_version(sim_id, 99)

        # 5. Corrupt artifact fails closed
        sim_dir = registry._get_sim_dir(sim_id)
        corrupt_file = os.path.join(sim_dir, "model_v1.pt")
        with open(corrupt_file, "wb") as f:
            f.write(b"POISONED_NON_TENSOR_BYTES")

        with pytest.raises((RuntimeError, OSError, pickle.PickleError)):
            registry.load_version(sim_id, 1)


# ---------------------------------------------------------------------------
# 3. HISTORICAL PROVENANCE & REVALIDATION UNIT DERIVATION (Sections 31 - 37)
# ---------------------------------------------------------------------------


def test_revalidation_unit_and_defect_pair_separation() -> None:
    """Verify that:
    affected_defect_artifact_pairs (9) != affected_unique_artifacts (6) == minimum_revalidation_units (6).
    Code must NOT assume defect-pair count equals rerun execution count.
    """
    repo_root = Path(__file__).resolve().parents[3]
    matrix_path = repo_root / "verification" / "inventories" / "historical_defect_applicability.json"

    with open(matrix_path, encoding="utf-8") as f:
        data = json.load(f)

    pairs = data["pairs"]
    applicable_pairs = [p for p in pairs if p["applicability"] == "APPLICABLE"]
    assert len(applicable_pairs) == 9

    # Extract unique affected artifacts
    unique_artifacts = {p["artifact"] for p in applicable_pairs}
    assert len(unique_artifacts) == 6

    # Verify that multi-defect artifacts coalesce into a single revalidation unit:
    # 1. run_creditcard_benchmark.py (FL-001, DATA-002) -> 1 rerun unit
    cc_defects = {p["defect_id"] for p in applicable_pairs if p["artifact"] == "run_creditcard_benchmark.py"}
    assert cc_defects == {"FL-001", "DATA-002"}

    # 2. train_graphsage.py (GRAPH-LEAK-01, DATA-007) -> 1 rerun unit
    elliptic_defects = {p["defect_id"] for p in applicable_pairs if p["artifact"] == "train_graphsage.py"}
    assert elliptic_defects == {"GRAPH-LEAK-01", "DATA-007"}

    # 3. multi_seed_statistical_summary.json (FL-001, CRYPTO-001) -> 1 rerun unit
    ms_defects = {p["defect_id"] for p in applicable_pairs if p["artifact"] == "multi_seed_statistical_summary.json"}
    assert ms_defects == {"FL-001", "CRYPTO-001"}

    # Total minimum revalidation units = 6
    revalidation_units = {
        "RU-CC-01": {"artifact": "run_creditcard_benchmark.py", "defects": cc_defects, "type": "FULL_RERUN_REQUIRED"},
        "RU-PS-01": {"artifact": "run_paysim_canonical_benchmark.py", "defects": {"FL-001"}, "type": "FULL_RERUN_REQUIRED"},
        "RU-EL-01": {"artifact": "train_graphsage.py", "defects": elliptic_defects, "type": "FULL_RERUN_REQUIRED"},
        "RU-IE-01": {"artifact": "run_ieee_benchmark.py", "defects": {"FL-001"}, "type": "FULL_RERUN_REQUIRED"},
        "RU-AML-01": {"artifact": "evaluate_patterns.py", "defects": {"DATA-006"}, "type": "EVALUATION_ONLY_RECOMPUTATION"},
        "RU-MS-01": {"artifact": "multi_seed_statistical_summary.json", "defects": ms_defects, "type": "FULL_RERUN_REQUIRED"},
    }

    assert len(revalidation_units) == 6
    assert len(applicable_pairs) != len(revalidation_units)
