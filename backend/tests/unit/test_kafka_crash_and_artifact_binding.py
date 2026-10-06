"""Focused behavioral tests for Kafka Crash/Ack Semantics and Model/Checkpoint Artifact Binding."""

from __future__ import annotations

import os
import pickle
import tempfile

import pytest
import torch
import torch.nn as nn

from app.application.services.coordinator_service import CoordinatorService
from app.application.services.model_registry import ModelRegistry
from app.infrastructure.connectors.kafka_connector import KafkaBankConnector
from app.infrastructure.connectors.kafka_streaming_connector import (
    CloudEvent,
    InMemoryKafkaBroker,
    KafkaStreamingConnector,
)
from app.infrastructure.grpc.servicer import FederatedLearningServicer
from app.infrastructure.grpc.types import ModelDownloadRequest

# ---------------------------------------------------------------------------
# 1. KAFKA CRASH & OFFSET COMMIT BEHAVIORAL TESTS (K1 - K9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kafka_offset_remains_uncommitted_on_processing_failure() -> None:
    """Verify that if processing crashes or fails before commit, offset does NOT advance."""
    broker = InMemoryKafkaBroker()
    topic = "org.cfi.finint.transactions.v1"
    group_id = "test-group"

    await broker.publish(topic, b'{"id": "msg-1", "source": "bank_a"}')
    await broker.publish(topic, b'{"id": "msg-2", "source": "bank_b"}')

    assert broker.get_committed_offset(topic, group_id) == 0

    # 1. Fetch with auto_commit=False (read-only peek)
    messages = await broker.fetch_messages(topic, group_id, max_messages=2, auto_commit=False)
    assert len(messages) == 2
    # Offset MUST still be 0 because processing has not completed!
    assert broker.get_committed_offset(topic, group_id) == 0

    # 2. Simulate process crash before commit -> re-fetch yields the exact same messages
    replay_messages = await broker.fetch_messages(topic, group_id, max_messages=2, auto_commit=False)
    assert len(replay_messages) == 2
    assert replay_messages == messages
    assert broker.get_committed_offset(topic, group_id) == 0

    # 3. Successful processing -> explicit commit
    broker.commit_offset(topic, group_id, offset=2)
    assert broker.get_committed_offset(topic, group_id) == 2

    # 4. Subsequent fetch yields no more uncommitted messages
    empty_messages = await broker.fetch_messages(topic, group_id, max_messages=2, auto_commit=False)
    assert len(empty_messages) == 0


@pytest.mark.asyncio
async def test_kafka_connector_consume_batch_commits_only_after_handling() -> None:
    """Verify KafkaStreamingConnector.consume_batch commits offset only after successful handling."""
    connector = KafkaStreamingConnector()
    topic = connector.transaction_topic
    group_id = connector.group_id

    # Create 2 valid CloudEvents
    ce1 = CloudEvent(
        source="urn:cfi:bank:bank_a",
        type="org.cfi.finint.transaction.v1",
        data={"transaction_id": "tx_101", "amount": 500.0},
    )
    ce2 = CloudEvent(
        source="urn:cfi:bank:bank_b",
        type="org.cfi.finint.transaction.v1",
        data={"transaction_id": "tx_102", "amount": 1200.0},
    )

    await connector.publish(ce1)
    await connector.publish(ce2)

    assert connector._broker.get_committed_offset(topic, group_id) == 0

    # Consume batch: processes 2 events and commits offset to 2
    events = await connector.consume_batch(topic, max_messages=10)
    assert len(events) == 2
    assert connector._broker.get_committed_offset(topic, group_id) == 2


def test_coordinator_duplicate_and_stale_submission_policy() -> None:
    """Verify CoordinatorService duplicate policies: NEW, EXACT_REPLAY, REPLACE_BEFORE_AGGREGATION, STALE_REJECTED."""
    coord = CoordinatorService(auto_seed=False)
    coord.register_client("bank_alpha")
    coord.register_client("bank_beta")

    rnd = coord.start_round(min_clients=2)
    round_id = rnd["round_id"]

    # 1. New submission
    res1 = coord.on_gradient_received(round_id, "bank_alpha", b"grad_v1")
    assert res1["status"] == "GRADIENT_STORED"
    assert res1["duplicate_policy"] == "NEW_SUBMISSION"
    assert coord.gradient_submissions[round_id]["bank_alpha"] == b"grad_v1"

    # 2. Exact duplicate replay (e.g. at-least-once transport replay)
    res_replay = coord.on_gradient_received(round_id, "bank_alpha", b"grad_v1")
    assert res_replay["status"] == "GRADIENT_STORED"
    assert res_replay["duplicate_policy"] == "EXACT_REPLAY_PRESERVED"
    assert coord.gradient_submissions[round_id]["bank_alpha"] == b"grad_v1"

    # 3. Conflicting duplicate before quorum -> replaced
    res_upd = coord.on_gradient_received(round_id, "bank_alpha", b"grad_v2_updated")
    assert res_upd["status"] == "GRADIENT_STORED"
    assert res_upd["duplicate_policy"] == "REPLACE_BEFORE_AGGREGATION"
    assert coord.gradient_submissions[round_id]["bank_alpha"] == b"grad_v2_updated"

    # 4. Quorum reached -> aggregates and completes
    res_quorum = coord.on_gradient_received(round_id, "bank_beta", b"grad_beta")
    assert res_quorum["status"] == "COMPLETED"
    assert coord.rounds[round_id]["status"] in ("COMPLETED", "AGGREGATING", "UNVERIFIED_NO_EVALUATION")

    # 5. Stale submission after round completed -> rejected
    res_stale = coord.on_gradient_received(round_id, "bank_alpha", b"grad_v3_late")
    assert res_stale["status"] == "STALE_SUBMISSION_REJECTED"
    assert coord.gradient_submissions[round_id]["bank_alpha"] == b"grad_v2_updated"


def test_coordinator_process_restart_orphan_redelivery_fails_closed() -> None:
    """Verify that coordinator process restart abandons in-flight round and redelivery fails closed."""
    coord_crashed = CoordinatorService(auto_seed=False)
    coord_crashed.register_client("bank_alpha")
    rnd = coord_crashed.start_round(min_clients=2)
    round_id = rnd["round_id"]
    coord_crashed.on_gradient_received(round_id, "bank_alpha", b"grad_alpha")

    # Simulate process crash: a new CoordinatorService instance is instantiated with empty state
    coord_restarted = CoordinatorService(auto_seed=False)
    # Redelivery of message for the crashed round encounters an uninitialized rounds table -> fails closed
    with pytest.raises(ValueError, match="Round ID .* does not exist"):
        coord_restarted.on_gradient_received(round_id, "bank_alpha", b"grad_alpha")


# ---------------------------------------------------------------------------
# 2. MODEL VERSION & CHECKPOINT ARTIFACT BINDING TESTS (AB1 - AB5)
# ---------------------------------------------------------------------------


class TinyMLP(nn.Module):
    def __init__(self, val: float) -> None:
        super().__init__()
        self.fc = nn.Linear(2, 1, bias=False)
        nn.init.constant_(self.fc.weight, val)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


def test_model_version_actual_artifact_binding_and_loading() -> None:
    """Prove behaviorally that model_version loads distinct physical weights from disk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = ModelRegistry(storage_dir=tmp_dir)
        sim_id = "sim_test_artifacts"

        # Model A: weights set to 1.5
        model_a = TinyMLP(1.5)
        entry_a = registry.save_version(
            simulation_id=sim_id,
            state_dict=model_a.state_dict(),
            metrics={"auc": 0.85},
            is_promoted=True,
        )
        assert entry_a["version"] == 1

        # Model B: weights set to 9.0
        model_b = TinyMLP(9.0)
        entry_b = registry.save_version(
            simulation_id=sim_id,
            state_dict=model_b.state_dict(),
            metrics={"auc": 0.92},
            is_promoted=True,
        )
        assert entry_b["version"] == 2

        # 1. Load Version 1 -> Assert weights match model_a, not model_b
        state_1 = registry.load_version(sim_id, 1)
        loaded_model_1 = TinyMLP(0.0)
        loaded_model_1.load_state_dict(state_1)
        test_input = torch.tensor([[1.0, 1.0]])
        output_1 = loaded_model_1(test_input).item()
        assert pytest.approx(output_1, 1e-4) == 3.0  # 1.5 * 1 + 1.5 * 1 = 3.0

        # 2. Load Version 2 -> Assert weights match model_b, not model_a
        state_2 = registry.load_version(sim_id, 2)
        loaded_model_2 = TinyMLP(0.0)
        loaded_model_2.load_state_dict(state_2)
        output_2 = loaded_model_2(test_input).item()
        assert pytest.approx(output_2, 1e-4) == 18.0  # 9.0 * 1 + 9.0 * 1 = 18.0

        # 3. Missing version fails closed with ValueError
        with pytest.raises(ValueError, match="Version 999 not found in registry"):
            registry.load_version(sim_id, 999)

        # 4. Missing physical file on disk fails closed with FileNotFoundError
        sim_dir = registry._get_sim_dir(sim_id)
        os.remove(os.path.join(sim_dir, "model_v1.pt"))
        with pytest.raises(FileNotFoundError, match="not found on disk"):
            registry.load_version(sim_id, 1)


def test_corrupt_model_artifact_fails_closed() -> None:
    """Verify that corrupt model weights file fails closed without silent fallback."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = ModelRegistry(storage_dir=tmp_dir)
        sim_id = "sim_corrupt_test"

        model = TinyMLP(2.0)
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"auc": 0.80},
        )

        sim_dir = registry._get_sim_dir(sim_id)
        corrupt_path = os.path.join(sim_dir, "model_v1.pt")
        with open(corrupt_path, "wb") as f:
            f.write(b"CORRUPTED_NOT_A_PYTORCH_TENSOR_FILE")

        with pytest.raises((RuntimeError, OSError, pickle.PickleError)):
            registry.load_version(sim_id, 1)


@pytest.mark.asyncio
async def test_grpc_download_global_model_fails_closed_on_missing_version() -> None:
    """Verify gRPC servicer DownloadGlobalModel fails closed when requested target_version is not in registry."""
    servicer = FederatedLearningServicer()
    assert "latest" in servicer.global_models

    # Request an existing version -> succeeds
    req_valid = ModelDownloadRequest(bank_id="bank_alpha", target_version="latest")
    chunks = [c async for c in servicer.DownloadGlobalModel(req_valid)]
    assert len(chunks) > 0

    # Request a non-existent version -> FAILS CLOSED (no silent fallback to 'latest')
    req_missing = ModelDownloadRequest(bank_id="bank_alpha", target_version="v99.9.9")
    with pytest.raises(ValueError, match="Requested model version 'v99.9.9' not found in global models"):
        _ = [c async for c in servicer.DownloadGlobalModel(req_missing)]


def test_checkpoint_id_opaque_identity_correlation_contracts() -> None:
    """Verify checkpoint_id opaque identifier correlation contracts in KafkaBankConnector."""
    connector = KafkaBankConnector()
    processed_set: set[str] = set()

    cmd_meta = {
        "correlation_id": "cid-ckpt-100",
        "bank_id": "bank_alpha",
        "command_type": "TRAIN",
        "run_id": "run_sim_1",
        "round_id": 1,
        "model_id": "fraud_detector_v1",
        "checkpoint_id": "ckpt-epoch-2-hash-abc",
    }

    # 1. Matching checkpoint_id succeeds
    worker_valid = {
        "correlation_id": "cid-ckpt-100",
        "bank_id": "bank_alpha",
        "command_type": "TRAIN",
        "run_id": "run_sim_1",
        "round_id": 1,
        "model_id": "fraud_detector_v1",
        "checkpoint_id": "ckpt-epoch-2-hash-abc",
        "loss": 0.15,
        "num_samples": 100,
    }
    res = connector.correlate_worker_result(cmd_meta, worker_valid, processed_set)
    assert res["status"] == "PROCESSED"
    assert res["checkpoint_id"] == "ckpt-epoch-2-hash-abc"

    # 2. Checkpoint ID mismatch fails closed
    worker_wrong_ckpt = {**worker_valid, "checkpoint_id": "ckpt-epoch-1-stale"}
    with pytest.raises(ValueError, match="checkpoint_id mismatch"):
        connector.correlate_worker_result(cmd_meta, worker_wrong_ckpt, processed_set)

    # 3. Expected checkpoint_id omitted fails closed
    worker_missing_ckpt = {k: v for k, v in worker_valid.items() if k != "checkpoint_id"}
    with pytest.raises(ValueError, match="checkpoint_id mismatch"):
        connector.correlate_worker_result(cmd_meta, worker_missing_ckpt, processed_set)

    # 4. Unexpected checkpoint_id fails closed
    cmd_no_ckpt = {k: v for k, v in cmd_meta.items() if k != "checkpoint_id"}
    with pytest.raises(ValueError, match="unexpected worker checkpoint_id"):
        connector.correlate_worker_result(cmd_no_ckpt, worker_valid, processed_set)
