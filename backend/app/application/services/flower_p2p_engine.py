"""Flower Serverless Peer-to-Peer (P2P) Federated Learning Engine.

Executes decentralized, serverless federated learning rounds using peer gossip weight mixing
over Ring or Fully-Connected Mesh network topologies. Eliminates central coordinator
server dependencies and centralized parameter aggregation single points of failure (SPOF).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from app.application.services.model_service import FraudDetectionModel, ModelService

logger = logging.getLogger(__name__)


class P2PTopologyType(StrEnum):
    RING = "RING"
    MESH = "MESH"


@dataclass(frozen=True)
class P2PGossipResult:
    """Container for Flower P2P Round execution metrics and convergence state."""

    round_id: int
    peer_ids: list[str]
    topology: P2PTopologyType
    avg_loss: float
    convergence_mae: float
    duration_ms: float
    per_peer_loss: dict[str, float] = field(default_factory=dict)


def _weights_to_ndarrays(model: FraudDetectionModel) -> list[np.ndarray]:
    """Extract model parameters as a list of NumPy ndarrays."""
    return [param.data.cpu().numpy().copy() for param in model.parameters()]


def _ndarrays_to_model(
    model: FraudDetectionModel,
    ndarrays: list[np.ndarray],
    device: Any,
) -> FraudDetectionModel:
    """Load a list of NumPy ndarrays into PyTorch model parameters."""
    import torch

    for param, arr in zip(model.parameters(), ndarrays, strict=False):
        param.data = torch.FloatTensor(arr).to(device)
    return model


class P2PGossipStrategy:
    """Peer gossip weight exchange, validation, and consensus mixing engine."""

    @staticmethod
    def build_ring_adjacency(peer_ids: list[str]) -> dict[str, list[str]]:
        """Builds a bidirectional 1D Ring topology adjacency map with self-loops."""
        n = len(peer_ids)
        if n == 0:
            return {}
        if n == 1:
            return {peer_ids[0]: [peer_ids[0]]}

        adj: dict[str, list[str]] = {}
        for i, peer in enumerate(peer_ids):
            prev_peer = peer_ids[(i - 1) % n]
            next_peer = peer_ids[(i + 1) % n]
            adj[peer] = sorted(list({peer, prev_peer, next_peer}))
        return adj

    @staticmethod
    def build_mesh_adjacency(peer_ids: list[str]) -> dict[str, list[str]]:
        """Builds a fully-connected mesh topology adjacency map with self-loops."""
        return {peer: list(peer_ids) for peer in peer_ids}

    @staticmethod
    def mix_peer_weights(
        peer_weights: dict[str, list[np.ndarray]],
        adjacency: dict[str, list[str]],
        defense: str = "none",
        metropolis_hastings: bool = False,
    ) -> dict[str, list[np.ndarray]]:
        """Executes peer gossip weight averaging across adjacent peer nodes.

        Supports degree-normalized averaging, Metropolis-Hastings doubly stochastic weights,
        and Byzantine-resilient coordinate-wise median or trimmed mean.
        Filters out non-finite (NaN/Inf) weights and layer dimension mismatches.
        """
        updated_weights: dict[str, list[np.ndarray]] = {}

        for peer_id, neighbors in adjacency.items():
            if peer_id not in peer_weights:
                continue

            local_layers = peer_weights[peer_id]
            expected_shapes = [layer.shape for layer in local_layers]

            # Validate neighbors: finite values, layer counts, and matching shapes
            valid_neighbors: list[str] = []
            for n in neighbors:
                if n not in peer_weights:
                    continue
                n_layers = peer_weights[n]
                if len(n_layers) != len(local_layers):
                    logger.warning("[P2P Gossip] Peer %s rejected: layer count mismatch", n)
                    continue
                if any(
                    layer.shape != exp_shape
                    for layer, exp_shape in zip(n_layers, expected_shapes, strict=False)
                ):
                    logger.warning("[P2P Gossip] Peer %s rejected: shape mismatch", n)
                    continue
                if any(not np.all(np.isfinite(layer)) for layer in n_layers):
                    logger.warning(
                        "[P2P Gossip] Peer %s rejected: non-finite weights (NaN/Inf detected)", n
                    )
                    continue
                valid_neighbors.append(n)

            if not valid_neighbors:
                updated_weights[peer_id] = [layer.copy() for layer in local_layers]
                continue

            # Byzantine defense: coordinate-wise median
            if defense == "coordinate_wise_median" and len(valid_neighbors) >= 3:
                mixed_layers: list[np.ndarray] = []
                for layer_idx in range(len(local_layers)):
                    stacked = np.stack(
                        [peer_weights[n][layer_idx] for n in valid_neighbors],
                        axis=0,
                    )
                    mixed_layers.append(np.median(stacked, axis=0).astype(np.float32))
                updated_weights[peer_id] = mixed_layers
                continue

            # Byzantine defense: trimmed mean (trim 1 lowest and 1 highest per coordinate)
            if defense == "trimmed_mean" and len(valid_neighbors) >= 3:
                mixed_layers = []
                for layer_idx in range(len(local_layers)):
                    stacked = np.stack(
                        [peer_weights[n][layer_idx] for n in valid_neighbors],
                        axis=0,
                    )
                    sorted_stacked = np.sort(stacked, axis=0)
                    trimmed = sorted_stacked[1:-1]
                    mixed_layers.append(np.mean(trimmed, axis=0).astype(np.float32))
                updated_weights[peer_id] = mixed_layers
                continue

            # Metropolis-Hastings doubly stochastic mixing
            if metropolis_hastings and len(valid_neighbors) > 1:
                deg_i = len(adjacency.get(peer_id, []))
                weights_dict: dict[str, float] = {}
                sum_off_diag = 0.0
                for n in valid_neighbors:
                    if n != peer_id:
                        deg_j = len(adjacency.get(n, []))
                        w_ij = 1.0 / (1.0 + max(deg_i, deg_j))
                        weights_dict[n] = w_ij
                        sum_off_diag += w_ij
                weights_dict[peer_id] = max(0.0, 1.0 - sum_off_diag)

                total_w = sum(weights_dict.values())
                if total_w > 0:
                    weights_dict = {k: v / total_w for k, v in weights_dict.items()}

                mixed_layers = [
                    np.zeros_like(layer, dtype=np.float32) for layer in local_layers
                ]
                for n, weight in weights_dict.items():
                    for layer_idx, layer_arr in enumerate(peer_weights[n]):
                        mixed_layers[layer_idx] += (layer_arr * weight).astype(np.float32)
                updated_weights[peer_id] = mixed_layers
                continue

            # Standard degree-normalized averaging
            num_neighbors = len(valid_neighbors)
            mixed_layers = [
                np.zeros_like(layer, dtype=np.float32) for layer in local_layers
            ]
            for neighbor in valid_neighbors:
                for layer_idx, layer_arr in enumerate(peer_weights[neighbor]):
                    mixed_layers[layer_idx] += layer_arr.astype(np.float32)

            mixed_layers = [(layer / num_neighbors).astype(np.float32) for layer in mixed_layers]
            updated_weights[peer_id] = mixed_layers

        return updated_weights

    @staticmethod
    def calculate_convergence_mae(peer_weights: dict[str, list[np.ndarray]]) -> float:
        """Calculates mean absolute peer divergence across all pairwise peer weights."""
        peers = list(peer_weights.keys())
        n = len(peers)
        if n <= 1:
            return 0.0

        total_diff = 0.0
        pair_count = 0

        for i in range(n):
            for j in range(i + 1, n):
                p1, p2 = peers[i], peers[j]
                w1_list, w2_list = peer_weights[p1], peer_weights[p2]
                layer_maes = [
                    float(np.mean(np.abs(w1 - w2)))
                    for w1, w2 in zip(w1_list, w2_list, strict=False)
                ]
                total_diff += float(np.mean(layer_maes))
                pair_count += 1

        return total_diff / pair_count if pair_count > 0 else 0.0


class FlowerP2PEngine:
    """Orchestrates serverless Flower P2P training rounds without a central server."""

    def __init__(self, model_service: ModelService | None = None) -> None:
        self.model_service = model_service

    def run_p2p_federated_round(
        self,
        peer_data: dict[str, dict[str, np.ndarray]],
        num_rounds: int = 1,
        topology: P2PTopologyType = P2PTopologyType.RING,
        dp_enabled: bool = False,
        dp_epsilon: float = 2.0,
        dp_delta: float = 1e-5,
        dp_max_grad_norm: float = 1.0,
        local_epochs: int = 1,
        learning_rate: float = 0.001,
        batch_size: int = 64,
        byzantine_defense: str = "none",
        byzantine_bank_id: str | None = None,
        byzantine_scale: float = 5.0,
        metropolis_hastings: bool = False,
    ) -> list[P2PGossipResult]:
        """Executes serverless peer-to-peer federated learning rounds with genuine PyTorch training.

        Args:
            peer_data: Dict mapping peer_id to train/test datasets.
            num_rounds: Number of P2P gossip training rounds.
            topology: Network topology (RING or MESH).
            dp_enabled: Whether to apply Opacus local Differential Privacy.
            dp_epsilon: Target privacy budget epsilon.
            dp_delta: Target privacy failure probability delta.
            dp_max_grad_norm: Maximum gradient clipping norm for Opacus DP.
            local_epochs: Epochs per local training step.
            learning_rate: SGD learning rate.
            batch_size: Mini-batch size.
            byzantine_defense: Defense algorithm ('none', 'coordinate_wise_median', 'trimmed_mean').
            byzantine_bank_id: Bank ID acting maliciously (poisoning weights).
            byzantine_scale: Poisoning scaling factor for malicious peer.
            metropolis_hastings: Whether to use Metropolis-Hastings mixing matrix.

        Returns:
            List of P2PGossipResult metrics per round.
        """
        peer_ids = sorted(list(peer_data.keys()))
        if not peer_ids:
            return []

        model_service = self.model_service
        if model_service is None:
            from app.application.services.model_service import ModelService
            from app.config import get_settings

            model_service = ModelService(get_settings())

        first_peer_data = peer_data[peer_ids[0]]
        x_train_sample = first_peer_data.get("X_train")
        input_dim = (
            int(x_train_sample.shape[1])
            if x_train_sample is not None and len(x_train_sample.shape) > 1
            else 10
        )

        adjacency = (
            P2PGossipStrategy.build_ring_adjacency(peer_ids)
            if topology == P2PTopologyType.RING
            else P2PGossipStrategy.build_mesh_adjacency(peer_ids)
        )

        # Initialize reference PyTorch model so all peers begin from synchronized architecture
        ref_model = model_service.create_model(input_dim=input_dim, dp_compatible=dp_enabled)
        initial_weights = _weights_to_ndarrays(ref_model)

        peer_weights: dict[str, list[np.ndarray]] = {
            peer: [layer.copy() for layer in initial_weights] for peer in peer_ids
        }
        peer_models: dict[str, FraudDetectionModel] = {
            peer: model_service.create_model(input_dim=input_dim, dp_compatible=dp_enabled)
            for peer in peer_ids
        }

        results: list[P2PGossipResult] = []

        for r in range(1, num_rounds + 1):
            start_time = time.perf_counter()

            # 1. Local real PyTorch training on each peer node
            per_peer_loss: dict[str, float] = {}
            for peer in peer_ids:
                p_model = peer_models[peer]
                _ndarrays_to_model(p_model, peer_weights[peer], model_service.device)

                data = peer_data[peer]
                x_tr = data.get("X_train")
                y_tr = data.get("y_train")

                if x_tr is not None and len(x_tr) > 0 and y_tr is not None and len(y_tr) > 0:
                    if dp_enabled:
                        p_model, loss_hist, _ = model_service.train_local_with_opacus(
                            p_model,
                            x_tr,
                            y_tr,
                            target_epsilon=dp_epsilon,
                            target_delta=dp_delta,
                            max_grad_norm=dp_max_grad_norm,
                            epochs=local_epochs,
                            learning_rate=learning_rate,
                            batch_size=batch_size,
                        )
                    else:
                        p_model, loss_hist, _ = model_service.train_local(
                            p_model,
                            x_tr,
                            y_tr,
                            epochs=local_epochs,
                            learning_rate=learning_rate,
                            batch_size=batch_size,
                        )
                    loss = (
                        float(loss_hist[-1])
                        if loss_hist
                        else float(model_service.evaluate(p_model, x_tr, y_tr)["loss"])
                    )
                else:
                    loss = 0.0

                updated_layers = _weights_to_ndarrays(p_model)

                # Byzantine poisoning injection simulation
                if byzantine_bank_id == peer:
                    logger.warning("[Flower P2P] Simulating Byzantine poisoning on peer: %s", peer)
                    updated_layers = [
                        (-byzantine_scale * layer).astype(np.float32) for layer in updated_layers
                    ]
                    loss = loss * byzantine_scale

                peer_weights[peer] = updated_layers
                per_peer_loss[peer] = loss

            # 2. Peer gossip weight exchange & mixing over adjacency topology
            peer_weights = P2PGossipStrategy.mix_peer_weights(
                peer_weights,
                adjacency,
                defense=byzantine_defense,
                metropolis_hastings=metropolis_hastings,
            )

            # 3. Calculate network convergence MAE
            convergence_mae = P2PGossipStrategy.calculate_convergence_mae(peer_weights)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            avg_loss = (
                sum(per_peer_loss.values()) / len(per_peer_loss) if per_peer_loss else 0.0
            )

            res = P2PGossipResult(
                round_id=r,
                peer_ids=peer_ids,
                topology=topology,
                avg_loss=avg_loss,
                convergence_mae=convergence_mae,
                duration_ms=duration_ms,
                per_peer_loss=per_peer_loss,
            )
            results.append(res)

            logger.info(
                "[Flower P2P] Round %d/%d (%s) | Avg Loss: %.4f | Convergence MAE: %.6f | Duration: %.1fms",
                r,
                num_rounds,
                topology.value,
                avg_loss,
                convergence_mae,
                duration_ms,
            )

        return results

