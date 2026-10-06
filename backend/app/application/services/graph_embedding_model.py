"""GraphSAGE model for Federated Graph Embedding.

Implements the GraphSAGE (SAmple and aggreGatE) architecture from
Hamilton et al. (2017) for inductive node representation learning.

Key design decisions:
- Mean aggregation: Simple, efficient, and compatible with DP noise injection.
  Max/LSTM aggregators would be more expressive but harder to federate.
- 2-layer architecture: Captures 2-hop neighborhood structures, sufficient
  for detecting fraud rings (account → device → account patterns).
- Separate classification head: Embeddings (pre-head) are used for similarity
  search; the classification head is used for fraud prediction during training.

The model weights (W_aggregate, W_combine per layer + classifier weights)
are the only artifacts that participate in federated aggregation.
Raw graph structure and node features never leave the bank.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F  # noqa: N812

from app.domain.enums import EntityType, RiskLevel
from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)

# Entity type to one-hot index mapping (7 entity types)
ENTITY_TYPE_INDEX: dict[str, int] = {
    EntityType.CUSTOMER: 0,
    EntityType.MERCHANT: 1,
    EntityType.DEVICE: 2,
    EntityType.CARD: 3,
    EntityType.EMAIL: 4,
    EntityType.PHONE: 5,
    EntityType.IP_ADDRESS: 6,
}

# Risk level to ordinal mapping
RISK_LEVEL_ORDINAL: dict[str, float] = {
    RiskLevel.MINIMAL: 0.0,
    RiskLevel.LOW: 0.25,
    RiskLevel.MEDIUM: 0.5,
    RiskLevel.HIGH: 0.75,
    RiskLevel.CRITICAL: 1.0,
}

# Total input feature dimension per node
# 7 (entity type one-hot) + 1 (risk ordinal) + 1 (alert count norm) + 1 (degree norm)
# + 1 (account age norm) + 1 (recency norm) = 12
NODE_FEATURE_DIM = 12


def extract_node_features(
    entity_dict: dict[str, Any],
    degree: int = 0,
    mask_label_leakage: bool = True,
) -> np.ndarray:
    """Convert an entity dictionary into a fixed-size numerical feature vector.

    Feature layout (12 dimensions):
        [0:7]   Entity type one-hot encoding
        [7]     Risk level ordinal (0.0 - 1.0) (masked to 0.0 when mask_label_leakage=True)
        [8]     Alert count (log-normalized)
        [9]     Degree centrality (log-normalized)
        [10]    Account age (days since first_seen, log-normalized)
        [11]    Recency (hours since last_seen, inverted and normalized)

    Args:
        entity_dict: Dictionary representation of an Entity dataclass.
        degree: Number of edges connected to this node.
        mask_label_leakage: If True, zeroes out position 7 to prevent post-investigation
            or target-derived risk_level from leaking ground-truth labels into GNN inputs.

    Returns:
        numpy array of shape (12,) with float32 features.
    """
    features = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)

    # One-hot entity type
    entity_type = entity_dict.get("entity_type", "customer")
    type_idx = ENTITY_TYPE_INDEX.get(entity_type, 0)
    features[type_idx] = 1.0

    # Risk level ordinal (fail-closed against label leakage during training/eval)
    if mask_label_leakage:
        features[7] = 0.0
    else:
        risk_level = entity_dict.get("risk_level", "minimal")
        features[7] = RISK_LEVEL_ORDINAL.get(risk_level, 0.0)

    # Alert count (log-normalized to prevent outlier domination)
    alert_count = entity_dict.get("alert_count", 0)
    features[8] = np.log1p(alert_count) / 5.0  # log1p(148) ≈ 5.0, reasonable upper bound

    # Degree centrality (log-normalized)
    features[9] = np.log1p(degree) / 5.0

    # Temporal features from datetime strings
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    try:
        first_seen_str = entity_dict.get("first_seen", "")
        if first_seen_str:
            first_seen = datetime.fromisoformat(str(first_seen_str))
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=UTC)
            age_days = max(0.0, (now - first_seen).total_seconds() / 86400.0)
            features[10] = float(min(1.0, np.log1p(age_days) / 7.0))  # log1p(1095) ≈ 7.0 (3 years)
    except (ValueError, TypeError):
        features[10] = 0.0

    try:
        last_seen_str = entity_dict.get("last_seen", "")
        if last_seen_str:
            last_seen = datetime.fromisoformat(str(last_seen_str))
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            hours_ago = max(0.0, (now - last_seen).total_seconds() / 3600.0)
            # Invert: recently seen → high value
            features[11] = float(max(0.0, 1.0 - min(1.0, hours_ago / 720.0)))  # 720h = 30 days
    except (ValueError, TypeError):
        features[11] = 0.0

    if not np.all(np.isfinite(features)):
        features = np.nan_to_num(features, nan=0.0, posinf=1.0, neginf=0.0)

    return features


class GraphSAGELayer(nn.Module):
    """Single GraphSAGE message-passing layer.

    Aggregation: Mean pooling of incoming neighbor features.
    Combination: Concatenation of self-features with aggregated neighbor
    features, followed by a linear projection and activation.

    W_neigh: Projects aggregated neighbor features.
    W_self:  Projects the node's own features.
    Output = ReLU(W_self · h_v || W_neigh · AGG(h_N(v)))

    This is the "mean" variant from the original paper, chosen for:
    1. Differentiability (important for DP gradient clipping)
    2. Simplicity (fewer parameters to federate)
    3. Permutation invariance (neighbor order doesn't matter)
    """

    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.W_neigh = nn.Linear(in_dim, out_dim, bias=False)
        self.W_self = nn.Linear(in_dim, out_dim, bias=False)
        self.bias = nn.Parameter(torch.zeros(out_dim))

    def forward(
        self,
        node_features: torch.Tensor,
        adjacency_lists: list[list[int]] | None = None,
        edge_index: torch.Tensor | None = None,
        num_sample: int = 10,
    ) -> torch.Tensor:
        """Forward pass for one GraphSAGE layer.

        Args:
            node_features: (N, in_dim) tensor of current node representations.
            adjacency_lists: Optional list of neighbor indices for each node.
                adjacency_lists[i] = [j, k, ...] means nodes j, k, ... are
                incoming neighbors of node i whose features node i aggregates.
            edge_index: Optional (2, E) PyG-style edge index tensor where
                edge_index[0] is source node (message sender) and
                edge_index[1] is target node (message recipient/aggregator).
            num_sample: Maximum neighbors to sample per node (for scalability).

        Returns:
            (N, out_dim) tensor of updated node representations.
        """
        num_nodes = node_features.size(0)
        device = node_features.device

        if edge_index is not None:
            if edge_index.numel() > 0:
                src, dst = edge_index[0], edge_index[1]
                valid = (src < num_nodes) & (dst < num_nodes) & (src >= 0) & (dst >= 0)
                if valid.any():
                    src, dst = src[valid], dst[valid]
                    # Target node dst aggregates incoming features from source node src
                    deg = torch.bincount(dst, minlength=num_nodes).float()
                    deg_inv = torch.where(deg > 0, 1.0 / deg, torch.zeros_like(deg))
                    weights = deg_inv[dst]
                    indices = torch.stack([dst, src])
                    adj_sparse = torch.sparse_coo_tensor(
                        indices, weights, size=(num_nodes, num_nodes), device=device
                    )
                    agg_features = torch.sparse.mm(adj_sparse, node_features)
                else:
                    agg_features = torch.zeros_like(node_features)
            else:
                agg_features = torch.zeros_like(node_features)
        else:
            # Build sparse adjacency matrix for vectorized mean pooling with neighbor sampling
            adj_lists = adjacency_lists or [[] for _ in range(num_nodes)]
            rows: list[int] = []
            cols: list[int] = []
            vals: list[float] = []

            for i in range(num_nodes):
                neighbors = adj_lists[i] if i < len(adj_lists) else []
                if not neighbors:
                    continue

                if len(neighbors) > num_sample:
                    sampled_idx = torch.randperm(len(neighbors))[:num_sample]
                    neighbors = [neighbors[idx] for idx in sampled_idx.tolist()]

                valid_neighbors = [n for n in neighbors if 0 <= n < num_nodes]
                if not valid_neighbors:
                    continue

                weight = 1.0 / len(valid_neighbors)
                for n in valid_neighbors:
                    rows.append(i)
                    cols.append(n)
                    vals.append(weight)

            if rows:
                indices = torch.tensor([rows, cols], dtype=torch.long, device=device)
                values = torch.tensor(vals, dtype=torch.float32, device=device)
                adj_sparse = torch.sparse_coo_tensor(
                    indices, values, size=(num_nodes, num_nodes), device=device, is_coalesced=True
                )
                agg_features = torch.sparse.mm(adj_sparse, node_features)
            else:
                agg_features = torch.zeros_like(node_features)

        # Combine: project self + project aggregated neighbors + bias
        h_self = self.W_self(node_features)
        h_neigh = self.W_neigh(agg_features)
        out = F.relu(h_self + h_neigh + self.bias)

        # L2 normalize embeddings to unit sphere (improves cosine similarity)
        out = F.normalize(out, p=2, dim=1)

        return out


class GraphSAGEModel(nn.Module):
    """Multi-layer GraphSAGE for fraud node embedding and classification.

    Architecture (default):
        Input (12 or 166) → GraphSAGE Layer 1 (128) → GraphSAGE Layer 2 (64) → Embedding
                                                                                    ↓
                                                                            Classifier (1)

    The model produces:
    1. Node embeddings (64-dim vectors) — used for similarity search & visualization
    2. Fraud predictions (0-1 probability) — used for training signal

    Only the model weights (W_self, W_neigh, bias per layer + classifier) are
    federated. Embeddings stay local.
    """

    def __init__(
        self,
        input_dim: int = NODE_FEATURE_DIM,
        hidden_dim: int = 128,
        embedding_dim: int = 64,
        num_layers: int = 2,
    ) -> None:
        super().__init__()
        self.num_layers = num_layers
        self.input_dim = input_dim
        self.embedding_dim = embedding_dim

        # Build GraphSAGE layers
        layers = []
        dims = [input_dim] + [hidden_dim] * (num_layers - 1) + [embedding_dim]
        for i in range(num_layers):
            layers.append(GraphSAGELayer(dims[i], dims[i + 1]))
        self.sage_layers = nn.ModuleList(layers)

        # Classification head for fraud prediction (training signal)
        self.classifier = nn.Sequential(
            nn.Linear(embedding_dim, 16),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

    def get_embeddings(
        self,
        node_features: torch.Tensor,
        adjacency_lists: list[list[int]] | None = None,
        edge_index: torch.Tensor | None = None,
        num_sample: int = 10,
    ) -> torch.Tensor:
        """Compute node embeddings without classification.

        Args:
            node_features: (N, input_dim) node feature matrix.
            adjacency_lists: Per-node neighbor index lists.
            edge_index: Optional (2, E) PyG edge index tensor.
            num_sample: Neighbor sampling budget per layer.

        Returns:
            (N, embedding_dim) embedding matrix.
        """
        h = node_features
        for layer in self.sage_layers:
            h = layer(h, adjacency_lists=adjacency_lists, edge_index=edge_index, num_sample=num_sample)
        return h

    def forward(
        self,
        node_features: torch.Tensor,
        adjacency_lists: list[list[int]] | None = None,
        edge_index: torch.Tensor | None = None,
        num_sample: int = 10,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass: embeddings + fraud predictions.

        Returns:
            Tuple of:
                embeddings: (N, embedding_dim) node embedding matrix
                predictions: (N,) fraud probability per node
        """
        embeddings = self.get_embeddings(
            node_features,
            adjacency_lists=adjacency_lists,
            edge_index=edge_index,
            num_sample=num_sample,
        )
        predictions = self.classifier(embeddings).squeeze(-1)
        return embeddings, predictions

    def compute_loss(
        self,
        predictions: torch.Tensor,
        targets: torch.Tensor,
        mask: torch.Tensor | None = None,
        pos_weight: float | None = None,
    ) -> torch.Tensor:
        """Compute binary cross-entropy loss optionally restricted to masked nodes.

        Supports semi-supervised graph learning where message passing runs on all nodes
        (including unlabeled background nodes), but loss is computed strictly on
        labeled nodes specified by the mask.
        """
        if mask is not None:
            preds = predictions[mask]
            targs = targets[mask].float()
        else:
            preds = predictions
            targs = targets.float()
        if preds.numel() == 0:
            return torch.tensor(0.0, device=predictions.device, requires_grad=True)
        if pos_weight is not None:
            weight = torch.where(targs == 1, torch.tensor(pos_weight, device=predictions.device), 1.0)
            return F.binary_cross_entropy(preds, targs, weight=weight)
        return F.binary_cross_entropy(preds, targs)

    def to_model_weights(self, include_classifier: bool = False) -> ModelWeights:
        """Serialize model parameters to ModelWeights for federation.

        **Privacy Policy:** By default, the classifier head (64→16→1) is EXCLUDED
        from federated aggregation. Including it would expose local fraud label
        distributions (class ratio leakage) to the coordinator.

        Only GNN layer parameters (W_self, W_neigh, bias per layer) are federated
        by default, encoding structural patterns without leaking label statistics.

        Args:
            include_classifier: If True, includes classifier head parameters in the
                exported weights. Only use for local inference — never for federation.
                Setting True in a federated round leaks local fraud label distributions.
        """
        layer_shapes = []
        flat_weights: list[float] = []

        params = self.parameters() if include_classifier else self.sage_layers.parameters()

        for param in params:
            shape = tuple(param.shape)
            layer_shapes.append(shape)
            flat_weights.extend(param.detach().cpu().numpy().flatten().tolist())

        return ModelWeights(layer_shapes=layer_shapes, flat_weights=flat_weights)

    def load_model_weights(self, weights: ModelWeights, include_classifier: bool = False) -> None:
        """Load federated model weights back into the model."""
        params = list(self.parameters() if include_classifier else self.sage_layers.parameters())
        if len(params) != len(weights.layer_shapes):
            raise ValueError(
                f"Model weights layer count mismatch: expected {len(params)} layers, "
                f"got {len(weights.layer_shapes)}"
            )

        offset = 0
        for param, shape in zip(params, weights.layer_shapes, strict=True):
            if tuple(param.shape) != tuple(shape):
                raise ValueError(
                    f"Model weights layer shape mismatch: expected {tuple(param.shape)}, "
                    f"got {tuple(shape)}"
                )
            numel = 1
            for s in shape:
                numel *= s
            if offset + numel > len(weights.flat_weights):
                raise ValueError(
                    f"Model weights truncated: expected at least {offset + numel} elements, "
                    f"got {len(weights.flat_weights)}"
                )
            param_data = weights.flat_weights[offset : offset + numel]
            param.data = torch.tensor(param_data, dtype=torch.float32).reshape(shape)
            offset += numel

