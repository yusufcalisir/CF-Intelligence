"""Model Architectures, Baselines, and Federated Aggregation for CrossBank v2.

Maintains strict model architecture parity across Isolated, Federated, and Centralized
conditions, and establishes principled non-pathological cold-start baselines.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.linear_model import LogisticRegression


class CrossBankMLP(nn.Module):
    """Deep feedforward classifier with LayerNorm and Dropout for transaction scoring."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 48,
        dropout_rate: float = 0.2,
    ) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout_rate),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.LayerNorm(hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout_rate),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_proba(self, x: Any) -> np.ndarray:
        self.eval()
        with torch.no_grad():
            if isinstance(x, torch.Tensor):
                t_x = x.float()
            else:
                t_x = torch.tensor(np.asarray(x), dtype=torch.float32)
            logits = self.forward(t_x).squeeze(-1)
            probs = torch.sigmoid(logits).cpu().numpy()
        return np.asarray(probs, dtype=np.float32)


def train_pytorch_model(
    model: CrossBankMLP,
    X_train: Any,
    y_train: Any,
    epochs: int = 5,
    lr: float = 0.005,
    weight_decay: float = 1e-4,
    batch_size: int = 64,
) -> tuple[CrossBankMLP, int]:
    """Train PyTorch model locally with cost-sensitive BCE. Returns (model, optimizer_steps)."""
    if len(X_train) == 0:
        return model, 0

    device = torch.device("cpu")
    model.to(device)
    model.train()

    X_arr = np.asarray(X_train, dtype=np.float32)
    y_arr = np.asarray(y_train, dtype=np.float32)

    n_pos = int(np.sum(y_arr == 1.0))
    n_neg = int(np.sum(y_arr == 0.0))
    pos_weight = float(n_neg / max(1, n_pos)) if n_pos > 0 else 1.0

    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight], device=device))
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    dataset_size = len(X_arr)
    indices = np.arange(dataset_size)
    total_steps = 0

    for _ in range(epochs):
        np.random.shuffle(indices)
        for start_idx in range(0, dataset_size, batch_size):
            batch_idx = indices[start_idx : start_idx + batch_size]
            b_x = torch.tensor(X_arr[batch_idx], dtype=torch.float32, device=device)
            b_y = torch.tensor(y_arr[batch_idx], dtype=torch.float32, device=device)

            optimizer.zero_grad()
            preds = model(b_x).squeeze(-1)
            loss = criterion(preds, b_y)
            loss.backward()
            optimizer.step()
            total_steps += 1

    return model, total_steps


def aggregate_fedavg(
    models: list[CrossBankMLP],
    sample_weights: list[int],
) -> dict[str, torch.Tensor]:
    """Perform sample-weighted FedAvg parameter aggregation."""
    total_weight = float(sum(sample_weights))
    assert total_weight > 0, "Total sample weight must be positive"
    norm_weights = [float(w) / total_weight for w in sample_weights]

    avg_state: dict[str, torch.Tensor] = {}
    first_state = models[0].state_dict()

    for key in first_state:
        stacked = torch.stack(
            [models[i].state_dict()[key].float() * norm_weights[i] for i in range(len(models))]
        )
        avg_state[key] = stacked.sum(dim=0)

    return avg_state


class ColdStartLocalBaseline:
    """Principled local baseline for cold-start institutions with zero historical fraud labels.

    Replaces the historical pathological all-zeros/bias=-10 model with a principled Laplace-smoothed
    empirical prior model.
    """

    def __init__(self, train_negative_count: int = 500, prior_mode: str = "laplace") -> None:
        self.prior_mode = prior_mode
        self.train_negative_count = train_negative_count
        # Laplace estimator: (0 + 1) / (N_neg + 2)
        self.prior = float(1.0 / max(1.0, float(train_negative_count) + 2.0))

    def fit(self, X: Any, y: Any) -> ColdStartLocalBaseline:
        n_pos = int(np.sum(y == 1))
        # Laplace estimator: (k + 1) / (N + 2)
        self.prior = float((n_pos + 1.0) / (len(y) + 2.0))
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        n_samples = len(X)
        return np.full(n_samples, self.prior, dtype=np.float32)


class SimpleLogisticBaseline:
    """Non-neural linear baseline to benchmark synthetic feature separability."""

    def __init__(self, seed: int = 42) -> None:
        self.clf = LogisticRegression(
            class_weight="balanced",
            random_state=seed,
            max_iter=500,
            C=1.0,
        )
        self.is_fitted = False

    def fit(self, X_train: Any, y_train: Any) -> SimpleLogisticBaseline:
        self.clf.fit(X_train, y_train)
        self.is_fitted = True
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        assert self.is_fitted, "Baseline must be fitted before predict"
        probs = self.clf.predict_proba(X)
        return probs[:, 1].astype(np.float32)
