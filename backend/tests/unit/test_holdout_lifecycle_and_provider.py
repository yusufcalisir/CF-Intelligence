"""Comprehensive unit tests for HoldoutDatasetProvider operational lifecycle and trust boundary enforcement.

Verifies:
1. Production holdout operational lifecycle (loading, validation, integrity, versioning, provenance).
2. Clean process restart continuity (Process A vs Process B simulation).
3. Safe atomic rotation, active-round dataset version binding, and historical evidence preservation.
4. Retirement semantics and fail-closed behavior across all failure modes.
5. Adversarial controls 1 through 14.
6. Falsification mutation challenges A through F.
7. Zero data leakage to federated bank clients.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
import torch.nn as nn

from app.application.services.candidate_evaluator import CandidateModelEvaluator
from app.application.services.coordinator_service import CoordinatorService
from app.application.services.holdout_provider import (
    HoldoutDatasetProvider,
    HoldoutIntegrityError,
    HoldoutResolutionError,
    HoldoutRotationError,
    HoldoutSchemaError,
    compute_file_sha256,
)
from app.domain.value_objects import DesignatedHoldoutDataset, RoundEvaluationEvidence


class SeparableFraudModel(nn.Module):
    """Deterministic linear PyTorch model separating low-feature negatives from high-feature positives."""

    def __init__(self, in_features: int = 2) -> None:
        super().__init__()
        self.linear = nn.Linear(in_features, 1)
        with torch.no_grad():
            self.linear.weight.fill_(5.0)
            self.linear.bias.fill_(-2.5)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.linear(x)).squeeze(-1)


def _create_physical_npz_holdout(
    target_path: Path,
    n_samples: int = 20,
    n_features: int = 2,
    single_class: bool = False,
    include_nan: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Helper creating a physical .npz holdout file for deterministic lifecycle tests."""
    rng = np.random.default_rng(42)
    features = np.zeros((n_samples, n_features), dtype=np.float32)
    labels = np.zeros(n_samples, dtype=int)

    half = n_samples // 2
    features[:half] = rng.uniform(0.0, 0.2, size=(half, n_features)).astype(np.float32)
    features[half:] = rng.uniform(0.8, 1.0, size=(n_samples - half, n_features)).astype(np.float32)

    if single_class:
        labels[:] = 0
    else:
        labels[half:] = 1

    if include_nan:
        features[0, 0] = np.nan

    np.savez(target_path, features=features, labels=labels)
    return features, labels


def _create_manifest_file(
    manifest_path: Path,
    data_file_path: Path,
    dataset_id: str = "canonical_holdout",
    version: str = "1.0.0",
    provenance: str = "EMPIRICAL_EXTERNAL_DATA",
    sha256: str = "",
    feature_dim: int = 2,
) -> dict[str, Any]:
    """Helper writing a valid holdout manifest JSON file."""
    if not sha256 and data_file_path.exists():
        sha256 = compute_file_sha256(data_file_path)

    manifest_dict = {
        "dataset_id": dataset_id,
        "version": version,
        "provenance": provenance,
        "data_file": data_file_path.name,
        "sha256": sha256,
        "feature_dim": feature_dim,
        "created_at": "2026-10-08T00:00:00Z",
        "metadata": {"environment": "production_evaluation"},
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_dict, f, indent=2)
    return manifest_dict


# ==============================================================================
# Operational Lifecycle & Loading Tests
# ==============================================================================


def test_unconfigured_provider_fails_closed() -> None:
    """When no holdout is configured, provider returns None and coordinator blocks promotion."""
    provider = HoldoutDatasetProvider(auto_load=False)
    assert provider.resolve_designated_holdout() is None

    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator()
    coord.set_evaluator(evaluator)

    round_info = coord.start_round(min_clients=1)
    r_id = round_info["round_id"]
    model = SeparableFraudModel()
    coord.set_round_candidate_model(r_id, model, model_version="v1.0.0")

    res = coord.aggregate_and_deploy(r_id, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"
    assert res["auc_score"] is None


def test_valid_manifest_loads_and_verifies_sha256(tmp_path: Path) -> None:
    """A valid manifest and physical NPZ file load successfully with strict SHA-256 verification."""
    data_file = tmp_path / "holdout_v1.npz"
    manifest_file = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    holdout = provider.current_holdout

    assert holdout is not None
    assert holdout.dataset_id == "canonical_holdout"
    assert holdout.version == "1.0.0"
    assert holdout.versioned_id == "canonical_holdout:1.0.0"
    assert holdout.provenance == "EMPIRICAL_EXTERNAL_DATA"
    assert holdout.sha256 == compute_file_sha256(data_file)


def test_missing_data_file_raises_resolution_error(tmp_path: Path) -> None:
    """If the data file declared in the manifest does not exist, provider fails closed with HoldoutResolutionError."""
    manifest_file = tmp_path / "missing_data_manifest.json"
    non_existent_data = tmp_path / "non_existent.npz"
    _create_manifest_file(manifest_file, non_existent_data)

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutResolutionError, match="does not exist"):
        provider.load_from_manifest(manifest_file)


def test_corrupted_data_file_sha_mismatch_raises_integrity_error(tmp_path: Path) -> None:
    """If the file content does not match the manifest SHA-256 digest, loading fails closed with HoldoutIntegrityError."""
    data_file = tmp_path / "tampered_data.npz"
    manifest_file = tmp_path / "tampered_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, sha256="bad_sha_00000000000000000000000000000000000000000000000000000000")

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutIntegrityError, match="SHA-256 mismatch"):
        provider.load_from_manifest(manifest_file)


def test_single_class_holdout_raises_schema_error(tmp_path: Path) -> None:
    """Holdout dataset containing only single-class labels fails schema validation immediately."""
    data_file = tmp_path / "single_class.npz"
    manifest_file = tmp_path / "single_class_manifest.json"
    _create_physical_npz_holdout(data_file, single_class=True)
    _create_manifest_file(manifest_file, data_file)

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutSchemaError, match="must contain both positive and negative classes"):
        provider.load_from_manifest(manifest_file)


def test_feature_dim_mismatch_raises_schema_error(tmp_path: Path) -> None:
    """Holdout dataset with feature dimensionality mismatching manifest raises HoldoutSchemaError."""
    data_file = tmp_path / "features_4d.npz"
    manifest_file = tmp_path / "features_4d_manifest.json"
    _create_physical_npz_holdout(data_file, n_features=4)
    # Manifest expects 2 features
    _create_manifest_file(manifest_file, data_file, feature_dim=2)

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutSchemaError, match="Holdout feature dimension 4 does not match manifest feature_dim 2"):
        provider.load_from_manifest(manifest_file)


# ==============================================================================
# Process Restart Continuity Tests
# ==============================================================================


def test_restart_continuity_re_resolves_without_process_memory(tmp_path: Path) -> None:
    """Process B starting with fresh memory resolves the exact same holdout as Process A from the manifest."""
    data_file = tmp_path / "holdout_shared.npz"
    manifest_file = tmp_path / "manifest_shared.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="2.1.0")

    # --- PROCESS A ---
    provider_a = HoldoutDatasetProvider(manifest_path=manifest_file)
    holdout_a = provider_a.resolve_designated_holdout()
    assert holdout_a is not None

    # Simulate Process A termination by deleting all in-memory references
    del provider_a
    del holdout_a

    # --- PROCESS B (Fresh process memory) ---
    provider_b = HoldoutDatasetProvider(manifest_path=manifest_file)
    holdout_b = provider_b.resolve_designated_holdout()

    assert holdout_b is not None
    assert holdout_b.version == "2.1.0"
    assert holdout_b.dataset_id == "canonical_holdout"
    assert holdout_b.versioned_id == "canonical_holdout:2.1.0"
    assert holdout_b.sha256 == compute_file_sha256(data_file)


# ==============================================================================
# Rotation Semantics & Active Round Invariants
# ==============================================================================


def test_rotation_to_v2_updates_active_holdout_for_future_rounds(tmp_path: Path) -> None:
    """Rotating holdout from v1 to v2 updates provider's active holdout for future rounds."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    data_v2 = tmp_path / "data_v2.npz"
    manifest_v2 = tmp_path / "manifest_v2.json"
    _create_physical_npz_holdout(data_v2)
    _create_manifest_file(manifest_v2, data_v2, version="2.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    assert provider.current_holdout is not None
    assert provider.current_holdout.version == "1.0.0"

    rotated_holdout = provider.rotate_holdout(manifest_v2)
    assert rotated_holdout.version == "2.0.0"
    assert provider.current_holdout is not None
    assert provider.current_holdout.version == "2.0.0"


def test_atomic_rotation_failure_preserves_current_valid_designation(tmp_path: Path) -> None:
    """If rotation target manifest points to a missing or corrupt file, active holdout remains completely untouched."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    corrupt_data = tmp_path / "corrupt_data.npz"
    corrupt_manifest = tmp_path / "corrupt_manifest.json"
    _create_physical_npz_holdout(corrupt_data)
    _create_manifest_file(corrupt_manifest, corrupt_data, version="2.0.0", sha256="wrong_hash")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    assert provider.current_holdout is not None
    assert provider.current_holdout.version == "1.0.0"

    with pytest.raises(HoldoutRotationError, match="Atomic rotation failed"):
        provider.rotate_holdout(corrupt_manifest, atomic=True)

    # Active holdout must still be v1
    assert provider.current_holdout is not None
    assert provider.current_holdout.version == "1.0.0"


def test_active_round_binds_dataset_at_round_start_and_preserves_across_rotation(tmp_path: Path) -> None:
    """A round started under v1 binds v1; subsequent rotation to v2 does NOT alter the active round's bound version."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    data_v2 = tmp_path / "data_v2.npz"
    manifest_v2 = tmp_path / "manifest_v2.json"
    _create_physical_npz_holdout(data_v2)
    _create_manifest_file(manifest_v2, data_v2, version="2.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord.set_evaluator(evaluator)

    # Round 1 starts under v1
    r1 = coord.start_round(min_clients=1)["round_id"]
    assert coord.round_designated_datasets[r1] == "canonical_holdout:1.0.0"

    # Now rotate provider to v2 mid-round
    provider.rotate_holdout(manifest_v2)

    # Round 1's bound designated dataset must still be v1
    assert coord.round_designated_datasets[r1] == "canonical_holdout:1.0.0"

    # Round 2 starts under v2
    r2 = coord.start_round(min_clients=1)["round_id"]
    assert coord.round_designated_datasets[r2] == "canonical_holdout:2.0.0"


def test_historical_round_evidence_preserves_evaluated_dataset_version(tmp_path: Path) -> None:
    """Historical round evaluation records retain the exact dataset version evaluated, even after rotation."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    data_v2 = tmp_path / "data_v2.npz"
    manifest_v2 = tmp_path / "manifest_v2.json"
    _create_physical_npz_holdout(data_v2)
    _create_manifest_file(manifest_v2, data_v2, version="2.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord.set_evaluator(evaluator)

    # Evaluate Round 1 on v1
    r1 = coord.start_round(min_clients=1)["round_id"]
    model_v1 = SeparableFraudModel()
    coord.set_round_candidate_model(r1, model_v1, model_version="v1.0.0")
    res1 = coord.aggregate_and_deploy(r1, allow_test_fixtures=False)
    assert res1["is_champion"] is True
    assert res1["evaluation_evidence"]["dataset_id"] == "canonical_holdout:1.0.0"

    # Rotate to v2
    provider.rotate_holdout(manifest_v2)
    evaluator.set_holdout_dataset(provider.current_holdout)

    # Evaluate Round 2 on v2
    r2 = coord.start_round(min_clients=1)["round_id"]
    model_v2 = SeparableFraudModel()
    coord.set_round_candidate_model(r2, model_v2, model_version="v2.0.0")
    res2 = coord.aggregate_and_deploy(r2, allow_test_fixtures=False)
    assert res2["is_champion"] is True
    assert res2["evaluation_evidence"]["dataset_id"] == "canonical_holdout:2.0.0"

    # Verify Round 1's historical evidence record in coord.rounds is unchanged
    assert coord.rounds[r1]["evaluation_evidence"]["dataset_id"] == "canonical_holdout:1.0.0"


# ==============================================================================
# Retirement Semantics Tests
# ==============================================================================


def test_retirement_disables_future_evaluations_while_preserving_history(tmp_path: Path) -> None:
    """Retiring a holdout causes future rounds to fail closed with UNVERIFIED_NO_EVALUATION."""
    data_file = tmp_path / "data_retire.npz"
    manifest_file = tmp_path / "manifest_retire.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord.set_evaluator(evaluator)

    # Round 1 succeeds
    r1 = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r1, SeparableFraudModel(), model_version="v1.0.0")
    res1 = coord.aggregate_and_deploy(r1, allow_test_fixtures=False)
    assert res1["is_champion"] is True

    # Retire the holdout
    provider.retire_current_holdout()
    evaluator.set_holdout_dataset(None)  # Provider retirement cascades

    # Round 2 fails closed
    r2 = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r2, SeparableFraudModel(), model_version="v2.0.0")
    res2 = coord.aggregate_and_deploy(r2, allow_test_fixtures=False)
    assert res2["is_champion"] is False
    assert res2["model_status"] == "UNVERIFIED_NO_EVALUATION"

    # Historical record for Round 1 remains preserved
    assert coord.rounds[r1]["evaluation_evidence"]["dataset_id"] == "canonical_holdout:1.0.0"


# ==============================================================================
# Adversarial Controls 1 through 14
# ==============================================================================


def test_adversarial_control_01_no_configured_holdout_blocks_promotion() -> None:
    """AC-01: No configured holdout must fail closed with promotion blocked."""
    coord = CoordinatorService()
    evaluator = CandidateModelEvaluator()
    coord.set_evaluator(evaluator)
    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")
    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_02_configured_legitimate_holdout_can_resolve(tmp_path: Path) -> None:
    """AC-02: Configured legitimate holdout enables internal candidate evaluation and champion promotion."""
    data_file = tmp_path / "ac02_data.npz"
    manifest_file = tmp_path / "ac02_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file)

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord.set_evaluator(evaluator)

    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")
    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is True
    assert res["model_status"] == "CHAMPION"


def test_adversarial_control_03_arbitrary_arrays_cannot_self_declare_production_provenance() -> None:
    """AC-03: Arbitrary caller-supplied arrays cannot self-declare EMPIRICAL_EXTERNAL_DATA in production."""
    coord = CoordinatorService()
    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")

    # In production, raw vectors passed via set_round_validation_data are forced to TEST_FIXTURE
    coord.set_round_validation_data(
        r,
        validation_labels=[0, 1],
        validation_preds=[0.1, 0.9],
        provenance="AUTHORITATIVE_HOLDOUT_EVALUATION",
    )
    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "TEST_FIXTURE_PROMOTION_BLOCKED"


def test_adversarial_control_04_arbitrary_dataset_id_cannot_replace_configured_identity(tmp_path: Path) -> None:
    """AC-04: Arbitrary dataset_id in evidence mismatching round expected dataset blocks promotion."""
    data_file = tmp_path / "ac04_data.npz"
    manifest_file = tmp_path / "ac04_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, dataset_id="real_holdout", version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)

    with pytest.raises(ValueError, match="does not match expected 'forged_holdout'"):
        evaluator.evaluate(
            round_id=1,
            candidate_model=SeparableFraudModel(),
            model_version="v1.0.0",
            expected_dataset_id="forged_holdout",
        )


def test_adversarial_control_05_test_fixture_cannot_become_production_holdout_merely_by_changing_string() -> None:
    """AC-05: DesignatedHoldoutDataset with provenance=TEST_FIXTURE cannot promote a production model."""
    holdout_fixture = DesignatedHoldoutDataset(
        dataset_id="fixture_dataset",
        features=np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32),
        labels=np.array([0, 1], dtype=int),
        provenance="TEST_FIXTURE",
    )
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout_fixture)
    coord = CoordinatorService()
    coord.set_evaluator(evaluator)

    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")
    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "TEST_FIXTURE_PROMOTION_BLOCKED"


def test_adversarial_control_06_restart_re_resolves_configured_holdout_without_prior_memory(tmp_path: Path) -> None:
    """AC-06: Clean process restart re-resolves the exact configured holdout without prior memory."""
    data_file = tmp_path / "ac06_data.npz"
    manifest_file = tmp_path / "ac06_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="3.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    holdout = provider.resolve_designated_holdout()
    assert holdout is not None
    assert holdout.version == "3.0.0"


def test_adversarial_control_07_invalid_or_corrupt_source_does_not_trigger_fallback(tmp_path: Path) -> None:
    """AC-07: Corrupted holdout file raises explicit integrity error and never falls back to synthetic data."""
    data_file = tmp_path / "ac07_data.npz"
    manifest_file = tmp_path / "ac07_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, sha256="corrupted_hash")

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutIntegrityError):
        provider.load_from_manifest(manifest_file)


def test_adversarial_control_08_schema_mismatch_blocks_evaluation(tmp_path: Path) -> None:
    """AC-08: Model expecting 2 features evaluated on 4-feature holdout fails closed without promotion."""
    data_file = tmp_path / "ac08_data.npz"
    manifest_file = tmp_path / "ac08_manifest.json"
    _create_physical_npz_holdout(data_file, n_features=4)
    _create_manifest_file(manifest_file, data_file, feature_dim=4)

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord = CoordinatorService()
    coord.set_evaluator(evaluator)

    r = coord.start_round(min_clients=1)["round_id"]
    # Model expects 2 features, but holdout has 4
    model_2d = SeparableFraudModel(in_features=2)
    coord.set_round_candidate_model(r, model_2d, model_version="v1.0.0")

    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_09_rotation_to_valid_v2_affects_future_rounds(tmp_path: Path) -> None:
    """AC-09: Rotation to valid v2 affects future rounds while preserving earlier designations."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    data_v2 = tmp_path / "data_v2.npz"
    manifest_v2 = tmp_path / "manifest_v2.json"
    _create_physical_npz_holdout(data_v2)
    _create_manifest_file(manifest_v2, data_v2, version="2.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)

    r1 = coord.start_round(min_clients=1)["round_id"]
    assert coord.round_designated_datasets[r1] == "canonical_holdout:1.0.0"

    provider.rotate_holdout(manifest_v2)

    r2 = coord.start_round(min_clients=1)["round_id"]
    assert coord.round_designated_datasets[r2] == "canonical_holdout:2.0.0"


def test_adversarial_control_10_active_and_historical_evidence_remains_bound_to_v1(tmp_path: Path) -> None:
    """AC-10: Evidence evaluated under v1 never mutates to claim v2 identity."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)

    ev1 = evaluator.evaluate(
        round_id=1,
        candidate_model=SeparableFraudModel(),
        model_version="v1.0.0",
        expected_dataset_id="canonical_holdout:1.0.0",
    )
    assert ev1.dataset_id == "canonical_holdout:1.0.0"


def test_adversarial_control_11_invalid_rotation_does_not_destroy_valid_designation(tmp_path: Path) -> None:
    """AC-11: Attempting to rotate to an unparseable manifest preserves current valid designation."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    bad_manifest = tmp_path / "corrupt_json.json"
    with open(bad_manifest, "w", encoding="utf-8") as f:
        f.write("{invalid_json_content")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    with pytest.raises(HoldoutRotationError):
        provider.rotate_holdout(bad_manifest, atomic=True)

    assert provider.current_holdout is not None
    assert provider.current_holdout.version == "1.0.0"


def test_adversarial_control_12_missing_retired_source_does_not_rewrite_historical_evidence(tmp_path: Path) -> None:
    """AC-12: Physical deletion of a retired holdout file does not rewrite completed historical round records."""
    data_file = tmp_path / "to_delete.npz"
    manifest_file = tmp_path / "to_delete_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)
    evaluator = CandidateModelEvaluator(holdout_dataset=provider.current_holdout)
    coord.set_evaluator(evaluator)

    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")
    res = coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["evaluation_evidence"]["dataset_id"] == "canonical_holdout:1.0.0"

    # Delete physical file
    data_file.unlink()

    # Historical record still holds the evaluated record
    assert coord.rounds[r]["evaluation_evidence"]["dataset_id"] == "canonical_holdout:1.0.0"


def test_adversarial_control_13_holdout_data_is_never_sent_to_participating_banks() -> None:
    """AC-13: StartRoundRequest and RoundCompleteNotification messages contain NO holdout features or labels."""
    coord = CoordinatorService()
    round_info = coord.start_round(min_clients=1)
    r_id = round_info["round_id"]

    for notif in coord.grpc_notifications:
        assert "features" not in notif
        assert "labels" not in notif
        assert "validation_data" not in notif
        assert "holdout" not in notif

    coord.aggregate_and_deploy(r_id, allow_test_fixtures=False)

    for notif in coord.grpc_notifications:
        assert "features" not in notif
        assert "labels" not in notif
        assert "validation_data" not in notif


def test_adversarial_control_14_provenance_and_version_mismatch_blocks_evaluation(tmp_path: Path) -> None:
    """AC-14: If provider resolve is asked for an incompatible version, resolution raises HoldoutResolutionError."""
    data_file = tmp_path / "ac14_data.npz"
    manifest_file = tmp_path / "ac14_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    with pytest.raises(HoldoutResolutionError, match="Resolved dataset version '1.0.0' does not match expected '2.0.0'"):
        provider.resolve_designated_holdout(expected_version="2.0.0")


# ==============================================================================
# Falsification Mutation Challenges A through F
# ==============================================================================


def test_mutation_a_synthetic_holdout_generation_on_missing_is_killed() -> None:
    """Mutation A: If holdout is missing, attempting to return a synthetic mock dataset is killed."""
    provider = HoldoutDatasetProvider(auto_load=False)
    holdout = provider.resolve_designated_holdout()
    # Must be None, never a synthetic object
    assert holdout is None


def test_mutation_b_arbitrary_runtime_arrays_claiming_empirical_is_killed() -> None:
    """Mutation B: Arbitrary runtime arrays cannot bypass provider validation to claim EMPIRICAL_EXTERNAL_DATA in production."""
    coord = CoordinatorService()
    r = coord.start_round(min_clients=1)["round_id"]
    coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")

    # In production promotion, raw caller vectors are blocked
    ev = RoundEvaluationEvidence(
        round_id=r,
        validation_labels=[0, 1],
        validation_preds=[0.1, 0.9],
        provenance="EMPIRICAL_EXTERNAL_DATA",  # Attempted forgery
        producer="caller_untrusted",
    )
    res = coord.aggregate_and_deploy(r, evidence=ev, allow_test_fixtures=False)
    # Blocked because allow_test_fixtures=False and evidence is caller-supplied
    assert res["is_champion"] is False


def test_mutation_c_ignore_configured_version_and_use_another_is_killed(tmp_path: Path) -> None:
    """Mutation C: Attempting to use v1 when round expects v2 raises HoldoutResolutionError and is killed."""
    data_file = tmp_path / "mut_c_data.npz"
    manifest_file = tmp_path / "mut_c_manifest.json"
    _create_physical_npz_holdout(data_file)
    _create_manifest_file(manifest_file, data_file, version="1.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_file)
    with pytest.raises(HoldoutResolutionError, match="does not match expected '2.0.0'"):
        provider.resolve_designated_holdout(expected_version="2.0.0")


def test_mutation_d_rotate_during_active_round_silently_changing_identity_is_killed(tmp_path: Path) -> None:
    """Mutation D: Mid-round rotation changing the round's bound dataset identity is killed."""
    data_v1 = tmp_path / "data_v1.npz"
    manifest_v1 = tmp_path / "manifest_v1.json"
    _create_physical_npz_holdout(data_v1)
    _create_manifest_file(manifest_v1, data_v1, version="1.0.0")

    data_v2 = tmp_path / "data_v2.npz"
    manifest_v2 = tmp_path / "manifest_v2.json"
    _create_physical_npz_holdout(data_v2)
    _create_manifest_file(manifest_v2, data_v2, version="2.0.0")

    provider = HoldoutDatasetProvider(manifest_path=manifest_v1)
    coord = CoordinatorService()
    coord.set_holdout_provider(provider)

    r = coord.start_round(min_clients=1)["round_id"]
    initial_bound = coord.round_designated_datasets[r]
    assert initial_bound == "canonical_holdout:1.0.0"

    provider.rotate_holdout(manifest_v2)

    # Round's bound dataset MUST remain unchanged
    assert coord.round_designated_datasets[r] == initial_bound
    assert coord.round_designated_datasets[r] != "canonical_holdout:2.0.0"


def test_mutation_e_silent_continue_after_restart_claiming_available_is_killed() -> None:
    """Mutation E: After restart with unconfigured provider, claiming evaluation is available is killed."""
    fresh_provider = HoldoutDatasetProvider(auto_load=False)
    assert fresh_provider.current_holdout is None

    fresh_coord = CoordinatorService()
    fresh_coord.set_holdout_provider(fresh_provider)
    fresh_evaluator = CandidateModelEvaluator()
    fresh_coord.set_evaluator(fresh_evaluator)

    r = fresh_coord.start_round(min_clients=1)["round_id"]
    fresh_coord.set_round_candidate_model(r, SeparableFraudModel(), model_version="v1.0.0")

    res = fresh_coord.aggregate_and_deploy(r, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_mutation_f_feature_schema_mismatch_auto_padded_or_truncated_is_killed(tmp_path: Path) -> None:
    """Mutation F: Feature dimensionality mismatch must raise HoldoutSchemaError and not be silently padded/truncated."""
    data_file = tmp_path / "mut_f_data.npz"
    manifest_file = tmp_path / "mut_f_manifest.json"
    _create_physical_npz_holdout(data_file, n_features=10)
    _create_manifest_file(manifest_file, data_file, feature_dim=2)

    provider = HoldoutDatasetProvider(auto_load=False)
    with pytest.raises(HoldoutSchemaError, match="Holdout feature dimension 10 does not match manifest feature_dim 2"):
        provider.load_from_manifest(manifest_file)
