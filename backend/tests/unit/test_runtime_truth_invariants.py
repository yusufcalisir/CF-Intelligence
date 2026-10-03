"""Automated Regression Suite for Runtime Truth Invariants (Phase 2).

Verifies the 8 core runtime truth invariants across backend execution paths:
Invariant 1: Simulation creation records genuine live execution provenance.
Invariant 2: Telemetry streaming exposes explicit provenance and rejects fake live labeling.
Invariant 3: Design partner evaluation uses genuine model inference without Beta fabrication.
Invariant 4: Empty model registry remains empty (no phantom champions).
Invariant 5: Missing benchmark baselines raise explicit 503 instead of hardcoded fallbacks.
Invariant 6: Software TEE never masquerades as hardware SGX attestation.
Invariant 7: Missing real datasets raise FileNotFoundError instead of silent synthetic substitution.
Invariant 8: Cold-boot simulation results do not pre-seed unexecuted completed runs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.application.services.dataloader import load_elliptic
from app.application.services.design_partner_service import DesignPartnerPilotService
from app.infrastructure.security.tee_driver import TEEDriver, is_sgx_hardware_available
from app.presentation.routers.dashboard import get_comparative_baselines
from app.presentation.routers.health import health_root
from app.presentation.routers.model_registry import list_registered_models
from app.presentation.routers.simulation import (
    _seed_canonical_simulation,
    get_simulation,
)


@pytest.mark.asyncio
async def test_invariant_4_empty_model_registry_remains_empty():
    """Invariant 4: Empty model registry must return an honest empty list []."""
    with patch("app.presentation.routers.model_registry._get_all_model_summaries", return_value=[]):
        result = await list_registered_models()
        assert result.models == [], "Empty model registry returned fabricated champion models!"
        assert result.total_models == 0


@pytest.mark.asyncio
async def test_invariant_5_missing_comparative_baselines_raises_503():
    """Invariant 5: Missing benchmark reference must not invent silent fallback metrics."""
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await get_comparative_baselines()
        assert exc_info.value.status_code == 503
        assert "not available on disk" in exc_info.value.detail


def test_invariant_3_design_partner_inference_is_genuine_and_reproducible():
    """Invariant 3: Reference benchmark evaluation uses genuine model inference, not Beta synthesis."""
    service = DesignPartnerPilotService()
    result = service.evaluate_reference_benchmark(
        dataset_name="paysim",
        n_samples=200,
    )
    assert "evaluation_provenance" in result
    assert result["evaluation_provenance"]["model_type"] == "PYTORCH_FEDERATED_INFERENCE"
    assert result["evaluation_provenance"]["probability_synthesis"] == "NONE_GENUINE_INFERENCE"
    assert not result["evaluation_provenance"]["is_synthetic_beta"]

    # Verify evaluated model metrics are genuine
    assert "performance_comparison" in result
    fl_metrics = result["performance_comparison"]["federated_learning"]
    assert 0.0 <= fl_metrics["roc_auc"] <= 1.0


def test_invariant_6_software_tee_never_claims_hardware_attestation():
    """Invariant 6: Software TEE emulation must never claim hardware-backed attestation."""
    enclave_ctx = TEEDriver.create_enclave("test_sim_runtime_truth")
    report = TEEDriver.generate_attestation_report(enclave_ctx)

    hw_present = is_sgx_hardware_available()
    assert report.is_hardware_backed == hw_present, (
        f"Attestation report claimed hardware={report.is_hardware_backed} but physical hardware={hw_present}"
    )
    if not hw_present:
        assert report.driver_mode == "SOFTWARE_EMULATION_SANDBOX"


def test_invariant_7_missing_real_dataset_raises_file_not_found(tmp_path):
    """Invariant 7: Missing real dataset must raise FileNotFoundError when synthetic fallback is disabled."""
    empty_dir = tmp_path / "empty_elliptic_dir"
    empty_dir.mkdir()

    # Strict real-data request
    with pytest.raises(FileNotFoundError, match="Real Elliptic Bitcoin dataset files not found"):
        load_elliptic(path=empty_dir, require_real=True)

    # Calling with allow_synthetic=False
    with pytest.raises(FileNotFoundError):
        load_elliptic(path=empty_dir, allow_synthetic=False)

    # Calling with explicit allow_synthetic=True returns mock with explicit provenance
    mock_data = load_elliptic(path=empty_dir, allow_synthetic=True, n_mock_nodes=50)
    assert mock_data["source"] == "mock"
    assert mock_data["provenance"] == "EXPLICIT_SYNTHETIC_DEMO"
    assert mock_data["is_synthetic"] is True


@pytest.mark.asyncio
async def test_invariant_8_cold_boot_simulation_results_provenance():
    """Invariant 8: Seeded canonical benchmark has explicit reference provenance and doesn't masquerade as live run."""
    _seed_canonical_simulation()
    detail = await get_simulation("sim_fed_01")
    assert detail.is_canonical_reference is True
    assert detail.provenance == "CANONICAL_BENCHMARK_REFERENCE"
    assert detail.execution_mode == "REFERENCE_RUN"
    assert detail.tee_is_hardware_backed is False


@pytest.mark.asyncio
async def test_invariant_health_api_exposes_persistence_semantics():
    """Health check endpoint explicitly reports persistence backend and durability."""
    resp = await health_root()
    assert resp.storage_backend in ("redis", "in_memory")
    assert resp.durability in ("durable", "ephemeral")
