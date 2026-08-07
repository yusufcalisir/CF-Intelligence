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


def extract_node_features(entity_dict: dict[str, Any], degree: int = 0) -> np.ndarray:
    """Convert an entity dictionary into a fixed-size numerical feature vector.

    Feature layout (12 dimensions):
        [0:7]   Entity type one-hot encoding
        [7]     Risk level ordinal (0.0 - 1.0)
        [8]     Alert count (log-normalized)
        [9]     Degree centrality (log-normalized)
        [10]    Account age (days since first_seen, log-normalized)
        [11]    Recency (hours since last_seen, inverted and normalized)

    Args:
        entity_dict: Dictionary representation of an Entity dataclass.
        degree: Number of edges connected to this node.

    Returns:
        numpy array of shape (12,) with float32 features.
    """
    features = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)

    # One-hot entity type
    entity_type = entity_dict.get("entity_type", "customer")
    type_idx = ENTITY_TYPE_INDEX.get(entity_type, 0)
    features[type_idx] = 1.0

    # Risk level ordinal
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
            first_seen = datetime.fromisoformat(first_seen_str)
            age_days = (now - first_seen).total_seconds() / 86400.0
            features[10] = min(1.0, np.log1p(age_days) / 7.0)  # log1p(1095) ≈ 7.0 (3 years)
    except (ValueError, TypeError):
        features[10] = 0.0

    try:
        last_seen_str = entity_dict.get("last_seen", "")
        if last_seen_str:
            last_seen = datetime.fromisoformat(last_seen_str)
            hours_ago = (now - last_seen).total_seconds() / 3600.0
            # Invert: recently seen → high value
            features[11] = max(0.0, 1.0 - min(1.0, hours_ago / 720.0))  # 720h = 30 days
    except (ValueError, TypeError):
        features[11] = 0.0

    return features


class GraphSAGELayer(nn.Module):
    """Single GraphSAGE message-passing layer.

    Aggregation: Mean pooling of neighbor features.
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
        adjacency_lists: list[list[int]],
        num_sample: int = 10,
    ) -> torch.Tensor:
        """Forward pass for one GraphSAGE layer.

        Args:
            node_features: (N, in_dim) tensor of current node representations.
            adjacency_lists: List of neighbor indices for each node.
                adjacency_lists[i] = [j, k, ...] means nodes j, k, ... are
                neighbors of node i.
            num_sample: Maximum neighbors to sample per node (for scalability).

        Returns:
            (N, out_dim) tensor of updated node representations.
        """
        num_nodes = node_features.size(0)
        device = node_features.device

        # Build sparse adjacency matrix for vectorized mean pooling with neighbor sampling
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []

        for i in range(num_nodes):
            neighbors = adjacency_lists[i] if i < len(adjacency_lists) else []
            if not neighbors:
                rows.append(i)
                cols.append(i)
                vals.append(1.0)
                continue

            if len(neighbors) > num_sample:
                sampled_idx = torch.randperm(len(neighbors))[:num_sample]
                neighbors = [neighbors[idx] for idx in sampled_idx.tolist()]

            valid_neighbors = [n for n in neighbors if 0 <= n < num_nodes]
            if not valid_neighbors:
                rows.append(i)
                cols.append(i)
                vals.append(1.0)
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
            agg_features = node_features

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
        Input (12) → GraphSAGE Layer 1 (128) → GraphSAGE Layer 2 (64) → Embedding
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
        adjacency_lists: list[list[int]],
        num_sample: int = 10,
    ) -> torch.Tensor:
        """Compute node embeddings without classification.

        Args:
            node_features: (N, input_dim) node feature matrix.
            adjacency_lists: Per-node neighbor index lists.
            num_sample: Neighbor sampling budget per layer.

        Returns:
            (N, embedding_dim) embedding matrix.
        """
        h = node_features
        for layer in self.sage_layers:
            h = layer(h, adjacency_lists, num_sample)
        return h

    def forward(
        self,
        node_features: torch.Tensor,
        adjacency_lists: list[list[int]],
        num_sample: int = 10,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass: embeddings + fraud predictions.

        Returns:
            Tuple of:
                embeddings: (N, embedding_dim) node embedding matrix
                predictions: (N,) fraud probability per node
        """
        embeddings = self.get_embeddings(node_features, adjacency_lists, num_sample)
        predictions = self.classifier(embeddings).squeeze(-1)
        return embeddings, predictions

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
        offset = 0
        params = self.parameters() if include_classifier else self.sage_layers.parameters()
        for param, shape in zip(params, weights.layer_shapes, strict=False):
            numel = 1
            for s in shape:
                numel *= s
            param_data = weights.flat_weights[offset : offset + numel]
            param.data = torch.tensor(param_data, dtype=torch.float32).reshape(shape)
            offset += numel
