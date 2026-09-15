import logging
import math
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import torch

from app.application.services.graph_embedding_model import NODE_FEATURE_DIM, extract_node_features

logger = logging.getLogger(__name__)


class StreamingGraphService:
    """Manages the real-time sliding-window transaction graph stream for GNNs.

    Supports thread-safe streaming ingestion, incremental O(1) index updates,
    lazy sliding-window pruning, and exponential time-decayed edge weights.
    """

    def __init__(self, max_window_minutes: int = 60, default_decay_lambda: float = 0.001) -> None:
        self.max_window_minutes = max_window_minutes
        self.default_decay_lambda = default_decay_lambda
        self._lock = threading.RLock()

        # In-memory graph representation for the sliding window
        self.nodes: dict[str, dict[str, Any]] = {}  # node_id -> node_attributes
        self.edges: list[
            dict[str, Any]
        ] = []  # list of {from_id, to_id, amount, timestamp, bank_id}

        # Fast topological adjacency
        self._adjacency: dict[str, set[str]] = defaultdict(set)

        # Node degree tracking for feature normalization
        self.node_degrees: dict[str, int] = defaultdict(int)

        # Fast lookup mapping for node IDs to tensor indices
        self.node_to_index: dict[str, int] = {}
        self.index_to_node: dict[int, str] = {}

    def add_transaction(self, tx: dict[str, Any]) -> None:
        """Ingest a new transaction into the streaming graph buffer with incremental indexing."""
        from_id = tx.get("sender_id") or tx.get("source_owner")
        to_id = tx.get("receiver_id") or tx.get("destination_owner")
        amount = float(tx.get("amount") or 0.0)
        timestamp_str = tx.get("timestamp")
        bank_id = tx.get("bank_id")

        if not from_id or not to_id:
            return

        # Parse timestamp safely
        try:
            if isinstance(timestamp_str, str):
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            elif isinstance(timestamp_str, datetime):
                timestamp = timestamp_str
            else:
                timestamp = datetime.now(UTC)
        except Exception:
            timestamp = datetime.now(UTC)

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        with self._lock:
            # Add or update nodes in the current window
            if from_id not in self.nodes:
                self.nodes[from_id] = {
                    "entity_type": "customer",
                    "risk_level": "high" if tx.get("is_fraud") else "minimal",
                    "alert_count": 1 if tx.get("is_fraud") else 0,
                    "first_seen": timestamp.isoformat(),
                    "last_seen": timestamp.isoformat(),
                }
                # Incremental O(1) node indexing
                idx = len(self.node_to_index)
                self.node_to_index[from_id] = idx
                self.index_to_node[idx] = from_id
            else:
                self.nodes[from_id]["last_seen"] = timestamp.isoformat()
                if tx.get("is_fraud"):
                    self.nodes[from_id]["alert_count"] += 1
                    self.nodes[from_id]["risk_level"] = "high"

            if to_id not in self.nodes:
                self.nodes[to_id] = {
                    "entity_type": "customer",
                    "risk_level": "high" if tx.get("is_fraud") else "minimal",
                    "alert_count": 1 if tx.get("is_fraud") else 0,
                    "first_seen": timestamp.isoformat(),
                    "last_seen": timestamp.isoformat(),
                }
                # Incremental O(1) node indexing
                idx = len(self.node_to_index)
                self.node_to_index[to_id] = idx
                self.index_to_node[idx] = to_id
            else:
                self.nodes[to_id]["last_seen"] = timestamp.isoformat()
                if tx.get("is_fraud"):
                    self.nodes[to_id]["alert_count"] += 1
                    self.nodes[to_id]["risk_level"] = "high"

            # Record edge
            self.edges.append(
                {
                    "from_id": from_id,
                    "to_id": to_id,
                    "amount": amount,
                    "timestamp": timestamp,
                    "bank_id": bank_id,
                }
            )

            # Update degrees and adjacency incrementally
            self.node_degrees[from_id] += 1
            self.node_degrees[to_id] += 1
            self._adjacency[from_id].add(to_id)
            self._adjacency[to_id].add(from_id)

            # Prune expired edges to keep sliding window size bounded
            self.prune_expired_edges(self.max_window_minutes)

    def _rebuild_indices(self) -> None:
        """Rebuild mapping between node string IDs and tensor indices."""
        self.node_to_index = {}
        self.index_to_node = {}
        for idx, node_id in enumerate(self.nodes.keys()):
            self.node_to_index[node_id] = idx
            self.index_to_node[idx] = node_id

    def prune_expired_edges(self, max_age_minutes: int) -> None:
        """Prune edges outside of the sliding window and remove orphan nodes."""
        with self._lock:
            now = datetime.now(UTC)
            cutoff_time = now - timedelta(minutes=max_age_minutes)

            if not self.edges:
                return

            # Filter active edges
            active_edges = []
            active_nodes_set = set()

            # Reset degrees and adjacency for clean recounting
            self.node_degrees.clear()
            self._adjacency.clear()

            for edge in self.edges:
                edge_time = edge["timestamp"]
                if edge_time.tzinfo is None:
                    edge_time = edge_time.replace(tzinfo=UTC)

                if edge_time >= cutoff_time:
                    active_edges.append(edge)
                    f_id = edge["from_id"]
                    t_id = edge["to_id"]
                    active_nodes_set.add(f_id)
                    active_nodes_set.add(t_id)
                    self.node_degrees[f_id] += 1
                    self.node_degrees[t_id] += 1
                    self._adjacency[f_id].add(t_id)
                    self._adjacency[t_id].add(f_id)

            self.edges = active_edges

            # Remove nodes no longer connected in the sliding window
            pruned_nodes = {}
            for node_id in active_nodes_set:
                if node_id in self.nodes:
                    pruned_nodes[node_id] = self.nodes[node_id]

            self.nodes = pruned_nodes
            self._rebuild_indices()

    def get_active_subgraph_tensors(
        self,
        return_weights: bool = False,
        decay_lambda: float | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, list[float]] | tuple[torch.Tensor, torch.Tensor, list[float], torch.Tensor]:
        """Construct PyTorch-compatible GNN tensors from the active sliding window.

        Args:
            return_weights: If True, also returns time-decayed edge weights tensor [E].
            decay_lambda: Exponential decay coefficient lambda (default: self.default_decay_lambda).

        Returns:
            Tuple containing:
            - features (Tensor of shape [N, NODE_FEATURE_DIM])
            - edge_index (Tensor of shape [2, E])
            - labels (list of float binary targets, length N)
            - (optional) edge_weights (Tensor of shape [E], values in (0, 1])
        """
        with self._lock:
            n_nodes = len(self.nodes)
            if n_nodes == 0:
                empty_features = torch.empty((0, NODE_FEATURE_DIM), dtype=torch.float32)
                empty_edges = torch.empty((2, 0), dtype=torch.long)
                if return_weights:
                    return empty_features, empty_edges, [], torch.empty((0,), dtype=torch.float32)
                return empty_features, empty_edges, []

            lam = decay_lambda if decay_lambda is not None else self.default_decay_lambda
            now = datetime.now(UTC)

            # Build feature matrix and labels
            feature_list = []
            labels = []
            for node_id in self.nodes:
                attrs = self.nodes[node_id]
                degree = self.node_degrees[node_id]
                feat = extract_node_features(attrs, degree=degree)
                feature_list.append(feat)

                risk = attrs.get("risk_level", "minimal")
                is_fraud = 1.0 if risk in ("high", "critical") else 0.0
                labels.append(is_fraud)

            features = torch.tensor(np.array(feature_list), dtype=torch.float32)

            # Build edge index and time-decayed edge weights
            edge_indices = []
            edge_weights_list: list[float] = []

            for edge in self.edges:
                f_idx = self.node_to_index.get(edge["from_id"])
                t_idx = self.node_to_index.get(edge["to_id"])
                if f_idx is not None and t_idx is not None:
                    # Undirected message-passing: forward [f, t] and reverse [t, f]
                    edge_indices.append([f_idx, t_idx])
                    edge_indices.append([t_idx, f_idx])

                    # Calculate exponential time decay: w = exp(-lambda * delta_t_seconds)
                    e_time = edge["timestamp"]
                    if e_time.tzinfo is None:
                        e_time = e_time.replace(tzinfo=UTC)
                    age_seconds = max(0.0, (now - e_time).total_seconds())
                    decay_weight = math.exp(-lam * age_seconds)

                    edge_weights_list.append(decay_weight)
                    edge_weights_list.append(decay_weight)

            if len(edge_indices) > 0:
                edge_index = torch.tensor(edge_indices, dtype=torch.long).t().contiguous()
                edge_weights_tensor = torch.tensor(edge_weights_list, dtype=torch.float32)
            else:
                edge_index = torch.empty((2, 0), dtype=torch.long)
                edge_weights_tensor = torch.empty((0,), dtype=torch.float32)

            if return_weights:
                return features, edge_index, labels, edge_weights_tensor
            return features, edge_index, labels

    def get_active_subgraph_tensors_with_weights(
        self,
        decay_lambda: float | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, list[float], torch.Tensor]:
        """Convenience method to retrieve tensors with exponential time-decayed edge weights."""
        tensors = self.get_active_subgraph_tensors(return_weights=True, decay_lambda=decay_lambda)
        return tensors  # type: ignore[return-value]

    def get_node_neighbors(self, node_id: str) -> list[str]:
        """Return connected neighbor node IDs in the active sliding window."""
        with self._lock:
            return sorted(list(self._adjacency.get(node_id, set())))

    def get_status_summary(self) -> dict[str, Any]:
        """Return status telemetry info of the streaming graph."""
        with self._lock:
            return {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "window_size_minutes": self.max_window_minutes,
                "decay_lambda": self.default_decay_lambda,
            }

    def clear(self) -> None:
        """Clear all nodes, edges, and index mappings."""
        with self._lock:
            self.nodes.clear()
            self.edges.clear()
            self._adjacency.clear()
            self.node_degrees.clear()
            self.node_to_index.clear()
            self.index_to_node.clear()
