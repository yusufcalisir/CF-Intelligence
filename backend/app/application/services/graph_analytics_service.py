"""Graph Analytics Service.

Provides advanced graph algorithms: PageRank-like risk propagation,
community risk density clustering, and temporal edge velocity anomaly detection.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.graph_engine import (
    GraphEngine,
    _neo4j_node_to_entity,
)
from app.domain.enums import RelationshipType, RiskLevel

logger = logging.getLogger(__name__)

# Map risk level to base numeric scores (0 to 1000)
RISK_LEVEL_TO_SCORE = {
    RiskLevel.CRITICAL: 1000.0,
    RiskLevel.HIGH: 800.0,
    RiskLevel.MEDIUM: 500.0,
    RiskLevel.LOW: 200.0,
    RiskLevel.MINIMAL: 50.0,
}

# Edge relationship multipliers for risk propagation
RELATIONSHIP_MULTIPLIERS = {
    RelationshipType.OWNS: 1.0,
    RelationshipType.USES: 0.9,
    RelationshipType.TRANSACTS_WITH: 0.8,
    RelationshipType.SHARES_DEVICE: 0.9,
    RelationshipType.SHARES_IP: 0.5,
    RelationshipType.LINKED_ALERT: 1.0,
    RelationshipType.SAME_ENTITY: 1.0,
}


def score_to_risk_level(score: float) -> RiskLevel:
    if score >= 800.0:
        return RiskLevel.CRITICAL
    if score >= 600.0:
        return RiskLevel.HIGH
    if score >= 400.0:
        return RiskLevel.MEDIUM
    if score >= 200.0:
        return RiskLevel.LOW
    return RiskLevel.MINIMAL


class GraphAnalyticsService:
    """Implements graph-based fraud detection algorithms."""

    def __init__(
        self,
        graph_engine: GraphEngine | None = None,
        entity_service: EntityResolutionService | None = None,
    ) -> None:
        self.graph_engine = graph_engine or GraphEngine()
        self.entity_service = entity_service or EntityResolutionService()
        from app.application.services.flink_graph_streaming import (
            FlinkGraphStreamProcessor,
        )

        self.flink_processor = FlinkGraphStreamProcessor()

    def stream_realtime_edge_event(
        self,
        edge_id: str,
        source_id: str,
        target_id: str,
        rel_type: str = "TRANSACTS_WITH",
        amount: float = 100.0,
    ) -> dict[str, Any]:
        """Streams a real-time graph edge transaction through Apache Flink processor."""
        from app.application.services.flink_graph_streaming import StreamingEdgeEvent

        event = StreamingEdgeEvent(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            rel_type=rel_type,
            amount=amount,
        )
        receipt = self.flink_processor.process_streaming_edge(event)
        return {
            "processed_count": receipt.processed_count,
            "latency_ms": receipt.latency_ms,
            "window_size_ms": receipt.window_size_ms,
            "velocity_anomalies": receipt.velocity_anomalies,
            "high_risk_entities": receipt.high_risk_entities,
            "processed_at": receipt.processed_at,
        }

    def get_flink_streaming_status(self) -> dict[str, Any]:
        """Returns Apache Flink stream processor engine status."""
        return self.flink_processor.get_stream_status()

    def propagate_risk(self, decay_factor: float = 0.85) -> dict[str, Any]:
        """Propagate risk scores from flagged entities through the graph.

        Uses an iterative propagation similar to PageRank with decay.
        Saves updated risk levels back to entity storage. When a Neo4j /
        Memgraph backend is active, risk updates are written back to the
        graph database in batch via Cypher.
        """
        # Load all entities and relationships
        if self.graph_engine.db_type in ("neo4j", "memgraph") and self.graph_engine.driver:
            # Load entities from graph DB
            entities = {}
            relationships = []
            with self.graph_engine.driver.session() as session:
                res_ents = session.run("MATCH (n:Entity) RETURN n")
                for r in res_ents:
                    e = _neo4j_node_to_entity(r["n"])
                    entities[e.id] = e

                res_rels = session.run(
                    "MATCH ()-[r]->() RETURN "
                    "r.id as id, r.source_entity_id as source_entity_id, "
                    "r.target_entity_id as target_entity_id, "
                    "r.relationship_type as relationship_type, "
                    "r.confidence as confidence, r.evidence as evidence, "
                    "r.created_at as created_at"
                )
                for r in res_rels:
                    from datetime import datetime

                    from app.domain.entities_phase2 import Relationship

                    relationships.append(
                        Relationship(
                            id=r["id"],
                            source_entity_id=r["source_entity_id"],
                            target_entity_id=r["target_entity_id"],
                            relationship_type=RelationshipType(r["relationship_type"]),
                            confidence=r["confidence"],
                            evidence=r["evidence"] or [],
                            created_at=datetime.fromisoformat(r["created_at"]),
                        )
                    )
        else:
            entities = {e.id: e for e in self.entity_service.get_entities(limit=1000)}
            relationships = self.entity_service.get_relationships()

        if not entities:
            return {"updated_nodes_count": 0, "max_score": 0.0, "avg_score_change": 0.0}

        # Initialize base and current scores
        base_scores = {
            eid: RISK_LEVEL_TO_SCORE.get(e.risk_level, 50.0) for eid, e in entities.items()
        }
        current_scores = base_scores.copy()

        # Build adjacency list representation: source_id -> list of (target_id, relationship_type, confidence)
        adjacency = defaultdict(list)
        for r in relationships:
            if r.source_entity_id in entities and r.target_entity_id in entities:
                adjacency[r.source_entity_id].append(
                    (r.target_entity_id, r.relationship_type, r.confidence)
                )
                adjacency[r.target_entity_id].append(
                    (r.source_entity_id, r.relationship_type, r.confidence)
                )

        # Run 3 iterations of Jacobi-style propagation
        iterations = 3
        updated_count = 0
        total_change = 0.0
        max_score = 0.0

        for _ in range(iterations):
            next_scores = current_scores.copy()
            for u in entities:
                # Max propagated score from all neighbors
                propagated_scores = []
                for v, rel_type, confidence in adjacency[u]:
                    mult = RELATIONSHIP_MULTIPLIERS.get(rel_type, 0.5)
                    score_from_neighbor = current_scores[v] * decay_factor * mult * confidence
                    propagated_scores.append(score_from_neighbor)

                if propagated_scores:
                    next_scores[u] = max(base_scores[u], max(propagated_scores))

            current_scores = next_scores

        # Save changes if risk level changed
        neo4j_updates = []
        for eid, new_score in current_scores.items():
            old_level = entities[eid].risk_level
            new_level = score_to_risk_level(new_score)
            max_score = max(max_score, new_score)

            if old_level != new_level:
                self.entity_service.update_risk_level(eid, new_level)
                neo4j_updates.append({"id": eid, "risk_level": new_level.value})
                updated_count += 1
                diff = abs(new_score - RISK_LEVEL_TO_SCORE.get(old_level, 50.0))
                total_change += diff

        # Batch-update risk levels in graph DB if active
        if (
            self.graph_engine.db_type in ("neo4j", "memgraph")
            and self.graph_engine.driver
            and neo4j_updates
        ):
            with self.graph_engine.driver.session() as session:
                session.run(
                    "UNWIND $updates AS upd "
                    "MATCH (n:Entity {id: upd.id}) "
                    "SET n.risk_level = upd.risk_level",
                    updates=neo4j_updates,
                )

        avg_change = total_change / len(entities) if entities else 0.0
        logger.info(
            "Risk propagation completed. Updated %d nodes. Max score: %.2f. Avg change: %.2f",
            updated_count,
            max_score,
            avg_change,
        )

        return {
            "updated_nodes_count": updated_count,
            "max_score": round(max_score, 2),
            "avg_score_change": round(avg_change, 2),
        }

    def get_community_analytics(self, min_size: int = 3) -> list[dict[str, Any]]:
        """Identify communities and calculate fraud/risk densities.

        Returns list of community stats sorted by fraud density and size.
        """
        clusters = self.graph_engine.detect_clusters(min_size=min_size)
        communities = []

        for i, cluster in enumerate(clusters):
            # Load entities in this community
            entities = []
            for eid in cluster:
                e = self.entity_service.get_entity(eid)
                if e:
                    entities.append(e)

            if not entities:
                continue

            # Calculate metrics
            total_nodes = len(entities)
            fraud_nodes = sum(
                1 for e in entities if e.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            )
            fraud_density = fraud_nodes / total_nodes if total_nodes > 0 else 0.0

            total_score = sum(RISK_LEVEL_TO_SCORE.get(e.risk_level, 50.0) for e in entities)
            avg_risk = total_score / total_nodes if total_nodes > 0 else 0.0

            communities.append(
                {
                    "community_id": i,
                    "node_ids": cluster,
                    "size": total_nodes,
                    "fraud_density": round(fraud_density, 4),
                    "average_risk": round(avg_risk, 2),
                }
            )

        # Sort: Fraud density descending, then size descending
        communities.sort(key=lambda c: (c["fraud_density"], c["size"]), reverse=True)
        return communities

    def get_temporal_anomalies(
        self, window_minutes: int = 5, min_edges: int = 3
    ) -> list[dict[str, Any]]:
        """Detect edge creation velocity spikes indicating coordinated fraud.

        Groups relations by window_minutes intervals, identifies spikes,
        and constructs anomaly subgraphs.
        """
        relationships = self.entity_service.get_relationships()
        if not relationships:
            return []

        # Sort relationships by timestamp
        sorted_rels = sorted(relationships, key=lambda r: r.created_at)

        # We construct sliding windows over relationships
        windows = []
        n_rels = len(sorted_rels)

        for i in range(n_rels):
            start_time = sorted_rels[i].created_at
            end_time = start_time + timedelta(minutes=window_minutes)

            # Gather all edges in this window
            window_edges = []
            for j in range(i, n_rels):
                if sorted_rels[j].created_at <= end_time:
                    window_edges.append(sorted_rels[j])
                else:
                    break

            if len(window_edges) >= min_edges:
                windows.append((start_time, end_time, window_edges))

        if not windows:
            return []

        # Sort windows by edge count descending to get the largest spikes
        windows.sort(key=lambda w: len(w[2]), reverse=True)

        anomalies = []
        seen_edges_sets: list[set[str]] = []

        for idx, (start, end, edges) in enumerate(windows):
            edge_ids = {r.id for r in edges}

            # Avoid duplicates / highly overlapping windows
            is_redundant = False
            for s in seen_edges_sets:
                # If 80%+ overlap, skip
                if len(edge_ids & s) / len(edge_ids) > 0.8:
                    is_redundant = True
                    break

            if is_redundant:
                continue

            seen_edges_sets.append(edge_ids)

            # Collect unique nodes involved
            node_ids = set()
            for r in edges:
                node_ids.add(r.source_entity_id)
                node_ids.add(r.target_entity_id)

            # Score anomaly velocity
            # Velocity score is edges/minute scaled
            duration_minutes = (end - start).total_seconds() / 60.0
            if duration_minutes <= 0:
                duration_minutes = 0.1
            velocity = len(edges) / duration_minutes

            anomalies.append(
                {
                    "subgraph_id": idx,
                    "node_ids": list(node_ids),
                    "edges_count": len(edges),
                    "velocity_score": round(velocity, 2),
                    "time_window_start": start.isoformat(),
                    "time_window_end": end.isoformat(),
                }
            )

            # Limit to top 10 anomalies
            if len(anomalies) >= 10:
                break

        return anomalies

    def embedding_enhanced_risk_propagation(
        self,
        embeddings: dict[str, list[float]],
        decay_factor: float = 0.85,
    ) -> dict[str, Any]:
        """Propagate risk using learned GNN embeddings instead of heuristic multipliers.

        Instead of using hardcoded RELATIONSHIP_MULTIPLIERS (e.g., OWNS=1.0,
        SHARES_IP=0.5), this method uses cosine similarity between node
        embeddings to weight risk propagation. Nodes with similar structural
        patterns (high embedding similarity) propagate more risk to each other.

        This replaces the heuristic assumption "device sharing = 0.9 risk transfer"
        with a learned signal: "structurally similar nodes in the fraud embedding
        space transfer more risk."

        Args:
            embeddings: Dict of entity_id → embedding vector (from GraphEmbeddingService).
            decay_factor: Risk decay per hop (same as standard propagation).

        Returns:
            Dict with updated node counts, max score, and average score change.
        """
        import numpy as np

        entities = {e.id: e for e in self.entity_service.get_entities(limit=1000)}
        relationships = self.entity_service.get_relationships()

        if not entities or not embeddings:
            return {"updated_nodes_count": 0, "max_score": 0.0, "avg_score_change": 0.0}

        # Initialize scores
        base_scores = {
            eid: RISK_LEVEL_TO_SCORE.get(e.risk_level, 50.0) for eid, e in entities.items()
        }
        current_scores = base_scores.copy()

        # Build adjacency with embedding-based weights
        adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)

        for r in relationships:
            src, tgt = r.source_entity_id, r.target_entity_id
            if src not in entities or tgt not in entities:
                continue

            # Compute embedding similarity as edge weight
            src_emb = embeddings.get(src)
            tgt_emb = embeddings.get(tgt)

            if src_emb and tgt_emb:
                src_arr = np.array(src_emb)
                tgt_arr = np.array(tgt_emb)
                src_norm = np.linalg.norm(src_arr)
                tgt_norm = np.linalg.norm(tgt_arr)

                if src_norm > 0 and tgt_norm > 0:
                    similarity = float(np.dot(src_arr, tgt_arr) / (src_norm * tgt_norm))
                    # Clamp to [0, 1] — negative similarity means structurally dissimilar
                    edge_weight = max(0.0, similarity)
                else:
                    edge_weight = 0.5
            else:
                # Fallback to heuristic multiplier if no embedding available
                edge_weight = RELATIONSHIP_MULTIPLIERS.get(r.relationship_type, 0.5)

            confidence = r.confidence
            adjacency[src].append((tgt, edge_weight * confidence))
            adjacency[tgt].append((src, edge_weight * confidence))

        # Iterative propagation (3 rounds, same as standard)
        updated_count = 0
        total_change = 0.0
        max_score = 0.0

        for _ in range(3):
            next_scores = current_scores.copy()
            for u in entities:
                propagated = []
                for v, weight in adjacency[u]:
                    score_from_neighbor = current_scores[v] * decay_factor * weight
                    propagated.append(score_from_neighbor)
                if propagated:
                    next_scores[u] = max(base_scores[u], max(propagated))
            current_scores = next_scores

        # Apply changes
        for eid, new_score in current_scores.items():
            old_level = entities[eid].risk_level
            new_level = score_to_risk_level(new_score)
            max_score = max(max_score, new_score)

            if old_level != new_level:
                self.entity_service.update_risk_level(eid, new_level)
                updated_count += 1
                diff = abs(new_score - RISK_LEVEL_TO_SCORE.get(old_level, 50.0))
                total_change += diff

        avg_change = total_change / len(entities) if entities else 0.0

        logger.info(
            "Embedding-enhanced risk propagation: updated %d nodes, max=%.2f, avg_change=%.2f",
            updated_count,
            max_score,
            avg_change,
        )

        return {
            "updated_nodes_count": updated_count,
            "max_score": round(max_score, 2),
            "avg_score_change": round(avg_change, 2),
            "method": "embedding_enhanced",
        }

    def find_fraud_clusters_by_embedding(
        self,
        embeddings: dict[str, list[float]],
        similarity_threshold: float = 0.75,
        min_cluster_size: int = 3,
    ) -> list[dict[str, Any]]:
        """Cluster entities by embedding similarity rather than graph connectivity.

        Standard community detection finds connected components (graph topology).
        This method finds clusters of entities with similar learned structural
        patterns, even if they are not directly connected in the graph.

        For example: Two separate fraud rings at Bank A and Bank B that use
        the same modus operandi (card testing via small transactions on
        high-risk merchants) would have similar embeddings but no direct
        graph connection.

        Args:
            embeddings: Dict of entity_id → embedding vector.
            similarity_threshold: Minimum cosine similarity for clustering.
            min_cluster_size: Minimum entities per cluster.

        Returns:
            List of clusters sorted by average risk, each with member IDs,
            size, average similarity, and fraud density.
        """
        import numpy as np

        if not embeddings:
            return []

        entity_ids = list(embeddings.keys())
        emb_matrix = np.array([embeddings[eid] for eid in entity_ids])

        # Normalize embeddings
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-8)
        normalized = emb_matrix / norms

        # Compute pairwise similarity matrix
        sim_matrix = normalized @ normalized.T

        # Simple greedy clustering: assign each node to the cluster of its
        # most similar already-clustered neighbor, or start a new cluster
        assigned = [-1] * len(entity_ids)
        clusters: list[list[int]] = []

        for i in range(len(entity_ids)):
            if assigned[i] >= 0:
                continue

            # Start a new cluster with node i
            cluster_idx = len(clusters)
            cluster = [i]
            assigned[i] = cluster_idx

            # Expand: find all unassigned nodes similar enough to any cluster member
            queue = [i]
            while queue:
                current = queue.pop(0)
                for j in range(len(entity_ids)):
                    if assigned[j] >= 0:
                        continue
                    if sim_matrix[current, j] >= similarity_threshold:
                        assigned[j] = cluster_idx
                        cluster.append(j)
                        queue.append(j)

            clusters.append(cluster)

        # Filter by minimum size and build results
        results: list[dict[str, Any]] = []

        for cluster_indices in clusters:
            if len(cluster_indices) < min_cluster_size:
                continue

            cluster_ids = [entity_ids[idx] for idx in cluster_indices]

            # Compute cluster metrics
            cluster_sims = []
            for a_idx in cluster_indices:
                for b_idx in cluster_indices:
                    if a_idx != b_idx:
                        cluster_sims.append(float(sim_matrix[a_idx, b_idx]))

            avg_similarity = float(np.mean(cluster_sims)) if cluster_sims else 0.0

            # Get risk levels for fraud density
            fraud_count = 0
            total_risk = 0.0
            for eid in cluster_ids:
                entity = self.entity_service.get_entity(eid)
                if entity:
                    if entity.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                        fraud_count += 1
                    total_risk += RISK_LEVEL_TO_SCORE.get(entity.risk_level, 50.0)

            results.append(
                {
                    "cluster_id": len(results),
                    "node_ids": cluster_ids,
                    "size": len(cluster_ids),
                    "avg_embedding_similarity": round(avg_similarity, 4),
                    "fraud_density": round(fraud_count / len(cluster_ids), 4),
                    "average_risk": round(total_risk / len(cluster_ids), 2),
                }
            )

        results.sort(key=lambda c: (c["fraud_density"], c["size"]), reverse=True)
        return results
