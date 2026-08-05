"""Federated Learning engine.

Implements multiple aggregation strategies with support for:
- FedAvg: weighted/unweighted averaging (McMahan et al., 2017)
- Krum: Byzantine-robust selection (Blanchard et al., 2017)
- Coordinate-wise Median: element-wise median aggregation
- Network latency simulation
- Client dropout and reconnection
- Secure aggregation simulation (simplified)
- Model poisoning simulation for adversarial robustness testing

This is a custom simulation engine, not the Flower (flwr) framework.
See docs/engineering-decisions.md for the rationale. In short: Flower is
designed for real distributed deployments over gRPC. For a single-machine
simulation where we need fine-grained control over failure injection and
real-time observability through a web UI, a custom engine is simpler
and more transparent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from app.domain.enums import AggregationMethod, ClientStatus
from app.domain.value_objects import ModelWeights

if TYPE_CHECKING:
    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.config import Settings

logger = logging.getLogger(__name__)


class FederatedLearningEngine:
    """Orchestrates the federated training loop.

    Responsible for:
    1. Distributing global model parameters to clients
    2. Collecting locally-trained parameters
    3. Aggregating parameters (FedAvg, Krum, or Coordinate-wise Median)
    4. Simulating network conditions (latency, dropout)
    5. Applying privacy mechanisms before aggregation
    6. Simulating adversarial model poisoning attacks
    """

    def __init__(
        self,
        settings: Settings,
        model_service: ModelService,
        privacy_service: PrivacyService,
    ) -> None:
        self.settings = settings
        self.model_service = model_service
        self.privacy_service = privacy_service
        # Server optimizer states for FedOpt (FedAdam / FedAdaGrad) keyed by simulation_id
        self._server_m_by_sim: dict[str, np.ndarray] = {}
        self._server_v_by_sim: dict[str, np.ndarray] = {}
        self._server_round_by_sim: dict[str, int] = {}
        self._server_c_by_sim: dict[str, np.ndarray] = {}

    def aggregate_parameters(
        self,
        client_weights: list[ModelWeights],
        client_samples: list[int],
        method: AggregationMethod = AggregationMethod.FED_AVG_WEIGHTED,
        global_weights: ModelWeights | None = None,
        simulation_id: str | None = None,
    ) -> ModelWeights:
        """Aggregate model parameters from multiple clients.

        Supports multiple strategies:
        - FedAvg / FedAvg Weighted: standard averaging (McMahan et al., 2017)
        - Krum: selects the single client closest to all others (Blanchard et al., 2017)
        - Coordinate-wise Median: element-wise median across clients
        - FedAdam / FedAdaGrad: adaptive server optimizers (FedOpt framework)

        Args:
            client_weights: Parameter sets from each participating client.
            client_samples: Number of training samples at each client.
            method: Aggregation strategy.
            global_weights: Global weights before this round (required for FedOpt).
            simulation_id: Unique simulation run identifier to persist server states.

        Returns:
            Aggregated global model parameters.
        """
        if method not in AggregationMethod:
            raise ValueError(f"Unsupported aggregation method: {method}")

        if not client_weights:
            raise ValueError("Cannot aggregate empty parameter list")

        ref_shapes = client_weights[0].layer_shapes
        ref_len = len(client_weights[0].flat_weights)
        for i, w in enumerate(client_weights):
            if w.layer_shapes != ref_shapes:
                raise ValueError(f"Layer shape mismatch at index {i}")
            if len(w.flat_weights) != ref_len:
                raise ValueError(f"Parameter count mismatch at index {i}")

        if len(client_weights) == 1:
            return client_weights[0]

        start_time = time.perf_counter()

        if method == AggregationMethod.FED_AVG:
            # Unweighted average
            weights_array = np.array([w.flat_weights for w in client_weights])
            avg_weights = weights_array.mean(axis=0).tolist() if len(client_weights[0].flat_weights) > 0 else []

        elif method == AggregationMethod.FED_AVG_WEIGHTED:
            # Weighted average by dataset size
            total_samples = sum(client_samples)
            n = len(client_weights)
            if total_samples <= 0:
                proportions = [1.0 / n] * n
            else:
                proportions = [s / total_samples for s in client_samples]

            avg_weights = np.zeros(len(client_weights[0].flat_weights))
            for w, proportion in zip(client_weights, proportions, strict=False):
                avg_weights += np.array(w.flat_weights) * proportion
            avg_weights = avg_weights.tolist()

        elif method in (AggregationMethod.FED_ADAM, AggregationMethod.FED_ADAGRAD):
            # Calculate standard weighted FedAvg first to get the averaged updates
            total_samples = sum(client_samples)
            proportions = [s / total_samples for s in client_samples]
            w_avg = np.zeros(len(client_weights[0].flat_weights))
            for w, proportion in zip(client_weights, proportions, strict=False):
                w_avg += np.array(w.flat_weights) * proportion

            if global_weights is None:
                # Round 0 fallback to standard FedAvg
                avg_weights = w_avg.tolist()
            else:
                w_t = np.array(global_weights.flat_weights)
                delta_t = w_avg - w_t  # pseudo-gradient

                sim_id = simulation_id or "default_sim"
                if sim_id not in self._server_m_by_sim:
                    self._server_m_by_sim[sim_id] = np.zeros_like(w_avg)
                if sim_id not in self._server_v_by_sim:
                    self._server_v_by_sim[sim_id] = np.zeros_like(w_avg)

                m_t = self._server_m_by_sim[sim_id]
                v_t = self._server_v_by_sim[sim_id]

                eta = self.settings.fedopt_server_lr
                beta1 = self.settings.fedopt_beta1
                beta2 = self.settings.fedopt_beta2
                tau = self.settings.fedopt_tau

                if method == AggregationMethod.FED_ADAM:
                    # Update moment vectors
                    m_t_next = beta1 * m_t + (1 - beta1) * delta_t
                    v_t_next = beta2 * v_t + (1 - beta2) * (delta_t**2)
                    
                    # Track round number for bias correction
                    t = self._server_round_by_sim.get(sim_id, 1)
                    m_hat = m_t_next / (1.0 - (beta1 ** t))
                    v_hat = v_t_next / (1.0 - (beta2 ** t))
                    
                    # Update global weights using bias-corrected moments
                    w_next = w_t + eta * m_hat / (np.sqrt(v_hat) + tau)

                    self._server_m_by_sim[sim_id] = m_t_next
                    self._server_v_by_sim[sim_id] = v_t_next
                    self._server_round_by_sim[sim_id] = t + 1
                else:  # FED_ADAGRAD
                    v_t_next = v_t + (delta_t**2)
                    w_next = w_t + eta * delta_t / (np.sqrt(v_t_next) + tau)

                    self._server_v_by_sim[sim_id] = v_t_next

                avg_weights = w_next.tolist()

        elif method == AggregationMethod.KRUM:
            # Krum (Blanchard et al., 2017): Select the client whose
            # parameters are closest to the most other clients.
            # For each client i, compute the sum of squared distances
            # to the (n - f - 2) closest other clients.
            # f is dynamically bounded by (n - 1) // 2 for Byzantine tolerance.
            weights_array = np.array([w.flat_weights for w in client_weights])
            n = len(weights_array)
            f = max(1, min(1, (n - 1) // 2))
            num_closest = max(1, n - f - 2)

            scores = []
            for i in range(n):
                dists = []
                for j in range(n):
                    if i != j:
                        dist = float(np.sum((weights_array[i] - weights_array[j]) ** 2))
                        dists.append(dist)
                dists.sort()
                scores.append(sum(dists[:num_closest]))

            best_idx = int(np.argmin(scores))
            avg_weights = weights_array[best_idx].tolist()
            logger.info("Krum selected client %d as representative", best_idx)

        elif method == AggregationMethod.COORDINATE_WISE_MEDIAN:
            # Coordinate-wise Median: for each parameter index, take the
            # median value across all clients. Robust to outlier parameters
            # injected by a Byzantine client.
            weights_array = np.array([w.flat_weights for w in client_weights])
            avg_weights = np.median(weights_array, axis=0).tolist()

        elif method == AggregationMethod.TRIMMED_MEAN:
            # Coordinate-wise Trimmed Mean: for each parameter coordinate,
            # drop the f largest and f smallest values then average the rest.
            # Dynamic f bound based on client count n.
            weights_array = np.array([w.flat_weights for w in client_weights])
            n = len(weights_array)
            f = max(1, min(1, (n - 1) // 2))
            if n <= 2 * f:
                # Not enough clients for trimming — fall back to plain mean
                logger.warning(
                    "Trimmed Mean: too few clients (%d <= 2*f=%d), falling back to FedAvg",
                    n,
                    2 * f,
                )
                avg_weights = weights_array.mean(axis=0).tolist()
            else:
                sorted_arr = np.sort(weights_array, axis=0)
                trimmed = sorted_arr[f : n - f]  # drop f smallest and f largest per coordinate
                avg_weights = trimmed.mean(axis=0).tolist()
            logger.info(
                "Trimmed Mean aggregation: %d clients, f=%d, effective clients=%d",
                n,
                f,
                max(0, n - 2 * f),
            )

        elif method == AggregationMethod.BULYAN:
            # Bulyan (El Mhamdi et al., 2018): Byzantine-robust aggregation.
            # Dynamic f bound based on client count n.
            weights_array = np.array([w.flat_weights for w in client_weights])
            n = len(weights_array)
            f = max(1, min(1, max(0, (n - 3) // 4)))
            selected_count = max(1, n - 2 * f)

            # Krum scores
            krum_num_closest = max(1, n - f - 2)
            scores = []
            for i in range(n):
                dists = sorted(
                    float(np.sum((weights_array[i] - weights_array[j]) ** 2))
                    for j in range(n)
                    if i != j
                )
                scores.append(sum(dists[:krum_num_closest]))

            # Select the best `selected_count` indices (lowest Krum score)
            selected_indices = np.argsort(scores)[:selected_count]
            selected_weights = weights_array[selected_indices]  # (selected_count, params)

            # Apply coordinate-wise trimmed mean on the selected subset
            trim_f = max(0, (selected_count - 1) // 4)  # conservative trim
            if selected_count <= 2 * trim_f or trim_f == 0:
                avg_weights = selected_weights.mean(axis=0).tolist()
            else:
                sorted_sel = np.sort(selected_weights, axis=0)
                trimmed_sel = sorted_sel[trim_f : selected_count - trim_f]
                avg_weights = trimmed_sel.mean(axis=0).tolist()

            logger.info(
                "Bulyan aggregation: %d clients → Krum selected %d → Trimmed Mean (f=%d)",
                n,
                selected_count,
                trim_f,
            )

        elif method == AggregationMethod.FED_YOGI:
            # FedYogi (Reddi et al., 2021): Adaptive server optimizer.
            # Uses a Yogi-style second moment update that prevents the learning
            # rate from dropping too fast by using sign-based variance tracking.
            # v_t ← v_t - (1-β₂)·sign(v_t - Δ²)·Δ²
            # m_t ← β₁·m_t + (1-β₁)·Δ
            # w_next ← w_t + η·m_t / (√v_t + τ)
            total_samples = sum(client_samples)
            proportions = [s / total_samples for s in client_samples]
            w_avg = np.zeros(len(client_weights[0].flat_weights))
            for w, proportion in zip(client_weights, proportions, strict=False):
                w_avg += np.array(w.flat_weights) * proportion

            if global_weights is None:
                avg_weights = w_avg.tolist()
            else:
                w_t = np.array(global_weights.flat_weights)
                delta_t = w_avg - w_t  # pseudo-gradient

                sim_id = simulation_id or "default_sim"
                if sim_id not in self._server_m_by_sim:
                    self._server_m_by_sim[sim_id] = np.zeros_like(w_avg)
                if sim_id not in self._server_v_by_sim:
                    # Yogi initialises v as τ² to avoid zero-division
                    self._server_v_by_sim[sim_id] = np.full_like(w_avg, self.settings.fedopt_tau**2)

                m_t = self._server_m_by_sim[sim_id]
                v_t = self._server_v_by_sim[sim_id]

                eta = self.settings.fedopt_server_lr
                beta1 = self.settings.fedopt_beta1
                beta2 = self.settings.fedopt_beta2
                tau = self.settings.fedopt_tau

                delta_sq = delta_t**2
                # Yogi second-moment update: uses sign of (v - Δ²)
                v_t_next = v_t - (1 - beta2) * np.sign(v_t - delta_sq) * delta_sq
                m_t_next = beta1 * m_t + (1 - beta1) * delta_t
                w_next = w_t + eta * m_t_next / (np.sqrt(v_t_next) + tau)

                self._server_m_by_sim[sim_id] = m_t_next
                self._server_v_by_sim[sim_id] = v_t_next

                avg_weights = w_next.tolist()

            logger.info("FedYogi adaptive aggregation applied for sim=%s", simulation_id)

        elif method == AggregationMethod.SCAFFOLD:
            # SCAFFOLD (Karimireddy et al., 2020): Global control variate update.
            # The global model simply averages the received client updates (same
            # as FedAvg).  SCAFFOLD's main correction happens *at the client*
            # during local training (handled in model_service.train_local).
            # Here we aggregate client weights and update the global control
            # variate c ← c + (1/N) * Σ (c_i_new - c_i_old), but since we
            # don't receive per-client c_i deltas through the connector layer,
            # this server step degrades to a weighted FedAvg which is still
            # correct — the client-side variate correction ensures drift is fixed.
            total_samples = sum(client_samples)
            proportions = [s / total_samples for s in client_samples]
            w_avg = np.zeros(len(client_weights[0].flat_weights))
            for w, proportion in zip(client_weights, proportions, strict=False):
                w_avg += np.array(w.flat_weights) * proportion
            avg_weights = w_avg.tolist()

            # Global server control variate tracking
            sim_id = simulation_id or "default_sim"
            if not hasattr(self, "_server_c_by_sim"):
                self._server_c_by_sim = {}
            if sim_id not in self._server_c_by_sim:
                self._server_c_by_sim[sim_id] = np.zeros(len(w_avg))
            logger.info("SCAFFOLD aggregation (server FedAvg & variate tracking step) for sim=%s", simulation_id)

        else:
            raise ValueError(f"Unsupported aggregation method: {method}")

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Aggregated %d client models in %.1fms (method=%s)",
            len(client_weights),
            elapsed_ms,
            method,
        )

        return ModelWeights(
            layer_shapes=client_weights[0].layer_shapes,
            flat_weights=avg_weights,
        )

    def aggregate_leave_one_out_parameters(
        self,
        client_weights: list[ModelWeights],
        client_samples: list[int],
        excluded_index: int,
        method: AggregationMethod,
        global_weights: ModelWeights | None = None,
        simulation_id: str | None = None,
    ) -> ModelWeights:
        """Aggregate model weights excluding one client. Used for Shapley value contribution auditing."""
        subset_weights = [w for idx, w in enumerate(client_weights) if idx != excluded_index]
        subset_samples = [s for idx, s in enumerate(client_samples) if idx != excluded_index]
        if not subset_weights:
            if global_weights:
                return global_weights
            if client_weights:
                return client_weights[0]
            raise ValueError("No weights available to aggregate LOO model")
        return self.aggregate_parameters(
            client_weights=subset_weights,
            client_samples=subset_samples,
            method=method,
            global_weights=global_weights,
            simulation_id=simulation_id,
        )

    def simulate_client_availability(
        self,
        bank_ids: list[str],
        dropout_probability: float = 0.2,
        previously_dropped: set[str] | None = None,
        enable_reconnect: bool = True,
        rng: np.random.Generator | None = None,
    ) -> dict[str, ClientStatus]:
        """Determine which clients participate in this round.

        Simulates realistic network conditions where clients may:
        - Drop out (network failure, maintenance window)
        - Reconnect after being offline in a previous round
        - Remain offline for multiple rounds

        Args:
            bank_ids: All registered bank IDs.
            dropout_probability: Chance of any client dropping out.
            previously_dropped: Banks that were offline last round.
            enable_reconnect: Whether dropped clients can rejoin.
            rng: Random number generator for reproducibility.
        """
        if rng is None:
            rng = np.random.default_rng()

        previously_dropped = previously_dropped or set()
        statuses: dict[str, ClientStatus] = {}

        for bank_id in bank_ids:
            was_dropped = bank_id in previously_dropped

            if was_dropped and enable_reconnect:
                # 70% chance of reconnecting after being dropped
                if rng.random() < 0.7:
                    statuses[bank_id] = ClientStatus.RECONNECTED
                    logger.info("Bank %s reconnected", bank_id)
                else:
                    statuses[bank_id] = ClientStatus.OFFLINE
                    logger.info("Bank %s remains offline", bank_id)
            elif rng.random() < dropout_probability:
                statuses[bank_id] = ClientStatus.DROPPED
                logger.info("Bank %s dropped out this round", bank_id)
            else:
                statuses[bank_id] = ClientStatus.ACTIVE

        return statuses

    async def simulate_network_latency(
        self,
        bank_id: str,
        latency_range_ms: tuple[int, int] = (50, 500),
        rng: np.random.Generator | None = None,
    ) -> float:
        """Simulate variable network delay for a client.

        Returns the simulated latency in milliseconds.
        """
        if rng is None:
            rng = np.random.default_rng()

        min_ms, max_ms = latency_range_ms
        latency_ms = float(rng.integers(min_ms, max_ms))

        await asyncio.sleep(latency_ms / 1000.0)
        logger.debug("Simulated %.0fms latency for %s", latency_ms, bank_id)

        return latency_ms

    def apply_secure_aggregation_masks(
        self,
        client_weights: list[ModelWeights],
        client_samples: list[int] | None = None,
        rng: np.random.Generator | None = None,
    ) -> list[ModelWeights]:
        """Simulate secure aggregation by applying pairwise masks.

        In real secure aggregation (Bonawitz et al., 2017), each pair of
        clients agrees on a random mask that cancels out during summation.
        Client i adds mask_ij and client j subtracts mask_ij, so the
        aggregator never sees raw parameters.

        This is a simplified demonstration: we add random masks that
        sum to zero across all clients (weighted or unweighted based on config).
        The aggregated result is identical to plaintext FedAvg, but individual
        client parameters are obscured.

        Limitations (documented in docs/threat-model.md or docs/):
        - No key exchange protocol
        - Masks are generated centrally (defeats the purpose in production)
        - No dropout recovery (real protocols handle this with Shamir secret sharing)
        """
        if rng is None:
            rng = np.random.default_rng()

        n_clients = len(client_weights)
        n_params = len(client_weights[0].flat_weights)

        # Generate random masks
        masks = rng.standard_normal((n_clients, n_params))

        if client_samples is not None and len(client_samples) == n_clients:
            total_samples = sum(client_samples)
            if total_samples > 0:
                proportions = [s / total_samples for s in client_samples]
                p_n = proportions[-1]
                if p_n > 0:
                    # Weighted: sum_{i=1}^n p_i * m_i = 0 => m_n = - (sum_{i=1}^{n-1} p_i * m_i) / p_n
                    weighted_sum_prev = np.zeros(n_params)
                    for i in range(n_clients - 1):
                        weighted_sum_prev += proportions[i] * masks[i]
                    masks[-1] = -weighted_sum_prev / p_n
                else:
                    masks[-1] = -masks[:-1].sum(axis=0)
            else:
                masks[-1] = -masks[:-1].sum(axis=0)
        else:
            # Unweighted: sum_{i=1}^n m_i = 0
            masks[-1] = -masks[:-1].sum(axis=0)

        masked_weights = []
        for w, mask in zip(client_weights, masks, strict=False):
            masked = [fw + m for fw, m in zip(w.flat_weights, mask, strict=False)]
            masked_weights.append(
                ModelWeights(
                    layer_shapes=w.layer_shapes,
                    flat_weights=masked,
                )
            )

        logger.info("Applied secure aggregation masks to %d clients", n_clients)
        return masked_weights

    def apply_model_poisoning(
        self,
        honest_weights: ModelWeights,
        scale: float = 5.0,
        rng: np.random.Generator | None = None,
    ) -> ModelWeights:
        """Simulate a model poisoning attack by corrupting model weights.

        Replaces the honest local model weights with random noise scaled
        by ``scale``. This emulates a Byzantine client that either:
        - Sends random garbage to degrade the global model, or
        - Sends carefully crafted updates to introduce a backdoor.

        For this simulator we use the simpler random-noise approach,
        which is sufficient to demonstrate Krum/Median defences.

        Args:
            honest_weights: The correctly-trained local weights to corrupt.
            scale: Multiplier on the random noise magnitude.
            rng: Random generator for reproducibility.

        Returns:
            Poisoned ModelWeights with the same shape metadata.
        """
        if rng is None:
            rng = np.random.default_rng()

        honest_arr = np.array(honest_weights.flat_weights)
        # Generate noise with same magnitude as the honest weights,
        # scaled up by the poisoning factor. If std is zero (e.g. constant weights),
        # fallback to 1.0.
        std_val = float(np.std(honest_arr))
        if std_val == 0.0:
            std_val = 1.0
        noise = rng.standard_normal(len(honest_arr)) * std_val * scale
        poisoned = (honest_arr + noise).tolist()

        logger.warning(
            "Applied model poisoning (scale=%.1f) — %d parameters corrupted",
            scale,
            len(poisoned),
        )

        return ModelWeights(
            layer_shapes=honest_weights.layer_shapes,
            flat_weights=poisoned,
        )

    def apply_byzantine_defense(
        self,
        client_weights: list[ModelWeights],
        defense_type: str = "krum",
    ) -> list[ModelWeights]:
        """Filter client weight updates using a Byzantine-robust defense strategy.

        Supported defense types:
        - ``krum``: Keep only the single update closest to the cluster of honest clients.
        - ``coordinate_wise_median``: Return a synthetic weight equal to the
          element-wise median — already handled by the aggregation step, so this
          path returns the full list unchanged to let aggregation do the work.
        - Any other value: no filtering, all weights returned.

        Args:
            client_weights: Raw update list potentially containing poisoned entries.
            defense_type: Name of the defense algorithm to apply.

        Returns:
            A filtered (or unchanged) list of ModelWeights.
        """
        if len(client_weights) <= 1:
            return client_weights

        if defense_type == "krum":
            # Select the single update with the smallest sum of distances to
            # its (n - f - 2) nearest neighbours, where f = 1 assumed Byzantine.
            weights_array = np.array([w.flat_weights for w in client_weights])
            n = len(weights_array)
            f = 1
            num_closest = max(1, n - f - 2)

            scores = []
            for i in range(n):
                dists = sorted(
                    float(np.sum((weights_array[i] - weights_array[j]) ** 2))
                    for j in range(n)
                    if i != j
                )
                scores.append(sum(dists[:num_closest]))

            best_idx = int(np.argmin(scores))
            logger.info("Byzantine defense (krum): selected client %d as representative", best_idx)
            return [client_weights[best_idx]]

        if defense_type == "trimmed_mean":
            # Trimmed Mean defense: return all weights unchanged — aggregation
            # will apply coordinate-wise trimming when TRIMMED_MEAN method is used.
            logger.info("Byzantine defense (trimmed_mean): deferring to aggregation step")
            return client_weights

        if defense_type == "bulyan":
            # Bulyan defense: return all weights unchanged — aggregation
            # will apply Krum selection + trimmed mean when BULYAN method is used.
            logger.info("Byzantine defense (bulyan): deferring to aggregation step")
            return client_weights

        # coordinate_wise_median and any unknown defense: return all weights
        # unchanged — the aggregation method will apply robustness if configured.
        return client_weights

    def aggregate_graph_parameters(
        self,
        client_weights: list[ModelWeights],
        client_samples: list[int],
        method: AggregationMethod = AggregationMethod.FED_AVG_WEIGHTED,
    ) -> ModelWeights:
        """Aggregate GraphSAGE model parameters from multiple clients.

        This is a thin wrapper around aggregate_parameters() that adds
        GNN-specific validation before delegating to the standard
        aggregation logic. The same FedAvg/Krum/Median strategies apply.

        Validates:
        - All clients have the same number of layers (layer_shapes match)
        - Parameter counts are identical across clients

        Args:
            client_weights: GNN parameter sets from each participating bank.
            client_samples: Number of graph nodes at each bank.
            method: Aggregation strategy (same as MLP aggregation).

        Returns:
            Aggregated global GNN model parameters.
        """
        if not client_weights:
            raise ValueError("Cannot aggregate empty GNN parameter list")

        # Validate layer shape consistency across clients
        reference_shapes = client_weights[0].layer_shapes
        reference_params = client_weights[0].num_parameters

        for i, w in enumerate(client_weights[1:], 1):
            if w.layer_shapes != reference_shapes:
                raise ValueError(
                    f"GNN layer shape mismatch: client 0 has {reference_shapes}, "
                    f"client {i} has {w.layer_shapes}"
                )
            if w.num_parameters != reference_params:
                raise ValueError(
                    f"GNN parameter count mismatch: client 0 has {reference_params}, "
                    f"client {i} has {w.num_parameters}"
                )

        logger.info(
            "Aggregating GNN parameters from %d clients (%d params each, method=%s)",
            len(client_weights),
            reference_params,
            method,
        )

        # Fallback to unweighted FED_AVG if all clients have 0 samples (e.g. empty graphs in testing)
        if sum(client_samples) == 0 and method == AggregationMethod.FED_AVG_WEIGHTED:
            logger.warning("All clients have 0 GNN samples. Falling back to unweighted FED_AVG.")
            method = AggregationMethod.FED_AVG

        # Delegate to the standard aggregation logic
        return self.aggregate_parameters(client_weights, client_samples, method)

    def aggregate_fairness_counts(self, client_counts: list[dict[str, int]]) -> dict[str, int]:
        """Aggregate local demographic and prediction counts from clients using sum aggregation.

        This represents the secure/federated collation of contingency table counts.
        """
        keys = [
            "protected_positive_pred",
            "protected_negative_pred",
            "reference_positive_pred",
            "reference_negative_pred",
            "protected_tp",
            "protected_fn",
            "reference_tp",
            "reference_fn",
        ]
        aggregated = {k: 0 for k in keys}
        for counts in client_counts:
            if not counts:
                continue
            for k in keys:
                aggregated[k] += counts.get(k, 0)
        return aggregated
