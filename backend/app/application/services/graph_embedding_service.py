"""Federated Graph Embedding Service.

Orchestrates the full FedGNN pipeline:
1. Builds per-bank local graphs from entity/relationship data
2. Extracts node features and constructs adjacency lists
3. Trains GraphSAGE locally on each bank's subgraph
4. Serializes model weights for federated aggregation
5. Computes node embeddings post-training for similarity search
6. Provides embedding-based fraud pattern matching

This service integrates with the existing FL pipeline:
- Model weights use the same ModelWeights format as the MLP classifier
- DP noise injection uses the same PrivacyService
- Aggregation uses the same FedAvg/Krum strategies in fl_engine.py
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
import torch.nn as nn

from app.application.services.graph_embedding_model import (
    NODE_FEATURE_DIM,
    GraphSAGEModel,
    extract_node_features,
)
from app.application.services.graph_engine import GraphEngine

if TYPE_CHECKING:
    from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


class GraphEmbeddingService:
    """Manages the federated graph embedding lifecycle.

    Each bank runs this service locally to:
    1. Build its local transaction graph
    2. Train GraphSAGE on labeled fraud data
    3. Export model weights for federation
    4. Import aggregated global weights
    5. Compute embeddings for similarity search
    """

    def __init__(
        self,
        graph_engine: GraphEngine | None = None,
        embedding_dim: int = 64,
        hidden_dim: int = 128,
        num_layers: int = 2,
        neighbor_sample_size: int = 10,
    ) -> None:
        self.graph_engine = graph_engine or GraphEngine()
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.neighbor_sample_size = neighbor_sample_size

        # Model (lazily initialized per training session)
        self._model: GraphSAGEModel | None = None

        # Cached embeddings after training
        self._embeddings: dict[str, np.ndarray] = {}
        self._node_id_to_index: dict[str, int] = {}
        self._index_to_node_id: dict[int, str] = {}
        self._query_counts: dict[str, int] = defaultdict(int)
        self.max_query_budget: int = 100

    def _get_or_create_model(self) -> GraphSAGEModel:
        """Lazily initialize the GraphSAGE model."""
        if self._model is None:
            self._model = GraphSAGEModel(
                input_dim=NODE_FEATURE_DIM,
                hidden_dim=self.hidden_dim,
                embedding_dim=self.embedding_dim,
                num_layers=self.num_layers,
            )
        return self._model

    def build_local_graph(
        self, bank_id: str | None = None
    ) -> tuple[torch.Tensor, list[list[int]], list[float], dict[str, int]]:
        """Build graph tensors from the GraphEngine for a specific bank.

        Extracts all entities and relationships, constructs:
        - Node feature matrix (N × 12)
        - Adjacency lists (list of neighbor indices per node)
        - Fraud labels (1.0 for HIGH/CRITICAL risk, 0.0 otherwise)
        - Node ID to index mapping

        Args:
            bank_id: Optional filter to only include entities from one bank.
                     If None, includes all entities.

        Returns:
            Tuple of (features, adjacency_lists, labels, node_id_to_index)
        """
        from app.domain.enums import RiskLevel

        # Get all entities from graph engine
        raw_entities = self.graph_engine._entities.list_values()
        raw_relationships = self.graph_engine._relationships.list_values()

        if not raw_entities:
            logger.warning("No entities found in graph engine for bank_id=%s", bank_id)
            return torch.zeros(0, NODE_FEATURE_DIM), [], [], {}

        # Filter by bank_id if specified
        if bank_id:
            entities = [e for e in raw_entities if e.get("bank_id") == bank_id]
        else:
            entities = list(raw_entities)

        if not entities:
            logger.warning("No entities found for bank_id=%s", bank_id)
            return torch.zeros(0, NODE_FEATURE_DIM), [], [], {}

        # Create node ID → index mapping
        node_id_to_index: dict[str, int] = {}
        for idx, entity in enumerate(entities):
            node_id_to_index[entity["id"]] = idx

        # Build adjacency lists
        adjacency: list[list[int]] = [[] for _ in range(len(entities))]
        degree_count: dict[int, int] = defaultdict(int)

        for rel in raw_relationships:
            src_id = rel.get("source_entity_id", "")
            tgt_id = rel.get("target_entity_id", "")

            if src_id in node_id_to_index and tgt_id in node_id_to_index:
                src_idx = node_id_to_index[src_id]
                tgt_idx = node_id_to_index[tgt_id]
                adjacency[src_idx].append(tgt_idx)
                adjacency[tgt_idx].append(src_idx)
                degree_count[src_idx] += 1
                degree_count[tgt_idx] += 1

        # Extract node features
        features = np.zeros((len(entities), NODE_FEATURE_DIM), dtype=np.float32)
        labels: list[float] = []

        for idx, entity in enumerate(entities):
            degree = degree_count.get(idx, 0)
            features[idx] = extract_node_features(entity, degree=degree)

            # Binary fraud label: HIGH/CRITICAL = fraud, else = legitimate
            risk = entity.get("risk_level", "minimal")
            is_fraud = 1.0 if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) else 0.0
            labels.append(is_fraud)

        feature_tensor = torch.tensor(features, dtype=torch.float32)

        logger.info(
            "Built local graph for bank_id=%s: %d nodes, %d edges, fraud_ratio=%.3f",
            bank_id,
            len(entities),
            sum(len(adj) for adj in adjacency) // 2,
            sum(labels) / max(len(labels), 1),
        )

        return feature_tensor, adjacency, labels, node_id_to_index

    def train_local_gnn(
        self,
        bank_id: str,
        global_weights: ModelWeights | None = None,
        epochs: int = 5,
        learning_rate: float = 0.01,
    ) -> tuple[ModelWeights, dict[str, Any]]:
        """Train GraphSAGE locally on one bank's subgraph.

        If global_weights are provided (from the coordinator), the model
        is initialized with those weights before local training begins.

        Args:
            bank_id: Which bank's graph to train on.
            global_weights: Optional pre-trained weights from federated aggregation.
            epochs: Number of local training epochs.
            learning_rate: Learning rate for Adam optimizer.

        Returns:
            Tuple of (updated_model_weights, training_metrics)
        """
        model = self._get_or_create_model()

        # Load global weights if provided (federated round initialization)
        if global_weights is not None:
            model.load_model_weights(global_weights)

        # Build local graph
        features, adjacency, labels, node_id_to_index = self.build_local_graph(bank_id)

        if features.size(0) == 0:
            logger.warning("Empty graph for bank %s, skipping training", bank_id)
            return model.to_model_weights(), {"loss": 0.0, "num_nodes": 0}

        label_tensor = torch.tensor(labels, dtype=torch.float32)

        # Store mapping for later embedding retrieval
        self._node_id_to_index = node_id_to_index
        self._index_to_node_id = {v: k for k, v in node_id_to_index.items()}

        # Training loop
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        # Use class-weighted BCE to handle fraud/legitimate imbalance
        fraud_count = sum(labels)
        legit_count = len(labels) - fraud_count
        criterion: nn.Module
        if fraud_count > 0 and legit_count > 0:
            # Since our model already applies sigmoid, we use BCELoss with manual weight
            criterion = nn.BCELoss(
                weight=None,
                reduction="none",
            )
        else:
            criterion = nn.BCELoss()

        model.train()
        total_loss = 0.0

        for epoch in range(epochs):
            optimizer.zero_grad()

            embeddings, predictions = model(
                features, adjacency, num_sample=self.neighbor_sample_size
            )

            # Compute weighted loss
            if fraud_count > 0 and legit_count > 0:
                weights = torch.where(
                    label_tensor == 1.0,
                    torch.tensor(legit_count / fraud_count),
                    torch.tensor(1.0),
                )
                per_sample_loss = criterion(predictions, label_tensor)
                loss = (per_sample_loss * weights).mean()
            else:
                loss = nn.functional.binary_cross_entropy(predictions, label_tensor)

            loss.backward()
            optimizer.step()
            total_loss = loss.item()

        avg_loss = total_loss  # Last epoch loss

        # Compute final embeddings and cache them
        model.eval()
        with torch.no_grad():
            final_embeddings = model.get_embeddings(
                features, adjacency, num_sample=self.neighbor_sample_size
            )
            for node_id, idx in node_id_to_index.items():
                self._embeddings[node_id] = final_embeddings[idx].numpy()

        metrics = {
            "loss": round(avg_loss, 6),
            "num_nodes": len(labels),
            "num_edges": sum(len(adj) for adj in adjacency) // 2,
            "fraud_nodes": int(fraud_count),
            "embedding_dim": self.embedding_dim,
        }

        logger.info(
            "GNN training complete for bank %s: loss=%.6f, nodes=%d, edges=%d",
            bank_id,
            avg_loss,
            metrics["num_nodes"],
            metrics["num_edges"],
        )

        return model.to_model_weights(include_classifier=False), metrics

    def get_model_weights(self) -> ModelWeights | None:
        """Get current model weights for federated aggregation."""
        if self._model is None:
            return None
        return self._model.to_model_weights()

    def load_global_weights(self, weights: ModelWeights) -> None:
        """Load aggregated global weights from the coordinator."""
        model = self._get_or_create_model()
        model.load_model_weights(weights)
        logger.info("Loaded global GNN weights (%d parameters)", weights.num_parameters)

    def get_embedding(self, entity_id: str) -> np.ndarray | None:
        """Get the cached embedding vector for a specific entity.

        Returns None if the entity hasn't been embedded yet.
        """
        return self._embeddings.get(entity_id)

    def find_similar_entities(
        self,
        query_entity_id: str,
        top_k: int = 10,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Find entities with similar structural patterns via cosine similarity.

        Enforces query rate-limiting budget to prevent binary-search membership
        triangulation attacks.

        Args:
            query_entity_id: Entity to find similar nodes for.
            top_k: Maximum number of similar entities to return.
            threshold: Minimum cosine similarity threshold (0.0 - 1.0).

        Returns:
            List of dicts with entity_id, similarity score, and metadata.
        """
        if self._query_counts[query_entity_id] >= self.max_query_budget:
            logger.warning("Query budget exhausted for entity_id=%s", query_entity_id)
            return []

        self._query_counts[query_entity_id] += 1

        query_emb = self._embeddings.get(query_entity_id)
        if query_emb is None:
            return []

        results: list[dict[str, Any]] = []
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return []

        for entity_id, emb in self._embeddings.items():
            if entity_id == query_entity_id:
                continue

            emb_norm = np.linalg.norm(emb)
            if emb_norm == 0:
                continue

            similarity = float(np.dot(query_emb, emb) / (query_norm * emb_norm))

            if similarity >= threshold:
                results.append(
                    {
                        "entity_id": entity_id,
                        "similarity": round(similarity, 4),
                    }
                )

        # Sort by similarity descending
        results.sort(key=lambda x: float(x["similarity"]), reverse=True)

        return results[:top_k]

    def get_all_embeddings(
        self, noise_scale: float = 0.05, dp_noise: bool = True
    ) -> dict[str, list[float]]:
        """Get cached embeddings with optional DP noise injection and L2 re-normalization.

        Injects calibrated Gaussian noise and re-normalizes vectors onto the L2 unit
        sphere to protect graph topology from reconstruction attacks while maintaining
        unit-norm invariants.

        In production (APP_ENV=production), disabling DP noise is prohibited.
        Raw embedding export leaks graph topology (neighbor cosine similarity=0.460
        enables topology reconstruction attacks).

        Args:
            noise_scale: Standard deviation of injected Gaussian noise. Default 0.05.
            dp_noise: If True (default), injects Gaussian noise before returning embeddings.
                      Setting False is only permitted in non-production environments.

        Raises:
            RuntimeError: If dp_noise=False and APP_ENV=production.
        """
        import os

        if not dp_noise and os.getenv("APP_ENV") == "production":
            raise RuntimeError(
                "DP noise cannot be disabled in production. "
                "Raw embedding export is prohibited: neighbor cosine similarity=0.460 "
                "enables graph topology reconstruction attacks."
            )

        out: dict[str, list[float]] = {}
        for entity_id, emb in self._embeddings.items():
            if dp_noise and noise_scale > 0.0:
                rng = np.random.default_rng()
                noise = rng.normal(0.0, noise_scale, size=emb.shape)
                noised_emb = emb + noise
                norm = np.linalg.norm(noised_emb)
                if norm > 1e-8:
                    noised_emb = noised_emb / norm
                out[entity_id] = noised_emb.tolist()
            else:
                out[entity_id] = emb.tolist()
        return out

    def get_embedding_stats(self) -> dict[str, Any]:
        """Get summary statistics about the embedding space."""
        if not self._embeddings:
            return {
                "num_embedded_nodes": 0,
                "embedding_dim": self.embedding_dim,
                "model_parameters": 0,
            }

        all_embs = np.array(list(self._embeddings.values()))

        # Compute pairwise cosine similarities for distribution stats
        norms = np.linalg.norm(all_embs, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-8)  # Avoid division by zero
        normalized = all_embs / norms

        # Sample pairwise similarities (full matrix too expensive for large graphs)
        n = len(normalized)
        if n > 100:
            # Random sample of 100 pairs
            rng = np.random.default_rng(42)
            idx_a = rng.choice(n, size=100, replace=True)
            idx_b = rng.choice(n, size=100, replace=True)
            similarities = np.array(
                [
                    float(np.dot(normalized[a], normalized[b]))
                    for a, b in zip(idx_a, idx_b)
                    if a != b
                ]
            )
        else:
            sim_matrix = normalized @ normalized.T
            np.fill_diagonal(sim_matrix, 0)
            similarities = sim_matrix[np.triu_indices(n, k=1)]

        model = self._get_or_create_model()
        num_params = sum(p.numel() for p in model.parameters())

        return {
            "num_embedded_nodes": len(self._embeddings),
            "embedding_dim": self.embedding_dim,
            "model_parameters": num_params,
            "mean_similarity": round(float(np.mean(similarities)), 4)
            if len(similarities) > 0
            else 0.0,
            "std_similarity": round(float(np.std(similarities)), 4)
            if len(similarities) > 0
            else 0.0,
            "max_similarity": round(float(np.max(similarities)), 4)
            if len(similarities) > 0
            else 0.0,
            "min_similarity": round(float(np.min(similarities)), 4)
            if len(similarities) > 0
            else 0.0,
        }
