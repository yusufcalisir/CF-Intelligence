"""
Unit tests verifying model registry storage integrity, fail-closed corruption handling,
and true multiprocess concurrency.

Threat Model & Invariants Tested:
1. DEFECT 8: Corrupted or truncated manifests must NEVER be silently treated as empty registries
   (returning []). A corrupted or truncated manifest must raise ModelIntegrityError fail-closed
   to prevent overwriting historical model versions.
2. Only a genuinely uninitialized directory (manifest file does not exist yet) returns [].
3. Multiprocess concurrency: True OS processes using multiprocessing.Process saving models
   concurrently must all succeed without corrupting JSON or assigning duplicate version integers.
"""

import multiprocessing
import os
import tempfile

import pytest
import torch

from app.application.services.model_registry import (
    ModelRegistry,
)
from app.domain.model_serving_errors import ModelIntegrityError


def _worker_save_model(storage_dir: str, simulation_id: str, worker_id: int, return_queue: multiprocessing.Queue):
    """Worker function executed in a separate operating system process."""
    try:
        registry = ModelRegistry(storage_dir=storage_dir)
        state_dict = {
            "weight": torch.randn(2, 5, dtype=torch.float32),
            "bias": torch.zeros(2, dtype=torch.float32),
        }

        entry = registry.save_version(
            simulation_id=simulation_id,
            state_dict=state_dict,
            metrics={"auc": 0.80},
            is_promoted=False,
            provenance_type="TRAINED",
        )
        return_queue.put({"success": True, "version": entry["version"], "worker_id": worker_id})
    except Exception as e:
        return_queue.put({"success": False, "error": str(e), "worker_id": worker_id})


class TestManifestIntegrityAndFailClosedBehavior:
    """Tests for Defect 8: Preventing silent registry reset on manifest corruption."""

    def test_uninitialized_registry_returns_empty_manifest(self):
        """Invariant: An empty, uninitialized directory cleanly returns [] without errors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            assert registry._load_manifest("sim_fresh") == []
            assert registry.list_versions("sim_fresh") == []

    def test_corrupted_json_manifest_raises_model_integrity_error(self):
        """
        Threat: Manifest contains invalid/truncated JSON (e.g. power loss or process kill).
        Invariant: Must raise ModelIntegrityError fail-closed, NOT silently return [].
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            sim_dir = registry._get_sim_dir("sim_corrupt")
            manifest_file = os.path.join(sim_dir, "registry.json")
            with open(manifest_file, "w") as f:
                f.write('{"versions": [{"version": 1, "status": "c')  # Truncated JSON

            with pytest.raises(ModelIntegrityError, match="corrupted or unreadable"):
                registry._load_manifest("sim_corrupt")

    def test_empty_zero_byte_manifest_file_raises_model_integrity_error(self):
        """
        Threat: Manifest file exists but is 0 bytes (truncated during write crash).
        Invariant: Must raise ModelIntegrityError fail-closed.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            sim_dir = registry._get_sim_dir("sim_empty")
            manifest_file = os.path.join(sim_dir, "registry.json")
            with open(manifest_file, "w") as f:
                f.write("")  # 0 bytes

            with pytest.raises(ModelIntegrityError, match="is empty"):
                registry._load_manifest("sim_empty")

    def test_invalid_manifest_schema_raises_model_integrity_error(self):
        """
        Threat: Manifest is valid JSON, but has invalid structure (not a list).
        Invariant: Must raise ModelIntegrityError fail-closed.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            sim_dir = registry._get_sim_dir("sim_bad_schema")
            manifest_file = os.path.join(sim_dir, "registry.json")
            with open(manifest_file, "w") as f:
                f.write('{"some_key": "some_value"}')  # Dict instead of List

            with pytest.raises(ModelIntegrityError, match="expected JSON list"):
                registry._load_manifest("sim_bad_schema")


class TestMultiprocessRegistryConcurrency:
    """Tests for multi-process concurrency safety using file locking."""

    def test_true_multiprocess_concurrent_saves(self):
        """
        Threat: Multiple independent OS processes write to the registry concurrently.
        Invariant: File lock prevents race conditions; versions remain monotonic and uncorrupted.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            q = multiprocessing.Queue()
            num_workers = 3
            processes = []

            for i in range(num_workers):
                p = multiprocessing.Process(
                    target=_worker_save_model,
                    args=(tmp_dir, "sim_mp_concurrency", i, q),
                )
                processes.append(p)
                p.start()

            for p in processes:
                p.join(timeout=10)

            results = []
            while not q.empty():
                results.append(q.get())

            assert len(results) == num_workers
            assert all(r["success"] is True for r in results), f"Worker failed: {results}"

            versions = [r["version"] for r in results]
            # Each worker must receive a unique version integer
            assert len(set(versions)) == num_workers

            # Post-concurrency verification: manifest remains intact and readable
            registry = ModelRegistry(storage_dir=tmp_dir)
            saved_versions = registry.list_versions("sim_mp_concurrency")
            assert len(saved_versions) == num_workers
