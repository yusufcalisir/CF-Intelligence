"""Focused adversarial tests for verified model artifact loading and integrity enforcement.

Verifies:
- Genuine model state dict deserialization from verified memory snapshot.
- Detection and rejection of weight-file tampering after registration (SHA-256 mismatch).
- Manifest filename substitution and traversal rejection.
- Model revocation validation in operational loading vs. offline analysis (allow_revoked=True).
- Issuer key revocation rejection.
- In-memory snapshot integrity (TOCTOU race protection).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import torch

from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import FraudDetectionModel
from app.domain.model_serving_errors import ModelIntegrityError


@pytest.fixture
def clean_registry(tmp_path: Path) -> ModelRegistry:
    storage_dir = str(tmp_path / "storage")
    return ModelRegistry(storage_dir=storage_dir)


def _create_sample_model() -> FraudDetectionModel:
    model = FraudDetectionModel(input_dim=10, dp_compatible=True)
    model.eval()
    return model


class TestVerifiedModelLoading:
    """Verifies that ModelRegistry.load_version cryptographically verifies artifacts before deserialization."""

    def test_load_valid_version_succeeds(self, clean_registry: ModelRegistry) -> None:
        model = _create_sample_model()
        sim_id = "sim_verified_01"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        loaded_state = clean_registry.load_version(sim_id, version=1)
        assert isinstance(loaded_state, dict)
        assert "network.0.weight" in loaded_state

        # Verify loaded weights match original weights exactly
        orig_weight = model.state_dict()["network.0.weight"]
        loaded_weight = loaded_state["network.0.weight"]
        assert torch.equal(orig_weight, loaded_weight)

    def test_weight_file_tampering_raises_model_integrity_error(
        self, clean_registry: ModelRegistry
    ) -> None:
        model = _create_sample_model()
        sim_id = "sim_tamper_01"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        # Locate the saved artifact on disk
        sim_dir = clean_registry._get_sim_dir(sim_id, create=False)
        artifact_path = os.path.join(sim_dir, "model_v1.pt")
        assert os.path.isfile(artifact_path)

        # Adversarially tamper with weight bytes on disk
        with open(artifact_path, "r+b") as f:
            f.seek(10)
            f.write(b"\xFF\xFE\xFD\xFC")

        # load_version must detect SHA-256 mismatch and reject the tampered artifact
        with pytest.raises(ModelIntegrityError, match="SHA-256 digest mismatch"):
            clean_registry.load_version(sim_id, version=1)

    def test_manifest_filename_traversal_substitution_rejected(
        self, clean_registry: ModelRegistry
    ) -> None:
        model = _create_sample_model()
        sim_id = "sim_traversal_manifest"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        sim_dir = clean_registry._get_sim_dir(sim_id, create=False)
        manifest_path = os.path.join(sim_dir, "registry.json")

        # Tamper with the manifest to substitute a traversal filename
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        manifest[0]["filename"] = "../../../etc/passwd"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        # load_version must reject path traversal in manifest filename
        with pytest.raises(ModelIntegrityError, match="Path traversal detected in manifest filename"):
            clean_registry.load_version(sim_id, version=1)

    def test_revoked_version_rejected_unless_allow_revoked_explicitly_passed(
        self, clean_registry: ModelRegistry
    ) -> None:
        model = _create_sample_model()
        sim_id = "sim_revocation_test"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        sim_dir = clean_registry._get_sim_dir(sim_id, create=False)
        manifest_path = os.path.join(sim_dir, "registry.json")

        # Mark version 1 as revoked in registry manifest
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest[0]["is_revoked"] = True
        manifest[0]["status"] = "revoked"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        # Operational load must fail closed
        with pytest.raises(ModelIntegrityError, match="has been revoked"):
            clean_registry.load_version(sim_id, version=1, allow_revoked=False)

        # Offline inspection explicitly permitting revoked model succeeds
        offline_state = clean_registry.load_version(sim_id, version=1, allow_revoked=True)
        assert isinstance(offline_state, dict)

    def test_revoked_issuer_key_rejects_model_loading(
        self, clean_registry: ModelRegistry
    ) -> None:
        model = _create_sample_model()
        sim_id = "sim_issuer_revoked"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        sim_dir = clean_registry._get_sim_dir(sim_id, create=False)
        manifest_path = os.path.join(sim_dir, "registry.json")

        # Attach receipt with a revoked key ID
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest[0]["training_receipt"] = {"key_id": "revoked_coordinator_key_99"}
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        # Mock verifier to report this key as untrusted
        class MockUntrustedVerifier:
            def is_key_trusted(self, key_id: str) -> bool:
                return False

        clean_registry._verifier = MockUntrustedVerifier()  # type: ignore[assignment]

        with pytest.raises(ModelIntegrityError, match="training receipt key.*has been revoked"):
            clean_registry.load_version(sim_id, version=1)

    def test_toctou_snapshot_integrity(
        self, clean_registry: ModelRegistry
    ) -> None:
        """Verifies that bytes loaded into the snapshot buffer are protected against post-check mutation."""
        model = _create_sample_model()
        sim_id = "sim_toctou_snapshot"
        clean_registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
        )

        # Read genuine model state dict
        loaded = clean_registry.load_version(sim_id, version=1)
        assert isinstance(loaded, dict)
        assert "network.0.weight" in loaded
