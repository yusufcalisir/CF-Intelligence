"""Authoritative Production Holdout Dataset Provider.

Manages the operational lifecycle of designated holdout evaluation datasets:
- Manifest parsing, file verification, SHA-256 integrity validation.
- Schema verification (feature dimensionality, binary label requirements).
- Atomic rotation and active round version binding.
- Process restart re-resolution without in-memory state dependencies.
- Strict fail-closed semantics: zero synthetic fallback, zero mock data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import get_settings
from app.domain.enums import DatasetProvenance
from app.domain.value_objects import DesignatedHoldoutDataset

logger = logging.getLogger(__name__)


class HoldoutResolutionError(ValueError):
    """Raised when designated holdout dataset cannot be resolved or loaded."""


class HoldoutIntegrityError(HoldoutResolutionError):
    """Raised when holdout file content does not match expected SHA-256 digest."""


class HoldoutSchemaError(HoldoutResolutionError):
    """Raised when holdout features, labels, or dimensions fail schema invariants."""


class HoldoutRotationError(HoldoutResolutionError):
    """Raised when holdout rotation target fails validation."""


@dataclass(frozen=True)
class HoldoutManifest:
    """Authoritative metadata manifest defining a designated holdout dataset."""

    dataset_id: str
    version: str
    provenance: str
    data_file: str
    sha256: str = ""
    feature_names: list[str] | None = None
    feature_dim: int | None = None
    label_column: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldoutManifest:
        """Construct HoldoutManifest from dictionary."""
        valid_keys = {
            "dataset_id",
            "version",
            "provenance",
            "data_file",
            "sha256",
            "feature_names",
            "feature_dim",
            "label_column",
            "created_at",
            "metadata",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to serializable dictionary."""
        return asdict(self)


def compute_file_sha256(file_path: Path | str) -> str:
    """Compute deterministic SHA-256 hex digest of a physical file."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found for SHA-256 computation: {p}")
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class HoldoutDatasetProvider:
    """Authoritative provider and lifecycle manager for designated holdout datasets."""

    def __init__(
        self,
        manifest_path: str | Path | None = None,
        data_path: str | Path | None = None,
        auto_load: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        settings = get_settings()

        resolved_manifest = manifest_path or settings.holdout_manifest_path
        resolved_data = data_path or settings.holdout_dataset_path

        self.manifest_path: Path | None = Path(resolved_manifest) if resolved_manifest else None
        self.data_path: Path | None = Path(resolved_data) if resolved_data else None

        self._current_holdout: DesignatedHoldoutDataset | None = None
        self._current_manifest: HoldoutManifest | None = None
        self._rotation_history: list[dict[str, Any]] = []

        if auto_load:
            self.resolve_designated_holdout()

    @property
    def current_holdout(self) -> DesignatedHoldoutDataset | None:
        """Returns currently resolved designated holdout, or None if unconfigured."""
        with self._lock:
            return self._current_holdout

    @property
    def current_manifest(self) -> HoldoutManifest | None:
        """Returns currently active manifest metadata, or None if unconfigured."""
        with self._lock:
            return self._current_manifest

    def resolve_designated_holdout(
        self,
        expected_dataset_id: str | None = None,
        expected_version: str | None = None,
        force_reload: bool = False,
    ) -> DesignatedHoldoutDataset | None:
        """Authoritatively resolves the designated holdout dataset.

        Fails closed with None if unconfigured. Raises HoldoutResolutionError on corrupt/invalid data.
        """
        with self._lock:
            if not force_reload and self._current_holdout is not None:
                holdout = self._current_holdout
            elif self.manifest_path is not None and str(self.manifest_path).strip():
                holdout = self.load_from_manifest(self.manifest_path)
                self._current_holdout = holdout
            elif self.data_path is not None and str(self.data_path).strip():
                holdout = self.load_from_data_file(
                    self.data_path,
                    dataset_id="production_holdout",
                    version="1.0.0",
                    provenance="EMPIRICAL_EXTERNAL_DATA",
                )
                self._current_holdout = holdout
            else:
                # Unconfigured: fail closed without synthetic fallback
                return None

            # Identity & Version Invariant Verification
            if expected_dataset_id is not None:
                canonical_expected = expected_dataset_id.split(":")[0]
                if holdout.dataset_id != canonical_expected and holdout.versioned_id != expected_dataset_id:
                    raise HoldoutResolutionError(
                        f"Resolved dataset ID '{holdout.dataset_id}' does not match expected '{expected_dataset_id}'."
                    )

            if expected_version is not None and holdout.version != expected_version:
                raise HoldoutResolutionError(
                    f"Resolved dataset version '{holdout.version}' does not match expected '{expected_version}'."
                )

            return holdout

    def load_from_manifest(self, manifest_path: str | Path) -> DesignatedHoldoutDataset:
        """Load and strictly validate holdout dataset defined by a manifest JSON file."""
        m_path = Path(manifest_path)
        if not m_path.is_file():
            raise HoldoutResolutionError(f"Holdout manifest file not found: {m_path}")

        try:
            with open(m_path, encoding="utf-8") as f:
                content = json.load(f)
        except Exception as exc:
            raise HoldoutResolutionError(f"Failed to parse manifest JSON '{m_path}': {exc}") from exc

        manifest = HoldoutManifest.from_dict(content)

        # Resolve physical data file relative to manifest directory if not absolute
        data_file_path = Path(manifest.data_file)
        if not data_file_path.is_absolute():
            data_file_path = (m_path.parent / data_file_path).resolve()

        if not data_file_path.is_file():
            raise HoldoutResolutionError(
                f"Data file '{data_file_path}' specified in manifest '{m_path}' does not exist."
            )

        # Integrity Check
        computed_sha = compute_file_sha256(data_file_path)
        if (
            manifest.sha256
            and manifest.sha256.strip()
            and computed_sha.lower() != manifest.sha256.lower().strip()
        ):
            raise HoldoutIntegrityError(
                f"Holdout file SHA-256 mismatch for {data_file_path}: expected {manifest.sha256}, got {computed_sha}"
            )

        features, labels, feature_names = self._read_data_file(
            data_file_path,
            label_column=manifest.label_column,
            declared_feature_names=manifest.feature_names,
        )

        # Schema Validation
        if manifest.feature_dim is not None and features.shape[1] != manifest.feature_dim:
            raise HoldoutSchemaError(
                f"Holdout feature dimension {features.shape[1]} does not match manifest feature_dim {manifest.feature_dim}."
            )

        # Single-Class Guard
        unique_labels = np.unique(labels)
        if len(unique_labels) < 2:
            raise HoldoutSchemaError(
                f"Holdout dataset must contain both positive and negative classes for PR-AUC evaluation (found {unique_labels.tolist()})."
            )

        # Provenance Validation
        valid_provenances = {
            DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
            DatasetProvenance.PUBLIC_SIMULATED_DATASET.value,
            DatasetProvenance.TEST_FIXTURE.value,
        }
        if manifest.provenance not in valid_provenances:
            raise HoldoutResolutionError(
                f"Unsupported dataset provenance '{manifest.provenance}'. Must be one of {valid_provenances}."
            )

        holdout = DesignatedHoldoutDataset(
            dataset_id=manifest.dataset_id,
            features=features,
            labels=labels,
            provenance=manifest.provenance,
            version=manifest.version,
            feature_names=feature_names,
            sha256=computed_sha,
            created_at=manifest.created_at,
        )

        with self._lock:
            self._current_manifest = manifest
            self._current_holdout = holdout

        logger.info(
            "Successfully loaded designated holdout '%s' (v%s, %d samples, %d features, sha=%s)",
            holdout.dataset_id,
            holdout.version,
            len(labels),
            features.shape[1],
            computed_sha[:8],
        )
        return holdout

    def load_from_data_file(
        self,
        data_path: str | Path,
        dataset_id: str = "production_holdout",
        version: str = "1.0.0",
        provenance: str = "EMPIRICAL_EXTERNAL_DATA",
        feature_names: list[str] | None = None,
        sha256: str = "",
        label_column: str | None = None,
    ) -> DesignatedHoldoutDataset:
        """Load holdout directly from a physical data file."""
        p = Path(data_path)
        if not p.is_file():
            raise HoldoutResolutionError(f"Holdout data file not found: {p}")

        computed_sha = compute_file_sha256(p)
        if sha256 and sha256.strip() and computed_sha.lower() != sha256.lower().strip():
            raise HoldoutIntegrityError(
                f"Holdout file SHA-256 mismatch for {p}: expected {sha256}, got {computed_sha}"
            )

        features, labels, resolved_names = self._read_data_file(
            p, label_column=label_column, declared_feature_names=feature_names
        )

        unique_labels = np.unique(labels)
        if len(unique_labels) < 2:
            raise HoldoutSchemaError(
                f"Holdout dataset must contain both positive and negative classes for PR-AUC evaluation (found {unique_labels.tolist()})."
            )

        holdout = DesignatedHoldoutDataset(
            dataset_id=dataset_id,
            features=features,
            labels=labels,
            provenance=provenance,
            version=version,
            feature_names=resolved_names,
            sha256=computed_sha,
        )
        with self._lock:
            self._current_holdout = holdout
        return holdout

    def rotate_holdout(
        self,
        new_manifest_path: str | Path,
        atomic: bool = True,
    ) -> DesignatedHoldoutDataset:
        """Rotates the designated holdout to a new validated version atomically.

        If loading/validation of the new manifest fails, the existing holdout remains active.
        """
        with self._lock:
            old_holdout = self._current_holdout

            try:
                # Load new holdout into a separate instance to verify atomically
                temp_provider = HoldoutDatasetProvider(auto_load=False)
                new_holdout = temp_provider.load_from_manifest(new_manifest_path)
                new_manifest = temp_provider.current_manifest
            except Exception as exc:
                if atomic:
                    logger.warning("Holdout rotation aborted due to validation failure: %s. Preserving active holdout.", exc)
                    raise HoldoutRotationError(f"Atomic rotation failed: {exc}") from exc
                raise

            # Atomic swap
            self.manifest_path = Path(new_manifest_path)
            self._current_holdout = new_holdout
            self._current_manifest = new_manifest
            self._rotation_history.append(
                {
                    "rotated_from": old_holdout.versioned_id if old_holdout else None,
                    "rotated_to": new_holdout.versioned_id,
                    "manifest_path": str(new_manifest_path),
                }
            )
            logger.info(
                "Holdout rotated successfully: '%s' -> '%s'",
                old_holdout.versioned_id if old_holdout else "NONE",
                new_holdout.versioned_id,
            )
            return new_holdout

    def retire_current_holdout(self) -> None:
        """Retire the current holdout designation, transitioning evaluation to unavailable."""
        with self._lock:
            retired_id = self._current_holdout.versioned_id if self._current_holdout else "NONE"
            self._current_holdout = None
            self._current_manifest = None
            self.manifest_path = None
            self.data_path = None
            logger.info("Retired designated holdout '%s'. Evaluation is now unavailable.", retired_id)

    @staticmethod
    def _read_data_file(
        p: Path,
        label_column: str | None = None,
        declared_feature_names: list[str] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[str] | None]:
        """Read features and labels from .npz, .parquet, or .csv."""
        suffix = p.suffix.lower()
        if suffix == ".npz":
            data = np.load(p)
            feat_key = "features" if "features" in data else ("X" if "X" in data else None)
            lbl_key = "labels" if "labels" in data else ("y" if "y" in data else None)
            if feat_key is None or lbl_key is None:
                raise HoldoutSchemaError(
                    f"NPZ holdout file must contain ('features' or 'X') and ('labels' or 'y') arrays. Found: {list(data.keys())}"
                )
            features = np.asarray(data[feat_key], dtype=np.float32)
            labels = np.asarray(data[lbl_key], dtype=int)
            feature_names = declared_feature_names
            return features, labels, feature_names

        if suffix in (".parquet", ".pq"):
            df = pd.read_parquet(p)
        elif suffix in (".csv", ".txt"):
            df = pd.read_csv(p)
        else:
            raise HoldoutResolutionError(f"Unsupported file format '{suffix}' for holdout dataset: {p}")

        if df.empty:
            raise HoldoutSchemaError(f"Holdout dataset in '{p}' is empty.")

        # Determine target label column
        target_col = label_column
        if not target_col:
            for cand in ("label", "is_fraud", "class", "target", "fraud"):
                if cand in df.columns:
                    target_col = cand
                    break
        if not target_col:
            # Default to last column
            last_col = df.columns[-1]
            target_col = last_col if isinstance(last_col, str) else str(last_col)

        if target_col not in df.columns:
            raise HoldoutSchemaError(f"Label column '{target_col}' not found in DataFrame columns: {list(df.columns)}")

        y_series = df[target_col]
        X_df = df.drop(columns=[target_col])

        # Ensure numeric features
        try:
            features = np.asarray(X_df.to_numpy(dtype=np.float32), dtype=np.float32)
        except (ValueError, TypeError) as exc:
            raise HoldoutSchemaError(f"Non-numeric feature values in holdout dataset: {exc}") from exc

        try:
            labels = np.asarray(y_series.to_numpy(dtype=int), dtype=int)
        except (ValueError, TypeError) as exc:
            raise HoldoutSchemaError(f"Non-integer label values in holdout dataset: {exc}") from exc

        feature_names = list(X_df.columns)
        return features, labels, feature_names
