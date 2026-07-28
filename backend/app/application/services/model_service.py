"""PyTorch fraud detection model and training utilities.

Defines a simple MLP binary classifier suitable for tabular fraud data.
The model is intentionally straightforward — the point of this project
is the federated learning architecture, not model complexity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from app.domain.value_objects import ModelWeights

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Number of input features after encoding
NUM_FEATURES = 10


class FraudDetectionModel(nn.Module):
    """3-layer MLP for binary fraud classification.

    Architecture: 10 → 64 → 32 → 1

    Deliberately simple to keep the focus on the FL pipeline.
    A production model would use attention, embeddings for categoricals,
    and possibly temporal features via LSTM.
    """

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

    def create_model(self, dp_compatible: bool = False) -> FraudDetectionModel:
        """Create a fresh model instance with random initialization."""
        model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=dp_compatible)
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

        if (fedprox_mu > 0.0 or moon_mu > 0.0) and global_weights is not None:
            global_model = self.create_model(dp_compatible=dp_comp)
            global_model = self.set_parameters(global_model, global_weights)
            global_model.eval()
            for p in global_model.parameters():
                p.requires_grad = False

        if moon_mu > 0.0 and prev_local_weights is not None:
            prev_local_model = self.create_model(dp_compatible=dp_comp)
            prev_local_model = self.set_parameters(prev_local_model, prev_local_weights)
            prev_local_model.eval()
            for p in prev_local_model.parameters():
                p.requires_grad = False

        # Use standard BCE since model has sigmoid
        criterion: Any = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        # Build tensor dataset including sensitive attribute
        if sens_attr is not None:
            sens_tensor = torch.FloatTensor(sens_attr).to(self.device)
        else:
            sens_tensor = torch.zeros(len(y_train)).to(self.device)

        dataset = TensorDataset(
            torch.FloatTensor(X_train).to(self.device),
            torch.FloatTensor(y_train).to(self.device),
            sens_tensor,
        )
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=False)

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
        max_grad_norm: float = 1.0,
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

        if (fedprox_mu > 0.0 or moon_mu > 0.0) and global_weights is not None:
            global_model = self.create_model(dp_compatible=dp_comp)
            global_model = self.set_parameters(global_model, global_weights)
            global_model.eval()
            for p in global_model.parameters():
                p.requires_grad = False

        if moon_mu > 0.0 and prev_local_weights is not None:
            prev_local_model = self.create_model(dp_compatible=dp_comp)
            prev_local_model = self.set_parameters(prev_local_model, prev_local_weights)
            prev_local_model.eval()
            for p in prev_local_model.parameters():
                p.requires_grad = False

        criterion: Any = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        # Build tensor dataset including sensitive attribute
        if sens_attr is not None:
            sens_tensor = torch.FloatTensor(sens_attr).to(self.device)
        else:
            sens_tensor = torch.zeros(len(y_train)).to(self.device)

        dataset = TensorDataset(
            torch.FloatTensor(X_train).to(self.device),
            torch.FloatTensor(y_train).to(self.device),
            sens_tensor,
        )
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=False)

        privacy_engine = PrivacyEngine()

        # Cast to Any to prevent IDE type checker errors caused by Opacus overloading definitions
        res: Any = privacy_engine.make_private_with_epsilon(
            module=model,
            optimizer=optimizer,
            data_loader=loader,
            target_epsilon=target_epsilon,
            target_delta=target_delta,
            epochs=epochs,
            max_grad_norm=max_grad_norm,
        )
        model_private: Any = res[0]
        optimizer_private: Any = res[1]
        loader_private: Any = res[2]

        import time

        loss_history: list[float] = []

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

        # De-wrap the module and remove Opacus hooks before returning it
        if hasattr(model_private, "remove_hooks"):
            model_private.remove_hooks()
        model_final = cast("FraudDetectionModel", model_private._module)

        import gc

        gc.collect()

        return model_final, loss_history, actual_epsilon

    def evaluate(
        self,
        model: FraudDetectionModel,
        X_test: np.ndarray,
        y_test: np.ndarray,
        sens_attr: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Evaluate model on test data.

        Returns a dict with accuracy, precision, recall, f1, auc_roc, loss,
        confusion_matrix, roc_fpr, roc_tpr, roc_thresholds, and fairness_counts.
        """
        from sklearn.metrics import (
            accuracy_score,
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
            roc_curve,
        )

        model.eval()
        with torch.no_grad():
            X_tensor = torch.FloatTensor(X_test).to(self.device)
            y_tensor = torch.FloatTensor(y_test).to(self.device)
            probs = model(X_tensor).cpu().numpy()
            loss_tensor = nn.BCELoss()(
                torch.FloatTensor(probs).to(self.device),
                y_tensor,
            )
            loss = loss_tensor.item()
            del X_tensor, y_tensor, loss_tensor
            import gc

            gc.collect()

        preds = (probs >= 0.5).astype(int)

        # Handle edge case where test set has only one class
        try:
            auc = roc_auc_score(y_test, probs)
            fpr, tpr, thresholds = roc_curve(y_test, probs)
        except ValueError:
            auc = 0.0
            fpr = np.array([0.0, 1.0])
            tpr = np.array([0.0, 1.0])
            thresholds = np.array([1.0, 0.0])

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
        test_dataset = TensorDataset(
            torch.FloatTensor(X_test).to(self.device),
            torch.FloatTensor(y_test).to(self.device),
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
            "auc_roc": float(auc),
            "loss": float(loss),
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
        }

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
        """Load parameters from a ModelWeights object into the model."""
        offset = 0
        for param, shape in zip(model.parameters(), weights.layer_shapes, strict=False):
            numel = 1
            for s in shape:
                numel *= s
            param_data = weights.flat_weights[offset : offset + numel]
            param.data = torch.FloatTensor(param_data).reshape(shape).to(self.device)
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
        self, model: FraudDetectionModel, X_ref: np.ndarray | None = None
    ) -> dict[str, float]:
        """Extract feature importance from the first layer weights.

        Uses absolute weight magnitude as a proxy for importance.
        This is a rough heuristic — not as rigorous as SHAP or permutation
        importance, but sufficient for visualization purposes.
        """
        from app.application.services.data_generator import FEATURE_NAMES

        if X_ref is None or len(X_ref) == 0:
            first_layer = list(model.parameters())[0]  # Shape: [64, 10]
            importance = first_layer.abs().mean(dim=0).detach().cpu().numpy()
        else:
            # Calculate Integrated Gradients on reference data
            ref_size = min(100, len(X_ref))
            input_tensor = torch.FloatTensor(X_ref[:ref_size]).to(self.device)
            attributions = self.compute_integrated_gradients(model, input_tensor)
            importance = np.mean(np.abs(attributions), axis=0)

        # Normalize to [0, 1]
        max_imp = importance.max()
        if max_imp > 0:
            importance = importance / max_imp

        return {name: float(imp) for name, imp in zip(FEATURE_NAMES, importance, strict=False)}

    def get_champion(self) -> FraudDetectionModel:
        """Retrieve or instantiate active champion model for production scoring."""
        model = self.create_model(dp_compatible=True)
        model.eval()
        return model

    def invalidate_model_cache(self) -> None:
        """Deletes Redis champion model cache key and publishes model_updated event to Redis PubSub."""
        try:
            from app.infrastructure.cache import get_redis_client

            client = get_redis_client()
            if client:
                client.delete("cfi:champion_model")
                client.publish("cfi:model_events", "model_updated")
                logger.info(
                    "Invalidated Redis champion model cache (cfi:champion_model) and published PubSub event."
                )
        except Exception as exc:
            logger.warning("Could not invalidate Redis model cache: %s", exc)
