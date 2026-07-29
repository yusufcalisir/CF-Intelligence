"""Flower FL framework adapter.

Provides an alternative FL engine using the Flower (flwr.dev) framework's
simulation mode. This demonstrates compatibility with industry-standard
FL tooling while running entirely in-process via Ray.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import flwr as fl

if TYPE_CHECKING:
    import numpy as np

    from app.application.services.model_service import ModelService
    from app.domain.value_objects import SimulationConfig

logger = logging.getLogger(__name__)

# Type for progress callback: (simulation_id, event_type, data)
ProgressCallback = Callable[[str, str, dict[str, Any]], None] | None


def _weights_to_ndarrays(
    model_service: ModelService,
    model: Any,
) -> list[np.ndarray]:
    """Convert model parameters to a list of NumPy arrays (Flower format)."""
    arrays: list[np.ndarray] = []
    for param in model.parameters():
        arrays.append(param.data.cpu().numpy().copy())
    return arrays


def _ndarrays_to_model(
    model_service: ModelService,
    model: Any,
    ndarrays: list[np.ndarray],
) -> Any:
    """Load a list of NumPy arrays into a PyTorch model."""
    import torch

    for param, arr in zip(model.parameters(), ndarrays, strict=False):
        param.data = torch.FloatTensor(arr).to(model_service.device)
    return model


class FraudFlowerClient(fl.client.NumPyClient):
    """Flower NumPyClient wrapping our ModelService — defined at top-level for Ray serialization."""

    def __init__(
        self,
        bank_id: str,
        bank_data: dict[str, np.ndarray],
        model_service: ModelService,
        sim_config: SimulationConfig,
        use_opacus_dp: bool,
    ) -> None:
        self.bank_id = bank_id
        self.data = bank_data
        self.model_service = model_service
        self.sim_config = sim_config
        self.use_opacus_dp = use_opacus_dp
        self.model = model_service.create_model(dp_compatible=use_opacus_dp)

    def get_parameters(self, config: dict[str, Any]) -> list[np.ndarray]:  # noqa: A002
        return _weights_to_ndarrays(self.model_service, self.model)

    def fit(
        self,
        parameters: list[np.ndarray],
        config: dict[str, Any],  # noqa: A002
    ) -> tuple[list[np.ndarray], int, dict[str, Any]]:
        _ndarrays_to_model(self.model_service, self.model, parameters)
        n_samples = len(self.data["X_train"])

        if self.use_opacus_dp:
            self.model, loss_hist, epsilon = self.model_service.train_local_with_opacus(
                self.model,
                self.data["X_train"],
                self.data["y_train"],
                target_epsilon=self.sim_config.dp_epsilon,
                target_delta=self.sim_config.dp_delta,
                max_grad_norm=self.sim_config.dp_max_grad_norm,
                epochs=self.sim_config.local_epochs,
                learning_rate=self.sim_config.learning_rate,
                batch_size=self.sim_config.batch_size,
            )
            metrics = {
                "loss": float(loss_hist[-1]) if loss_hist else 0.05,
                "epsilon": float(epsilon),
            }
        else:
            self.model, loss_hist, _ = self.model_service.train_local(
                self.model,
                self.data["X_train"],
                self.data["y_train"],
                epochs=self.sim_config.local_epochs,
                learning_rate=self.sim_config.learning_rate,
                batch_size=self.sim_config.batch_size,
            )
            metrics = {"loss": float(loss_hist[-1]) if loss_hist else 0.05}

        updated_params = _weights_to_ndarrays(self.model_service, self.model)
        return updated_params, n_samples, metrics

    def evaluate(
        self,
        parameters: list[np.ndarray],
        config: dict[str, Any],  # noqa: A002
    ) -> tuple[float, int, dict[str, Any]]:
        _ndarrays_to_model(self.model_service, self.model, parameters)
        eval_result = self.model_service.evaluate(
            self.model,
            self.data["X_test"],
            self.data["y_test"],
        )
        n_samples = len(self.data["X_test"])
        loss = float(eval_result["loss"])
        return (
            loss,
            n_samples,
            {
                "accuracy": float(eval_result["accuracy"]),
                "f1_score": float(eval_result["f1_score"]),
            },
        )


class CallbackFedAvg(fl.server.strategy.FedAvg):
    """FedAvg strategy that fires progress callbacks after each round — defined at top-level."""

    def __init__(
        self,
        bank_ids: list[str],
        bank_data: dict[str, dict[str, np.ndarray]],
        num_rounds: int,
        round_results: list[dict[str, Any]],
        progress_callback: ProgressCallback,
        simulation_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.bank_ids = bank_ids
        self.bank_data = bank_data
        self.num_rounds = num_rounds
        self.round_results = round_results
        self.progress_callback = progress_callback
        self.simulation_id = simulation_id

    def aggregate_fit(
        self,
        server_round: int,
        results: list,
        failures: list,
    ) -> Any:
        round_start = time.perf_counter()
        aggregated = super().aggregate_fit(server_round, results, failures)
        round_duration = (time.perf_counter() - round_start) * 1000

        per_bank_loss: dict[str, float] = {}
        for client_proxy, fit_res in results:
            try:
                cid_idx = int(getattr(client_proxy, "cid", -1))
                if 0 <= cid_idx < len(self.bank_ids):
                    bid = self.bank_ids[cid_idx]
                    metrics = getattr(fit_res, "metrics", {}) or {}
                    per_bank_loss[bid] = float(metrics.get("loss", 0.05))
            except Exception:
                pass

        for bid in self.bank_ids:
            if bid not in per_bank_loss:
                per_bank_loss[bid] = 0.05

        avg_loss = sum(per_bank_loss.values()) / len(per_bank_loss) if per_bank_loss else 0.05

        round_info = {
            "round_number": server_round,
            "global_loss": avg_loss,
            "per_bank_loss": per_bank_loss,
            "participating_bank_ids": self.bank_ids,
            "dropped_bank_ids": [],
            "aggregation_time_ms": round_duration,
            "round_duration_ms": round_duration,
            "per_bank_samples": {bid: len(self.bank_data[bid]["X_train"]) for bid in self.bank_ids},
        }
        self.round_results.append(round_info)

        if self.progress_callback:
            self.progress_callback(
                self.simulation_id,
                "round_complete",
                {
                    "round": server_round,
                    "total": self.num_rounds,
                    "loss": avg_loss,
                    "participants": self.bank_ids,
                    "dropped": [],
                    "duration_ms": round_duration,
                    "privacy_budget": 0.0,
                },
            )

        logger.info(
            "[Flower] Round %d/%d — avg loss: %.4f, duration: %.0fms",
            server_round,
            self.num_rounds,
            avg_loss,
            round_duration,
        )

        return aggregated


class FlowerFLEngine:
    """Flower-based FL engine using simulation mode."""

    def __init__(self, model_service: ModelService) -> None:
        self.model_service = model_service

    def run_federated_training(
        self,
        config: SimulationConfig,
        bank_data: dict[str, dict[str, np.ndarray]],
        global_model: Any,
        progress_callback: ProgressCallback = None,
        simulation_id: str = "",
    ) -> dict[str, Any]:
        """Execute federated training using Flower's simulation engine."""
        from flwr.common import ndarrays_to_parameters
        from flwr.simulation import start_simulation

        sim_config = config
        bank_ids = list(bank_data.keys())
        model_service = self.model_service
        use_opacus_dp = getattr(sim_config, "dp_mode", "post_hoc") == "opacus" and getattr(
            sim_config, "enable_differential_privacy", False
        )

        round_results: list[dict[str, Any]] = []

        def client_fn(cid: str) -> fl.client.Client:
            bank_id = bank_ids[int(cid)]
            return FraudFlowerClient(
                bank_id=bank_id,
                bank_data=bank_data[bank_id],
                model_service=model_service,
                sim_config=sim_config,
                use_opacus_dp=use_opacus_dp,
            ).to_client()

        initial_params = ndarrays_to_parameters(_weights_to_ndarrays(model_service, global_model))

        strategy = CallbackFedAvg(
            bank_ids=bank_ids,
            bank_data=bank_data,
            num_rounds=sim_config.num_rounds,
            round_results=round_results,
            progress_callback=progress_callback,
            simulation_id=simulation_id,
            fraction_fit=1.0,
            fraction_evaluate=1.0,
            min_fit_clients=len(bank_ids),
            min_evaluate_clients=len(bank_ids),
            min_available_clients=len(bank_ids),
            initial_parameters=initial_params,
        )

        logger.info(
            "[Flower] Starting simulation: %d clients, %d rounds",
            len(bank_ids),
            sim_config.num_rounds,
        )

        import ray

        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        current_pp = os.environ.get("PYTHONPATH", "")
        if backend_dir not in current_pp:
            os.environ["PYTHONPATH"] = (
                f"{backend_dir}{os.pathsep}{current_pp}" if current_pp else backend_dir
            )

        if ray.is_initialized():
            ray.shutdown()
        ray.init(
            object_store_memory=100 * 1024 * 1024,
            num_cpus=2,
            include_dashboard=False,
            runtime_env={
                "sys_paths": [backend_dir],
                "env_vars": {"PYTHONPATH": os.environ["PYTHONPATH"]},
            },
        )

        history = start_simulation(
            client_fn=client_fn,
            num_clients=len(bank_ids),
            config=fl.server.ServerConfig(num_rounds=sim_config.num_rounds),
            strategy=strategy,
            client_resources={"num_cpus": 1, "num_gpus": 0.0},
        )

        ray.shutdown()

        logger.info(
            "[Flower] Simulation complete. History losses: %s",
            history.losses_distributed,
        )

        return {
            "rounds": round_results,
            "history": history,
        }
