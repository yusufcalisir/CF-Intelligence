"""Pooled Centralized Benchmark (Theoretical Upper Bound).

Simulates the hypothetical, privacy-violating upper bound where all consortium
banks aggregate their raw transactional records into a single centralized
data lake:
    D_pooled = Union_{k=1}^K D_k

While legally prohibited under GDPR Art. 4/9 and banking secrecy laws, this
benchmark establishes the absolute empirical ceiling of collaborative intelligence,
allowing exact quantification of the 'centralization gap' (privacy penalty)
incurred by Federated Learning.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from experiments.baselines.classical_baselines import (
    ClassicalBaselines,
    compute_safe_metrics,
)

logger = logging.getLogger(__name__)


class CentralizedMLP(nn.Module):
    """PyTorch MLP baseline for pooled centralized tabular fraud detection."""

    def __init__(self, input_dim: int, hidden_dim: int = 64):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(32, 1),
        )

    def forward(self, x):
        return self.network(x)


class PooledCentralizedBenchmark:
    """Benchmark runner for pooled centralized upper bound models."""

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.baselines = ClassicalBaselines(random_state=random_state)
        self.fitted_models: dict[str, Any] = {}
        self.evaluation_results: dict[str, dict[str, Any]] = {}

    def pool_partitions(
        self,
        bank_partitions: Mapping[str, tuple[Any, Any]] | dict[str, Any],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Aggregate disjoint bank partitions into a single centralized matrix."""
        all_X = []
        all_y = []
        for bank_id, (X_k, y_k) in bank_partitions.items():
            all_X.append(X_k)
            all_y.append(y_k)

        X_pooled = np.vstack(all_X)
        y_pooled = np.concatenate(all_y)
        logger.info(
            "Pooled %d bank partitions into centralized dataset: %d samples, %d features, %.3f%% fraud",
            len(bank_partitions),
            len(y_pooled),
            X_pooled.shape[1],
            float(np.mean(y_pooled) * 100),
        )
        return X_pooled, y_pooled

    def fit_and_evaluate_all(
        self,
        X_pooled_train: np.ndarray | Any,
        y_pooled_train: np.ndarray | Any,
        X_global_test: np.ndarray | Any,
        y_global_test: np.ndarray | Any,
        train_neural_mlp: bool = True,
        mlp_epochs: int = 10,
    ) -> dict[str, dict[str, Any]]:
        """Train classical tabular models and optional MLP on pooled data and evaluate."""
        results: dict[str, dict[str, Any]] = {}

        # 1. Classical Baselines on Pooled Data
        classical_res = self.baselines.run_all_baselines(
            X_train=X_pooled_train,
            y_train=y_pooled_train,
            X_test=X_global_test,
            y_test=y_global_test,
            dataset_name="CentralizedPooledUpper",
        )
        for k, v in classical_res.items():
            results[f"pooled_{k}"] = v

        # 2. Neural MLP on Pooled Data
        if train_neural_mlp:
            mlp_res = self._train_and_eval_mlp(
                X_train=X_pooled_train,
                y_train=y_pooled_train,
                X_test=X_global_test,
                y_test=y_global_test,
                epochs=mlp_epochs,
            )
            results["pooled_neural_mlp"] = mlp_res

        self.evaluation_results = results
        return results

    def _train_and_eval_mlp(
        self,
        X_train: np.ndarray | Any,
        y_train: np.ndarray | Any,
        X_test: np.ndarray | Any,
        y_test: np.ndarray | Any,
        epochs: int = 10,
        batch_size: int = 64,
        lr: float = 0.001,
    ) -> dict[str, Any]:
        """Train and evaluate a centralized PyTorch MLP classifier."""
        torch.manual_seed(self.random_state)
        input_dim = X_train.shape[1]

        # Calculate class weight for BCEWithLogitsLoss
        pos_weight_val = float((len(y_train) - np.sum(y_train)) / max(1, np.sum(y_train)))
        pos_weight = torch.tensor([pos_weight_val], dtype=torch.float32)

        dataset = TensorDataset(
            torch.tensor(X_train, dtype=torch.float32),
            torch.tensor(y_train, dtype=torch.float32).unsqueeze(1),
        )
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=False)

        model = CentralizedMLP(input_dim=input_dim)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)

        model.train()
        for epoch in range(epochs):
            for batch_x, batch_y in loader:
                if len(batch_x) < 2:
                    continue
                optimizer.zero_grad()
                out = model(batch_x)
                loss = criterion(out, batch_y)
                loss.backward()
                optimizer.step()

        # Inference on global test set
        model.eval()
        start = time.perf_counter()
        with torch.no_grad():
            x_test_t = torch.tensor(X_test, dtype=torch.float32)
            logits = model(x_test_t).squeeze()
            probs = torch.sigmoid(logits).cpu().numpy()
        duration = time.perf_counter() - start

        if probs.ndim == 0:
            probs = np.array([float(probs)])

        self.fitted_models["neural_mlp"] = model
        return compute_safe_metrics(
            y_true=y_test,
            y_pred_proba=probs,
            inference_duration_s=duration,
            model_name="Centralized Deep MLP (Pooled Upper Bound)",
        )

    def compute_centralization_gap(
        self,
        federated_pr_auc: float,
        federated_roc_auc: float,
        champion_pooled_key: str = "pooled_gradient_boosting",
    ) -> dict[str, Any]:
        """Quantify the mathematical gap between pooled upper bound and federated consensus."""
        target_metrics = self.evaluation_results.get(champion_pooled_key)
        if not target_metrics:
            # Fallback to first available result
            if self.evaluation_results:
                target_metrics = next(iter(self.evaluation_results.values()))
            else:
                return {
                    "pooled_pr_auc": 0.0,
                    "pooled_roc_auc": 0.5,
                    "centralization_gap_pr_auc": 0.0,
                    "centralization_gap_roc_auc": 0.0,
                    "federated_efficiency_pct": 100.0,
                }

        pooled_pr = target_metrics["pr_auc"]
        pooled_roc = target_metrics["roc_auc"]

        gap_pr = round(pooled_pr - federated_pr_auc, 4)
        gap_roc = round(pooled_roc - federated_roc_auc, 4)

        # Efficiency percentage: how close federated comes to centralized upper bound
        eff_pct = round((federated_pr_auc / pooled_pr * 100.0) if pooled_pr > 0 else 100.0, 2)

        return {
            "pooled_champion_model": champion_pooled_key,
            "pooled_pr_auc": pooled_pr,
            "pooled_roc_auc": pooled_roc,
            "pooled_recall_at_01_fpr": target_metrics.get("recall_at_01_fpr", 0.0),
            "centralization_gap_pr_auc": gap_pr,
            "centralization_gap_roc_auc": gap_roc,
            "federated_efficiency_pct": eff_pct,
        }
