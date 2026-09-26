"""Feature Service for Data Hygiene, Temporal Leakage Elimination & Split Isolation.

Sub-Plan 3.1 Production Implementation:
1. Temporal Chronological Partitioning:
   - Enforces the arrow of time: t_train_max <= t_val_min <= t_test_min.
   - Eliminates temporal lookahead bias and future-information leakage.
   - Detects timestamp boundary overlaps and logs temporal metrics.
2. Feature & Target Leakage Detection:
   - Detects target proxies (|r| >= threshold or identical binary masks).
   - Detects future-looking outcome signals (e.g. isFlaggedFraud, post-settlement chargeback).
   - Detects raw identifier bleeding (e.g. transaction_id, nameOrig, nameDest).
   - Detects zero-variance / uninformative constant features.
3. Data Hygiene & Quality Audit:
   - Row-level duplicate collision analysis.
   - Missing value and infinite value (inf, -inf) auditing.
   - Class imbalance ratios and quality scoring (0 to 100).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Known time column candidate names in financial AML/fraud benchmarks
CANDIDATE_TIME_COLUMNS: list[str] = [
    "step",            # PaySim (1 hour increments)
    "TransactionDT",   # IEEE-CIS Fraud Detection (seconds from reference)
    "time_step",       # Elliptic Bitcoin Dataset (2-week discrete steps)
    "Time",            # Kaggle Credit Card Fraud (seconds from start)
    "timestamp",
    "datetime",
    "tx_time",
    "created_at",
    "date",
]

# Known future-looking or outcome features that constitute direct leakage
KNOWN_OUTCOME_FEATURE_PATTERNS: list[str] = [
    r"^isflaggedfraud$",
    r"^is_flagged_fraud$",
    r"^flagged_fraud$",
    r"^chargeback.*",
    r"^post_.*",
    r"^settled_.*",
    r"^dispute_status.*",
    r"^fraud_confirmed.*",
    r"^investigation_result.*",
]

# Identifier patterns that leak transaction/customer memorization
IDENTIFIER_PATTERNS: list[str] = [
    r"^nameorig$",
    r"^namedest$",
    r"^tx_?id$",
    r"^transaction_?id$",
    r"^account_?id$",
    r"^customer_?id$",
    r"^merchant_?id$",
    r"^hash$",
    r"^tx_hash$",
    r"^row_?id$",
    r"^index$",
]


class TemporalSplitResult(BaseModel):
    """Result and metadata for a chronological temporal split."""

    time_column: str
    train_size: int
    val_size: int
    test_size: int
    train_time_min: float | str | None = None
    train_time_max: float | str | None = None
    val_time_min: float | str | None = None
    val_time_max: float | str | None = None
    test_time_min: float | str | None = None
    test_time_max: float | str | None = None
    is_strictly_chronological: bool = True
    boundary_overlap: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)


class FeatureLeakageIssue(BaseModel):
    """Specific leakage or data anomaly identified in a column."""

    column: str
    reason: str
    metric_name: str
    metric_value: float
    recommendation: str


class FeatureLeakageReport(BaseModel):
    """Audit report detailing feature leakage, proxy variables, and identifier bleeding."""

    is_clean: bool
    label_column: str
    total_features_scanned: int
    leaked_columns: list[str] = Field(default_factory=list)
    clean_columns: list[str] = Field(default_factory=list)
    issues: list[FeatureLeakageIssue] = Field(default_factory=list)
    summary: str


class DataHygieneReport(BaseModel):
    """Comprehensive hygiene and data quality score report."""

    total_rows: int
    total_columns: int
    quality_score: float  # Scale 0.0 - 100.0
    passed: bool
    duplicate_rows_count: int
    duplicate_rows_ratio: float
    missing_counts: dict[str, int] = Field(default_factory=dict)
    missing_ratios: dict[str, float] = Field(default_factory=dict)
    infinite_counts: dict[str, int] = Field(default_factory=dict)
    constant_columns: list[str] = Field(default_factory=list)
    high_cardinality_columns: list[str] = Field(default_factory=list)
    class_imbalance: dict[str, Any] | None = None
    hygiene_issues: list[str] = Field(default_factory=list)


class FeatureService:
    """Enterprise Feature Hygiene, Temporal Split, and Leakage Elimination Service."""

    @classmethod
    def detect_time_column(cls, df: pd.DataFrame) -> str | None:
        """Auto-detect the canonical time or step column in a financial dataset."""
        df_cols_lower = {col.lower(): col for col in df.columns}
        for candidate in CANDIDATE_TIME_COLUMNS:
            if candidate.lower() in df_cols_lower:
                return df_cols_lower[candidate.lower()]
        return None

    @classmethod
    def temporal_split(
        cls,
        df: pd.DataFrame,
        time_col: str | None = None,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        label_col: str | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, TemporalSplitResult]:
        """Perform a strictly chronological split to eliminate temporal lookahead leakage.

        Guarantees that:
            t_train_max <= t_val_min <= t_test_min
        """
        if len(df) == 0:
            raise ValueError("Cannot perform temporal split on empty DataFrame.")

        ratio_sum = train_ratio + val_ratio + test_ratio
        if not (0.999 <= ratio_sum <= 1.001):
            raise ValueError(
                f"Split ratios must sum to 1.0, got train={train_ratio}, val={val_ratio}, test={test_ratio} (sum={ratio_sum})"
            )

        if time_col is None:
            detected = cls.detect_time_column(df)
            if detected is None:
                raise ValueError(
                    f"No time column provided and none found among candidates: {CANDIDATE_TIME_COLUMNS}"
                )
            time_col = detected

        if time_col not in df.columns:
            raise ValueError(f"Specified time column '{time_col}' does not exist in DataFrame.")

        # Sort strictly ascending by time column
        sorted_df = df.sort_values(by=time_col, ascending=True).reset_index(drop=True)
        n_total = len(sorted_df)

        n_train = int(n_total * train_ratio)
        n_val = int(n_total * val_ratio)
        if n_train == 0 or (val_ratio > 0 and n_val == 0):
            raise ValueError(
                f"Dataset size ({n_total}) too small for specified split ratios (n_train={n_train}, n_val={n_val})"
            )

        train_df = sorted_df.iloc[:n_train].copy().reset_index(drop=True)
        val_df = sorted_df.iloc[n_train : n_train + n_val].copy().reset_index(drop=True)
        test_df = sorted_df.iloc[n_train + n_val :].copy().reset_index(drop=True)

        # Extract boundary timestamps
        train_t_min = train_df[time_col].iloc[0] if len(train_df) > 0 else None
        train_t_max = train_df[time_col].iloc[-1] if len(train_df) > 0 else None
        val_t_min = val_df[time_col].iloc[0] if len(val_df) > 0 else None
        val_t_max = val_df[time_col].iloc[-1] if len(val_df) > 0 else None
        test_t_min = test_df[time_col].iloc[0] if len(test_df) > 0 else None
        test_t_max = test_df[time_col].iloc[-1] if len(test_df) > 0 else None

        # Check for boundary overlaps (identical timestamps on boundary cuts)
        train_val_overlap = bool(train_t_max is not None and val_t_min is not None and train_t_max == val_t_min)
        val_test_overlap = bool(val_t_max is not None and test_t_min is not None and val_t_max == test_t_min)
        boundary_overlap = train_val_overlap or val_test_overlap

        # Verify chronological monotonic property
        is_strictly_chronological = True
        if train_t_max is not None and val_t_min is not None and train_t_max > val_t_min:
            is_strictly_chronological = False
        if val_t_max is not None and test_t_min is not None and val_t_max > test_t_min:
            is_strictly_chronological = False

        metrics: dict[str, Any] = {
            "total_samples": n_total,
            "train_samples": len(train_df),
            "val_samples": len(val_df),
            "test_samples": len(test_df),
            "train_ratio_actual": len(train_df) / n_total,
            "val_ratio_actual": len(val_df) / n_total,
            "test_ratio_actual": len(test_df) / n_total,
        }

        # Calculate fraud distribution across splits if label column provided
        if label_col and label_col in sorted_df.columns:
            for split_name, split_data in [("train", train_df), ("val", val_df), ("test", test_df)]:
                if len(split_data) > 0:
                    y_split = pd.to_numeric(split_data[label_col], errors="coerce").fillna(0).astype(int)
                    n_fraud = int((y_split == 1).sum())
                    ratio = float(n_fraud / len(split_data))
                    metrics[f"{split_name}_fraud_count"] = n_fraud
                    metrics[f"{split_name}_fraud_ratio"] = ratio

        result = TemporalSplitResult(
            time_column=time_col,
            train_size=len(train_df),
            val_size=len(val_df),
            test_size=len(test_df),
            train_time_min=float(train_t_min) if isinstance(train_t_min, (int, float, np.number)) else str(train_t_min),
            train_time_max=float(train_t_max) if isinstance(train_t_max, (int, float, np.number)) else str(train_t_max),
            val_time_min=float(val_t_min) if isinstance(val_t_min, (int, float, np.number)) else str(val_t_min),
            val_time_max=float(val_t_max) if isinstance(val_t_max, (int, float, np.number)) else str(val_t_max),
            test_time_min=float(test_t_min) if isinstance(test_t_min, (int, float, np.number)) else str(test_t_min),
            test_time_max=float(test_t_max) if isinstance(test_t_max, (int, float, np.number)) else str(test_t_max),
            is_strictly_chronological=is_strictly_chronological,
            boundary_overlap=boundary_overlap,
            metrics=metrics,
        )

        logger.info(
            "[TemporalSplit] Split completed: train=[%s, %s] (N=%d), val=[%s, %s] (N=%d), test=[%s, %s] (N=%d), monotonic=%s",
            result.train_time_min,
            result.train_time_max,
            result.train_size,
            result.val_time_min,
            result.val_time_max,
            result.val_size,
            result.test_time_min,
            result.test_time_max,
            result.test_size,
            result.is_strictly_chronological,
        )

        return train_df, val_df, test_df, result

    @classmethod
    def detect_feature_leakage(
        cls,
        df: pd.DataFrame,
        label_col: str,
        correlation_threshold: float = 0.98,
        id_uniqueness_threshold: float = 0.95,
        exclude_cols: Sequence[str] | None = None,
    ) -> FeatureLeakageReport:
        """Detect target proxy variables, outcome features, ID bleeding, and zero-variance columns."""
        if label_col not in df.columns:
            raise ValueError(f"Label column '{label_col}' not found in DataFrame.")

        exclude = set(exclude_cols or [])
        exclude.add(label_col)

        candidate_cols = [c for c in df.columns if c not in exclude]
        issues: list[FeatureLeakageIssue] = []
        leaked_cols_set: set[str] = set()

        y_series = pd.to_numeric(df[label_col], errors="coerce").fillna(0).to_numpy()
        y_std = float(np.std(y_series))

        for col in candidate_cols:
            col_series = df[col]
            col_lower = col.lower()

            # 1. Known future-looking or outcome features
            for pattern in KNOWN_OUTCOME_FEATURE_PATTERNS:
                if re.search(pattern, col_lower):
                    issues.append(
                        FeatureLeakageIssue(
                            column=col,
                            reason="KNOWN_OUTCOME_FEATURE",
                            metric_name="pattern_match",
                            metric_value=1.0,
                            recommendation=f"Exclude '{col}'; post-event outcome flag causes severe training leakage.",
                        )
                    )
                    leaked_cols_set.add(col)
                    break

            # 2. Identifier bleeding check (IDs memorization)
            for pattern in IDENTIFIER_PATTERNS:
                if re.search(pattern, col_lower):
                    unique_ratio = float(col_series.nunique(dropna=True) / max(1, len(df)))
                    if unique_ratio >= id_uniqueness_threshold or unique_ratio > 0.5:
                        issues.append(
                            FeatureLeakageIssue(
                                column=col,
                                reason="UNIQUE_IDENTIFIER_BLEEDING",
                                metric_name="uniqueness_ratio",
                                metric_value=round(unique_ratio, 4),
                                recommendation=f"Exclude '{col}'; high-cardinality entity ID leads to memorization.",
                            )
                        )
                        leaked_cols_set.add(col)
                        break

            # 3. Numeric correlation check (Target proxy detection)
            if pd.api.types.is_numeric_dtype(col_series):
                clean_num = pd.to_numeric(col_series, errors="coerce").fillna(0).to_numpy()
                col_std = float(np.std(clean_num))

                # Zero variance check
                if col_std < 1e-9:
                    issues.append(
                        FeatureLeakageIssue(
                            column=col,
                            reason="ZERO_VARIANCE_CONSTANT",
                            metric_name="standard_deviation",
                            metric_value=col_std,
                            recommendation=f"Exclude '{col}'; feature is constant across all records.",
                        )
                    )
                    leaked_cols_set.add(col)
                    continue

                # Compute Pearson correlation with target
                if y_std > 1e-9:
                    corr = float(np.corrcoef(clean_num, y_series)[0, 1])
                    abs_corr = abs(corr) if not np.isnan(corr) else 0.0

                    if abs_corr >= correlation_threshold:
                        issues.append(
                            FeatureLeakageIssue(
                                column=col,
                                reason="TARGET_PROXY_HIGH_CORRELATION",
                                metric_name="pearson_correlation",
                                metric_value=round(abs_corr, 4),
                                recommendation=f"Exclude '{col}'; near-perfect correlation (|r|={abs_corr:.3f}) with label indicates target leakage.",
                            )
                        )
                        leaked_cols_set.add(col)

        leaked_columns = sorted(list(leaked_cols_set))
        clean_columns = [c for c in candidate_cols if c not in leaked_cols_set]
        is_clean = len(leaked_columns) == 0

        summary = (
            f"Leakage audit passed: 0 of {len(candidate_cols)} features flagged."
            if is_clean
            else f"Leakage audit detected {len(leaked_columns)} compromised features among {len(candidate_cols)} columns."
        )

        return FeatureLeakageReport(
            is_clean=is_clean,
            label_column=label_col,
            total_features_scanned=len(candidate_cols),
            leaked_columns=leaked_columns,
            clean_columns=clean_columns,
            issues=issues,
            summary=summary,
        )

    @classmethod
    def audit_data_hygiene(
        cls,
        df: pd.DataFrame,
        label_col: str | None = None,
        id_col: str | None = None,
        missing_threshold: float = 0.30,
    ) -> DataHygieneReport:
        """Perform a rigorous hygiene audit evaluating missingness, infs, duplicates, and balance."""
        n_rows = len(df)
        n_cols = len(df.columns)
        hygiene_issues: list[str] = []

        if n_rows == 0:
            return DataHygieneReport(
                total_rows=0,
                total_columns=n_cols,
                quality_score=0.0,
                passed=False,
                duplicate_rows_count=0,
                duplicate_rows_ratio=0.0,
                hygiene_issues=["DataFrame is empty."],
            )

        # 1. Duplicate row collisions
        duplicate_count = int(df.duplicated().sum())
        duplicate_ratio = float(duplicate_count / n_rows)
        if duplicate_count > 0:
            hygiene_issues.append(f"Detected {duplicate_count} duplicate rows ({duplicate_ratio:.2%}).")

        # 2. Missing values per column
        missing_counts: dict[str, int] = {}
        missing_ratios: dict[str, float] = {}
        for col in df.columns:
            n_missing = int(df[col].isna().sum())
            if n_missing > 0:
                missing_counts[col] = n_missing
                ratio = float(n_missing / n_rows)
                missing_ratios[col] = round(ratio, 4)
                if ratio >= missing_threshold:
                    hygiene_issues.append(f"Column '{col}' has severe missingness: {ratio:.1%}.")

        # 3. Infinite values check (inf, -inf)
        infinite_counts: dict[str, int] = {}
        for col in df.select_dtypes(include=[np.number]).columns:
            n_inf = int(np.isinf(df[col].to_numpy()).sum())
            if n_inf > 0:
                infinite_counts[col] = n_inf
                hygiene_issues.append(f"Column '{col}' contains {n_inf} infinite values.")

        # 4. Constant columns
        constant_columns: list[str] = []
        for col in df.columns:
            if df[col].nunique(dropna=False) <= 1:
                constant_columns.append(col)
                hygiene_issues.append(f"Column '{col}' is constant (zero information entropy).")

        # 5. High-cardinality categorical columns
        high_cardinality_cols: list[str] = []
        for col in df.select_dtypes(include=["object", "string", "category"]).columns:
            if col != id_col:
                n_unique = df[col].nunique(dropna=True)
                if n_unique > 500 and (n_unique / n_rows) > 0.5:
                    high_cardinality_cols.append(col)
                    hygiene_issues.append(f"Categorical column '{col}' has high cardinality ({n_unique} unique values).")

        # 6. Class imbalance audit (if label column is specified)
        class_imbalance: dict[str, Any] | None = None
        if label_col and label_col in df.columns:
            y_arr = pd.to_numeric(df[label_col], errors="coerce").fillna(0).astype(int)
            fraud_count = int((y_arr == 1).sum())
            legit_count = int((y_arr == 0).sum())
            fraud_ratio = float(fraud_count / n_rows) if n_rows > 0 else 0.0
            imbalance_ratio = float(legit_count / max(1, fraud_count))

            class_imbalance = {
                "label_column": label_col,
                "fraud_count": fraud_count,
                "legit_count": legit_count,
                "fraud_ratio": fraud_ratio,
                "imbalance_ratio": round(imbalance_ratio, 2),
            }
            if fraud_ratio < 0.0001:
                hygiene_issues.append(f"Extreme class imbalance: fraud ratio is {fraud_ratio:.5f} ({imbalance_ratio:.0f}:1).")

        # 7. Holistic Data Quality Scoring (0.0 to 100.0)
        quality_score = 100.0
        # Duplicate penalty: up to 20 points
        quality_score -= min(20.0, duplicate_ratio * 100.0)
        # Infinite values penalty: 15 points per infected column (max 30)
        quality_score -= min(30.0, len(infinite_counts) * 15.0)
        # Missing values penalty: proportional
        avg_missing = float(np.mean(list(missing_ratios.values()))) if missing_ratios else 0.0
        quality_score -= min(25.0, avg_missing * 50.0)
        # Constant columns penalty: 5 points each
        quality_score -= min(15.0, len(constant_columns) * 5.0)

        quality_score = round(max(0.0, min(100.0, quality_score)), 2)
        passed = quality_score >= 70.0 and len(infinite_counts) == 0

        return DataHygieneReport(
            total_rows=n_rows,
            total_columns=n_cols,
            quality_score=quality_score,
            passed=passed,
            duplicate_rows_count=duplicate_count,
            duplicate_rows_ratio=round(duplicate_ratio, 4),
            missing_counts=missing_counts,
            missing_ratios=missing_ratios,
            infinite_counts=infinite_counts,
            constant_columns=constant_columns,
            high_cardinality_columns=high_cardinality_cols,
            class_imbalance=class_imbalance,
            hygiene_issues=hygiene_issues,
        )

    @classmethod
    def clean_and_prepare(
        cls,
        df: pd.DataFrame,
        label_col: str = "isFraud",
        time_col: str | None = None,
        drop_duplicates: bool = True,
        drop_leaked: bool = True,
        correlation_threshold: float = 0.98,
    ) -> tuple[pd.DataFrame, FeatureLeakageReport, DataHygieneReport]:
        """Execute end-to-end data hygiene and feature leakage sanitization."""
        working_df = df.copy()

        # Step 1: Leakage detection and pruning
        leakage_report = cls.detect_feature_leakage(
            working_df,
            label_col=label_col,
            correlation_threshold=correlation_threshold,
        )

        if drop_leaked and leakage_report.leaked_columns:
            logger.info(
                "[FeatureService] Removing %d leaked columns: %s",
                len(leakage_report.leaked_columns),
                leakage_report.leaked_columns,
            )
            working_df = working_df.drop(columns=leakage_report.leaked_columns)

        # Step 2: Deduplication on sanitized feature space
        if drop_duplicates:
            initial_count = len(working_df)
            working_df = working_df.drop_duplicates().reset_index(drop=True)
            n_dropped = initial_count - len(working_df)
            if n_dropped > 0:
                logger.info("[FeatureService] Dropped %d duplicate rows.", n_dropped)

        # Step 3: Hygiene Audit on the prepared dataframe
        hygiene_report = cls.audit_data_hygiene(
            working_df,
            label_col=label_col if label_col in working_df.columns else None,
        )

        return working_df, leakage_report, hygiene_report
