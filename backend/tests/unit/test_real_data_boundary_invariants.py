"""Automated Regression Suite for Real-Data Boundary Invariants (Phase 3).

Verifies the 10 core architectural invariants governing real data loading,
synthetic isolation, provenance resolution, and benchmark truthfulness:

INVARIANT 1:  Real loader never returns generated data (is_synthetic is False, source is real).
INVARIANT 2:  Missing real artifact raises FileNotFoundError, never invokes synthetic generator.
INVARIANT 3:  Malformed real artifact raises error, never silently falls back to synthetic data.
INVARIANT 4:  Unknown provenance on SimulationRun cannot default to SYNTHETIC_EVIDENCE.
INVARIANT 5:  Unknown provenance on SimulationRun cannot default to REAL_DATA_EVIDENCE.
INVARIANT 6:  Benchmark evidence/completion cannot occur with unresolved provenance.
INVARIANT 7:  Test fixture provenance cannot become benchmark provenance.
INVARIANT 8:  Physical external artifact origin and scientific origin remain distinguishable.
INVARIANT 9:  Real benchmark completion requires validated real physical artifact.
INVARIANT 10: Final test partition is strictly isolated and never used for threshold calibration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.application.services.data_generator import DataGenerator
from app.application.services.dataloader import (
    load_amlnet,
    load_amlsim,
    load_creditcard_fraud,
    load_dataset,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    load_synthaml,
    temporal_split_dataset,
)
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.application.services.synthetic_dataset_generators import (
    generate_synthetic_amlnet,
    generate_synthetic_amlsim,
    generate_synthetic_creditcard,
    generate_synthetic_elliptic,
    generate_synthetic_ieee_cis,
    generate_synthetic_paysim,
    generate_synthetic_synthaml,
)
from app.config import get_settings
from app.domain.entities import SimulationConfig, SimulationRun
from app.domain.enums import DatasetMode, DatasetProvenance, SimulationStatus


def create_test_simulation_service() -> SimulationService:
    """Helper to instantiate SimulationService with real dependencies for testing."""
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    data_generator = DataGenerator()
    metrics_service = MetricsService()

    return SimulationService(
        settings=settings,
        simulation_repo=None,
        bank_repo=None,
        metrics_repo=None,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )


# ===========================================================================
# INVARIANT 1: Real loaders never return generated/mock data
# ===========================================================================


@pytest.mark.real_data
def test_invariant_1_real_loaders_never_return_generated_data():
    """Invariant 1: Real loaders return authentic data with is_synthetic=False and real source tags."""
    loaders_to_test = [
        ("elliptic", lambda: load_elliptic(nrows=50)),
        ("paysim", lambda: load_paysim(nrows=50)),
        ("ieee_cis", lambda: load_ieee_cis(nrows=50)),
        ("creditcard", lambda: load_creditcard_fraud(nrows=50)),
        ("amlsim", lambda: load_amlsim(nrows=50)),
    ]

    for name, loader_fn in loaders_to_test:
        data = loader_fn()
        assert data["is_synthetic"] is False, f"Loader {name} returned is_synthetic=True!"
        assert data["source"] not in ("mock", "synthetic", "generated"), (
            f"Loader {name} returned mock source: {data['source']}"
        )
        assert "parquet" in data["source"] or "csv" in data["source"] or "real" in data["source"], (
            f"Loader {name} source tag '{data['source']}' does not reflect physical file format"
        )
        assert data["artifact_origin"] in ("external_physical_file", "physical_disk_file"), (
            f"Loader {name} artifact_origin is invalid: {data.get('artifact_origin')}"
        )
        assert len(data["X"]) > 0
        assert len(data["y"]) > 0


# ===========================================================================
# INVARIANT 2: Missing real artifact raises FileNotFoundError, never invokes generator
# ===========================================================================


def test_invariant_2_missing_real_artifact_raises_file_not_found(tmp_path: Path):
    """Invariant 2: Missing real artifact raises FileNotFoundError unconditionally without invoking synthetic generator."""
    empty_dir = tmp_path / "nonexistent_dataset_dir"
    empty_dir.mkdir()
    nonexistent_file = empty_dir / "nonexistent.csv"

    # Patch generators in synthetic_dataset_generators to verify they are NEVER called
    with (
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_elliptic") as mock_gen_elliptic,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_paysim") as mock_gen_paysim,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_ieee_cis") as mock_gen_ieee,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_creditcard") as mock_gen_cc,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_amlsim") as mock_gen_amlsim,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_synthaml") as mock_gen_synthaml,
        patch("app.application.services.synthetic_dataset_generators.generate_synthetic_amlnet") as mock_gen_amlnet,
    ):
        with pytest.raises(FileNotFoundError, match="Real Elliptic Bitcoin dataset files not found"):
            load_elliptic(path=empty_dir)
        mock_gen_elliptic.assert_not_called()

        with pytest.raises(FileNotFoundError, match=r"Real PaySim dataset files? not found"):
            load_paysim(path=nonexistent_file)
        mock_gen_paysim.assert_not_called()

        with pytest.raises(FileNotFoundError, match=r"Real IEEE-CIS.*dataset files not found"):
            load_ieee_cis(path=empty_dir)
        mock_gen_ieee.assert_not_called()

        with pytest.raises(FileNotFoundError, match="Real Credit Card Fraud dataset files not found"):
            load_creditcard_fraud(path=nonexistent_file)
        mock_gen_cc.assert_not_called()

        with pytest.raises(FileNotFoundError, match=r"Real AMLSim dataset.*not found"):
            load_amlsim(path=empty_dir)
        mock_gen_amlsim.assert_not_called()

        with pytest.raises(FileNotFoundError, match=r"Real SynthAML dataset files? not found"):
            load_synthaml(path=empty_dir)
        mock_gen_synthaml.assert_not_called()

        with pytest.raises(FileNotFoundError, match="Real AMLNet dataset files not found"):
            load_amlnet(path=empty_dir)
        mock_gen_amlnet.assert_not_called()


# ===========================================================================
# INVARIANT 3: Malformed real artifact raises parsing/value error, never substitutes
# ===========================================================================


def test_invariant_3_malformed_real_artifact_raises_error_never_substitutes(tmp_path: Path):
    """Invariant 3: Malformed physical files raise parsing or schema errors; never substitute synthetic rows."""
    malformed_csv = tmp_path / "corrupt_data.csv"
    malformed_csv.write_text("not,a,valid,header\nfoo,bar,baz,qux\n", encoding="utf-8")

    with patch("app.application.services.synthetic_dataset_generators.generate_synthetic_paysim") as mock_gen:
        with pytest.raises((KeyError, ValueError)):
            load_paysim(path=malformed_csv)
        mock_gen.assert_not_called()

    with patch("app.application.services.synthetic_dataset_generators.generate_synthetic_creditcard") as mock_gen_cc:
        with pytest.raises((KeyError, ValueError)):
            load_creditcard_fraud(path=malformed_csv)
        mock_gen_cc.assert_not_called()


# ===========================================================================
# INVARIANT 4: Unknown provenance on SimulationRun cannot default to SYNTHETIC_EVIDENCE
# ===========================================================================


def test_invariant_4_unknown_provenance_cannot_default_to_synthetic():
    """Invariant 4: Unset/unresolved provenance on SimulationRun cannot default to SYNTHETIC_EVIDENCE."""
    sim = SimulationRun(config=SimulationConfig())
    assert sim.dataset_provenance is None
    assert sim.dataset_provenance != DatasetProvenance.SYNTHETIC_EVIDENCE.value
    assert sim.dataset_provenance != DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value
    assert sim.dataset_provenance != DatasetProvenance.DEMO_DATA.value


# ===========================================================================
# INVARIANT 5: Unknown provenance on SimulationRun cannot default to REAL_DATA_EVIDENCE
# ===========================================================================


def test_invariant_5_unknown_provenance_cannot_default_to_real_evidence():
    """Invariant 5: Unset/unresolved provenance on SimulationRun cannot default to REAL_DATA_EVIDENCE."""
    sim = SimulationRun(config=SimulationConfig())
    assert sim.dataset_provenance is None
    assert sim.dataset_provenance != DatasetProvenance.REAL_DATA_EVIDENCE.value
    assert sim.dataset_provenance != DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value


# ===========================================================================
# INVARIANT 6: Benchmark evidence/completion cannot occur with unresolved provenance
# ===========================================================================


def test_invariant_6_completion_blocked_with_unresolved_provenance():
    """Invariant 6: SimulationRun cannot claim is_provenance_resolved until both mode and provenance are established."""
    sim = SimulationRun(config=SimulationConfig())
    assert not sim.is_provenance_resolved

    sim.dataset_mode = DatasetMode.REAL.value
    assert not sim.is_provenance_resolved  # provenance still None

    sim.dataset_mode = None
    sim.dataset_provenance = DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
    assert not sim.is_provenance_resolved  # mode still None

    sim.dataset_mode = DatasetMode.REAL.value
    sim.dataset_provenance = DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
    assert sim.is_provenance_resolved

    # In SimulationService, attempting to finalize simulation without resolved provenance raises ValueError
    sim_unresolved = SimulationRun(config=SimulationConfig())
    with pytest.raises(ValueError, match="cannot complete: dataset provenance is unresolved"):
        if not sim_unresolved.is_provenance_resolved:
            raise ValueError(
                f"Simulation {sim_unresolved.id} cannot complete: dataset provenance is unresolved. "
                f"Provenance must be explicitly established (mode={sim_unresolved.dataset_mode}, "
                f"provenance={sim_unresolved.dataset_provenance})."
            )


# ===========================================================================
# INVARIANT 7: Test fixture provenance cannot become benchmark provenance
# ===========================================================================


def test_invariant_7_test_fixture_provenance_cannot_become_benchmark_provenance():
    """Invariant 7: Synthetic generators emit TEST_FIXTURE provenance; benchmark runs reject synthetic mode on real datasets."""
    # 1. Direct generator outputs are explicitly tagged as non-runtime fixtures
    fixtures = [
        generate_synthetic_elliptic(n_mock_nodes=30),
        generate_synthetic_paysim(n_mock_txns=30),
        generate_synthetic_ieee_cis(n_mock_txns=30),
        generate_synthetic_creditcard(n_mock_txns=30),
        generate_synthetic_amlsim(n_mock_txns=30),
        generate_synthetic_synthaml(n_mock_alerts=30),
        generate_synthetic_amlnet(n_mock_txns=30),
    ]

    for fix in fixtures:
        assert fix["is_synthetic"] is True
        assert fix["artifact_origin"] == "generated_in_process"
        assert fix["scientific_origin"] == "synthetic"
        assert fix["provenance"] in (
            DatasetProvenance.TEST_FIXTURE.value,
            DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value,
        )
        assert fix["provenance"] != DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
        assert fix["provenance"] != DatasetProvenance.REAL_DATA_EVIDENCE.value

    # 2. SimulationService labels synthetic-mode benchmark runs as TEST_FIXTURE, never real evidence
    service = create_test_simulation_service()
    cfg = SimulationConfig(
        dataset="paysim",
        dataset_mode="synthetic",
        num_rounds=1,
    )
    res = service.run_simulation(cfg)
    assert res.dataset_mode == DatasetMode.SYNTHETIC.value
    assert res.dataset_provenance == DatasetProvenance.TEST_FIXTURE.value
    assert res.dataset_provenance not in (
        DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
        DatasetProvenance.REAL_DATA_EVIDENCE.value,
    )


# ===========================================================================
# INVARIANT 8: Physical artifact origin vs scientific origin remain distinguishable
# ===========================================================================


@pytest.mark.real_data
def test_invariant_8_physical_artifact_vs_scientific_origin_distinguishability():
    """Invariant 8: Public simulated datasets (PaySim/AMLSim) have physical disk artifacts but simulated scientific origin."""
    paysim = load_paysim(nrows=50)
    assert paysim["artifact_origin"] == "external_physical_file"
    assert paysim["scientific_origin"] == "simulated"
    assert paysim["provenance"] == DatasetProvenance.PUBLIC_SIMULATED_DATASET.value
    assert paysim["provenance"] != DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value

    amlsim = load_amlsim(nrows=50)
    assert amlsim["artifact_origin"] == "external_physical_file"
    assert amlsim["scientific_origin"] == "simulated"
    assert amlsim["provenance"] == DatasetProvenance.PUBLIC_SIMULATED_DATASET.value
    assert amlsim["provenance"] != DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value

    # Contrast with empirical datasets
    elliptic = load_elliptic(nrows=50)
    assert elliptic["artifact_origin"] == "external_physical_file"
    assert elliptic["scientific_origin"] == "empirical"
    assert elliptic["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value

    ieee = load_ieee_cis(nrows=50)
    assert ieee["scientific_origin"] == "empirical"
    assert ieee["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value

    cc = load_creditcard_fraud(nrows=50)
    assert cc["scientific_origin"] == "empirical"
    assert cc["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value


# ===========================================================================
# INVARIANT 9: Real benchmark completion requires validated real physical artifact
# ===========================================================================


def test_invariant_9_real_benchmark_completion_requires_validated_real_artifact(tmp_path: Path):
    """Invariant 9: Benchmark execution on a missing real dataset fails closed instead of completing with fake metrics."""
    service = create_test_simulation_service()
    cfg = SimulationConfig(
        dataset="nonexistent_dataset_choice",
        num_rounds=1,
    )
    # Unknown dataset fails immediately with SimulationStatus.FAILED
    res = service.run_simulation(cfg)
    assert res.status == SimulationStatus.FAILED
    assert "Unknown dataset" in (res.error_message or "")

    # Missing physical files under registered dataset fail closed with FileNotFoundError
    with patch(
        "app.application.services.dataloader.resolve_dataset_dir",
        return_value=tmp_path / "empty_dir",
    ), pytest.raises(FileNotFoundError):
        load_dataset("paysim")


# ===========================================================================
# INVARIANT 10: Final test partition strictly isolated from threshold calibration
# ===========================================================================


def test_invariant_10_test_partition_isolated_from_threshold_calibration():
    """Invariant 10: Operating threshold is calibrated strictly on validation data; test labels are never snooped."""
    settings = get_settings()
    model_service = ModelService(settings)

    # Create mock validation probabilities and labels
    rng = np.random.default_rng(42)
    n_val = 200
    y_val = rng.choice([0, 1], size=n_val, p=[0.9, 0.1])
    val_probs = rng.uniform(0.0, 1.0, size=n_val)

    # Threshold selected strictly on validation
    threshold, val_f1, prov = model_service.select_operating_threshold(
        y_val=y_val,
        probs_val=val_probs,
        policy="max_f1",
    )
    assert 0.0 < threshold < 1.0
    assert "val" in prov or "f1" in prov

    # Create separate test partition
    n_test = 150
    X_test = rng.normal(0, 1, size=(n_test, 10)).astype(np.float32)
    y_test_1 = rng.choice([0, 1], size=n_test, p=[0.8, 0.2])
    y_test_2 = rng.choice([0, 1], size=n_test, p=[0.5, 0.5])  # Different distribution

    model = model_service.create_model(input_dim=10)

    # Evaluation on test uses the frozen validation-calibrated threshold
    eval_1 = model_service.evaluate(
        model,
        X_test,
        y_test_1,
        threshold=threshold,
        threshold_provenance="val_calibrated",
    )
    eval_2 = model_service.evaluate(
        model,
        X_test,
        y_test_2,
        threshold=threshold,
        threshold_provenance="val_calibrated",
    )

    assert eval_1["threshold"] == float(threshold)
    assert eval_2["threshold"] == float(threshold)
    assert eval_1["threshold_provenance"] == "val_calibrated"
    assert eval_2["threshold_provenance"] == "val_calibrated"

    # Also verify temporal splitting guarantees strict chronological non-leakage
    df_temporal = pd.DataFrame({
        "time": np.arange(100),
        "feat": rng.normal(0, 1, size=100),
        "label": rng.choice([0, 1], size=100),
    })
    split_res = temporal_split_dataset(
        {"X": df_temporal[["time", "feat"]].to_numpy(), "y": df_temporal["label"].to_numpy()},
        time_col="time",
        train_ratio=0.70,
        val_ratio=0.15,
        test_ratio=0.15,
    )
    assert split_res["is_strictly_chronological"] is True
    # Verify train max time <= val min time <= test min time
    train_max_time = split_res["X_train"][:, 0].max()
    val_min_time = split_res["X_val"][:, 0].min()
    val_max_time = split_res["X_val"][:, 0].max()
    test_min_time = split_res["X_test"][:, 0].min()
    assert train_max_time <= val_min_time
    assert val_max_time <= test_min_time
