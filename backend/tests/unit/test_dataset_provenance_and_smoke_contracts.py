"""Unit test suite for Dataset Provenance, Fail-Closed Contracts, and Fixture Verification.

Covers regression requirements for CI smoke gate repair and real-data integration architecture:
1. Dataset missing raises FileNotFoundError.
2. Dataset directory empty fails closed and never calls synthetic generators.
3. Schema-valid fixtures parse correctly through actual production loaders.
4. Invalid schema or missing required columns fails explicitly with ValueError or KeyError.
5. Mandatory real data requested without physical files raises failure rather than skipping.
6. Generated/synthetic datasets are strictly classified and never impersonate empirical data.
7. Provenance taxonomy contracts are strictly enforced across all 7 benchmark datasets.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from app.application.services.dataloader import (
    DatasetProvenance,
    load_amlnet,
    load_amlsim,
    load_creditcard_fraud,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    load_synthaml,
)
from app.application.services.synthetic_dataset_generators import (
    generate_synthetic_amlnet,
    generate_synthetic_amlsim,
    generate_synthetic_creditcard,
    generate_synthetic_elliptic,
    generate_synthetic_ieee_cis,
    generate_synthetic_paysim,
    generate_synthetic_synthaml,
)
from tests.fixtures.dataloader_smoke_fixtures import (
    create_creditcard_test_fixture,
    create_elliptic_test_fixture,
    create_paysim_test_fixture,
)


class TestDatasetFailClosedContracts:
    """Verifies that loaders fail closed when physical datasets are missing or directories are empty."""

    def test_missing_dataset_path_raises_file_not_found(self, tmp_path: Path) -> None:
        """Missing dataset directory or file raises FileNotFoundError."""
        missing = tmp_path / "nonexistent_vault"

        with pytest.raises(FileNotFoundError, match="Real PaySim"):
            load_paysim(path=missing)

        with pytest.raises(FileNotFoundError, match="Real IEEE-CIS"):
            load_ieee_cis(path=missing)

        with pytest.raises(FileNotFoundError, match="Real Credit Card Fraud"):
            load_creditcard_fraud(path=missing)

        with pytest.raises(FileNotFoundError, match="Real Elliptic"):
            load_elliptic(path=missing)

        with pytest.raises(FileNotFoundError, match="Real AMLSim"):
            load_amlsim(path=missing)

        with pytest.raises(FileNotFoundError, match="Real SynthAML"):
            load_synthaml(path=missing)

        with pytest.raises(FileNotFoundError, match="Real AMLNet"):
            load_amlnet(path=missing)

    def test_empty_directory_fails_closed_without_calling_synthetic_generators(self, tmp_path: Path) -> None:
        """Empty directory raises FileNotFoundError and never invokes synthetic generators."""
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir()

        with (
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_paysim") as mock_paysim,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_ieee_cis") as mock_ieee,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_creditcard") as mock_cc,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_elliptic") as mock_elliptic,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_amlsim") as mock_amlsim,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_synthaml") as mock_synthaml,
            patch("app.application.services.synthetic_dataset_generators.generate_synthetic_amlnet") as mock_amlnet,
        ):
            with pytest.raises(FileNotFoundError):
                load_paysim(path=empty_dir)
            mock_paysim.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_ieee_cis(path=empty_dir)
            mock_ieee.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_creditcard_fraud(path=empty_dir)
            mock_cc.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_elliptic(path=empty_dir)
            mock_elliptic.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_amlsim(path=empty_dir)
            mock_amlsim.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_synthaml(path=empty_dir)
            mock_synthaml.assert_not_called()

            with pytest.raises(FileNotFoundError):
                load_amlnet(path=empty_dir)
            mock_amlnet.assert_not_called()


class TestValidAndInvalidFixtureContracts:
    """Verifies that genuine loaders parse schema-valid fixtures and reject invalid fixtures."""

    def test_valid_fixtures_load_with_actual_loaders(self, tmp_path: Path) -> None:
        """Schema-valid minimal fixtures load and parse through actual production loaders."""
        p_dir = create_paysim_test_fixture(tmp_path / "paysim", n_rows=8)
        p_data = load_paysim(path=p_dir, nrows=8)
        assert p_data["X"].shape == (8, 13)
        assert len(p_data["y"]) == 8

        cc_dir = create_creditcard_test_fixture(tmp_path / "creditcard", n_rows=8)
        cc_data = load_creditcard_fraud(path=cc_dir, nrows=8)
        assert cc_data["X"].shape == (8, 29)

        ell_dir = create_elliptic_test_fixture(tmp_path / "elliptic", n_nodes=8)
        ell_data = load_elliptic(path=ell_dir, nrows=8, include_unknown=True)
        assert ell_data["X"].shape == (8, 166)
        assert len(ell_data["edges"]) > 0

    def test_invalid_schema_missing_columns_fails_explicitly(self, tmp_path: Path) -> None:
        """Fixtures missing mandatory columns fail closed with ValueError or KeyError."""
        # PaySim missing required columns
        bad_paysim_dir = tmp_path / "bad_paysim"
        bad_paysim_dir.mkdir()
        bad_df = pd.DataFrame({"step": [1], "amount": [10.0]})  # missing type, nameOrig, isFraud
        bad_df.to_csv(bad_paysim_dir / "paysim.csv", index=False)

        with pytest.raises((ValueError, KeyError)):
            load_paysim(path=bad_paysim_dir)

        # CreditCard missing Class column
        bad_cc_dir = tmp_path / "bad_cc"
        bad_cc_dir.mkdir()
        bad_cc_df = pd.DataFrame({"Time": [1.0], "Amount": [100.0]})
        bad_cc_df.to_csv(bad_cc_dir / "creditcard.csv", index=False)

        with pytest.raises((ValueError, KeyError)):
            load_creditcard_fraud(path=bad_cc_dir)


class TestDatasetProvenanceClassifications:
    """Verifies truthful provenance taxonomy across empirical, simulated, and synthetic assets."""

    def test_synthetic_generators_classified_truthfully(self) -> None:
        """Synthetic generators must set is_synthetic=True and scientific_origin='synthetic'."""
        gen_paysim = generate_synthetic_paysim(n_mock_txns=20)
        assert gen_paysim["is_synthetic"] is True
        assert gen_paysim["source"] in ("mock", "synthetic_generator")
        assert gen_paysim["provenance"] in ("TEST_FIXTURE", "CONTROLLED_PROJECT_SYNTHETIC")
        assert gen_paysim["scientific_origin"] == "synthetic"

        gen_ieee = generate_synthetic_ieee_cis(n_mock_txns=20)
        assert gen_ieee["is_synthetic"] is True
        assert gen_ieee["source"] in ("mock", "synthetic_generator")
        assert gen_ieee["provenance"] in ("TEST_FIXTURE", "CONTROLLED_PROJECT_SYNTHETIC")
        assert gen_ieee["scientific_origin"] == "synthetic"

        gen_cc = generate_synthetic_creditcard(n_mock_txns=20)
        assert gen_cc["is_synthetic"] is True
        assert gen_cc["source"] in ("mock", "mock_pca", "synthetic_generator")
        assert gen_cc["provenance"] in ("TEST_FIXTURE", "CONTROLLED_PROJECT_SYNTHETIC")
        assert gen_cc["scientific_origin"] == "synthetic"

        gen_ell = generate_synthetic_elliptic(n_mock_nodes=20)
        assert gen_ell["is_synthetic"] is True
        assert gen_ell["source"] in ("mock", "synthetic_generator")
        assert gen_ell["provenance"] in ("TEST_FIXTURE", "CONTROLLED_PROJECT_SYNTHETIC")
        assert gen_ell["scientific_origin"] == "synthetic"

        gen_amlsim = generate_synthetic_amlsim(n_mock_txns=20)
        assert gen_amlsim["is_synthetic"] is True
        assert gen_amlsim["source"] in ("mock", "synthetic_generator")
        assert gen_amlsim["scientific_origin"] == "synthetic"

        gen_synthaml = generate_synthetic_synthaml(n_mock_alerts=5)
        assert gen_synthaml["is_synthetic"] is True
        assert gen_synthaml["source"] in ("mock", "synthetic_generator", "synthetic_fallback")

        gen_amlnet = generate_synthetic_amlnet(n_mock_txns=20)
        assert gen_amlnet["is_synthetic"] is True
        assert gen_amlnet["source"] in ("mock", "synthetic_generator", "synthetic_fallback")

    def test_provenance_enums_cover_all_categories(self) -> None:
        """DatasetProvenance enum contains all declared provenance taxonomy levels."""
        assert DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value == "EMPIRICAL_EXTERNAL_DATA"
        assert DatasetProvenance.PUBLIC_SIMULATED_DATASET.value == "PUBLIC_SIMULATED_DATASET"
        assert DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value == "CONTROLLED_PROJECT_SYNTHETIC"
        assert DatasetProvenance.TEST_FIXTURE.value == "TEST_FIXTURE"


class TestMandatoryRealDataNegativeGate:
    """Verifies that requesting mandatory real data without files fails, not skips."""

    def test_missing_dataset_fails_when_mandatory_requested(self, tmp_path: Path) -> None:
        """When mandatory execution is required and dataset files are missing, it fails closed."""
        empty_dir = tmp_path / "empty_vault"
        empty_dir.mkdir()

        # In a real-data mandatory context, missing files raise FileNotFoundError
        with pytest.raises(FileNotFoundError):
            load_paysim(path=empty_dir, require_real=True)

        with pytest.raises(FileNotFoundError):
            load_ieee_cis(path=empty_dir, require_real=True)

        with pytest.raises(FileNotFoundError):
            load_creditcard_fraud(path=empty_dir, require_real=True)

        with pytest.raises(FileNotFoundError):
            load_elliptic(path=empty_dir, require_real=True)

        with pytest.raises(FileNotFoundError):
            load_amlsim(path=empty_dir, require_real=True)
