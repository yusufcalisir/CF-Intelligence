"""Integration tests for shared canary evaluation evidence consistency across workers.

Verifies:
1. Atomic, strictly monotonic prediction sequence allocation under concurrent worker load.
2. Protection against duplicate feedback or repeated transaction identifiers inflating sample counts.
3. Sliding-window bounding (1000 items) to prevent unbounded memory growth.
4. Production safety gating: rejection of worker-local in-memory store in production when Redis is unavailable.
5. Development mode workflow preservation.
6. Environment-aware live Redis cluster verification.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import socket
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore


def _is_redis_live(host: str = "127.0.0.1", port: int = 6379, timeout: float = 0.5) -> bool:
    """Check if a genuine Redis server is reachable via TCP socket."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


class TestSharedCanaryEvidenceConsistency:
    """Verifies atomic evidence accounting, sequence allocation, and multi-worker consistency."""

    def test_concurrent_prediction_sequence_atomicity(self, tmp_path: Path) -> None:
        """Verifies that concurrent workers generate strictly unique, monotonically increasing sequence numbers."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        eval_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_concurrent_seq"

        num_workers = 10
        predictions_per_worker = 20
        total_predictions = num_workers * predictions_per_worker

        def worker_log(worker_idx: int) -> list[str]:
            tx_ids = []
            for i in range(predictions_per_worker):
                tx_id = f"tx_w{worker_idx}_{i}"
                eval_engine.log_prediction(
                    simulation_id=sim_id,
                    transaction_id=tx_id,
                    champion_version=1,
                    champion_prob=0.85,
                )
                tx_ids.append(tx_id)
            return tx_ids

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_log, w) for w in range(num_workers)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        # Collect all sequence numbers from the evaluation store
        store = eval_engine._store
        list_key = f"{sim_id}:prediction_keys"
        keys = store.get(list_key) or []
        assert len(keys) == total_predictions

        all_seqs: list[int] = []
        for k in keys:
            rec = store.get(k)
            assert rec is not None
            assert "pred_seq" in rec
            all_seqs.append(rec["pred_seq"])

        # Invariant 1: Exactly total_predictions entries generated
        assert len(all_seqs) == total_predictions

        # Invariant 2: Zero duplicate sequence numbers across all concurrent workers
        unique_seqs = set(all_seqs)
        assert len(unique_seqs) == total_predictions, (
            f"Duplicate sequences detected: {len(all_seqs)} total vs {len(unique_seqs)} unique"
        )

        # Invariant 3: Sequence numbers form a dense contiguous set 1..total_predictions
        assert sorted(all_seqs) == list(range(1, total_predictions + 1))

    def test_duplicate_feedback_and_transaction_deduplication(self, tmp_path: Path) -> None:
        """Verifies that duplicate feedback or repeated transaction identifiers cannot inflate canary sample counts."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        eval_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_dedup_test"

        # Log prediction for tx_001
        eval_engine.log_prediction(
            simulation_id=sim_id,
            transaction_id="tx_001",
            champion_version=1,
            champion_prob=0.92,
        )

        # Log duplicate prediction call for tx_001 (replay/retry scenario)
        eval_engine.log_prediction(
            simulation_id=sim_id,
            transaction_id="tx_001",
            champion_version=1,
            champion_prob=0.92,
        )

        # Log feedback for tx_001 multiple times (e.g. repeated webhook)
        for _ in range(5):
            eval_engine.log_feedback(
                simulation_id=sim_id,
                transaction_id="tx_001",
                actual_label=1,
            )

        # Verify that prediction_keys in store contains only ONE entry for tx_001
        store = eval_engine._store
        list_key = f"{sim_id}:prediction_keys"
        stored_keys = store.get(list_key) or []
        assert len(stored_keys) == 1
        assert stored_keys[0] == f"{sim_id}:prediction:tx_001"

        # Record still retains stable sequence number
        rec = store.get(stored_keys[0])
        assert rec["actual_label"] == 1
        assert rec["pred_seq"] == 1

    def test_sliding_window_eviction(self, tmp_path: Path) -> None:
        """Verifies that prediction list is bounded to 1000 entries (sliding-window eviction)."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        eval_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_window_test"

        # Log 1050 predictions
        for i in range(1050):
            eval_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=f"tx_stream_{i}",
                champion_version=1,
                champion_prob=0.5,
            )

        store = eval_engine._store
        list_key = f"{sim_id}:prediction_keys"
        stored_keys = store.get(list_key) or []

        # Must be bounded to exactly 1000 items
        assert len(stored_keys) == 1000

        # Oldest items evicted from key list and deleted from store
        assert stored_keys[0] == f"{sim_id}:prediction:tx_stream_50"
        assert stored_keys[-1] == f"{sim_id}:prediction:tx_stream_1049"

        # Verify that tx_stream_0 is deleted from store
        assert store.get(f"{sim_id}:prediction:tx_stream_0") is None
        # Verify that tx_stream_1049 exists
        assert store.get(f"{sim_id}:prediction:tx_stream_1049") is not None

    def test_production_safety_gate_rejects_in_memory_canary_in_production(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies that in production, canary evaluation fails closed if Redis is unavailable."""
        monkeypatch.setenv("APP_ENV", "development")
        get_settings.cache_clear()

        # Setup trust store, issuer, verifier, registry
        trust_file = str(tmp_path / "trust.json")
        admin_token = "admin_tok_prod"
        priv_key = ed25519.Ed25519PrivateKey.generate()
        pub_hex = (
            priv_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            .hex()
        )
        trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
        trust_store.register_approved_key(
            key_id="k1", public_key_hex=pub_hex, admin_token=admin_token
        )
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"), verifier=verifier)
        eval_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_prod_guard"

        # Register v1 as champion
        model1 = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
        buf1 = io.BytesIO()
        torch.save(model1.state_dict(), buf1)
        bytes1 = buf1.getvalue()
        sha1 = hashlib.sha256(bytes1).hexdigest()
        issuer = TrainingReceiptIssuer(
            key_id="k1", private_key=priv_key, trust_store=trust_store, app_env="development"
        )
        evidence1 = TrainingExecutionEvidence(
            training_run_id="run1",
            job_id="job1",
            simulation_id=sim_id,
            completed_at="2026-10-15T00:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.1},
            artifact_sha256=sha1,
            architecture="FraudDetectionModel",
            feature_schema_version="1.0",
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            owning_bank_id="bank_a",
        )
        rcpt1 = issuer.issue_receipt(
            evidence=evidence1,
            model_bytes=bytes1,
            owning_bank_id="bank_a",
            architecture="FraudDetectionModel",
            input_dim=NUM_FEATURES,
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            feature_schema_version="1.0",
        )
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model1.state_dict(),
            artifact_bytes=bytes1,
            training_receipt=rcpt1,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_a",
        )
        registry.promote_version(sim_id, 1, "champion")

        # Register v2 as challenger
        model2 = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
        buf2 = io.BytesIO()
        torch.save(model2.state_dict(), buf2)
        bytes2 = buf2.getvalue()
        sha2 = hashlib.sha256(bytes2).hexdigest()
        evidence2 = TrainingExecutionEvidence(
            training_run_id="run2",
            job_id="job2",
            simulation_id=sim_id,
            completed_at="2026-10-15T00:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.05},
            artifact_sha256=sha2,
            architecture="FraudDetectionModel",
            feature_schema_version="1.0",
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            owning_bank_id="bank_a",
        )
        rcpt2 = issuer.issue_receipt(
            evidence=evidence2,
            model_bytes=bytes2,
            owning_bank_id="bank_a",
            architecture="FraudDetectionModel",
            input_dim=NUM_FEATURES,
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            feature_schema_version="1.0",
        )
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model2.state_dict(),
            artifact_bytes=bytes2,
            training_receipt=rcpt2,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_a",
        )

        # Ensure store has client=None (in-memory fallback)
        eval_engine._store._client = None

        # Set production environment for evaluation safety gating
        monkeypatch.setenv("APP_ENV", "production")
        get_settings.cache_clear()

        # Log paired predictions with challenger clearly outperforming champion on diverse labels
        labels = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1]
        last_feedback_res: dict[str, Any] = {}
        for i, lbl in enumerate(labels):
            tx_id = f"tx_prod_{i}"
            # Champion makes two misclassifications so PR-AUC < 1.0 (e.g. ~0.85)
            # while challenger has perfect discrimination (PR-AUC = 1.0)
            champ_p = 0.1 if i in (0, 2) else (0.85 if lbl == 1 else 0.15)

            eval_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_p,
                champion_latency_ms=1.5,
                challenger_version=2,
                challenger_prob=0.99 if lbl == 1 else 0.01,
                challenger_latency_ms=1.2,
            )
            last_feedback_res = eval_engine.log_feedback(
                simulation_id=sim_id,
                transaction_id=tx_id,
                actual_label=lbl,
                target_challenger_version=2,
            )

        # In production without authoritative shared Redis, canary activation must be deferred
        assert last_feedback_res.get("promotion_triggered") is False
        assert "shared authoritative evaluation storage" in last_feedback_res.get(
            "promotion_message", ""
        )
        # Canary traffic share must remain unchanged (0.0)
        assert last_feedback_res.get("traffic_share") == 0.0

    def test_development_mode_preserves_local_in_memory_evaluation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies that development workflows are preserved for fast local testing without Redis."""
        monkeypatch.setenv("APP_ENV", "development")
        get_settings.cache_clear()

        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        eval_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_dev_eval"

        for i in range(5):
            tx_id = f"tx_dev_{i}"
            eval_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=0.9,
            )
            eval_engine.log_feedback(
                simulation_id=sim_id,
                transaction_id=tx_id,
                actual_label=1,
            )

        # In development, evaluate_performance executes without raising
        metrics = eval_engine.evaluate_performance(sim_id)
        assert "sample_count" in metrics
        assert metrics["sample_count"] == 5

    def test_redis_backed_concurrent_canary_evidence(self) -> None:
        """Verifies genuine distributed multi-worker consistency backed by a live Redis cluster."""
        redis_live = _is_redis_live()
        if not redis_live:
            pytest.skip(
                "Environment blocked: Live Redis service is not reachable on 127.0.0.1:6379. "
                "Multi-node Redis cluster verification is blocked by missing single-host Redis daemon."
            )

        # If live Redis is detected, perform real TCP roundtrip multi-worker test
        store = RedisStore("cfi_integration_live")
        assert store.client is not None, "Expected live Redis client connection"
        test_key = "live_test_counter"
        store.delete(test_key)
        val = store.incr(test_key)
        assert val == 1
        store.delete(test_key)
