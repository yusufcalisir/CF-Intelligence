"""Flower FL framework adapter.

Provides an alternative FL engine using the Flower (flwr.dev) framework's
simulation mode. This demonstrates compatibility with industry-standard
FL tooling while running entirely in-process via Ray.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

import flwr as fl
import numpy as np

if TYPE_CHECKING:
    from app.application.services.model_service import ModelService
    from app.domain.value_objects import SimulationConfig

logger = logging.getLogger(__name__)

# Type for progress callback: (simulation_id, event_type, data)
ProgressCallback = Callable[[str, str, dict[str, Any]], None] | None


def _ray_worker_process_setup_hook() -> None:
    """Setup hook executed inside Ray worker processes on Windows to prevent faulthandler dump on actor exit."""
    import atexit
    import faulthandler
    import os

    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    os.environ["RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO"] = "0"
    try:
        faulthandler.disable()
    except Exception:
        pass
    try:
        import ray._private.worker as rw

        atexit.unregister(rw.shutdown)
    except Exception:
        pass


_FLWR_ACTOR_POOL_PATCHED = False


def _patch_flwr_ray_actor_pool() -> None:
    """Ensure Flower's Ray actor pool terminates actors cleanly without tripping Raylet shutdown faulthandler."""
    global _FLWR_ACTOR_POOL_PATCHED
    if _FLWR_ACTOR_POOL_PATCHED:
        return

    try:
        import flwr.simulation.ray_transport.ray_actor as ra
        from flwr.common.logger import log

        if not getattr(ra, "_cfi_patched", False):

            def _graceful_terminate_all_actors(self: Any) -> None:
                futures = []
                for actor in getattr(self, "pool", []):
                    try:
                        futures.append(actor.terminate.remote())
                    except Exception:
                        pass
                if futures:
                    try:
                        import ray

                        ray.get(futures, timeout=2.0)
                    except Exception:
                        pass
                import time

                time.sleep(0.3)

            setattr(ra.BasicActorPool, "terminate_all_actors", _graceful_terminate_all_actors)

            def _graceful_actor_terminate(self: Any) -> None:
                log(logging.INFO, "Gracefully stopping %s", self.__class__.__name__)
                import ray

                ray.actor.exit_actor()

            setattr(ra.VirtualClientEngineActor, "terminate", _graceful_actor_terminate)
            setattr(ra, "_cfi_patched", True)

        _FLWR_ACTOR_POOL_PATCHED = True
    except Exception:
        pass


def _weights_to_ndarrays(
    model_service: ModelService,
    model: Any,
) -> list[np.ndarray]:
    """Convert model parameters to a list of NumPy arrays (Flower format)."""
    arrays: list[np.ndarray] = []
    if hasattr(model, "parameters") and callable(model.parameters):
        params = model.parameters()
        if isinstance(params, Iterable):
            for param in params:
                if hasattr(param, "data") and hasattr(param.data, "cpu"):
                    arrays.append(param.data.cpu().numpy().copy())
                elif isinstance(param, np.ndarray):
                    arrays.append(param.copy())
    return arrays


def _ndarrays_to_model(
    model_service: ModelService,
    model: Any,
    ndarrays: list[np.ndarray],
) -> Any:
    """Load a list of NumPy arrays into a PyTorch model."""
    import torch

    if hasattr(model, "parameters") and callable(model.parameters):
        device = getattr(model_service, "device", "cpu")
        params = model.parameters()
        if isinstance(params, Iterable):
            for param, arr in zip(params, ndarrays, strict=False):
                if hasattr(param, "data") and isinstance(arr, np.ndarray):
                    param.data = torch.tensor(arr, dtype=torch.float32, device=device)
    return model


def _cleanup_pytorch_memory() -> None:
    """Explicitly garbage-collect and flush PyTorch CUDA/MPS cache to prevent GPU/RAM memory bloat."""
    import gc

    gc.collect()
    with contextlib.suppress(Exception):
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif (
            hasattr(torch, "mps")
            and hasattr(torch.mps, "empty_cache")
            and hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            torch.mps.empty_cache()


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
                "bank_id": self.bank_id,
                "loss": float(loss_hist[-1]) if loss_hist else 0.0,
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
            metrics = {
                "bank_id": self.bank_id,
                "loss": float(loss_hist[-1]) if loss_hist else 0.0,
            }

        updated_params = _weights_to_ndarrays(self.model_service, self.model)
        _cleanup_pytorch_memory()
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
        _cleanup_pytorch_memory()
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
        for idx, (client_proxy, fit_res) in enumerate(results):
            metrics = getattr(fit_res, "metrics", {}) or {}
            bid = metrics.get("bank_id")
            if not bid:
                try:
                    cid_idx = int(getattr(client_proxy, "cid", -1))
                    if 0 <= cid_idx < len(self.bank_ids):
                        bid = self.bank_ids[cid_idx]
                    elif 0 <= idx < len(self.bank_ids):
                        bid = self.bank_ids[idx]
                except Exception:
                    if 0 <= idx < len(self.bank_ids):
                        bid = self.bank_ids[idx]
            if bid and bid in self.bank_ids and "loss" in metrics and metrics["loss"] is not None:
                per_bank_loss[bid] = float(metrics["loss"])

        reporting_losses = list(per_bank_loss.values())
        avg_loss = sum(reporting_losses) / len(reporting_losses) if reporting_losses else 0.0
        reporting_bank_ids = list(per_bank_loss.keys())
        dropped_bank_ids = [b for b in self.bank_ids if b not in per_bank_loss]

        round_info = {
            "round_number": server_round,
            "global_loss": avg_loss,
            "per_bank_loss": per_bank_loss,
            "participating_bank_ids": reporting_bank_ids,
            "dropped_bank_ids": dropped_bank_ids,
            "aggregation_time_ms": round_duration,
            "round_duration_ms": round_duration,
            "per_bank_samples": {bid: len(self.bank_data[bid]["X_train"]) for bid in self.bank_ids if bid in self.bank_data},
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
            "[Flower] Round %d/%d | avg loss: %.4f, duration: %.0fms",
            server_round,
            self.num_rounds,
            avg_loss,
            round_duration,
        )

        _cleanup_pytorch_memory()
        return aggregated


def _aggregate_flower_metrics(metrics: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate client metrics using weighted average by sample count."""
    total_examples = sum(num_examples for num_examples, _ in metrics)
    if total_examples == 0:
        return {}
    aggregated: dict[str, float] = {}
    for num_examples, m in metrics:
        if isinstance(m, dict):
            for k, v in m.items():
                if isinstance(v, (int, float)):
                    aggregated[k] = aggregated.get(k, 0.0) + (v * num_examples)
    return {k: round(v / total_examples, 4) for k, v in aggregated.items()}


class FlowerFLEngine:
    """Flower-based FL engine using simulation mode."""

    def __init__(self, model_service: ModelService) -> None:
        self.model_service = model_service

    def run_p2p_federated_training(
        self,
        config: SimulationConfig,
        bank_data: dict[str, dict[str, np.ndarray]],
        progress_callback: ProgressCallback = None,
        simulation_id: str = "",
        topology: str = "RING",
    ) -> dict[str, Any]:
        """Execute serverless peer-to-peer federated learning training rounds without a central server."""
        from app.application.services.flower_p2p_engine import (
            FlowerP2PEngine,
            P2PTopologyType,
        )

        p2p_engine = FlowerP2PEngine(model_service=self.model_service)
        topo_enum = (
            P2PTopologyType.MESH if topology.upper() == "MESH" else P2PTopologyType.RING
        )
        dp_enabled = getattr(config, "enable_differential_privacy", False)

        byz_id = (
            getattr(config, "poisoning_bank_id", None)
            if getattr(config, "enable_poisoning_simulation", False)
            else None
        )
        p2p_results = p2p_engine.run_p2p_federated_round(
            peer_data=bank_data,
            num_rounds=config.num_rounds,
            topology=topo_enum,
            dp_enabled=dp_enabled,
            dp_epsilon=getattr(config, "dp_epsilon", 2.0),
            dp_delta=getattr(config, "dp_delta", 1e-5),
            dp_max_grad_norm=getattr(config, "dp_max_grad_norm", 1.0),
            local_epochs=getattr(config, "local_epochs", 1),
            learning_rate=getattr(config, "learning_rate", 0.001),
            batch_size=getattr(config, "batch_size", 64),
            byzantine_defense=getattr(config, "byzantine_defense", "none"),
            byzantine_bank_id=byz_id,
            byzantine_scale=getattr(config, "poisoning_scale", 5.0),
        )

        round_results = [
            {
                "round_number": res.round_id,
                "global_loss": res.avg_loss,
                "convergence_mae": res.convergence_mae,
                "per_bank_loss": res.per_peer_loss,
                "participating_bank_ids": res.peer_ids,
                "dropped_bank_ids": [],
                "aggregation_time_ms": res.duration_ms,
                "round_duration_ms": res.duration_ms,
                "topology": res.topology.value,
            }
            for res in p2p_results
        ]

        if progress_callback and round_results:
            last = round_results[-1]
            progress_callback(
                simulation_id,
                "round_complete",
                {
                    "round": len(round_results),
                    "total": config.num_rounds,
                    "loss": last["global_loss"],
                    "participants": list(bank_data.keys()),
                    "dropped": [],
                    "duration_ms": last["round_duration_ms"],
                    "privacy_budget": getattr(config, "dp_epsilon", 0.0),
                    "serverless_p2p": True,
                },
            )

        return {
            "status": "SUCCESS",
            "rounds": round_results,
            "final_loss": round_results[-1]["global_loss"] if round_results else 0.0,
            "engine": "FlowerP2PEngine (Serverless)",
        }

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

        sim_config = config
        bank_ids = list(bank_data.keys())
        model_service = self.model_service
        use_opacus_dp = getattr(sim_config, "dp_mode", "post_hoc") == "opacus" and getattr(
            sim_config, "enable_differential_privacy", False
        )

        round_results: list[dict[str, Any]] = []

        def client_fn(context: Any) -> fl.client.Client:
            _ray_worker_process_setup_hook()
            if (
                hasattr(context, "node_config")
                and isinstance(context.node_config, dict)
                and "partition-id" in context.node_config
            ):
                cid_str = str(context.node_config["partition-id"])
            elif hasattr(context, "node_id") and isinstance(context.node_id, int):
                cid_str = str(context.node_id % len(bank_ids))
            elif hasattr(context, "partition_id"):
                cid_str = str(context.partition_id)
            else:
                cid_str = str(context)

            try:
                idx = int(cid_str)
            except (ValueError, TypeError):
                idx = 0

            bank_id = bank_ids[idx % len(bank_ids)]
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
            fit_metrics_aggregation_fn=_aggregate_flower_metrics,
            evaluate_metrics_aggregation_fn=_aggregate_flower_metrics,
        )

        logger.info(
            "[Flower] Starting simulation: %d clients, %d rounds",
            len(bank_ids),
            sim_config.num_rounds,
        )

        # When native simulation is explicitly configured via FLWR_SIMULATION_NATIVE,
        # verify capability requirements before dispatching to native execution.
        if os.environ.get("FLWR_SIMULATION_NATIVE") == "1":
            if getattr(sim_config, "require_flower_backend", False) or not getattr(
                sim_config, "allow_native_fallback", True
            ):
                raise RuntimeError(
                    "Required execution backend 'FLOWER_RAY' failed to initialize: Native simulation "
                    "requested via FLWR_SIMULATION_NATIVE but Flower backend is strictly required by configuration."
                )
            if getattr(sim_config, "enable_secure_aggregation", False):
                raise RuntimeError(
                    "Required execution backend 'FLOWER_RAY' failed: Native simulation requested via "
                    "FLWR_SIMULATION_NATIVE does not implement cryptographically masked Secure Aggregation (SecAgg). "
                    "Execution rejected to prevent unencrypted parameter aggregation."
                )
            return self._run_native_production_fl(
                config=sim_config,
                bank_data=bank_data,
                global_model=global_model,
                progress_callback=progress_callback,
                simulation_id=simulation_id,
                use_opacus_dp=use_opacus_dp,
                backend_provenance="NATIVE_FEDAVG",
            )

        try:
            import ray

            backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            current_pp = os.environ.get("PYTHONPATH", "")
            if backend_dir not in current_pp:
                os.environ["PYTHONPATH"] = (
                    f"{backend_dir}{os.pathsep}{current_pp}" if current_pp else backend_dir
                )

            _patch_flwr_ray_actor_pool()
            os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
            os.environ["RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO"] = "0"

            if ray.is_initialized():
                ray.shutdown()
            ray.init(
                object_store_memory=128 * 1024 * 1024,
                num_cpus=2,
                include_dashboard=False,
                ignore_reinit_error=True,
                logging_level=logging.ERROR,
                _system_config={
                    "object_store_full_delay_ms": 100,
                },
                runtime_env={
                    "sys_paths": [backend_dir],
                    "env_vars": {
                        "PYTHONPATH": os.environ["PYTHONPATH"],
                        "RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO": "0",
                        "KMP_DUPLICATE_LIB_OK": "TRUE",
                    },
                    "worker_process_setup_hook": _ray_worker_process_setup_hook,
                },
            )

            try:
                # Modern Flower (Flower >= 1.10+) App Simulation Runtime
                # Replaces deprecated start_simulation() with ClientApp, ServerApp & _run_simulation
                from flwr.client import ClientApp
                from flwr.compat.server import ServerAppComponents
                from flwr.server import Server, ServerConfig, ServerApp
                from flwr.server.client_manager import SimpleClientManager
                from flwr.simulation.app import _run_simulation
                from flwr.supercore.telemetry import EventType

                class _RecordingServer(Server):
                    """Server subclass to capture simulation History cleanly during fit execution."""

                    def __init__(self, *args: Any, **kwargs: Any) -> None:
                        super().__init__(*args, **kwargs)
                        self.simulation_history: Any = None

                    def fit(self, num_rounds: int, timeout: float | None) -> tuple[Any, float]:
                        hist, elapsed = super().fit(num_rounds, timeout)
                        self.simulation_history = hist
                        return hist, elapsed

                recording_server = _RecordingServer(
                    client_manager=SimpleClientManager(),
                    strategy=strategy,
                )
                client_app = ClientApp(client_fn=client_fn)
                server_app = ServerApp(
                    server_fn=lambda _ctx: ServerAppComponents(
                        server=recording_server,
                        config=ServerConfig(num_rounds=sim_config.num_rounds),
                    )
                )

                _run_simulation(
                    num_supernodes=len(bank_ids),
                    client_app=client_app,
                    server_app=server_app,
                    app_dir=backend_dir,
                    exit_event=EventType.START_SIMULATION_LEAVE,
                    backend_config={"client_resources": {"num_cpus": 0.5, "num_gpus": 0.0}},
                )
                history = recording_server.simulation_history
                if history is None:
                    history = fl.server.history.History()
            except (ImportError, AttributeError):
                from flwr.simulation import start_simulation

                history = start_simulation(
                    client_fn=client_fn,
                    num_clients=len(bank_ids),
                    config=fl.server.ServerConfig(num_rounds=sim_config.num_rounds),
                    strategy=strategy,
                    client_resources={"num_cpus": 0.5, "num_gpus": 0.0},
                )

            if ray.is_initialized():
                time.sleep(0.2)
                ray.shutdown()

            logger.info(
                "[Flower] Simulation complete. History losses: %s",
                history.losses_distributed,
            )

            return {
                "rounds": round_results,
                "history": history,
                "execution_backend": "FLOWER_RAY",
            }
        except Exception as exc:
            # Check backend authorization and capability requirements (AGENTS.md Rules 5 & 11)
            if getattr(sim_config, "require_flower_backend", False) or not getattr(
                sim_config, "allow_native_fallback", True
            ):
                raise RuntimeError(
                    f"Required execution backend 'FLOWER_RAY' failed to initialize: {exc}. "
                    "Fallback to native simulation is unauthorized by configuration."
                ) from exc

            if getattr(sim_config, "enable_secure_aggregation", False):
                raise RuntimeError(
                    f"Required execution backend 'FLOWER_RAY' failed: {exc}. Native production fallback "
                    "does not implement cryptographically masked Secure Aggregation (SecAgg). "
                    "Execution rejected to prevent unencrypted parameter aggregation."
                ) from exc

            logger.warning(
                "[Flower] Simulation runtime initialization failed: %s. Executing authorized native fallback...",
                exc,
            )
            return self._run_native_production_fl(
                config=sim_config,
                bank_data=bank_data,
                global_model=global_model,
                progress_callback=progress_callback,
                simulation_id=simulation_id,
                use_opacus_dp=use_opacus_dp,
                backend_provenance="NATIVE_FEDAVG_FALLBACK",
                fallback_reason=str(exc),
            )
        finally:
            try:
                import ray

                if ray.is_initialized():
                    time.sleep(0.2)
                    ray.shutdown()
            except Exception:
                pass

    def _run_native_production_fl(
        self,
        config: SimulationConfig,
        bank_data: dict[str, dict[str, np.ndarray]],
        global_model: Any,
        progress_callback: ProgressCallback,
        simulation_id: str,
        use_opacus_dp: bool,
        backend_provenance: str = "NATIVE_FEDAVG",
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        """Zero-mock native production FL execution when external Ray cluster is unavailable.

        Executes genuine PyTorch model training across client partitions and aggregates
        parameters using exact Federated Averaging.
        """
        import numpy as np

        bank_ids = list(bank_data.keys())
        fallback_rounds: list[dict[str, Any]] = []

        for r in range(1, config.num_rounds + 1):
            round_start = time.perf_counter()
            per_bank_loss: dict[str, float] = {}
            client_weights: list[list[np.ndarray]] = []
            client_samples: list[int] = []

            for bid in bank_ids:
                data = bank_data[bid]
                x_train = data.get("X_train")
                y_train = data.get("y_train")
                n_samples = len(x_train) if x_train is not None else 0
                client_samples.append(n_samples)

                # Initialize local client model with current global parameters
                client_model = self.model_service.create_model(dp_compatible=use_opacus_dp)
                if hasattr(global_model, "state_dict") and hasattr(client_model, "load_state_dict"):
                    with contextlib.suppress(Exception):
                        client_model.load_state_dict(global_model.state_dict())

                if (
                    x_train is not None
                    and len(x_train) > 0
                    and y_train is not None
                    and len(y_train) > 0
                ):
                    res: Any
                    if use_opacus_dp:
                        res = self.model_service.train_local_with_opacus(
                            client_model,
                            x_train,
                            y_train,
                            target_epsilon=config.dp_epsilon,
                            target_delta=config.dp_delta,
                            max_grad_norm=config.dp_max_grad_norm,
                            epochs=config.local_epochs,
                            learning_rate=config.learning_rate,
                            batch_size=config.batch_size,
                        )
                    else:
                        res = self.model_service.train_local(
                            client_model,
                            x_train,
                            y_train,
                            epochs=config.local_epochs,
                            learning_rate=config.learning_rate,
                            batch_size=config.batch_size,
                        )

                    if isinstance(res, tuple):
                        if len(res) >= 2:
                            client_model, loss_hist = res[0], res[1]
                        elif len(res) == 1:
                            client_model, loss_hist = res[0], [0.0]
                        else:
                            loss_hist = [0.0]
                    else:
                        client_model = res
                        loss_hist = [0.0]

                    if loss_hist and isinstance(loss_hist, (list, tuple)) and len(loss_hist) > 0:
                        try:
                            b_loss = float(loss_hist[-1])
                        except (ValueError, TypeError):
                            b_loss = 0.0
                    else:
                        try:
                            eval_res = self.model_service.evaluate(client_model, x_train, y_train)
                            b_loss = float(eval_res.get("loss", 0.0) if isinstance(eval_res, dict) else 0.0)
                        except Exception:
                            b_loss = 0.0
                else:
                    b_loss = 0.0

                per_bank_loss[bid] = b_loss
                client_weights.append(_weights_to_ndarrays(self.model_service, client_model))

            # Aggregate client weights via FedAvg
            total_samples = sum(client_samples)
            if total_samples > 0 and client_weights and any(len(w) > 0 for w in client_weights):
                first_valid = next(w for w in client_weights if len(w) > 0)
                avg_weights = [
                    np.zeros_like(layer, dtype=np.float32) for layer in first_valid
                ]
                for c_w, c_s in zip(client_weights, client_samples, strict=False):
                    if not c_w:
                        continue
                    weight_factor = c_s / total_samples
                    for l_idx, layer in enumerate(c_w):
                        avg_weights[l_idx] += layer.astype(np.float32) * weight_factor

                _ndarrays_to_model(self.model_service, global_model, avg_weights)

            round_duration = (time.perf_counter() - round_start) * 1000.0
            avg_loss = (
                sum(per_bank_loss.values()) / len(per_bank_loss) if per_bank_loss else 0.0
            )

            round_info = {
                "round_number": r,
                "global_loss": avg_loss,
                "per_bank_loss": per_bank_loss,
                "participating_bank_ids": bank_ids,
                "dropped_bank_ids": [],
                "aggregation_time_ms": round_duration,
                "round_duration_ms": round_duration,
                "per_bank_samples": {
                    bid: len(bank_data[bid].get("X_train", [])) for bid in bank_ids
                },
            }
            fallback_rounds.append(round_info)

            if progress_callback:
                progress_callback(
                    simulation_id,
                    "round_complete",
                    {
                        "round": r,
                        "total": config.num_rounds,
                        "loss": avg_loss,
                        "participants": bank_ids,
                        "dropped": [],
                        "duration_ms": round_duration,
                        "privacy_budget": (
                            getattr(config, "dp_epsilon", 0.0) if use_opacus_dp else 0.0
                        ),
                    },
                )

            _cleanup_pytorch_memory()

        _cleanup_pytorch_memory()
        res = {
            "rounds": fallback_rounds,
            "history": None,
            "execution_backend": backend_provenance,
        }
        if fallback_reason is not None:
            res["fallback_reason"] = fallback_reason
        return res
