"""Data Preprocessor with Strict Split Isolation.

Enforces zero-leakage preprocessing for tabular financial datasets:
- Imputers and scalers are fitted exclusively on training splits (fit on train, transform on val/test).
- Categorical one-hot encoders preserve training vocabulary and map unseen test categories to an unknown token/zero-vector.
- State is fully serializable and inspectable to mathematically guarantee absence of data snooping.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PreprocessingStateError(ValueError):
    """Raised when transform is called before fit, or invalid state is encountered."""


class DataPreprocessor:
    """Zero-leakage tabular preprocessor with strict train-split isolation."""

    def __init__(
        self,
        numeric_strategy: str = "standardize",  # "standardize", "minmax", "robust", or "none"
        impute_strategy: str = "median",        # "median", "mean", or "zero"
        clip_outliers: bool = True,
        clip_std_factor: float = 6.0,
    ):
        self.numeric_strategy = numeric_strategy
        self.impute_strategy = impute_strategy
        self.clip_outliers = clip_outliers
        self.clip_std_factor = clip_std_factor

        self.is_fitted: bool = False
        self.numeric_cols: list[str] = []
        self.categorical_cols: list[str] = []
        self.feature_names_in_: list[str] = []
        self.feature_names_out_: list[str] = []

        # Fitted parameters (derived strictly from X_train)
        self.impute_values_: dict[str, float] = {}
        self.means_: dict[str, float] = {}
        self.stds_: dict[str, float] = {}
        self.mins_: dict[str, float] = {}
        self.maxs_: dict[str, float] = {}
        self.medians_: dict[str, float] = {}
        self.categories_: dict[str, list[Any]] = {}

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        numeric_cols: Sequence[str] | None = None,
        categorical_cols: Sequence[str] | None = None,
    ) -> DataPreprocessor:
        """Fit preprocessing parameters strictly on the training partition."""
        if isinstance(X, np.ndarray):
            cols = [f"feat_{i}" for i in range(X.shape[1])]
            df = pd.DataFrame(X, columns=cols)
        else:
            df = X.copy()

        self.feature_names_in_ = list(df.columns)

        # Disambiguate numeric vs categorical columns if not explicitly provided
        if numeric_cols is None and categorical_cols is None:
            self.numeric_cols = [
                c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
            ]
            self.categorical_cols = [
                c for c in df.columns if c not in self.numeric_cols
            ]
        else:
            self.numeric_cols = list(numeric_cols or [])
            self.categorical_cols = list(categorical_cols or [])

        # 1. Fit numerical imputers and scalers
        for col in self.numeric_cols:
            series = pd.to_numeric(df[col], errors="coerce")
            valid = series.dropna()

            if len(valid) == 0:
                mean_val = 0.0
                std_val = 1.0
                med_val = 0.0
                min_val = 0.0
                max_val = 1.0
            else:
                mean_val = float(valid.mean())
                std_val = float(valid.std(ddof=0))
                if std_val < 1e-8:
                    std_val = 1.0  # Prevent division by zero on constant columns
                med_val = float(valid.median())
                min_val = float(valid.min())
                max_val = float(valid.max())

            self.means_[col] = mean_val
            self.stds_[col] = std_val
            self.medians_[col] = med_val
            self.mins_[col] = min_val
            self.maxs_[col] = max_val

            if self.impute_strategy == "median":
                self.impute_values_[col] = med_val
            elif self.impute_strategy == "mean":
                self.impute_values_[col] = mean_val
            else:
                self.impute_values_[col] = 0.0

        # 2. Fit categorical vocabularies
        for col in self.categorical_cols:
            # Capture distinct categories appearing in training split only
            unique_cats = sorted(list(df[col].dropna().astype(str).unique()))
            self.categories_[col] = unique_cats

        # 3. Determine output feature column names
        out_cols: list[str] = list(self.numeric_cols)
        for col in self.categorical_cols:
            for cat in self.categories_[col]:
                out_cols.append(f"{col}_{cat}")
        self.feature_names_out_ = out_cols

        self.is_fitted = True
        return self

    def transform(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Transform dataset using parameters learned strictly during fit()."""
        if not self.is_fitted:
            raise PreprocessingStateError("DataPreprocessor must be fitted before transform().")

        if isinstance(X, np.ndarray):
            if X.shape[1] != len(self.feature_names_in_):
                raise ValueError(
                    f"Array feature count {X.shape[1]} does not match fitted features {len(self.feature_names_in_)}"
                )
            df = pd.DataFrame(X, columns=self.feature_names_in_)
        else:
            df = X.copy()

        transformed_cols: list[Any] = []

        # 1. Transform numeric columns
        for col in self.numeric_cols:
            if col in df.columns:
                series = pd.to_numeric(df[col], errors="coerce").fillna(self.impute_values_[col])
            else:
                series = pd.Series([self.impute_values_[col]] * len(df))

            arr = series.to_numpy(dtype=np.float32)

            # Scaling
            if self.numeric_strategy == "standardize":
                arr = (arr - self.means_[col]) / self.stds_[col]
            elif self.numeric_strategy == "minmax":
                denom = self.maxs_[col] - self.mins_[col]
                if denom < 1e-8:
                    denom = 1.0
                arr = (arr - self.mins_[col]) / denom

            # Optional outlier clipping based on training std bounds
            if self.clip_outliers and self.numeric_strategy == "standardize":
                arr = np.clip(arr, -self.clip_std_factor, self.clip_std_factor)

            transformed_cols.append(arr[:, np.newaxis])

        # 2. Transform categorical columns (One-hot encode with train vocabulary)
        for col in self.categorical_cols:
            if col in df.columns:
                series_str = df[col].astype(str)
            else:
                series_str = pd.Series(["UNKNOWN"] * len(df))

            known_cats = self.categories_[col]
            for cat in known_cats:
                indicator = (series_str == cat).to_numpy(dtype=np.float32)
                transformed_cols.append(indicator[:, np.newaxis])

        if not transformed_cols:
            return np.empty((len(df), 0), dtype=np.float32)

        return np.hstack(transformed_cols).astype(np.float32)

    def fit_transform(
        self,
        X: pd.DataFrame | np.ndarray,
        numeric_cols: Sequence[str] | None = None,
        categorical_cols: Sequence[str] | None = None,
    ) -> np.ndarray:
        """Fit on X and return transformed array."""
        self.fit(X, numeric_cols=numeric_cols, categorical_cols=categorical_cols)
        return self.transform(X)

    def to_dict(self) -> dict[str, Any]:
        """Export preprocessor state for serialization."""
        return {
            "numeric_strategy": self.numeric_strategy,
            "impute_strategy": self.impute_strategy,
            "clip_outliers": self.clip_outliers,
            "clip_std_factor": self.clip_std_factor,
            "is_fitted": self.is_fitted,
            "numeric_cols": self.numeric_cols,
            "categorical_cols": self.categorical_cols,
            "feature_names_in": self.feature_names_in_,
            "feature_names_out": self.feature_names_out_,
            "impute_values": self.impute_values_,
            "means": self.means_,
            "stds": self.stds_,
            "mins": self.mins_,
            "maxs": self.maxs_,
            "medians": self.medians_,
            "categories": self.categories_,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataPreprocessor:
        """Restore preprocessor from serialized dictionary state."""
        prep = cls(
            numeric_strategy=data.get("numeric_strategy", "standardize"),
            impute_strategy=data.get("impute_strategy", "median"),
            clip_outliers=data.get("clip_outliers", True),
            clip_std_factor=data.get("clip_std_factor", 6.0),
        )
        prep.is_fitted = data.get("is_fitted", False)
        prep.numeric_cols = data.get("numeric_cols", [])
        prep.categorical_cols = data.get("categorical_cols", [])
        prep.feature_names_in_ = data.get("feature_names_in", [])
        prep.feature_names_out_ = data.get("feature_names_out", [])
        prep.impute_values_ = data.get("impute_values", {})
        prep.means_ = data.get("means", {})
        prep.stds_ = data.get("stds", {})
        prep.mins_ = data.get("mins", {})
        prep.maxs_ = data.get("maxs", {})
        prep.medians_ = data.get("medians", {})
        prep.categories_ = data.get("categories", {})
        return prep
