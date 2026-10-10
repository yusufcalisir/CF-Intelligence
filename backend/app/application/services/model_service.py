"""PyTorch fraud detection model and training utilities.

Defines a simple MLP binary classifier suitable for tabular fraud data.
The model is intentionally straightforward — the point of this project
is the federated learning architecture, not model complexity.
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from torch.utils.data import DataLoader, TensorDataset

from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelExecutionError,
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.domain.value_objects import ModelWeights

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Number of input features after encoding
NUM_FEATURES = 10


def _to_tensor(
    data: Any,
    device: torch.device | str = "cpu",
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """Safely convert array/tensor data to a PyTorch tensor on the specified device.

    Avoids PyTorch UserWarning when NumPy arrays are read-only (e.g. deserialized from Ray/pickle)
    by ensuring proper memory ownership via torch.tensor().
    """
    if isinstance(data, torch.Tensor):
        return data.to(device=device, dtype=dtype)
    return torch.tensor(data, dtype=dtype, device=device)


class FraudDetectionModel(nn.Module):
    """3-layer MLP for binary fraud classification.

    Architecture: 10 → 64 → 32 → 1

    Deliberately simple to keep the focus on the FL pipeline.
    A production model would use attention, embeddings for categoricals,
    and possibly temporal features via LSTM.
    """

    dp_provenance: dict[str, Any] | None = None

    def __init__(self, input_dim: int = NUM_FEATURES, dp_compatible: bool = False) -> None:
        super().__init__()
        norm1 = nn.GroupNorm(8, 64) if dp_compatible else nn.BatchNorm1d(64)
        norm2 = nn.GroupNorm(4, 32) if dp_compatible else nn.BatchNorm1d(32)
        self.network = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            norm1,
            nn.Dropout(0.3),
            nn.Linear(64, 32),
            nn.ReLU(),
            norm2,
            nn.Dropout(0.2),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(
        self, x: torch.Tensor, return_features: bool = False
    ) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:
        if return_features:
            feats = x
            for i in range(8):
                feats = self.network[i](feats)
            preds = self.network[8](feats)
            preds = self.network[9](preds).squeeze(-1)
            return preds, feats
        return self.network(x).squeeze(-1)


class ModelService:
    """Manages model lifecycle: creation, training, evaluation, and parameter exchange.

    This service owns the PyTorch model logic and exposes methods that the
    FL engine and simulation service call.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._active_simulation_id: str | None = None
        logger.info("ModelService using device: %s", self.device)

    def get_active_simulation_id(self) -> str | None:
        """Return the active simulation ID if available."""
        return self._active_simulation_id

    def create_model(
        self, input_dim: int = NUM_FEATURES, dp_compatible: bool = False
    ) -> FraudDetectionModel:
        """Create a fresh model instance with dynamic input dimension and random initialization."""
        model = FraudDetectionModel(input_dim=input_dim, dp_compatible=dp_compatible)
        return model.to(self.device)

    def train_local(
        self,
        model: FraudDetectionModel,
        X_train: np.ndarray,
        y_train: np.ndarray,
        epochs: int | None = None,
        learning_rate: float | None = None,
        batch_size: int | None = None,
        fedprox_mu: float = 0.0,
        moon_mu: float = 0.0,
        moon_temperature: float = 0.5,
        global_weights: ModelWeights | None = None,
        prev_local_weights: ModelWeights | None = None,
        # SCAFFOLD control variates
        c_global: list[torch.Tensor] | None = None,
        c_local: list[torch.Tensor] | None = None,
        # Bias mitigation config
        sens_attr: np.ndarray | None = None,
        enable_bias_mitigation: bool = False,
        fairness_lambda: float = 0.5,
        # Active Defense & Adversarial Training config
        enable_adversarial_training: bool = False,
        adversarial_attack_type: str = "fgsm",
        adversarial_epsilon: float = 0.05,
        adversarial_alpha: float = 0.01,
        adversarial_steps: int = 5,
        adversarial_loss_weight: float = 0.5,
    ) -> tuple[FraudDetectionModel, list[float], list[torch.Tensor] | None]:
        """Train the model on a bank's local data.

        Returns the trained model, per-epoch loss history, and updated
        SCAFFOLD local control variates (or None if SCAFFOLD not used).
        """
        epochs = epochs or self.settings.fl_default_local_epochs
        learning_rate = learning_rate or self.settings.fl_default_learning_rate
        batch_size = batch_size or self.settings.fl_default_batch_size

        model.train()

        # Prepare reference models for FedProx and MOON
        global_model = None
        prev_local_model = None
        dp_comp = any(isinstance(m, nn.GroupNorm) for m in model.modules())

        in_dim = int(X_train.shape[1]) if len(X_train.shape) > 1 else NUM_FEATURES

        if (fedprox_mu > 0.0 or moon_mu > 0.0) and global_weights is not None:
            global_model = self.create_model(input_dim=in_dim, dp_compatible=dp_comp)
            global_model = self.set_parameters(global_model, global_weights)
            global_model.eval()
            for p in global_model.parameters():
                p.requires_grad = False

        if moon_mu > 0.0 and prev_local_weights is not None:
            prev_local_model = self.create_model(input_dim=in_dim, dp_compatible=dp_comp)
            prev_local_model = self.set_parameters(prev_local_model, prev_local_weights)
            prev_local_model.eval()
            for p in prev_local_model.parameters():
                p.requires_grad = False

        # Use standard BCE since model has sigmoid
        criterion: Any = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        # Build tensor dataset including sensitive attribute
        if sens_attr is not None:
            sens_tensor = _to_tensor(sens_attr, device=self.device)
        else:
            sens_tensor = torch.zeros(len(y_train), device=self.device)

        dataset = TensorDataset(
            _to_tensor(X_train, device=self.device),
            _to_tensor(y_train, device=self.device),
            sens_tensor,
        )
        drop_last_batch = len(dataset) > batch_size and (len(dataset) % batch_size == 1)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=drop_last_batch)

        import time

        loss_history: list[float] = []

        for epoch in range(epochs):
            epoch_loss = 0.0
            n_batches = 0

            for X_batch, y_batch, sens_batch in loader:
                optimizer.zero_grad()

                # Check if we need representation features for MOON
                if moon_mu > 0.0 and global_model is not None and prev_local_model is not None:
                    predictions, feats = model(X_batch, return_features=True)
                else:
                    predictions = model(X_batch)
                    feats = None

                loss = criterion(predictions, y_batch)

                # Active Defense: Adversarial Training (FGSM / PGD evasion robustness)
                if enable_adversarial_training and adversarial_epsilon > 0.0:
                    from app.application.services.adversarial_service import (
                        AdversarialDefenseService,
                    )

                    adv_service = AdversarialDefenseService.get_instance()
                    if adversarial_attack_type.lower() == "pgd":
                        x_adv = adv_service.generate_pgd_perturbation(
                            model,
                            X_batch,
                            y_batch,
                            criterion,
                            epsilon=adversarial_epsilon,
                            alpha=adversarial_alpha,
                            steps=adversarial_steps,
                        )
                    else:
                        x_adv = adv_service.generate_fgsm_perturbation(
                            model,
                            X_batch,
                            y_batch,
                            criterion,
                            epsilon=adversarial_epsilon,
                        )
                    pred_adv = model(x_adv)
                    adv_loss = criterion(pred_adv, y_batch)
                    loss = (
                        adversarial_loss_weight * loss + (1.0 - adversarial_loss_weight) * adv_loss
                    )

                # Bias mitigation: penalize covariance between predictions and protected group
                if enable_bias_mitigation:
                    p_mean = torch.mean(predictions)
                    a_mean = torch.mean(sens_batch)
                    cov = torch.mean((predictions - p_mean) * (sens_batch - a_mean))
                    fair_loss = fairness_lambda * (cov**2)
                    loss = loss + fair_loss

                # FedProx proximal term
                if fedprox_mu > 0.0 and global_model is not None:
                    proximal_term: float | torch.Tensor = 0.0
                    for param, g_param in zip(model.parameters(), global_model.parameters()):
                        proximal_term += (param - g_param).pow(2).sum()
                    loss = loss + (fedprox_mu / 2.0) * proximal_term

                # MOON model-contrastive loss
                if (
                    moon_mu > 0.0
                    and feats is not None
                    and global_model is not None
                    and prev_local_model is not None
                ):
                    with torch.no_grad():
                        _, g_feats = global_model(X_batch, return_features=True)
                        _, p_feats = prev_local_model(X_batch, return_features=True)

                    cos = nn.CosineSimilarity(dim=-1)
                    sim_global = cos(feats, g_feats) / moon_temperature
                    sim_prev = cos(feats, p_feats) / moon_temperature

                    logits = torch.cat([sim_global.unsqueeze(1), sim_prev.unsqueeze(1)], dim=1)
                    targets = torch.zeros(feats.size(0), dtype=torch.long, device=self.device)
                    con_loss = nn.CrossEntropyLoss()(logits, targets)
                    loss = loss + moon_mu * con_loss

                loss.backward()

                # SCAFFOLD: correct gradients before optimizer step
                # g_i ← g_i - c_i + c  (subtract local variate, add global variate)
                if c_global is not None and c_local is not None:
                    for param, cg, cl in zip(model.parameters(), c_global, c_local):
                        if param.grad is not None:
                            param.grad.data.add_(cg.to(self.device) - cl.to(self.device))

                optimizer.step()

                epoch_loss += loss.item()
                n_batches += 1

                # Yield control to event loop/other threads to prevent GIL starvation
                time.sleep(0.005)

            avg_loss = epoch_loss / max(n_batches, 1)
            loss_history.append(avg_loss)
            logger.debug("Epoch %d/%d — loss: %.4f", epoch + 1, epochs, avg_loss)

            # Additional yield between epochs
            time.sleep(0.02)

        import gc

        gc.collect()

        # SCAFFOLD: update local control variates
        # c_i+ = c_i - c + (1 / K*lr) * (w_old - w_new)  — approximated here as:
        # c_i+ = c_i - c + mean_grad_correction
        updated_c_local: list[torch.Tensor] | None = None
        if c_global is not None and c_local is not None:
            updated_c_local = [
                (cl - cg + param.grad.data.clone() if param.grad is not None else cl)
                for cl, cg, param in zip(c_local, c_global, model.parameters())
            ]

        return model, loss_history, updated_c_local

    def train_local_with_opacus(
        self,
        model: FraudDetectionModel,
        X_train: np.ndarray,
        y_train: np.ndarray,
        target_epsilon: float,
        target_delta: float,
        max_grad_norm: float = 0.5,
        epochs: int | None = None,
        learning_rate: float | None = None,
        batch_size: int | None = None,
        fedprox_mu: float = 0.0,
        moon_mu: float = 0.0,
        moon_temperature: float = 0.5,
        global_weights: ModelWeights | None = None,
        prev_local_weights: ModelWeights | None = None,
        # Bias mitigation config
        sens_attr: np.ndarray | None = None,
        enable_bias_mitigation: bool = False,
        fairness_lambda: float = 0.5,
    ) -> tuple[FraudDetectionModel, list[float], float]:
        """Train the model on a bank's local data with Differential Privacy using Opacus.

        Performs per-sample gradient clipping and noise injection during training.
        Returns the trained model, loss history, and actual epsilon spent.
        """
        from opacus import PrivacyEngine

        epochs = epochs or self.settings.fl_default_local_epochs
        learning_rate = learning_rate or self.settings.fl_default_learning_rate
        batch_size = batch_size or self.settings.fl_default_batch_size

        model.train()

        # Prepare reference models for FedProx and MOON
        global_model = None
        prev_local_model = None
        dp_comp = any(isinstance(m, nn.GroupNorm) for m in model.modules())

        in_dim = int(X_train.shape[1]) if len(X_train.shape) > 1 else NUM_FEATURES

        if (fedprox_mu > 0.0 or moon_mu > 0.0) and global_weights is not None:
            global_model = self.create_model(input_dim=in_dim, dp_compatible=dp_comp)
            global_model = self.set_parameters(global_model, global_weights)
            global_model.eval()
            for p in global_model.parameters():
                p.requires_grad = False

        if moon_mu > 0.0 and prev_local_weights is not None:
            prev_local_model = self.create_model(input_dim=in_dim, dp_compatible=dp_comp)
            prev_local_model = self.set_parameters(prev_local_model, prev_local_weights)
            prev_local_model.eval()
            for p in prev_local_model.parameters():
                p.requires_grad = False

        criterion: Any = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        # Build tensor dataset including sensitive attribute
        if sens_attr is not None:
            sens_tensor = _to_tensor(sens_attr, device=self.device)
        else:
            sens_tensor = torch.zeros(len(y_train), device=self.device)

        dataset = TensorDataset(
            _to_tensor(X_train, device=self.device),
            _to_tensor(y_train, device=self.device),
            sens_tensor,
        )
        drop_last_batch = len(dataset) > batch_size and (len(dataset) % batch_size == 1)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=drop_last_batch)

        privacy_engine = PrivacyEngine()

        # Prefer PyTorch native ExpandedWeights ('ew') mode to compute per-sample
        # gradients directly on the autograd graph without registering full backward hooks
        # on the input layer (which triggers PyTorch UserWarning when inputs do not require grad).
        # Fall back to 'hooks' mode if ExpandedWeights is unsupported for the current architecture.
        try:
            res: Any = privacy_engine.make_private_with_epsilon(
                module=model,
                optimizer=optimizer,
                data_loader=loader,
                target_epsilon=target_epsilon,
                target_delta=target_delta,
                epochs=epochs,
                max_grad_norm=max_grad_norm,
                grad_sample_mode="ew",
            )
        except Exception as e:
            logger.debug(
                "ExpandedWeights grad sample mode unavailable, falling back to hooks: %s", e
            )
            res = privacy_engine.make_private_with_epsilon(
                module=model,
                optimizer=optimizer,
                data_loader=loader,
                target_epsilon=target_epsilon,
                target_delta=target_delta,
                epochs=epochs,
                max_grad_norm=max_grad_norm,
                grad_sample_mode="hooks",
            )
        model_private: Any = res[0]
        optimizer_private: Any = res[1]
        loader_private: Any = res[2]

        import time

        loss_history: list[float] = []

        try:
            for epoch in range(epochs):
                epoch_loss = 0.0
                n_batches = 0

                for X_batch, y_batch, sens_batch in loader_private:
                    optimizer_private.zero_grad()

                    # Check if we need representation features for MOON
                    if moon_mu > 0.0 and global_model is not None and prev_local_model is not None:
                        predictions, feats = model_private(X_batch, return_features=True)
                    else:
                        predictions = model_private(X_batch)
                        feats = None

                    loss = criterion(predictions, y_batch)

                    # Bias mitigation: penalize covariance between predictions and protected group
                    if enable_bias_mitigation:
                        p_mean = torch.mean(predictions)
                        a_mean = torch.mean(sens_batch)
                        cov = torch.mean((predictions - p_mean) * (sens_batch - a_mean))
                        fair_loss = fairness_lambda * (cov**2)
                        loss = loss + fair_loss

                    # FedProx proximal term
                    if fedprox_mu > 0.0 and global_model is not None:
                        proximal_term: float | torch.Tensor = 0.0
                        for param, g_param in zip(
                            model_private.parameters(), global_model.parameters()
                        ):
                            proximal_term += (param - g_param).pow(2).sum()
                        loss = loss + (fedprox_mu / 2.0) * proximal_term

                    # MOON model-contrastive loss
                    if (
                        moon_mu > 0.0
                        and feats is not None
                        and global_model is not None
                        and prev_local_model is not None
                    ):
                        with torch.no_grad():
                            _, g_feats = global_model(X_batch, return_features=True)
                            _, p_feats = prev_local_model(X_batch, return_features=True)

                        cos = nn.CosineSimilarity(dim=-1)
                        sim_global = cos(feats, g_feats) / moon_temperature
                        sim_prev = cos(feats, p_feats) / moon_temperature

                        logits = torch.cat([sim_global.unsqueeze(1), sim_prev.unsqueeze(1)], dim=1)
                        targets = torch.zeros(feats.size(0), dtype=torch.long, device=self.device)
                        con_loss = nn.CrossEntropyLoss()(logits, targets)
                        loss = loss + moon_mu * con_loss

                    loss.backward()
                    optimizer_private.step()

                    epoch_loss += loss.item()
                    n_batches += 1

                    # Yield control to prevent GIL starvation
                    time.sleep(0.005)

                avg_loss = epoch_loss / max(n_batches, 1)
                loss_history.append(avg_loss)
                logger.debug("Opacus Epoch %d/%d — loss: %.4f", epoch + 1, epochs, avg_loss)

                time.sleep(0.02)

            actual_epsilon = privacy_engine.get_epsilon(delta=target_delta)
        finally:
            # Exception-safe cleanup: guaranteed de-wrapping and hook removal
            if hasattr(model_private, "remove_hooks"):
                with contextlib.suppress(Exception):
                    model_private.remove_hooks()

        model_final = cast(
            "FraudDetectionModel", getattr(model_private, "_module", model_private)
        )
        object.__setattr__(
            model_final,
            "dp_provenance",
            {
                "mechanism": "opacus_rdp",
                "epsilon": float(actual_epsilon),
                "delta": float(target_delta),
                "accountant": "rdp",
                "noise_multiplier": float(getattr(optimizer_private, "noise_multiplier", 0.0)),
                "clip_norm": float(max_grad_norm),
                "sample_rate": float(getattr(loader_private, "sample_rate", (batch_size or 64) / max(len(dataset), 1))),
                "steps": int(getattr(optimizer_private, "steps_done", (epochs or 1) * max(n_batches, 1))),
                "dp_mode": "opacus",
                "secure_rng": False,
                "version": "1.0",
            },
        )

        import gc

        gc.collect()

        return model_final, loss_history, actual_epsilon

    def evaluate(
        self,
        model: FraudDetectionModel,
        X_test: np.ndarray,
        y_test: np.ndarray,
        sens_attr: np.ndarray | None = None,
        threshold: float = 0.5,
        threshold_provenance: str = "default_fixed_0.5",
    ) -> dict[str, Any]:
        """Evaluate model on test data using a specified classification threshold.

        Returns a dict with accuracy, precision, recall, f1, auc_roc, pr_auc, loss,
        confusion_matrix, roc_fpr, roc_tpr, roc_thresholds, threshold provenance,
        and fairness/robustness counts.
        """
        model.eval()
        with torch.no_grad():
            X_tensor = _to_tensor(X_test, device=self.device)
            y_tensor = _to_tensor(y_test, device=self.device)
            probs = model(X_tensor).cpu().numpy()
            loss_tensor = nn.BCELoss()(
                _to_tensor(probs, device=self.device),
                y_tensor,
            )
            loss = loss_tensor.item()
            del X_tensor, y_tensor, loss_tensor
            import gc

            gc.collect()

        preds = (probs >= threshold).astype(int)

        score_min = float(np.min(probs)) if len(probs) > 0 else 0.0
        score_max = float(np.max(probs)) if len(probs) > 0 else 0.0
        score_mean = float(np.mean(probs)) if len(probs) > 0 else 0.0

        # Handle edge case where test set has only one class (mathematically undefined)
        is_defined = len(np.unique(y_test)) >= 2
        auc: float | None = None
        pr_auc: float | None = None
        if is_defined:
            try:
                calc_auc = float(roc_auc_score(y_test, probs))
                calc_pr = float(average_precision_score(y_test, probs))
                if math.isfinite(calc_auc) and 0.0 <= calc_auc <= 1.0 and math.isfinite(calc_pr) and 0.0 <= calc_pr <= 1.0:
                    auc = calc_auc
                    pr_auc = calc_pr
                    fpr, tpr, thresholds = roc_curve(y_test, probs)
                    auc_status = "defined"
                else:
                    is_defined = False
                    auc_status = "undefined_non_finite_metric"
                    fpr = np.array([])
                    tpr = np.array([])
                    thresholds = np.array([])
            except Exception:
                auc = None
                pr_auc = None
                fpr = np.array([])
                tpr = np.array([])
                thresholds = np.array([])
                is_defined = False
                auc_status = "undefined_computation_error"
        else:
            auc = None
            pr_auc = None
            fpr = np.array([])
            tpr = np.array([])
            thresholds = np.array([])
            auc_status = "undefined_single_class"

        cm = confusion_matrix(y_test, preds, labels=[0, 1])

        # Compute contingency counts if sens_attr is provided
        fairness_counts = {}
        disparate_impact = 1.0
        equal_opportunity_diff = 0.0
        protected_selection_rate = 1.0
        reference_selection_rate = 1.0

        if sens_attr is not None:
            prot_pos = int(np.sum((sens_attr == 1) & (preds == 0)))
            prot_neg = int(np.sum((sens_attr == 1) & (preds == 1)))
            ref_pos = int(np.sum((sens_attr == 0) & (preds == 0)))
            ref_neg = int(np.sum((sens_attr == 0) & (preds == 1)))

            prot_total = prot_pos + prot_neg
            ref_total = ref_pos + ref_neg

            protected_selection_rate = prot_pos / prot_total if prot_total > 0 else 1.0
            reference_selection_rate = ref_pos / ref_total if ref_total > 0 else 1.0

            if reference_selection_rate > 0:
                disparate_impact = protected_selection_rate / reference_selection_rate
            else:
                disparate_impact = 1.0

            prot_tp = int(np.sum((sens_attr == 1) & (y_test == 1) & (preds == 1)))
            prot_fn = int(np.sum((sens_attr == 1) & (y_test == 1) & (preds == 0)))
            ref_tp = int(np.sum((sens_attr == 0) & (y_test == 1) & (preds == 1)))
            ref_fn = int(np.sum((sens_attr == 0) & (y_test == 1) & (preds == 0)))

            prot_tpr = prot_tp / (prot_tp + prot_fn) if (prot_tp + prot_fn) > 0 else 1.0
            ref_tpr = ref_tp / (ref_tp + ref_fn) if (ref_tp + ref_fn) > 0 else 1.0
            equal_opportunity_diff = abs(prot_tpr - ref_tpr)

            fairness_counts = {
                "protected_positive_pred": prot_pos,
                "protected_negative_pred": prot_neg,
                "reference_positive_pred": ref_pos,
                "reference_negative_pred": ref_neg,
                "protected_tp": prot_tp,
                "protected_fn": prot_fn,
                "reference_tp": ref_tp,
                "reference_fn": ref_fn,
            }

        # Active Defense & Adversarial Evaluation
        from app.application.services.adversarial_service import (
            AdversarialDefenseService,
        )

        adv_service = AdversarialDefenseService.get_instance()
        adv_eval_size = min(500, len(X_test))
        test_dataset = TensorDataset(
            _to_tensor(X_test[:adv_eval_size], device=self.device),
            _to_tensor(y_test[:adv_eval_size], device=self.device),
        )
        test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False)
        adv_report = adv_service.evaluate_adversarial_robustness(
            model, test_loader, nn.BCELoss(), epsilon=0.05
        )

        return {
            "accuracy": float(accuracy_score(y_test, preds)),
            "precision": float(precision_score(y_test, preds, zero_division=0)),
            "recall": float(recall_score(y_test, preds, zero_division=0)),
            "f1_score": float(f1_score(y_test, preds, zero_division=0)),
            "auc_roc": auc,
            "auc_roc_defined": is_defined,
            "auc_roc_status": auc_status,
            "loss": loss,
            "confusion_matrix": cm.tolist(),
            "roc_fpr": fpr.tolist(),
            "roc_tpr": tpr.tolist(),
            "roc_thresholds": thresholds.tolist(),
            "fairness_counts": fairness_counts,
            "disparate_impact": disparate_impact,
            "equal_opportunity_diff": equal_opportunity_diff,
            "protected_selection_rate": protected_selection_rate,
            "reference_selection_rate": reference_selection_rate,
            "adversarial_robustness_score": adv_report["adversarial_robustness_score"],
            "clean_accuracy": adv_report["clean_accuracy"],
            "robust_accuracy": adv_report["robust_accuracy"],
            "fgsm_evasion_rate": adv_report["fgsm_evasion_rate"],
            "pgd_evasion_rate": adv_report["pgd_evasion_rate"],
            "threshold": float(threshold),
            "threshold_provenance": threshold_provenance,
            "pr_auc": float(pr_auc) if pr_auc is not None else None,
            "predicted_positives": int(preds.sum()),
            "score_min": score_min,
            "score_max": score_max,
            "score_mean": score_mean,
        }

    def select_operating_threshold(
        self,
        y_val: np.ndarray,
        probs_val: np.ndarray,
        policy: str = "max_f1",
        n_candidates: int = 200,
    ) -> tuple[float, float, str]:
        """Select an authoritative operating threshold using validation data only.

        Strictly leak-free: never consumes holdout test labels.

        Supported policies:
        - 'max_f1': maximize F1 score on validation distribution
        - 'youden': maximize Youden's J statistic (TPR - FPR)
        - 'fixed_0.5': uncalibrated legacy default

        Returns:
            (selected_threshold, validation_metric_value, policy_provenance)
        """
        if policy == "fixed_0.5" or len(np.unique(y_val)) < 2:
            return 0.5, 0.0, "fixed_0.5"

        p_min, p_max = float(np.min(probs_val)), float(np.max(probs_val))
        if p_min >= p_max:
            return 0.5, 0.0, f"{policy}_degenerate_probs"

        candidates = np.linspace(p_min, p_max, n_candidates)
        scores: list[float] = []

        for th in candidates:
            preds = (probs_val >= th).astype(int)
            if policy == "max_f1":
                score = float(f1_score(y_val, preds, zero_division=0))
            elif policy == "youden":
                tn, fp, fn, tp = confusion_matrix(y_val, preds, labels=[0, 1]).ravel()
                tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
                fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
                score = float(tpr - fpr)
            else:
                score = float(f1_score(y_val, preds, zero_division=0))
            scores.append(score)

        scores_arr = np.array(scores)
        best_score = float(np.max(scores_arr)) if len(scores_arr) > 0 else 0.0

        if best_score > 0.0:
            # Place decision boundary in the robust center of the optimal validation plateau/margin
            optimal_candidates = [
                th for th, s in zip(candidates, scores_arr) if np.isclose(s, best_score, atol=1e-5)
            ]
            best_th = float(np.median(optimal_candidates))
        else:
            best_th = 0.5

        provenance = f"validation_calibrated_{policy}_val_{best_score:.4f}"
        return best_th, best_score, provenance

    def get_parameters(self, model: FraudDetectionModel) -> ModelWeights:
        """Extract model parameters as a serializable ModelWeights object."""
        shapes = []
        flat: list[float] = []

        for param in model.parameters():
            shapes.append(tuple(param.shape))
            flat.extend(param.data.cpu().numpy().flatten().tolist())

        return ModelWeights(layer_shapes=shapes, flat_weights=flat)

    def set_parameters(
        self,
        model: FraudDetectionModel,
        weights: ModelWeights,
    ) -> FraudDetectionModel:
        """Load parameters from a ModelWeights object into the model.

        Raises:
            ValueError: If layer count, shapes, or parameter counts do not match the model.
        """
        model_params = list(model.parameters())
        if len(model_params) != len(weights.layer_shapes):
            raise ValueError(
                f"Layer count mismatch: model has {len(model_params)} layers, "
                f"weights have {len(weights.layer_shapes)}"
            )

        expected_total = sum(math.prod(s) for s in weights.layer_shapes)
        if len(weights.flat_weights) != expected_total:
            raise ValueError(
                f"Flat weights length mismatch: expected {expected_total}, "
                f"got {len(weights.flat_weights)}"
            )

        offset = 0
        for i, (param, shape) in enumerate(zip(model_params, weights.layer_shapes, strict=True)):
            if tuple(param.shape) != tuple(shape):
                raise ValueError(
                    f"Layer {i} shape mismatch: model expects {tuple(param.shape)}, "
                    f"weights specify {tuple(shape)}"
                )
            numel = math.prod(shape)
            param_data = weights.flat_weights[offset : offset + numel]
            param.data = _to_tensor(param_data, device=self.device).reshape(shape)
            offset += numel

        return model

    def compute_integrated_gradients(
        self,
        model: FraudDetectionModel,
        input_tensor: torch.Tensor,
        baseline_tensor: torch.Tensor | None = None,
        steps: int = 50,
    ) -> np.ndarray:
        """Compute Integrated Gradients attribution for a given input tensor.

        Args:
            model: The trained FraudDetectionModel.
            input_tensor: Shape (N, 10) representing the input features.
            baseline_tensor: Optional shape (10,) or (N, 10) representing the baseline.
                             Defaults to all zeros if None.
            steps: Number of integration steps.

        Returns:
            np.ndarray of shape (N, 10) representing feature attributions.
        """
        model.eval()
        if baseline_tensor is None:
            baseline_tensor = torch.zeros_like(input_tensor)

        # Scale inputs from baseline to input
        scaled_inputs = []
        for i in range(steps + 1):
            alpha = i / steps
            scaled_inputs.append(baseline_tensor + alpha * (input_tensor - baseline_tensor))

        # Stack scaled inputs to process as a single batch
        scaled_inputs_batch = torch.cat(scaled_inputs, dim=0).requires_grad_(True).to(self.device)

        # Forward pass
        predictions = model(scaled_inputs_batch)

        # Backward pass to get gradients
        gradients = torch.autograd.grad(
            outputs=predictions,
            inputs=scaled_inputs_batch,
            grad_outputs=torch.ones_like(predictions),
            create_graph=False,
            retain_graph=False,
        )[0]

        # Average the gradients across steps
        gradients = gradients.reshape(steps + 1, input_tensor.shape[0], input_tensor.shape[1])
        avg_gradients = gradients.mean(dim=0)  # Shape (N, 10)

        # IG = (input - baseline) * avg_gradients
        delta = input_tensor.to(self.device) - baseline_tensor.to(self.device)
        attributions = delta * avg_gradients

        return attributions.detach().cpu().numpy()

    def get_feature_importance(
        self,
        model: FraudDetectionModel,
        X_ref: np.ndarray | None = None,
        feature_names: list[str] | None = None,
    ) -> dict[str, float]:
        """Extract feature importance from the first layer weights.

        Uses absolute weight magnitude as a proxy for importance.
        This is a rough heuristic — not as rigorous as SHAP or permutation
        importance, but sufficient for visualization purposes.
        """
        from app.application.services.data_generator import FEATURE_NAMES

        if X_ref is None or len(X_ref) == 0:
            first_layer = list(model.parameters())[0]
            importance = first_layer.abs().mean(dim=0).detach().cpu().numpy()
        else:
            # Calculate Integrated Gradients on reference data
            ref_size = min(100, len(X_ref))
            input_tensor = _to_tensor(X_ref[:ref_size], device=self.device)
            attributions = self.compute_integrated_gradients(model, input_tensor)
            importance = np.mean(np.abs(attributions), axis=0)

        # Normalize to [0, 1]
        max_imp = importance.max()
        if max_imp > 0:
            importance = importance / max_imp

        if feature_names is not None and len(feature_names) == len(importance):
            names = feature_names
        elif len(importance) == len(FEATURE_NAMES):
            names = FEATURE_NAMES
        else:
            names = [f"feature_{i + 1}" for i in range(len(importance))]

        return {name: float(imp) for name, imp in zip(names, importance, strict=False)}

    def get_champion(
        self,
        dp_compatible: bool | None = True,
        registry: Any | None = None,
    ) -> FraudDetectionModel:
        """Retrieve and validate the active trained champion model for production scoring.

        Loads actual trained weights from ModelRegistry.
        Rejects missing models, invalid parameters, or incompatible normalization.
        Returns the verified model in evaluation mode.
        """
        from app.application.services.model_registry import ModelRegistry

        reg = registry or ModelRegistry()
        artifact_path = reg.get_champion_artifact_path()
        if not artifact_path:
            raise ModelNotAvailableError("No trained champion model artifact found in registry.")

        try:
            state_dict = torch.load(artifact_path, map_location=self.device, weights_only=True)
        except Exception as exc:
            logger.error("Failed to load champion artifact %s: %s", artifact_path, exc)
            raise ModelIntegrityError(f"Champion artifact could not be deserialized: {exc}") from exc

        if not isinstance(state_dict, dict):
            raise ModelIntegrityError("Champion artifact is not a valid state dictionary.")

        # Check normalization compatibility
        is_batchnorm = any(
            "running_mean" in k or "running_var" in k or "num_batches_tracked" in k
            for k in state_dict
        )

        effective_dp: bool
        if dp_compatible is None:
            effective_dp = not is_batchnorm
        else:
            effective_dp = dp_compatible
            if effective_dp and is_batchnorm:
                raise ModelCompatibilityError(
                    "Champion artifact was trained with BatchNorm, which is incompatible "
                    "with GroupNorm (dp_compatible=True). Automatic conversion is prohibited."
                )
            if not effective_dp and not is_batchnorm:
                raise ModelCompatibilityError(
                    "Champion artifact was trained with GroupNorm, which is incompatible "
                    "with BatchNorm (dp_compatible=False)."
                )

        # Dimension validation
        first_layer_weight = state_dict.get("network.0.weight")
        if first_layer_weight is not None:
            in_dim = int(first_layer_weight.shape[1])
            if in_dim != NUM_FEATURES:
                raise ModelCompatibilityError(
                    f"Model input dimension mismatch: artifact expects {in_dim} features, "
                    f"but system requires {NUM_FEATURES}."
                )

        model = self.create_model(input_dim=NUM_FEATURES, dp_compatible=effective_dp)

        try:
            model.load_state_dict(state_dict, strict=True)
        except (RuntimeError, ValueError) as exc:
            raise ModelCompatibilityError(
                f"Strict parameter loading failed for champion model: {exc}"
            ) from exc

        model.eval()

        # Sanity check forward pass
        try:
            with torch.no_grad():
                dummy_input = torch.zeros(1, NUM_FEATURES, device=self.device)
                test_out = model(dummy_input)
                if not torch.isfinite(test_out).all():
                    raise ModelExecutionError("Champion sanity check produced non-finite values (NaN/Inf).")
                val = float(test_out.item()) if hasattr(test_out, "item") else float(test_out[0])
                if not (0.0 <= val <= 1.0):
                    raise ModelExecutionError(f"Champion sanity check produced invalid score {val} outside [0.0, 1.0].")
        except ModelExecutionError:
            raise
        except Exception as exc:
            raise ModelExecutionError(f"Champion sanity check forward pass failed: {exc}") from exc

        return model

    def invalidate_model_cache(self) -> None:
        """Deletes Redis champion model cache keys and publishes model_updated event to Redis PubSub."""
        try:
            from app.infrastructure.cache import get_redis_client

            client = get_redis_client()
            if client:
                client.delete("cfi:champion_model", "cfi:champion_model:auth")
                client.publish("cfi:model_events", "model_updated")
                logger.info(
                    "Invalidated Redis champion model cache (cfi:champion_model) and published PubSub event."
                )
        except Exception as exc:
            logger.warning("Could not invalidate Redis model cache: %s", exc)
