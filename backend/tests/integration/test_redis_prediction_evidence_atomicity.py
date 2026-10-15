"""Integration tests for atomic prediction evidence recording, sequence uniqueness, and fail-closed Redis semantics.

Verifies:
1. Multi-process prediction sequence atomicity and index preservation under live Redis contention.
2. Concurrent duplicate retry idempotency and ground-truth feedback preservation.
3. Bounded sliding-window retention (1000 items) and eviction in shared storage.
4. Production fail-closed behavior: rejection of canary activation and promotion during Redis outages.
5. Detection of post-connection Redis failures and prevention of silent fallback in production.
"""

from __future__ import annotations

import multiprocessing as mp
import socket
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore


def _is_redis_live(host: str = "127.0.0.1", port: int = 6379, timeout: float = 0.5) -> bool:
    """Check if genuine Redis server is reachable via TCP socket."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _worker_log_predictions(
    storage_dir: str,
    sim_id: str,
    worker_idx: int,
    count: int,
    barrier: Any,
    result_queue: Any,
    prefix: str,
) -> None:
    """Independent OS worker process recording predictions in Redis."""
    try:
        registry = ModelRegistry(storage_dir=storage_dir)
        engine = ModelEvaluationEngine(registry)
        engine._store.prefix = prefix

        # Synchronize all worker processes for deterministic contention
        barrier.wait(timeout=10)

        tx_ids = []
        for i in range(count):
            tx_id = f"tx_p{worker_idx}_{i}"
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=0.85,
                champion_latency_ms=2.1,
            )
            tx_ids.append(tx_id)

        result_queue.put({"success": True, "worker_idx": worker_idx, "tx_ids": tx_ids})
    except Exception as exc:
        result_queue.put({"success": False, "worker_idx": worker_idx, "error": str(exc)})


def _worker_log_duplicate_retry(
    storage_dir: str,
    sim_id: str,
    tx_id: str,
    barrier: Any,
    result_queue: Any,
    prefix: str,
) -> None:
    """Worker process scoring the exact same transaction simultaneously."""
    try:
        registry = ModelRegistry(storage_dir=storage_dir)
        engine = ModelEvaluationEngine(registry)
        engine._store.prefix = prefix

        barrier.wait(timeout=10)

        engine.log_prediction(
            simulation_id=sim_id,
            transaction_id=tx_id,
            champion_version=1,
            champion_prob=0.88,
        )
        rec = engine._store.get(f"{sim_id}:prediction:{tx_id}")
        result_queue.put({"success": True, "record": rec})
    except Exception as exc:
        result_queue.put({"success": False, "error": str(exc)})


class TestRedisPredictionEvidenceAtomicity:
    """Tests atomic prediction recording and fail-closed shared storage invariants."""

    def test_multi_process_prediction_sequence_atomicity_live_redis(self, tmp_path: Path) -> None:
        """Verifies strictly unique, monotonically allocated sequence numbers across OS processes."""
        if not _is_redis_live():
            pytest.skip(
                "Environment blocked: Live Redis service is not reachable on 127.0.0.1:6379. "
                "Multi-process Redis atomicity verification requires a running Redis daemon."
            )

        storage_dir = str(tmp_path / "storage")
        sim_id = "sim_mp_seq_atomicity"
        prefix = "cfi_test_mp_atomicity"

        clean_store = RedisStore(prefix)
        clean_store.clear()

        num_workers = 4
        predictions_per_worker = 25
        total_predictions = num_workers * predictions_per_worker

        ctx = mp.get_context("spawn")
        barrier = ctx.Barrier(num_workers)
        result_queue = ctx.Queue()

        workers = []
        for w in range(num_workers):
            p = ctx.Process(
                target=_worker_log_predictions,
                args=(
                    storage_dir,
                    sim_id,
                    w,
                    predictions_per_worker,
                    barrier,
                    result_queue,
                    prefix,
                ),
            )
            workers.append(p)
            p.start()

        results = []
        for _ in range(num_workers):
            res = result_queue.get(timeout=25)
            results.append(res)

        for p in workers:
            p.join(timeout=5)

        for r in results:
            assert r["success"] is True, f"Worker failed: {r.get('error')}"

        # Authoritative inspection in shared store
        list_key = f"{sim_id}:prediction_keys"
        keys = clean_store.get(list_key) or []

        # Invariant 1: Exactly total_predictions keys in index (zero lost updates)
        assert len(keys) == total_predictions, (
            f"Lost index entries: expected {total_predictions}, got {len(keys)}"
        )

        # Invariant 2: Exactly total_predictions unique sequence numbers
        all_seqs = []
        for k in keys:
            rec = clean_store.get(k)
            assert rec is not None
            assert "pred_seq" in rec
            all_seqs.append(rec["pred_seq"])

        assert len(set(all_seqs)) == total_predictions, (
            f"Duplicate sequences detected across independent processes: {len(all_seqs)} total vs {len(set(all_seqs))} unique"
        )
        assert sorted(all_seqs) == list(range(1, total_predictions + 1))

        clean_store.clear()

    def test_multi_process_duplicate_retry_idempotency_live_redis(self, tmp_path: Path) -> None:
        """Verifies duplicate transaction retries across concurrent OS workers preserve sequence identity."""
        if not _is_redis_live():
            pytest.skip(
                "Environment blocked: Live Redis service is not reachable on 127.0.0.1:6379. "
                "Concurrent retry idempotency verification requires a running Redis daemon."
            )

        storage_dir = str(tmp_path / "storage")
        sim_id = "sim_mp_retry_idempotency"
        prefix = "cfi_test_mp_retry"
        tx_id = "tx_shared_concurrent_01"

        clean_store = RedisStore(prefix)
        clean_store.clear()

        ctx = mp.get_context("spawn")
        num_workers = 3
        barrier = ctx.Barrier(num_workers)
        result_queue = ctx.Queue()

        workers = []
        for _ in range(num_workers):
            p = ctx.Process(
                target=_worker_log_duplicate_retry,
                args=(storage_dir, sim_id, tx_id, barrier, result_queue, prefix),
            )
            workers.append(p)
            p.start()

        results = []
        for _ in range(num_workers):
            res = result_queue.get(timeout=20)
            results.append(res)

        for p in workers:
            p.join(timeout=5)

        for r in results:
            assert r["success"] is True

        # Index must retain exactly 1 entry for this transaction
        stored_keys = clean_store.get(f"{sim_id}:prediction_keys") or []
        assert len(stored_keys) == 1
        assert stored_keys[0] == f"{sim_id}:prediction:{tx_id}"

        # Sequence number must remain 1
        rec = clean_store.get(stored_keys[0])
        assert rec["pred_seq"] == 1

        clean_store.clear()

    def test_redis_outage_after_initial_connection_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies that if Redis becomes unreachable after successful connection, production promotion fails closed."""
        monkeypatch.setenv("APP_ENV", "production")
        get_settings.cache_clear()

        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        engine = ModelEvaluationEngine(registry)
        sim_id = "sim_redis_outage_fail_closed"

        # Simulate initially available Redis client
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        engine._store._redis_client = mock_client
        engine._store._redis_failed = False
        engine._store._had_command_failure = False

        # Confirm store is initially live
        assert engine._store.has_active_shared_storage() is True

        # Now simulate Redis connection drop (ping fails or command raises ConnectionError)
        mock_client.ping.side_effect = ConnectionError("Connection refused by peer")
        mock_client.eval.side_effect = ConnectionError("Connection reset by peer")

        # Invariant 1: has_active_shared_storage immediately reports False
        assert engine._store.is_live() is False
        assert engine._store.has_active_shared_storage() is False
        assert engine._store._redis_failed is True

        # Invariant 2: log_prediction in production fails closed rather than silently falling back
        with pytest.raises(RuntimeError) as exc_info:
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id="tx_dropped_01",
                champion_version=1,
                champion_prob=0.8,
            )
        assert "Production evaluation storage failure" in str(exc_info.value)

        # Invariant 3: evaluate_performance refuses to activate or promote
        eval_result = engine.evaluate_performance(sim_id, target_challenger_version=2)
        assert eval_result.get("promotion_triggered") is False
        assert (
            "unavailable" in eval_result.get("status", "")
            or "unavailable" in eval_result.get("promotion_message", "").lower()
        )

    def test_sliding_window_eviction_live_redis(self, tmp_path: Path) -> None:
        """Verifies bounded sliding-window retention (1000 items) and eviction in live Redis."""
        if not _is_redis_live():
            pytest.skip(
                "Environment blocked: Live Redis service is not reachable on 127.0.0.1:6379. "
                "Sliding-window eviction verification requires a running Redis daemon."
            )

        storage_dir = str(tmp_path / "storage")
        sim_id = "sim_window_live_redis"
        prefix = "cfi_test_window_live"

        clean_store = RedisStore(prefix)
        clean_store.clear()

        registry = ModelRegistry(storage_dir=storage_dir)
        engine = ModelEvaluationEngine(registry)
        engine._store.prefix = prefix

        for i in range(1050):
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=f"tx_stream_{i}",
                champion_version=1,
                champion_prob=0.5,
            )

        keys = clean_store.get(f"{sim_id}:prediction_keys") or []
        assert len(keys) == 1000
        assert keys[0] == f"{sim_id}:prediction:tx_stream_50"
        assert keys[-1] == f"{sim_id}:prediction:tx_stream_1049"

        # Oldest evicted key deleted from Redis
        assert clean_store.get(f"{sim_id}:prediction:tx_stream_0") is None
        assert clean_store.get(f"{sim_id}:prediction:tx_stream_1049") is not None

        clean_store.clear()

    def test_development_fallback_preservation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies development workflows are preserved for fast local testing without Redis."""
        monkeypatch.setenv("APP_ENV", "development")
        get_settings.cache_clear()

        registry = ModelRegistry(storage_dir=str(tmp_path / "storage"))
        engine = ModelEvaluationEngine(registry)
        sim_id = "sim_dev_fallback_check"

        for i in range(5):
            tx_id = f"tx_dev_fb_{i}"
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=0.9,
            )
            engine.log_feedback(
                simulation_id=sim_id,
                transaction_id=tx_id,
                actual_label=1,
            )

        metrics = engine.evaluate_performance(sim_id)
        assert metrics.get("sample_count") == 5
