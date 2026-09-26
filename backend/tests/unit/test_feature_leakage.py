"""Unit tests for Feature & Target Leakage Detection and Data Hygiene Auditing.

Verifies:
- Detection of target proxies (|r| >= threshold).
- Detection of future-looking outcome features (isFlaggedFraud, chargeback, etc.).
- Detection of unique entity identifiers that lead to memorization.
- Detection of zero-variance constant features.
- Full dataset cleaning and hygiene verification pipeline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.application.services.feature_service import FeatureService


class TestFeatureLeakageDetection:
    """Test suite ensuring target proxies, outcome features, and ID bleeding are caught."""

    def test_target_proxy_high_correlation_detected(self) -> None:
        """Features with near-perfect correlation with target must be flagged."""
        y = np.array([0, 0, 0, 0, 1, 1, 1, 1, 0, 1])
        df = pd.DataFrame({
            "isFraud": y,
            "normal_feat": [1.2, 3.4, 2.1, 0.5, 4.3, 5.1, 3.9, 4.8, 1.1, 4.5],
            "leaked_target_copy": y.astype(float),  # r = 1.0
            "leaked_noisy_proxy": y * 100.0 + np.array([0.01] * 10),  # r ~ 1.0
        })

        report = FeatureService.detect_feature_leakage(df, label_col="isFraud", correlation_threshold=0.98)

        assert report.is_clean is False
        assert "leaked_target_copy" in report.leaked_columns
        assert "leaked_noisy_proxy" in report.leaked_columns
        assert "normal_feat" in report.clean_columns

    def test_known_outcome_feature_detected(self) -> None:
        """Features matching known post-event outcome patterns must be flagged."""
        df = pd.DataFrame({
            "isFraud": [0, 1, 0, 1],
            "step": [1, 2, 3, 4],
            "isFlaggedFraud": [0, 1, 0, 0],
            "chargeback_amount": [0.0, 100.0, 0.0, 250.0],
            "post_transaction_status": ["OK", "FLAGGED", "OK", "REVIEW"],
        })

        report = FeatureService.detect_feature_leakage(df, label_col="isFraud")

        assert "isflaggedfraud" in [c.lower() for c in report.leaked_columns]
        assert "chargeback_amount" in report.leaked_columns
        assert "post_transaction_status" in report.leaked_columns

    def test_unique_identifier_bleeding_detected(self) -> None:
        """High-cardinality entity ID columns must be flagged as memorization leakage."""
        n_rows = 50
        df = pd.DataFrame({
            "isFraud": [0] * 45 + [1] * 5,
            "amount": np.linspace(10, 500, n_rows),
            "nameOrig": [f"C{1000 + i}" for i in range(n_rows)],  # 100% unique IDs
            "nameDest": [f"M{2000 + i}" for i in range(n_rows)],  # 100% unique IDs
            "tx_id": [f"TX_{i}" for i in range(n_rows)],
        })

        report = FeatureService.detect_feature_leakage(df, label_col="isFraud")

        assert "nameOrig" in report.leaked_columns
        assert "nameDest" in report.leaked_columns
        assert "tx_id" in report.leaked_columns

    def test_zero_variance_constant_detected(self) -> None:
        """Columns with zero standard deviation must be flagged."""
        df = pd.DataFrame({
            "isFraud": [0, 1, 0, 0],
            "valid_feat": [10.0, 20.0, 30.0, 40.0],
            "constant_zero": [0.0, 0.0, 0.0, 0.0],
            "constant_pi": [3.14159, 3.14159, 3.14159, 3.14159],
        })

        report = FeatureService.detect_feature_leakage(df, label_col="isFraud")

        assert "constant_zero" in report.leaked_columns
        assert "constant_pi" in report.leaked_columns

    def test_clean_and_prepare_sanitizes_dataset(self) -> None:
        """clean_and_prepare removes duplicate rows and drops leaked columns."""
        df = pd.DataFrame({
            "isFraud": [0, 1, 0, 1, 0],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
            "nameOrig": ["C1", "C2", "C3", "C4", "C5"],
            "isFlaggedFraud": [0, 1, 0, 0, 0],
        })
        # Add exact duplicate row (duplicate of row 0)
        df_with_dupe = pd.concat([df, df.iloc[[0]]], ignore_index=True)

        clean_df, leakage_rep, hygiene_rep = FeatureService.clean_and_prepare(
            df_with_dupe,
            label_col="isFraud",
            drop_duplicates=True,
            drop_leaked=True,
        )

        assert len(clean_df) == 5  # Duplicate removed (6 -> 5)
        assert "nameOrig" not in clean_df.columns
        assert "isFlaggedFraud" not in clean_df.columns
        assert "amount" in clean_df.columns
        assert "isFraud" in clean_df.columns
        assert hygiene_rep.duplicate_rows_count == 0  # Cleaned df has 0 duplicates


class TestDataHygieneAudit:
    """Test suite ensuring data quality auditing and scoring function accurately."""

    def test_hygiene_audit_detects_infinite_values(self) -> None:
        """Infinite values must be flagged and fail the hygiene check."""
        df = pd.DataFrame({
            "feat1": [1.0, 2.0, np.inf, 4.0],
            "feat2": [10.0, -np.inf, 30.0, 40.0],
            "isFraud": [0, 1, 0, 0],
        })

        report = FeatureService.audit_data_hygiene(df, label_col="isFraud")

        assert report.passed is False
        assert report.infinite_counts["feat1"] == 1
        assert report.infinite_counts["feat2"] == 1
        assert any("infinite values" in issue for issue in report.hygiene_issues)

    def test_hygiene_audit_detects_missingness(self) -> None:
        """Missing values and percentages are computed per column."""
        df = pd.DataFrame({
            "feat1": [1.0, np.nan, np.nan, 4.0],  # 50% missing
            "feat2": [10.0, 20.0, 30.0, 40.0],
            "isFraud": [0, 0, 1, 0],
        })

        report = FeatureService.audit_data_hygiene(df, label_col="isFraud")

        assert report.missing_counts["feat1"] == 2
        assert report.missing_ratios["feat1"] == pytest.approx(0.5, abs=0.01)
        assert "feat2" not in report.missing_counts

    def test_hygiene_audit_quality_score_bounds(self) -> None:
        """Quality score must be bounded between 0.0 and 100.0."""
        # Perfectly clean dataset
        clean_df = pd.DataFrame({
            "feat1": [1.0, 2.0, 3.0, 4.0, 5.0],
            "feat2": [10.0, 20.0, 30.0, 40.0, 50.0],
            "isFraud": [0, 0, 1, 0, 0],
        })
        clean_report = FeatureService.audit_data_hygiene(clean_df, label_col="isFraud")
        assert clean_report.quality_score == 100.0
        assert clean_report.passed is True

        # Heavily degraded dataset
        degraded_df = pd.DataFrame({
            "feat1": [np.inf, -np.inf, np.nan, np.nan, 1.0],
            "feat2": [0.0, 0.0, 0.0, 0.0, 0.0],  # constant
            "isFraud": [0, 0, 0, 0, 0],
        })
        degraded_report = FeatureService.audit_data_hygiene(degraded_df, label_col="isFraud")
        assert degraded_report.quality_score < 70.0
        assert degraded_report.passed is False
