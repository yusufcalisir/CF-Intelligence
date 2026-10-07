"""Authoritative candidate model evaluation service for federated coordinator quality gates.

Executes direct inference of candidate models against designated holdout datasets,
computes PR-AUC internally, and produces round/model/dataset-bound evaluation evidence.
Zero fabricated truth: fails closed if model, holdout, or predictions are unavailable.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import math
from datetime import UTC
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from app.domain.enums import DatasetProvenance
from app.domain.metrics_service import compute_pr_auc
from app.domain.value_objects import (
    DesignatedHoldoutDataset,
    ModelWeights,
    RoundEvaluationEvidence,
)

logger = logging.getLogger(__name__)


class CandidateModelEvaluator:
    """Authoritative evaluator for federated candidate models on designated holdout datasets."""

    def __init__(
        self,
        holdout_dataset: DesignatedHoldoutDataset | None = None,
        preprocessor: Any | None = None,
    ) -> None:
        self.holdout_dataset = holdout_dataset
        self.preprocessor = preprocessor

    def set_holdout_dataset(self, holdout: DesignatedHoldoutDataset | None) -> None:
        """Sets the designated holdout dataset for candidate evaluations."""
        self.holdout_dataset = holdout

    @staticmethod
    def compute_model_hash(candidate_model: Any) -> str | None:
        """Computes deterministic SHA-256 hash of model parameters if accessible."""
        if candidate_model is None:
            return None
        hasher = hashlib.sha256()
        if isinstance(candidate_model, nn.Module):
            has_params = False
            for p in candidate_model.parameters():
                has_params = True
                hasher.update(p.detach().cpu().numpy().tobytes())
            return hasher.hexdigest() if has_params else None
        if isinstance(candidate_model, ModelWeights):
            arr = np.asarray(candidate_model.flat_weights, dtype=np.float32)
            hasher.update(arr.tobytes())
            return hasher.hexdigest()
        if isinstance(candidate_model, dict):
            has_arr = False
            for k in sorted(candidate_model.keys()):
                val = candidate_model[k]
                if isinstance(val, np.ndarray):
                    has_arr = True
                    hasher.update(k.encode("utf-8"))
                    hasher.update(val.tobytes())
            return hasher.hexdigest() if has_arr else None
        return None

    def evaluate(
        self,
        round_id: int,
        candidate_model: Any,
        model_version: str | None = None,
        expected_dataset_id: str | None = None,
    ) -> RoundEvaluationEvidence:
        """Evaluates candidate model on designated holdout dataset via internal model inference.

        Raises:
            ValueError: If holdout dataset is missing, candidate model is missing,
                        or schema/inference/binding fails.
        """
        if self.holdout_dataset is None:
            raise ValueError(
                f"Candidate evaluation failed: no designated holdout dataset configured for round {round_id}."
            )

        if expected_dataset_id:
            expected_canonical = expected_dataset_id.split(":")[0]
            if (
                self.holdout_dataset.dataset_id != expected_dataset_id
                and self.holdout_dataset.versioned_id != expected_dataset_id
                and self.holdout_dataset.dataset_id != expected_canonical
            ):
                raise ValueError(
                    f"Holdout dataset_id '{self.holdout_dataset.dataset_id}' does not match expected '{expected_dataset_id}'."
                )

        if candidate_model is None:
            raise ValueError(
                f"Candidate evaluation failed: candidate model is None for round {round_id}."
            )

        X_raw = self.holdout_dataset.features
        y = self.holdout_dataset.labels

        # Optional Preprocessor Transformation
        if self.preprocessor is not None:
            try:
                X = self.preprocessor.transform(X_raw)
            except Exception as exc:
                raise ValueError(f"Feature preprocessor transformation failed: {exc}") from exc
        else:
            X = X_raw

        # Feature shape validation
        if X.ndim != 2:
            raise ValueError(f"Holdout features must be 2D, got shape {X.shape}")

        # Model Inference
        probs = self._run_inference(candidate_model, X)

        # Validate prediction vector
        if len(probs) != len(y):
            raise ValueError(
                f"Inference output size ({len(probs)}) does not match holdout label count ({len(y)})."
            )
        if not np.isfinite(probs).all():
            raise ValueError("Model inference produced non-finite prediction probabilities (NaN/Inf).")
        if (probs < 0.0).any() or (probs > 1.0).any():
            raise ValueError("Model inference produced out-of-range probabilities outside [0.0, 1.0].")

        # Metric computation
        unique_labels = np.unique(y)
        if len(unique_labels) < 2:
            # Single-class holdout: mathematically undefined
            metric_score = None
            metric_status = "undefined_single_class"
        else:
            raw_metric = compute_pr_auc(list(y), list(probs))
            if raw_metric is not None and math.isfinite(raw_metric) and 0.0 <= raw_metric <= 1.0:
                metric_score = float(raw_metric)
                metric_status = "defined"
            else:
                metric_score = None
                metric_status = "undefined_non_finite_metric"

        model_hash = self.compute_model_hash(candidate_model)
        if isinstance(self.holdout_dataset.provenance, DatasetProvenance):
            prov_str = self.holdout_dataset.provenance.value
        else:
            prov_str = self.holdout_dataset.provenance

        return RoundEvaluationEvidence(
            round_id=round_id,
            validation_labels=list(int(x) for x in y),
            validation_preds=list(float(x) for x in probs),
            dataset_id=self.holdout_dataset.versioned_id,
            model_version=model_version,
            model_hash=model_hash,
            metric_name="pr_auc",
            metric_score=metric_score,
            metric_status=metric_status,
            sample_count=len(y),
            provenance=prov_str,
            producer="CandidateModelEvaluator",
            evaluated_at=datetime.datetime.now(UTC).isoformat(),
        )

    def _run_inference(self, candidate_model: Any, X: np.ndarray) -> np.ndarray:
        """Executes forward pass inference using candidate model representation."""
        if isinstance(candidate_model, nn.Module):
            candidate_model.eval()
            with torch.no_grad():
                tensor_x = torch.as_tensor(X, dtype=torch.float32)
                device = next(candidate_model.parameters()).device if list(candidate_model.parameters()) else torch.device("cpu")
                out = candidate_model(tensor_x.to(device))
                if isinstance(out, tuple):
                    out = out[0]
                probs = out.detach().cpu().numpy().ravel()
                return probs

        if isinstance(candidate_model, ModelWeights):
            from app.application.services.model_service import FraudDetectionModel, ModelService
            from app.config import get_settings

            model_svc = ModelService(get_settings())
            model = FraudDetectionModel(input_dim=X.shape[1])
            model = model_svc.set_parameters(model, candidate_model)
            model.eval()
            with torch.no_grad():
                tensor_x = torch.as_tensor(X, dtype=torch.float32).to(model_svc.device)
                probs = model(tensor_x).detach().cpu().numpy().ravel()
                return probs

        if isinstance(candidate_model, dict):
            # dict of weights from AsyncFLEngine or PyTorch state_dict
            try:
                from app.application.services.model_service import FraudDetectionModel

                model = FraudDetectionModel(input_dim=X.shape[1])
                state_dict = {
                    k: torch.as_tensor(v, dtype=torch.float32) for k, v in candidate_model.items()
                }
                model.load_state_dict(state_dict, strict=False)
                model.eval()
                with torch.no_grad():
                    tensor_x = torch.as_tensor(X, dtype=torch.float32)
                    probs = model(tensor_x).detach().cpu().numpy().ravel()
                    return probs
            except Exception as e:
                raise ValueError(f"Failed to run inference on weight dictionary: {e}") from e

        if hasattr(candidate_model, "predict_proba"):
            probs_2d = candidate_model.predict_proba(X)
            if probs_2d.ndim == 2 and probs_2d.shape[1] >= 2:
                return probs_2d[:, 1]
            return probs_2d.ravel()

        if hasattr(candidate_model, "predict"):
            preds = candidate_model.predict(X)
            return np.asarray(preds, dtype=np.float32).ravel()

        if callable(candidate_model):
            res = candidate_model(X)
            return np.asarray(res, dtype=np.float32).ravel()

        raise ValueError(
            f"Unsupported candidate model representation: {type(candidate_model).__name__}"
        )
