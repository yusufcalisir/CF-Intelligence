"""Automated FL Hyperparameter Optimizer (Optuna / Bayesian TPE).

Executes Bayesian optimization over federated learning hyperparameters (learning rate, local epochs,
DP clip norm, noise multiplier, staleness decay, FedProx penalty mu) tailored to Non-IID bank data distributions.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import optuna

from app.application.services.fl_dirichlet_partitioner import DirichletPartitioner
from app.application.services.fl_engine import FederatedLearningEngine as FLEngine
from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)

# Suppress noisy Optuna verbosity by default
optuna.logging.set_verbosity(optuna.logging.WARNING)


class FLHyperparameterOptimizer:
    """Automated Optuna Bayesian TPE Hyperparameter Tuning Engine for FL."""

    def __init__(
        self,
        study_name: str = "fl_hpo_study",
        dirichlet_alpha: float = 0.5,
        num_clients: int = 3,
        num_rounds: int = 5,
        seed: int = 42,
    ) -> None:
        self.study_name = study_name
        self.dirichlet_alpha = dirichlet_alpha
        self.num_clients = num_clients
        self.num_rounds = num_rounds
        self.seed = seed

        # Create Optuna TPE sampler and MedianPruner
        self.sampler = optuna.samplers.TPESampler(seed=seed)
        self.pruner = optuna.pruners.MedianPruner(n_startup_trials=2, n_warmup_steps=1)
        self.study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=self.sampler,
            pruner=self.pruner,
        )

    def _generate_synthetic_bank_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Generate reproducible synthetic fraud transaction features and labels."""
        rng = np.random.default_rng(self.seed)
        n_samples = 600
        n_features = 10

        # Class 0: Legitimate, Class 1: Fraud
        X0 = rng.normal(loc=0.0, scale=1.0, size=(int(n_samples * 0.9), n_features))
        y0 = np.zeros(int(n_samples * 0.9))

        X1 = rng.normal(loc=2.5, scale=1.2, size=(int(n_samples * 0.1), n_features))
        y1 = np.ones(int(n_samples * 0.1))

        X = np.vstack([X0, X1])
        y = np.concatenate([y0, y1])

        perm = rng.permutation(len(y))
        return X[perm], y[perm]

    def objective(self, trial: optuna.Trial) -> float:
        """Evaluate a single trial set of candidate FL hyperparameters using real PyTorch training."""
        # 1. Suggest hyperparameters
        lr = trial.suggest_float("learning_rate", 1e-4, 1e-1, log=True)
        local_epochs = trial.suggest_int("local_epochs", 1, 5)
        dp_clip_norm = trial.suggest_float("dp_clip_norm", 0.1, 5.0)
        dp_noise_multiplier = trial.suggest_float("dp_noise_multiplier", 0.1, 2.0)
        staleness_gamma = trial.suggest_float("staleness_gamma", 0.1, 3.0)
        fedprox_mu = trial.suggest_float("fedprox_mu", 1e-3, 1.0, log=True)
        batch_size = trial.suggest_categorical("batch_size", [16, 32, 64])

        # 2. Generate dataset and partition across bank nodes using Dirichlet Dir(alpha)
        X, y = self._generate_synthetic_bank_data()
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        client_datasets = DirichletPartitioner.partition_dataset(
            features=X_train,
            labels=y_train,
            num_clients=self.num_clients,
            alpha=self.dirichlet_alpha,
            seed=self.seed + trial.number,
        )

        from app.application.services.model_service import ModelService
        from app.application.services.privacy_service import PrivacyService
        from app.config import get_settings

        settings = get_settings()
        model_service = ModelService(settings)
        privacy_service = PrivacyService()
        fl_engine = FLEngine(settings, model_service, privacy_service)

        # Initialize global model with reproducible seed
        rng = np.random.default_rng(self.seed)
        global_model = model_service.create_model(dp_compatible=True)
        global_weights = model_service.get_parameters(global_model)

        final_score = 0.5

        # 3. Simulate federated rounds with genuine local PyTorch training
        for round_idx in range(1, self.num_rounds + 1):
            client_updates = []
            client_samples = []

            # Staleness attenuation factor S(tau) = (1 + 0.1 * round)^(-gamma)
            staleness_weight = (1.0 + (round_idx - 1) * 0.1) ** (-staleness_gamma)

            for client_idx, (X_i, y_i) in enumerate(client_datasets):
                # Instantiate client model matching global weights
                local_model = model_service.create_model(dp_compatible=True)
                local_model = model_service.set_parameters(local_model, global_weights)

                # Train genuine local PyTorch model with FedProx proximal regularization
                local_model, _, _ = model_service.train_local(
                    model=local_model,
                    X_train=X_i,
                    y_train=y_i,
                    epochs=local_epochs,
                    batch_size=batch_size,
                    learning_rate=lr,
                    global_weights=global_weights,
                    fedprox_mu=fedprox_mu,
                )
                updated_weights = model_service.get_parameters(local_model)

                # Calibrate Differential Privacy noise perturbation on local weights
                dp_noise = rng.normal(
                    0.0,
                    (dp_noise_multiplier * dp_clip_norm) / np.sqrt(max(len(X_i), 1)),
                    len(updated_weights.flat_weights),
                )
                noisy_flat = (np.array(updated_weights.flat_weights) + dp_noise).tolist()
                noisy_weights = ModelWeights(
                    layer_shapes=updated_weights.layer_shapes,
                    flat_weights=noisy_flat,
                )

                client_updates.append(noisy_weights)
                # Attenuate sample influence by staleness factor
                effective_samples = max(1, int(len(X_i) * staleness_weight))
                client_samples.append(effective_samples)

            # Aggregate round updates via sample-weighted consensus
            global_weights = fl_engine.aggregate_parameters(client_updates, client_samples)
            global_model = model_service.set_parameters(global_model, global_weights)

            # Evaluate round performance on real holdout validation dataset
            eval_metrics = model_service.evaluate(global_model, X_val, y_val)
            round_auc = float(eval_metrics.get("auc_roc", 0.5))
            final_score = round_auc

            # Report step metric to Optuna for early MedianPruner decisions
            trial.report(round_auc, round_idx)
            if trial.should_prune():
                logger.info(
                    "Trial %d pruned at round %d (validation AUC: %.4f)",
                    trial.number,
                    round_idx,
                    round_auc,
                )
                raise optuna.TrialPruned()

        return final_score

    def run_optimization(self, n_trials: int = 5, timeout: float | None = 60.0) -> dict[str, Any]:
        """Run Optuna optimization study over n_trials with graceful pruning handling."""
        start_time = time.perf_counter()
        logger.info(
            "Starting Optuna FL hyperparameter optimization study '%s' (Dirichlet alpha=%.2f, trials=%d)...",
            self.study_name,
            self.dirichlet_alpha,
            n_trials,
        )

        self.study.optimize(self.objective, n_trials=n_trials, timeout=timeout)

        duration = (time.perf_counter() - start_time) * 1000

        default_params = {
            "learning_rate": 0.01,
            "local_epochs": 2,
            "dp_clip_norm": 1.0,
            "dp_noise_multiplier": 0.5,
            "staleness_gamma": 0.5,
            "fedprox_mu": 0.01,
            "batch_size": 32,
        }

        # Safe best trial extraction (handles all-pruned or empty study cases)
        completed_trials = [
            t for t in self.study.trials if t.state == optuna.trial.TrialState.COMPLETE
        ]
        if completed_trials:
            best_t = self.study.best_trial
            best_trial_number = best_t.number
            best_value = float(best_t.value) if best_t.value is not None else 0.5
            best_params = best_t.params if best_t.params else default_params
        elif self.study.trials:
            best_t = max(
                self.study.trials,
                key=lambda t: max(t.intermediate_values.values()) if t.intermediate_values else -1.0,
            )
            best_trial_number = best_t.number
            best_value = (
                max(best_t.intermediate_values.values()) if best_t.intermediate_values else 0.5
            )
            best_params = best_t.params if best_t.params else default_params
        else:
            best_trial_number = -1
            best_value = 0.5
            best_params = default_params

        try:
            if len(completed_trials) >= 2:
                importances = optuna.importance.get_param_importances(self.study)
            else:
                importances = {}
        except Exception as exc:
            logger.debug("Could not compute Optuna parameter importances: %s", exc)
            importances = {}

        results = {
            "study_name": self.study_name,
            "dirichlet_alpha": self.dirichlet_alpha,
            "best_trial_number": best_trial_number,
            "best_value": round(best_value, 4),
            "best_params": best_params,
            "param_importances": importances,
            "total_trials": len(self.study.trials),
            "completed_trials": len(completed_trials),
            "pruned_trials": len(
                [t for t in self.study.trials if t.state == optuna.trial.TrialState.PRUNED]
            ),
            "duration_ms": round(duration, 2),
        }

        logger.info(
            "Completed Optuna study '%s' in %.2fms. Best trial #%d (AUC: %.4f)",
            self.study_name,
            duration,
            best_trial_number,
            best_value,
        )

        return results
