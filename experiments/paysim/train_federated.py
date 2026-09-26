"""End-to-End Federated Optimization (FedAvg, FedProx, SCAFFOLD) for PaySim.

This module implements federated optimization pipelines on PaySim fraud partitions:
1. PaySimNeuralClassifier: 3-layer MLP tuned for binary fraud detection under extreme imbalance.
2. FederatedPaySimTrainer:
   - FedAvg (McMahan et al., 2017): Weighted model parameter aggregation.
   - FedProx (Li et al., 2020): Proximal regularization term (mu > 0) to mitigate non-IID client drift.
   - SCAFFOLD (Karimireddy et al., 2020): Client-server control variates correcting client drift.
3. Multi-round convergence tracking evaluating on the untouched global holdout test set:
   - PR-AUC, ROC-AUC, F1, Precision, Recall, Brier score.
   - Fixed-FPR Recall: Recall @ 0.01%, 0.05%, 0.1%, and 1.0% FPR.
4. Schema-compliant empirical serialization adhering to experiments/harness/schema.py.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from torch.utils.data import DataLoader, TensorDataset

from experiments.harness.schema import (
    CalibrationData,
    ConfusionMatrixData,
    CurvePoint,
    StepMetric,
)

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. Neural Architecture
# ===========================================================================
class PaySimNeuralClassifier(nn.Module):
    """3-layer PyTorch MLP for PaySim mobile money fraud classification.

    Architecture: input_dim -> 64 -> 32 -> 1
    Uses LayerNorm to support arbitrary batch sizes without client-drift
    running statistics corruption in federated settings.
    """

    def __init__(self, input_dim: int = 13, hidden_dim: int = 64, dropout: float = 0.2):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 32),
            nn.LayerNorm(32),
            nn.ReLU(),
            nn.Dropout(dropout / 2.0),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass producing calibrated fraud probabilities in [0, 1]."""
        return self.network(x).squeeze(-1)


# ===========================================================================
# 2. Metric Computation Helpers
# ===========================================================================
def compute_recall_at_fpr(y_true: np.ndarray, y_pred_proba: np.ndarray, target_fpr: float) -> float:
    """Calculate true positive rate (recall) at a strict maximum false positive rate.

    Parameters
    ----------
    y_true : np.ndarray
        Ground-truth binary labels (0 or 1).
    y_pred_proba : np.ndarray
        Predicted probabilities of fraud class.
    target_fpr : float
        Maximum allowable false positive rate (e.g. 0.0001 for 0.01% FPR).
    """
    if len(np.unique(y_true)) < 2:
        return 0.0
    fpr, tpr, _ = roc_curve(y_true, y_pred_proba)
    idx = np.where(fpr <= target_fpr)[0]
    if len(idx) == 0:
        return 0.0
    return float(tpr[idx[-1]])


def compute_comprehensive_metrics(
    y_true: np.ndarray,
    y_pred_proba: np.ndarray,
    threshold: float = 0.5,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    """Compute full suite of classification, probability calibration, and operational metrics."""
    probs = np.clip(y_pred_proba, 0.0, 1.0)
    y_binary = (y_true > 0).astype(int)
    n_samples = len(y_binary)

    if n_samples == 0:
        return {
            "roc_auc": 0.5,
            "pr_auc": 0.0,
            "f1_score": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "brier_score": 0.0,
            "recall_at_001_fpr": 0.0,
            "recall_at_005_fpr": 0.0,
            "recall_at_01_fpr": 0.0,
            "recall_at_1_fpr": 0.0,
            "samples_evaluated": 0,
            "duration_seconds": duration_seconds,
        }

    preds_binary = (probs >= threshold).astype(int)
    unique_classes = np.unique(y_binary)

    if len(unique_classes) < 2:
        roc_auc = 0.5
        pr_auc = float(np.mean(y_binary))
        f1 = 0.0
        prec = 0.0
        rec = 0.0
        rec_001 = 0.0
        rec_005 = 0.0
        rec_01 = 0.0
        rec_10 = 0.0
    else:
        try:
            roc_auc = float(roc_auc_score(y_binary, probs))
        except ValueError:
            roc_auc = 0.5

        try:
            pr_auc = float(average_precision_score(y_binary, probs))
        except ValueError:
            pr_auc = 0.0

        try:
            f1 = float(f1_score(y_binary, preds_binary, zero_division=0))
            prec = float(precision_score(y_binary, preds_binary, zero_division=0))
            rec = float(recall_score(y_binary, preds_binary, zero_division=0))
        except Exception:
            f1, prec, rec = 0.0, 0.0, 0.0

        rec_001 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.0001)
        rec_005 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.0005)
        rec_01 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.001)
        rec_10 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.01)

    try:
        brier = float(brier_score_loss(y_binary, probs))
    except Exception:
        brier = 0.0

    return {
        "roc_auc": round(roc_auc, 5),
        "pr_auc": round(pr_auc, 5),
        "f1_score": round(f1, 5),
        "precision": round(prec, 5),
        "recall": round(rec, 5),
        "brier_score": round(brier, 5),
        "recall_at_001_fpr": round(rec_001, 5),
        "recall_at_005_fpr": round(rec_005, 5),
        "recall_at_01_fpr": round(rec_01, 5),
        "recall_at_1_fpr": round(rec_10, 5),
        "samples_evaluated": n_samples,
        "duration_seconds": round(duration_seconds, 4),
    }


def compute_curve_points(y_true: np.ndarray, y_pred_proba: np.ndarray, max_points: int = 100) -> CurvePoint:
    """Subsample ROC and PR curves for compact JSON serialization and charting."""
    y_binary = (y_true > 0).astype(int)
    if len(np.unique(y_binary)) < 2:
        return CurvePoint(fpr=[0.0, 1.0], tpr=[0.0, 1.0], precision=[0.0, 0.0], recall=[1.0, 0.0], thresholds=[0.5, 0.5])

    fpr_raw, tpr_raw, thresh_roc = roc_curve(y_binary, y_pred_proba)
    prec_raw, rec_raw, thresh_pr = precision_recall_curve(y_binary, y_pred_proba)

    # Subsample indices evenly if points exceed max_points
    if len(fpr_raw) > max_points:
        idx = np.round(np.linspace(0, len(fpr_raw) - 1, max_points)).astype(int)
        fpr_list = [round(float(fpr_raw[i]), 5) for i in idx]
        tpr_list = [round(float(tpr_raw[i]), 5) for i in idx]
        thresh_list = [round(float(thresh_roc[i]), 5) for i in idx]
    else:
        fpr_list = [round(float(v), 5) for v in fpr_raw]
        tpr_list = [round(float(v), 5) for v in tpr_raw]
        thresh_list = [round(float(v), 5) for v in thresh_roc]

    if len(prec_raw) > max_points:
        idx_pr = np.round(np.linspace(0, len(prec_raw) - 1, max_points)).astype(int)
        prec_list = [round(float(prec_raw[i]), 5) for i in idx_pr]
        rec_list = [round(float(rec_raw[i]), 5) for i in idx_pr]
    else:
        prec_list = [round(float(v), 5) for v in prec_raw]
        rec_list = [round(float(v), 5) for v in rec_raw]

    return CurvePoint(
        fpr=fpr_list,
        tpr=tpr_list,
        precision=prec_list,
        recall=rec_list,
        thresholds=thresh_list,
    )


def compute_confusion_matrix_data(y_true: np.ndarray, y_pred_proba: np.ndarray, threshold: float = 0.5) -> ConfusionMatrixData:
    """Compute binary confusion matrix components at given threshold."""
    y_binary = (y_true > 0).astype(int)
    preds = (y_pred_proba >= threshold).astype(int)
    cm = confusion_matrix(y_binary, preds, labels=[0, 1])
    return ConfusionMatrixData(
        tn=int(cm[0, 0]),
        fp=int(cm[0, 1]),
        fn=int(cm[1, 0]),
        tp=int(cm[1, 1]),
        labels=["Legitimate", "Fraud"],
    )


def compute_calibration_data(y_true: np.ndarray, y_pred_proba: np.ndarray, n_bins: int = 10) -> CalibrationData:
    """Discretize probabilities into uniform bins and compute empirical calibration."""
    y_binary = (y_true > 0).astype(int)
    brier = float(brier_score_loss(y_binary, y_pred_proba)) if len(y_binary) > 0 else 0.0

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_indices = np.digitize(y_pred_proba, bins) - 1
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)

    prob_true = []
    prob_pred = []
    for b in range(n_bins):
        mask = bin_indices == b
        if np.any(mask):
            prob_true.append(round(float(np.mean(y_binary[mask])), 5))
            prob_pred.append(round(float(np.mean(y_pred_proba[mask])), 5))

    return CalibrationData(
        prob_true=prob_true,
        prob_pred=prob_pred,
        brier_score=round(brier, 5),
    )


# ===========================================================================
# 3. Model Weight Operations
# ===========================================================================
def clone_weights(model: nn.Module) -> dict[str, torch.Tensor]:
    """Extract a detached CPU clone of model state dict."""
    return {k: v.detach().clone().cpu() for k, v in model.state_dict().items()}


def set_weights(model: nn.Module, weights: dict[str, torch.Tensor], device: torch.device) -> None:
    """Load cloned weights into a model on target device."""
    target_dict = {k: v.to(device) for k, v in weights.items()}
    model.load_state_dict(target_dict)


def aggregate_weights(
    client_weights_list: list[tuple[dict[str, torch.Tensor], int]],
) -> dict[str, torch.Tensor]:
    """Perform sample-weighted parameter averaging across participating client models."""
    if not client_weights_list:
        raise ValueError("Cannot aggregate empty client weights list")

    total_samples = sum(n_k for _, n_k in client_weights_list)
    if total_samples <= 0:
        total_samples = len(client_weights_list)
        client_weights_list = [(w, 1) for w, _ in client_weights_list]

    first_weights = client_weights_list[0][0]
    aggregated: dict[str, torch.Tensor] = {}

    for key, val in first_weights.items():
        if val.dtype in (torch.float32, torch.float64, torch.float16):
            accum = torch.zeros_like(val)
            for weights, n_k in client_weights_list:
                accum.add_(weights[key] * (n_k / total_samples))
            aggregated[key] = accum
        else:
            aggregated[key] = val.clone()

    return aggregated


# ===========================================================================
# 4. Federated Optimizer Engine
# ===========================================================================
class FederatedPaySimTrainer:
    """Coordinates federated training across PaySim partitions using FedAvg, FedProx, or SCAFFOLD."""

    def __init__(
        self,
        input_dim: int = 13,
        hidden_dim: int = 64,
        learning_rate: float = 0.001,
        batch_size: int = 64,
        local_epochs: int = 2,
        seed: int = 42,
        device: torch.device | str | None = None,
    ) -> None:
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.local_epochs = local_epochs
        self.seed = seed

        if device is not None:
            self.device = torch.device(device)
        else:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Set seeds
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)

    def create_model(self) -> PaySimNeuralClassifier:
        """Instantiate a fresh PaySimNeuralClassifier on device."""
        model = PaySimNeuralClassifier(input_dim=self.input_dim, hidden_dim=self.hidden_dim)
        return model.to(self.device)

    def evaluate_model(
        self,
        model: nn.Module,
        X_test: np.ndarray,
        y_test: np.ndarray,
        batch_size: int = 512,
    ) -> tuple[float, np.ndarray, dict[str, Any]]:
        """Evaluate model on test dataset and return (loss, probabilities, metrics)."""
        model.eval()
        criterion = nn.BCELoss()

        X_t = torch.FloatTensor(X_test)
        y_t = torch.FloatTensor(y_test)
        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)

        all_probs: list[float] = []
        total_loss = 0.0
        n_batches = 0

        t0 = time.perf_counter()
        with torch.no_grad():
            for bx, by in loader:
                bx = bx.to(self.device)
                by = by.to(self.device)
                probs = model(bx)
                loss = criterion(probs, by)
                total_loss += loss.item()
                n_batches += 1
                all_probs.extend(probs.cpu().numpy().tolist())
        duration = time.perf_counter() - t0

        avg_loss = float(total_loss / max(1, n_batches))
        probs_arr = np.array(all_probs, dtype=np.float32)
        metrics = compute_comprehensive_metrics(
            y_true=y_test,
            y_pred_proba=probs_arr,
            threshold=0.5,
            duration_seconds=duration,
        )
        return avg_loss, probs_arr, metrics

    def _train_client_local(
        self,
        client_id: str,
        initial_weights: dict[str, torch.Tensor],
        X_k: np.ndarray,
        y_k: np.ndarray,
        strategy: str = "fedavg",
        fedprox_mu: float = 0.01,
        server_c: dict[str, torch.Tensor] | None = None,
        client_c: dict[str, torch.Tensor] | None = None,
    ) -> tuple[dict[str, torch.Tensor], float, dict[str, torch.Tensor] | None]:
        """Execute local client training for specified epochs under the chosen strategy.

        Returns
        -------
        updated_weights : dict[str, torch.Tensor]
            Trained local model parameters.
        avg_train_loss : float
            Mean training loss across local batches.
        updated_client_c : dict[str, torch.Tensor] | None
            Updated SCAFFOLD local control variate (or None for FedAvg/FedProx).
        """
        model = self.create_model()
        set_weights(model, initial_weights, self.device)
        model.train()

        criterion = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=self.learning_rate)

        # Reference global model for FedProx proximal regularizer
        global_ref_dict: dict[str, torch.Tensor] | None = None
        if strategy.lower() == "fedprox" and fedprox_mu > 0.0:
            global_ref_dict = {k: v.to(self.device) for k, v in initial_weights.items()}

        dataset = TensorDataset(torch.FloatTensor(X_k), torch.FloatTensor(y_k))
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        total_loss = 0.0
        n_steps = 0

        for _ in range(self.local_epochs):
            for bx, by in loader:
                bx = bx.to(self.device)
                by = by.to(self.device)
                optimizer.zero_grad()

                predictions = model(bx)
                loss = criterion(predictions, by)

                # FedProx proximal regularizer: (mu / 2) * sum(||w - w_global||^2)
                if strategy.lower() == "fedprox" and global_ref_dict is not None:
                    proximal_penalty = torch.tensor(0.0, device=self.device)
                    for name, param in model.named_parameters():
                        if name in global_ref_dict:
                            proximal_penalty = proximal_penalty + (param - global_ref_dict[name]).pow(2).sum()
                    loss = loss + (fedprox_mu / 2.0) * proximal_penalty

                loss.backward()

                # SCAFFOLD gradient correction: g_k <- g_k - c_k + c
                if strategy.lower() == "scaffold" and server_c is not None and client_c is not None:
                    with torch.no_grad():
                        for name, param in model.named_parameters():
                            if param.grad is not None and name in server_c and name in client_c:
                                correction = server_c[name].to(self.device) - client_c[name].to(self.device)
                                param.grad.data.add_(correction)

                optimizer.step()

                total_loss += loss.item()
                n_steps += 1

        avg_loss = float(total_loss / max(1, n_steps))
        updated_weights = clone_weights(model)

        # SCAFFOLD local control variate update:
        # c_k^+ = c_k - c + (1 / (K_steps * lr)) * (w_global - w_local)
        updated_c: dict[str, torch.Tensor] | None = None
        if strategy.lower() == "scaffold" and server_c is not None and client_c is not None:
            updated_c = {}
            step_scale = max(1, n_steps) * self.learning_rate
            for name in client_c:
                c_k_param = client_c[name].to(self.device)
                c_srv_param = server_c[name].to(self.device)
                w_glob = initial_weights[name].to(self.device)
                w_loc = updated_weights[name].to(self.device)

                c_new = c_k_param - c_srv_param + (w_glob - w_loc) / max(1e-6, step_scale)
                updated_c[name] = c_new.detach().clone().cpu()

        return updated_weights, avg_loss, updated_c

    def train_federated(
        self,
        client_partitions: Mapping[str, tuple[np.ndarray, np.ndarray]] | dict[str, Any],
        X_global_test: np.ndarray,
        y_global_test: np.ndarray,
        strategy: str = "fedavg",
        rounds: int = 10,
        fedprox_mu: float = 0.01,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """Execute full federated optimization loop across banking client partitions.

        Parameters
        ----------
        client_partitions : dict[str, tuple[np.ndarray, np.ndarray]]
            Mapping of client_id -> (X_k, y_k).
        X_global_test : np.ndarray
            Untouched global consortium test features.
        y_global_test : np.ndarray
            Untouched global consortium test labels.
        strategy : str
            Federated optimization strategy: 'fedavg', 'fedprox', or 'scaffold'.
        rounds : int
            Number of federated aggregation communication rounds.
        fedprox_mu : float
            Proximal regularization parameter (active when strategy='fedprox').
        verbose : bool
            Whether to log step progression.
        """
        strat_key = strategy.lower().strip()
        if strat_key not in ("fedavg", "fedprox", "scaffold"):
            raise ValueError(f"Unsupported strategy '{strategy}'. Expected 'fedavg', 'fedprox', or 'scaffold'.")

        client_ids = list(client_partitions.keys())
        n_clients = len(client_ids)
        if n_clients == 0:
            raise ValueError("client_partitions must contain at least one client partition")

        if verbose:
            logger.info(
                "--- Starting Federated Training [%s] --- (%d clients, %d rounds, lr=%.4f, mu=%.3f)",
                strat_key.upper(),
                n_clients,
                rounds,
                self.learning_rate,
                fedprox_mu if strat_key == "fedprox" else 0.0,
            )

        # 1. Initialize global model and weights
        global_model = self.create_model()
        global_weights = clone_weights(global_model)

        # 2. Initialize SCAFFOLD control variates if requested
        server_c: dict[str, torch.Tensor] | None = None
        client_c_dict: dict[str, dict[str, torch.Tensor]] | None = None
        if strat_key == "scaffold":
            server_c = {k: torch.zeros_like(v) for k, v in global_weights.items()}
            client_c_dict = {
                cid: {k: torch.zeros_like(v) for k, v in global_weights.items()}
                for cid in client_ids
            }

        history: list[StepMetric] = []
        start_time_all = time.perf_counter()

        # Initial zero-round evaluation baseline
        loss_init, probs_init, metrics_init = self.evaluate_model(global_model, X_global_test, y_global_test)
        history.append(
            StepMetric(
                step=0,
                train_loss=loss_init,
                val_loss=loss_init,
                pr_auc=metrics_init["pr_auc"],
                roc_auc=metrics_init["roc_auc"],
                accuracy=round(float(np.mean((probs_init >= 0.5) == y_global_test)), 5),
                f1_score=metrics_init["f1_score"],
                precision=metrics_init["precision"],
                recall=metrics_init["recall"],
                duration_seconds=0.0,
                extra={"recall_at_01_fpr": metrics_init["recall_at_01_fpr"], "brier_score": metrics_init["brier_score"]},
            )
        )

        for r in range(1, rounds + 1):
            t_round_start = time.perf_counter()
            client_updates: list[tuple[dict[str, torch.Tensor], int]] = []
            round_train_losses: list[float] = []
            delta_c_sum: dict[str, torch.Tensor] = {k: torch.zeros_like(v) for k, v in global_weights.items()}

            for cid in client_ids:
                X_k, y_k = client_partitions[cid]
                n_k = len(y_k)
                if n_k == 0:
                    continue

                c_k = client_c_dict[cid] if client_c_dict is not None else None
                up_w, tr_loss, updated_c = self._train_client_local(
                    client_id=cid,
                    initial_weights=global_weights,
                    X_k=X_k,
                    y_k=y_k,
                    strategy=strat_key,
                    fedprox_mu=fedprox_mu,
                    server_c=server_c,
                    client_c=c_k,
                )
                client_updates.append((up_w, n_k))
                round_train_losses.append(tr_loss)

                # SCAFFOLD control variates accumulation
                if strat_key == "scaffold" and updated_c is not None and client_c_dict is not None:
                    for k in delta_c_sum:
                        delta_c_sum[k].add_(updated_c[k] - client_c_dict[cid][k])
                    client_c_dict[cid] = updated_c

            # Server Model Parameter Aggregation
            if strat_key == "scaffold":
                # SCAFFOLD uniform client aggregation: w <- w + 1/K * sum(w_k - w)
                global_weights = aggregate_weights([(w, 1) for w, _ in client_updates])
                # Server control variate update: c <- c + 1/K * sum(c_k^+ - c_k)
                if server_c is not None:
                    for k in server_c:
                        server_c[k].add_(delta_c_sum[k] / max(1, len(client_updates)))
            else:
                # FedAvg / FedProx: Sample-weighted parameter averaging
                global_weights = aggregate_weights(client_updates)

            set_weights(global_model, global_weights, self.device)

            # Global Evaluation on Untouched Future Holdout Set
            val_loss, probs_test, metrics = self.evaluate_model(global_model, X_global_test, y_global_test)
            round_duration = time.perf_counter() - t_round_start
            avg_round_train_loss = float(np.mean(round_train_losses)) if round_train_losses else val_loss

            step_metric = StepMetric(
                step=r,
                train_loss=round(avg_round_train_loss, 5),
                val_loss=round(val_loss, 5),
                pr_auc=metrics["pr_auc"],
                roc_auc=metrics["roc_auc"],
                accuracy=round(float(np.mean((probs_test >= 0.5) == y_global_test)), 5),
                f1_score=metrics["f1_score"],
                precision=metrics["precision"],
                recall=metrics["recall"],
                duration_seconds=round(round_duration, 4),
                extra={
                    "recall_at_001_fpr": metrics["recall_at_001_fpr"],
                    "recall_at_01_fpr": metrics["recall_at_01_fpr"],
                    "brier_score": metrics["brier_score"],
                },
            )
            history.append(step_metric)

            if verbose:
                logger.info(
                    "Round %2d/%2d [%s] — Loss: %.4f (Val: %.4f) | PR-AUC: %.4f | ROC-AUC: %.4f | Rec@0.1%%FPR: %.2f%% (%.2fs)",
                    r,
                    rounds,
                    strat_key.upper(),
                    avg_round_train_loss,
                    val_loss,
                    metrics["pr_auc"],
                    metrics["roc_auc"],
                    metrics["recall_at_01_fpr"] * 100,
                    round_duration,
                )

        total_duration = time.perf_counter() - start_time_all

        # Final predictions & comprehensive diagnostics
        final_loss, final_probs, final_metrics = self.evaluate_model(global_model, X_global_test, y_global_test)
        curves = compute_curve_points(y_global_test, final_probs)
        confusion_mat = compute_confusion_matrix_data(y_global_test, final_probs, threshold=0.5)
        calibration = compute_calibration_data(y_global_test, final_probs, n_bins=10)

        return {
            "strategy": strat_key,
            "final_model": global_model,
            "final_weights": global_weights,
            "final_metrics": final_metrics,
            "history": history,
            "curves": curves,
            "confusion_matrix": confusion_mat,
            "calibration": calibration,
            "predictions": final_probs,
            "total_duration_seconds": round(total_duration, 3),
            "num_clients": n_clients,
            "rounds": rounds,
        }

    def run_multi_optimizer_benchmark(
        self,
        client_partitions: Mapping[str, tuple[np.ndarray, np.ndarray]] | dict[str, Any],
        X_global_test: np.ndarray,
        y_global_test: np.ndarray,
        rounds: int = 10,
        fedprox_mu: float = 0.01,
    ) -> dict[str, Any]:
        """Concurrently train and benchmark FedAvg, FedProx, and SCAFFOLD on the same partitions."""
        logger.info("=================================================================")
        logger.info("Executing Federated PaySim Multi-Optimizer Benchmark Suite")
        logger.info("=================================================================")

        strategies = ["fedavg", "fedprox", "scaffold"]
        optimizer_results: dict[str, Any] = {}

        for strat in strategies:
            # Reset deterministic seed for consistent baseline model initialization
            torch.manual_seed(self.seed)
            np.random.seed(self.seed)

            res = self.train_federated(
                client_partitions=client_partitions,
                X_global_test=X_global_test,
                y_global_test=y_global_test,
                strategy=strat,
                rounds=rounds,
                fedprox_mu=fedprox_mu,
                verbose=True,
            )
            optimizer_results[strat] = res

        # Synthesize convergence comparison matrix
        convergence_comparison = {}
        for strat, res in optimizer_results.items():
            hist = res["history"]
            convergence_comparison[strat] = {
                "round_losses": [m.val_loss for m in hist],
                "round_pr_aucs": [m.pr_auc for m in hist],
                "round_roc_aucs": [m.roc_auc for m in hist],
                "final_pr_auc": res["final_metrics"]["pr_auc"],
                "final_roc_auc": res["final_metrics"]["roc_auc"],
                "final_recall_at_01_fpr": res["final_metrics"]["recall_at_01_fpr"],
                "final_recall_at_001_fpr": res["final_metrics"]["recall_at_001_fpr"],
                "final_brier_score": res["final_metrics"]["brier_score"],
                "duration_seconds": res["total_duration_seconds"],
            }

        return {
            "optimizer_results": optimizer_results,
            "convergence_comparison": convergence_comparison,
            "best_optimizer": max(optimizer_results.keys(), key=lambda s: optimizer_results[s]["final_metrics"]["pr_auc"]),
        }
