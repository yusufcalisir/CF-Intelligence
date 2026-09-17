"""Background Redis Pub/Sub Event Listener for Bank Clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis

from app.application.services.data_generator import DataGenerator
from app.domain.value_objects import ModelWeights
from app.presentation.routers.bank_client import (
    _client_state,
    _model_service,
)

if TYPE_CHECKING:
    from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)


class RedisBankClientListener:
    """Listens to FL command events on Redis Pub/Sub channels for a specific bank client."""

    def __init__(self, redis_url: str, bank_id: str) -> None:
        self.redis_url = redis_url
        self.bank_id = bank_id
        self.redis: Redis | None = None
        self.pubsub: PubSub | None = None
        self.is_running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start listening loop task."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.is_running = True

        norm_id = self.bank_id.replace("-", "_")
        channels = [
            f"bank_client_{self.bank_id}_init",
            f"bank_client_{self.bank_id}_train",
            f"bank_client_{self.bank_id}_evaluate",
        ]
        if norm_id != self.bank_id:
            channels.extend([
                f"bank_client_{norm_id}_init",
                f"bank_client_{norm_id}_train",
                f"bank_client_{norm_id}_evaluate",
            ])
        await self.pubsub.subscribe(*channels)
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Redis Event Listener started for bank %s", self.bank_id)

    async def stop(self) -> None:
        """Stop listening task and close connections."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.pubsub:
            await self.pubsub.unsubscribe()
        if self.redis:
            await self.redis.close()
        logger.info("Redis Event Listener stopped for bank %s", self.bank_id)

    async def _listen_loop(self) -> None:
        norm_id = self.bank_id.replace("-", "_")
        init_channels = {f"bank_client_{self.bank_id}_init", f"bank_client_{norm_id}_init"}
        train_channels = {f"bank_client_{self.bank_id}_train", f"bank_client_{norm_id}_train"}
        eval_channels = {f"bank_client_{self.bank_id}_evaluate", f"bank_client_{norm_id}_evaluate"}

        while self.is_running:
            try:
                if self.pubsub is None:
                    await asyncio.sleep(0.5)
                    continue

                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue

                channel = message["channel"]
                data = json.loads(message["data"])

                if channel in init_channels:
                    await self._handle_init(data)
                elif channel in train_channels:
                    await self._handle_train(data)
                elif channel in eval_channels:
                    await self._handle_evaluate(data)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in Redis bank listener loop for %s: %s", self.bank_id, exc)
                await asyncio.sleep(1.0)

    async def _handle_init(self, data: dict[str, Any]) -> None:
        try:
            num_tx = data.get("num_transactions", 1000)
            seed = data.get("seed", 42)

            generator = DataGenerator(seed=seed)
            sizes = {"bank_a": 5000, "bank_b": 3000, "bank_c": 2000}
            sizes[self.bank_id] = num_tx

            # Generate partition
            datasets = generator.generate_bank_datasets(
                bank_a_size=sizes["bank_a"],
                bank_b_size=sizes["bank_b"],
                bank_c_size=sizes["bank_c"],
            )
            df, labels = datasets[self.bank_id]
            X = DataGenerator.encode_features(df)
            y = labels.values

            # Perform train-test split
            from sklearn.model_selection import train_test_split

            try:
                X_train, X_test, y_train, y_test = train_test_split(
                    X,
                    y,
                    test_size=0.2,
                    random_state=42,
                    stratify=y,
                )
            except Exception:
                X_train, X_test, y_train, y_test = train_test_split(
                    X,
                    y,
                    test_size=0.2,
                    random_state=42,
                )

            _client_state.X_train = X_train
            _client_state.y_train = y_train
            _client_state.X_test = X_test
            _client_state.y_test = y_test

            response = {
                "status": "initialized",
                "bank_id": self.bank_id,
                "train_samples": len(X_train),
                "test_samples": len(X_test),
                "correlation_id": data.get("correlation_id"),
            }
        except Exception as exc:
            logger.error("Event init failed: %s", exc)
            response = {
                "status": "failed",
                "error": str(exc),
                "correlation_id": data.get("correlation_id"),
            }

        if self.redis:
            norm_id = self.bank_id.replace("-", "_")
            payload = json.dumps(response)
            await self.redis.publish(f"bank_client_{self.bank_id}_init_response", payload)
            if norm_id != self.bank_id:
                await self.redis.publish(f"bank_client_{norm_id}_init_response", payload)

    async def _handle_train(self, data: dict[str, Any]) -> None:
        try:
            if _client_state.X_train is None or _client_state.y_train is None:
                raise ValueError("Dataset not initialized.")

            weights_data = data["weights"]
            converted_shapes = [tuple(shape) for shape in weights_data["layer_shapes"]]
            input_weights = ModelWeights(
                layer_shapes=converted_shapes,
                flat_weights=weights_data["flat_weights"],
            )

            use_opacus_dp = data.get("enable_dp", False)

            # Run local training
            model = _model_service.create_model(dp_compatible=use_opacus_dp)
            model = _model_service.set_parameters(model, input_weights)

            actual_eps = None
            if use_opacus_dp:
                trained_model, loss_hist, actual_eps = _model_service.train_local_with_opacus(
                    model,
                    _client_state.X_train,
                    _client_state.y_train,
                    target_epsilon=data.get("dp_epsilon", 1.0),
                    target_delta=data.get("dp_delta", 1e-5),
                    max_grad_norm=data.get("dp_max_grad_norm", 1.0),
                    epochs=data.get("epochs", 1),
                    learning_rate=data.get("learning_rate", 0.01),
                    batch_size=data.get("batch_size", 32),
                )
            else:
                trained_model, loss_hist, _ = _model_service.train_local(
                    model,
                    _client_state.X_train,
                    _client_state.y_train,
                    epochs=data.get("epochs", 1),
                    learning_rate=data.get("learning_rate", 0.01),
                    batch_size=data.get("batch_size", 32),
                )

            output_weights = _model_service.get_parameters(trained_model)

            response = {
                "weights": {
                    "layer_shapes": [list(shape) for shape in output_weights.layer_shapes],
                    "flat_weights": output_weights.flat_weights,
                },
                "num_samples": len(_client_state.X_train),
                "loss": float(loss_hist[-1] if loss_hist else 0.0),
                "actual_epsilon": float(actual_eps) if actual_eps is not None else None,
                "correlation_id": data.get("correlation_id"),
            }
        except Exception as exc:
            logger.error("Event train failed: %s", exc)
            response = {"error": str(exc), "correlation_id": data.get("correlation_id")}

        if self.redis:
            norm_id = self.bank_id.replace("-", "_")
            payload = json.dumps(response)
            await self.redis.publish(f"bank_client_{self.bank_id}_train_response", payload)
            if norm_id != self.bank_id:
                await self.redis.publish(f"bank_client_{norm_id}_train_response", payload)

    async def _handle_evaluate(self, data: dict[str, Any]) -> None:
        try:
            if _client_state.X_test is None or _client_state.y_test is None:
                raise ValueError("Dataset not initialized.")

            weights_data = data["weights"]
            converted_shapes = [tuple(shape) for shape in weights_data["layer_shapes"]]
            input_weights = ModelWeights(
                layer_shapes=converted_shapes,
                flat_weights=weights_data["flat_weights"],
            )

            model = _model_service.create_model(dp_compatible=False)
            model = _model_service.set_parameters(model, input_weights)

            eval_result = _model_service.evaluate(
                model,
                _client_state.X_test,
                _client_state.y_test,
            )

            response = {
                "loss": eval_result["loss"],
                "num_samples": len(_client_state.X_test),
                "accuracy": eval_result["accuracy"],
                "precision": eval_result["precision"],
                "recall": eval_result["recall"],
                "f1_score": eval_result["f1_score"],
                "auc_roc": eval_result["auc_roc"],
                "confusion_matrix": eval_result["confusion_matrix"],
                "roc_fpr": eval_result["roc_fpr"],
                "roc_tpr": eval_result["roc_tpr"],
                "roc_thresholds": eval_result["roc_thresholds"],
                "correlation_id": data.get("correlation_id"),
            }
        except Exception as exc:
            logger.error("Event evaluate failed: %s", exc)
            response = {"error": str(exc), "correlation_id": data.get("correlation_id")}

        if self.redis:
            norm_id = self.bank_id.replace("-", "_")
            payload = json.dumps(response)
            await self.redis.publish(f"bank_client_{self.bank_id}_evaluate_response", payload)
            if norm_id != self.bank_id:
                await self.redis.publish(f"bank_client_{norm_id}_evaluate_response", payload)
