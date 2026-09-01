"""Graph engine.

Builds and queries the entity relationship graph. The graph represents
connections between entities (customers, merchants, devices, etc.)
across institutions. Graph operations include neighbor expansion,
connected component detection, and serialization to React Flow format
for interactive visualization.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any

from app.config import get_settings
from app.domain.entities_phase2 import Entity, Relationship
from app.domain.enums import EntityType, RelationshipType, RiskLevel
from app.domain.value_objects_phase2 import GraphSubgraph
from app.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


def _entity_to_dict(e: Entity) -> dict[str, Any]:
    return {
        "id": e.id,
        "entity_type": e.entity_type.value,
        "privacy_id": e.privacy_id,
        "bank_id": e.bank_id,
        "display_label": e.display_label,
        "attributes": e.attributes,
        "risk_level": e.risk_level.value,
        "alert_count": e.alert_count,
        "first_seen": e.first_seen.isoformat(),
        "last_seen": e.last_seen.isoformat(),
    }


def _dict_to_entity(d: dict[str, Any]) -> Entity:
    from datetime import datetime

    d_copy = d.copy()
    d_copy["entity_type"] = EntityType(d_copy["entity_type"])
    d_copy["risk_level"] = RiskLevel(d_copy["risk_level"])
    d_copy["first_seen"] = datetime.fromisoformat(d_copy["first_seen"])
    d_copy["last_seen"] = datetime.fromisoformat(d_copy["last_seen"])
    return Entity(**d_copy)


def _relationship_to_dict(r: Relationship) -> dict[str, Any]:
    return {
        "id": r.id,
        "source_entity_id": r.source_entity_id,
        "target_entity_id": r.target_entity_id,
        "relationship_type": r.relationship_type.value,
        "confidence": r.confidence,
        "evidence": r.evidence,
        "created_at": r.created_at.isoformat(),
    }


def _dict_to_relationship(d: dict[str, Any]) -> Relationship:
    from datetime import datetime

    d_copy = d.copy()
    d_copy["relationship_type"] = RelationshipType(d_copy["relationship_type"])
    d_copy["created_at"] = datetime.fromisoformat(d_copy["created_at"])
    return Relationship(**d_copy)


# Color mapping for React Flow node types
_NODE_COLORS: dict[str, str] = {
    EntityType.CUSTOMER: "#6366f1",
    EntityType.MERCHANT: "#f59e0b",
    EntityType.DEVICE: "#14b8a6",
    EntityType.CARD: "#ec4899",
    EntityType.EMAIL: "#8b5cf6",
    EntityType.PHONE: "#06b6d4",
    EntityType.IP_ADDRESS: "#f43f5e",
}

_EDGE_STYLES: dict[str, dict] = {
    RelationshipType.OWNS: {"stroke": "#6366f1", "strokeWidth": 2},
    RelationshipType.USES: {"stroke": "#14b8a6", "strokeWidth": 1.5},
    RelationshipType.TRANSACTS_WITH: {"stroke": "#f59e0b", "strokeWidth": 2},
    RelationshipType.SHARES_DEVICE: {"stroke": "#ec4899", "strokeDasharray": "5,5"},
    RelationshipType.SHARES_IP: {"stroke": "#f43f5e", "strokeDasharray": "5,5"},
    RelationshipType.LINKED_ALERT: {"stroke": "#ef4444", "strokeWidth": 2.5},
    RelationshipType.SAME_ENTITY: {"stroke": "#a855f7", "strokeWidth": 3},
}


def _neo4j_node_to_entity(node) -> Entity:
    import json
    from datetime import datetime

    props = dict(node)
    if isinstance(props.get("first_seen"), str):
        props["first_seen"] = datetime.fromisoformat(props["first_seen"])
    if isinstance(props.get("last_seen"), str):
        props["last_seen"] = datetime.fromisoformat(props["last_seen"])
    props["entity_type"] = EntityType(props["entity_type"])
    props["risk_level"] = RiskLevel(props["risk_level"])
    if isinstance(props.get("attributes"), str):
        props["attributes"] = json.loads(props["attributes"])
    return Entity(**props)


def _neo4j_rel_to_relationship(rel) -> Relationship:
    from datetime import datetime

    props = dict(rel)
    if isinstance(props.get("created_at"), str):
        props["created_at"] = datetime.fromisoformat(props["created_at"])
    props["relationship_type"] = RelationshipType(props["relationship_type"])
    return Relationship(**props)


class GraphEngine:
    """Builds and queries the entity relationship graph.

    The graph is backed by a graph database (Neo4j, Memgraph, etc.)
    if enabled, or falls back to Redis / in-memory adjacency structures.
    """

    def __init__(self) -> None:
        self._entities = RedisStore("entity")
        self._relationships = RedisStore("relationship")
        self._adjacency: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)

        settings = get_settings()

        self.db_type = settings.graph_db_type
        self.driver = None
        if self.db_type in ("neo4j", "memgraph"):
            try:
                import importlib

                neo4j_mod = importlib.import_module("neo4j")
                self.driver = neo4j_mod.GraphDatabase.driver(
                    settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
                )
                logger.info("Connected to graph database of type: %s", self.db_type)

            except Exception as e:
                logger.error(
                    "Failed to connect to graph database of type %s: %s. Falling back to redis/in-memory.",
                    self.db_type,
                    e,
                )
                self.db_type = "redis"

    def _build_adjacency_list(self) -> None:
        self._adjacency = defaultdict(set)
        raw_relationships = self._relationships.list_values()
        for r_dict in raw_relationships:
            r = _dict_to_relationship(r_dict)
            self._adjacency[r.source_entity_id].add((r.target_entity_id, r.id))
            self._adjacency[r.target_entity_id].add((r.source_entity_id, r.id))

    def register_entity(self, entity: Entity) -> None:
        """Register an entity in the graph."""
        self._entities.set(entity.id, _entity_to_dict(entity))
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            import json

            with self.driver.session() as session:
                session.run(
                    "MERGE (e:Entity {id: $id}) "
                    "SET e.entity_type = $entity_type, "
                    "    e.privacy_id = $privacy_id, "
                    "    e.bank_id = $bank_id, "
                    "    e.display_label = $display_label, "
                    "    e.risk_level = $risk_level, "
                    "    e.alert_count = $alert_count, "
                    "    e.first_seen = $first_seen, "
                    "    e.last_seen = $last_seen, "
                    "    e.attributes = $attributes",
                    id=entity.id,
                    entity_type=entity.entity_type.value,
                    privacy_id=entity.privacy_id,
                    bank_id=entity.bank_id,
                    display_label=entity.display_label,
                    risk_level=entity.risk_level.value,
                    alert_count=entity.alert_count,
                    first_seen=entity.first_seen.isoformat(),
                    last_seen=entity.last_seen.isoformat(),
                    attributes=json.dumps(entity.attributes),
                )

    def register_entities(self, entities: list[Entity]) -> None:
        for entity in entities:
            self.register_entity(entity)

    def add_relationship(self, relationship: Relationship) -> None:
        """Add a relationship (edge) to the graph."""
        self._relationships.set(relationship.id, _relationship_to_dict(relationship))
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            rel_type = relationship.relationship_type.value
            query = (
                f"MATCH (s:Entity {{id: $source_id}}) "
                f"MATCH (t:Entity {{id: $target_id}}) "
                f"MERGE (s)-[r:{rel_type} {{id: $id}}]->(t) "
                "SET r.relationship_type = $relationship_type, "
                "    r.confidence = $confidence, "
                "    r.evidence = $evidence, "
                "    r.created_at = $created_at, "
                "    r.source_entity_id = $source_id, "
                "    r.target_entity_id = $target_id"
            )
            with self.driver.session() as session:
                session.run(
                    query,
                    id=relationship.id,
                    source_id=relationship.source_entity_id,
                    target_id=relationship.target_entity_id,
                    relationship_type=relationship.relationship_type.value,
                    confidence=relationship.confidence,
                    evidence=relationship.evidence,
                    created_at=relationship.created_at.isoformat(),
                )

    def find_neighbors(
        self,
        entity_id: str,
        depth: int = 1,
        relationship_types: set[RelationshipType] | None = None,
    ) -> list[Entity]:
        """BFS traversal to find neighbors up to a given depth."""
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            if relationship_types:
                rel_types_str = "|".join(rt.value for rt in relationship_types)
                query = (
                    f"MATCH (s:Entity {{id: $entity_id}})-[:{rel_types_str}*1..{depth}]-(n:Entity) "
                    "RETURN DISTINCT n"
                )
            else:
                query = (
                    f"MATCH (s:Entity {{id: $entity_id}})-[*1..{depth}]-(n:Entity) "
                    "RETURN DISTINCT n"
                )
            neighbors = []
            with self.driver.session() as session:
                result = session.run(query, entity_id=entity_id)
                for record in result:
                    neighbors.append(_neo4j_node_to_entity(record["n"]))
            return neighbors

        self._build_adjacency_list()
        visited: set[str] = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        neighbors = []

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            for neighbor_id, rel_id in self._adjacency.get(current_id, set()):
                if neighbor_id in visited:
                    continue

                # Filter by relationship type if specified
                if relationship_types:
                    rel_val = self._relationships.get(rel_id)
                    rel = _dict_to_relationship(rel_val) if rel_val else None
                    if rel and rel.relationship_type not in relationship_types:
                        continue

                visited.add(neighbor_id)
                entity_val = self._entities.get(neighbor_id)
                if entity_val:
                    entity = _dict_to_entity(entity_val)
                    neighbors.append(entity)
                    queue.append((neighbor_id, current_depth + 1))

        return neighbors

    def detect_clusters(self, min_size: int = 3) -> list[list[str]]:
        """Find connected components (clusters) of at least min_size."""
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            adjacency = defaultdict(set)
            all_nodes = set()
            with self.driver.session() as session:
                result = session.run(
                    "MATCH (n:Entity) "
                    "OPTIONAL MATCH (n)-[r]->(m:Entity) "
                    "RETURN n.id as source, m.id as target"
                )
                for record in result:
                    s = record["source"]
                    t = record["target"]
                    all_nodes.add(s)
                    if t:
                        all_nodes.add(t)
                        adjacency[s].add(t)
                        adjacency[t].add(s)

            visited = set()
            clusters = []
            for node_id in all_nodes:
                if node_id in visited:
                    continue
                component = []
                queue = deque([node_id])
                while queue:
                    curr = queue.popleft()
                    if curr in visited:
                        continue
                    visited.add(curr)
                    component.append(curr)
                    for neighbor in adjacency.get(curr, set()):
                        if neighbor not in visited:
                            queue.append(neighbor)
                if len(component) >= min_size:
                    clusters.append(component)
            clusters.sort(key=len, reverse=True)
            logger.info("Found %d clusters (min_size=%d) in graph DB", len(clusters), min_size)
            return clusters

        self._build_adjacency_list()
        fallback_visited: set[str] = set()
        fallback_clusters: list[list[str]] = []

        raw_entities = [_dict_to_entity(v) for v in self._entities.list_values()]
        for entity in raw_entities:
            entity_id = entity.id
            if entity_id in fallback_visited:
                continue

            # BFS to find the connected component
            fallback_component: list[str] = []
            fallback_queue: deque[str] = deque([entity_id])

            while fallback_queue:
                current = fallback_queue.popleft()
                if current in fallback_visited:
                    continue
                fallback_visited.add(current)
                fallback_component.append(current)

                for neighbor_id, _ in self._adjacency.get(current, set()):
                    if neighbor_id not in fallback_visited:
                        fallback_queue.append(neighbor_id)

            if len(fallback_component) >= min_size:
                fallback_clusters.append(fallback_component)

        # Sort clusters by size, largest first
        fallback_clusters.sort(key=len, reverse=True)
        logger.info("Found %d clusters (min_size=%d)", len(fallback_clusters), min_size)
        return fallback_clusters

    def get_subgraph(
        self,
        center_entity_id: str,
        radius: int = 2,
        max_nodes: int = 100,
    ) -> GraphSubgraph:
        """Extract a subgraph centered on an entity with bounded node budget."""
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            nodes_dict = {}
            rels_dict = {}
            with self.driver.session() as session:
                result = session.run(
                    "MATCH (s:Entity {id: $center_id}) "
                    "OPTIONAL MATCH p = (s)-[*1..$radius]-(n:Entity) "
                    "RETURN s, collect(p)[..$max_nodes] as paths",
                    center_id=center_entity_id,
                    radius=radius,
                    max_nodes=max_nodes,
                )
                record = result.single()
                if not record:
                    return GraphSubgraph(center_entity_id=center_entity_id, depth=radius)

                s_node = record["s"]
                s_entity = _neo4j_node_to_entity(s_node)
                nodes_dict[s_entity.id] = s_entity

                paths = record["paths"] or []
                for path in paths:
                    for node in path.nodes:
                        entity = _neo4j_node_to_entity(node)
                        nodes_dict[entity.id] = entity
                    for rel in path.relationships:
                        relationship = _neo4j_rel_to_relationship(rel)
                        rels_dict[relationship.id] = relationship

            node_ids = list(nodes_dict.keys())[:max_nodes]

            # Build React Flow nodes
            nodes = []
            for i, nid in enumerate(node_ids):
                entity = nodes_dict[nid]
                # Radial layout
                import math

                if nid == center_entity_id:
                    x: float = 400.0
                    y: float = 300.0
                else:
                    angle = 2 * math.pi * (i - 1) / max(1, len(node_ids) - 1)
                    layer = 1
                    for depth_check in range(radius):
                        if i > len(node_ids) * (depth_check + 1) / (radius + 1):
                            layer = depth_check + 2
                    r = 150 * layer
                    x = 400 + r * math.cos(angle)
                    y = 300 + r * math.sin(angle)

                color = _NODE_COLORS.get(entity.entity_type, "#6366f1")
                is_high_risk = entity.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

                nodes.append(
                    {
                        "id": nid,
                        "type": "default",
                        "position": {"x": round(x), "y": round(y)},
                        "data": {
                            "label": entity.display_label,
                            "entityType": entity.entity_type.value,
                            "bankId": entity.bank_id,
                            "riskLevel": entity.risk_level.value,
                            "alertCount": entity.alert_count,
                            "isCenter": nid == center_entity_id,
                        },
                        "style": {
                            "background": color if not is_high_risk else "#ef4444",
                            "color": "#ffffff",
                            "border": f"2px solid {'#ef4444' if is_high_risk else color}",
                            "borderRadius": "8px",
                            "padding": "8px 12px",
                            "fontSize": "11px",
                            "fontWeight": "600",
                        },
                    }
                )

            # Build React Flow edges
            edges = []
            node_id_set = set(node_ids)
            for rel in rels_dict.values():
                if rel.source_entity_id in node_id_set and rel.target_entity_id in node_id_set:
                    style = _EDGE_STYLES.get(rel.relationship_type, {})
                    edges.append(
                        {
                            "id": rel.id,
                            "source": rel.source_entity_id,
                            "target": rel.target_entity_id,
                            "label": rel.relationship_type.value.replace("_", " "),
                            "type": "smoothstep",
                            "animated": rel.relationship_type == RelationshipType.LINKED_ALERT,
                            "style": style,
                            "data": {
                                "confidence": rel.confidence,
                                "relationshipType": rel.relationship_type.value,
                            },
                        }
                    )

            # Detect clusters within subgraph
            self._adjacency = defaultdict(set)
            for rel in rels_dict.values():
                self._adjacency[rel.source_entity_id].add((rel.target_entity_id, rel.id))
                self._adjacency[rel.target_entity_id].add((rel.source_entity_id, rel.id))
            clusters = self._detect_subgraph_clusters(node_id_set)

            return GraphSubgraph(
                nodes=nodes,
                edges=edges,
                clusters=clusters,
                center_entity_id=center_entity_id,
                depth=radius,
            )

        self._build_adjacency_list()
        center_entity_val = self._entities.get(center_entity_id)
        if not center_entity_val:
            return GraphSubgraph(center_entity_id=center_entity_id, depth=radius)

        # Collect nodes via BFS bounded by max_nodes
        fb_visited: set[str] = {center_entity_id}
        fb_queue: deque[tuple[str, int]] = deque([(center_entity_id, 0)])
        fb_node_ids: list[str] = [center_entity_id]
        bounded_max_nodes = max(1, min(max_nodes, 200))

        while fb_queue and len(fb_node_ids) < bounded_max_nodes:
            current_id, current_depth = fb_queue.popleft()
            if current_depth >= radius:
                continue
            for neighbor_id, _ in self._adjacency.get(current_id, set()):
                if neighbor_id not in fb_visited:
                    fb_visited.add(neighbor_id)
                    fb_node_ids.append(neighbor_id)
                    fb_queue.append((neighbor_id, current_depth + 1))
                    if len(fb_node_ids) >= bounded_max_nodes:
                        break

        # Build React Flow nodes
        nodes = []
        for i, nid in enumerate(fb_node_ids):
            entity_val = self._entities.get(nid)
            if not entity_val:
                continue
            entity = _dict_to_entity(entity_val)

            # Radial layout
            import math

            if nid == center_entity_id:
                x = 400.0
                y = 300.0
            else:
                angle = 2 * math.pi * (i - 1) / max(1, len(fb_node_ids) - 1)
                layer = 1
                for depth_check in range(radius):
                    # Rough depth estimation
                    if i > len(fb_node_ids) * (depth_check + 1) / (radius + 1):
                        layer = depth_check + 2
                r = 150 * layer
                x = 400 + r * math.cos(angle)
                y = 300 + r * math.sin(angle)

            color = _NODE_COLORS.get(entity.entity_type, "#6366f1")
            is_high_risk = entity.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

            nodes.append(
                {
                    "id": nid,
                    "type": "default",
                    "position": {"x": round(x), "y": round(y)},
                    "data": {
                        "label": entity.display_label,
                        "entityType": entity.entity_type.value,
                        "bankId": entity.bank_id,
                        "riskLevel": entity.risk_level.value,
                        "alertCount": entity.alert_count,
                        "isCenter": nid == center_entity_id,
                    },
                    "style": {
                        "background": color if not is_high_risk else "#ef4444",
                        "color": "#ffffff",
                        "border": f"2px solid {'#ef4444' if is_high_risk else color}",
                        "borderRadius": "8px",
                        "padding": "8px 12px",
                        "fontSize": "11px",
                        "fontWeight": "600",
                    },
                }
            )

        # Build React Flow edges
        edges = []
        node_id_set = set(fb_node_ids)

        raw_relationships = [_dict_to_relationship(v) for v in self._relationships.list_values()]
        for rel in raw_relationships:
            if rel.source_entity_id in node_id_set and rel.target_entity_id in node_id_set:
                style = _EDGE_STYLES.get(rel.relationship_type, {})
                edges.append(
                    {
                        "id": rel.id,
                        "source": rel.source_entity_id,
                        "target": rel.target_entity_id,
                        "label": rel.relationship_type.value.replace("_", " "),
                        "type": "smoothstep",
                        "animated": rel.relationship_type == RelationshipType.LINKED_ALERT,
                        "style": style,
                        "data": {
                            "confidence": rel.confidence,
                            "relationshipType": rel.relationship_type.value,
                        },
                    }
                )

        # Detect clusters within subgraph
        clusters = self._detect_subgraph_clusters(node_id_set)

        return GraphSubgraph(
            nodes=nodes,
            edges=edges,
            clusters=clusters,
            center_entity_id=center_entity_id,
            depth=radius,
        )

    def search_nodes(
        self,
        query: str,
        entity_type: EntityType | None = None,
        limit: int = 20,
    ) -> list[Entity]:
        """Search entities by display label or privacy ID prefix."""
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            et_str = entity_type.value if entity_type else None
            query_lower = query.lower()
            results = []
            with self.driver.session() as session:
                res = session.run(
                    "MATCH (n:Entity) "
                    "WHERE ($entity_type IS NULL OR n.entity_type = $entity_type) "
                    "  AND (toLower(n.display_label) CONTAINS $query OR toLower(n.privacy_id) CONTAINS $query) "
                    "RETURN n LIMIT $limit",
                    entity_type=et_str,
                    query=query_lower,
                    limit=limit,
                )
                for r in res:
                    results.append(_neo4j_node_to_entity(r["n"]))
            return results

        query_lower = query.lower()
        results = []
        raw_entities = [_dict_to_entity(v) for v in self._entities.list_values()]
        for entity in raw_entities:
            if entity_type and entity.entity_type != entity_type:
                continue
            if (
                query_lower in entity.display_label.lower()
                or query_lower in entity.privacy_id.lower()
            ):
                results.append(entity)
            if len(results) >= limit:
                break
        return results

    @property
    def node_count(self) -> int:
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            with self.driver.session() as session:
                res = session.run("MATCH (n:Entity) RETURN count(n) as count")
                return res.single()["count"]
        return len(self._entities.list_values())

    @property
    def edge_count(self) -> int:
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            with self.driver.session() as session:
                res = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                return res.single()["count"]
        return len(self._relationships.list_values())

    def get_stats(self) -> dict:
        """Graph statistics for the dashboard."""
        if self.db_type in ("neo4j", "memgraph") and self.driver:
            with self.driver.session() as session:
                res_type = session.run(
                    "MATCH (n:Entity) RETURN n.entity_type as type, count(n) as count"
                )
                type_counts = {r["type"]: r["count"] for r in res_type}

                res_risk = session.run(
                    "MATCH (n:Entity) RETURN n.risk_level as risk, count(n) as count"
                )
                risk_counts = {r["risk"]: r["count"] for r in res_risk}

                total_nodes = sum(type_counts.values())
                total_edges = self.edge_count
                cluster_count = len(self.detect_clusters(min_size=3))

                return {
                    "total_nodes": total_nodes,
                    "total_edges": total_edges,
                    "nodes_by_type": type_counts,
                    "nodes_by_risk": risk_counts,
                    "cluster_count": cluster_count,
                    "database_backend": self.db_type.capitalize(),
                }

        fb_type_counts: dict[str, int] = defaultdict(int)
        fb_risk_counts: dict[str, int] = defaultdict(int)
        raw_entities = [_dict_to_entity(v) for v in self._entities.list_values()]
        for entity in raw_entities:
            fb_type_counts[entity.entity_type.value] += 1
            fb_risk_counts[entity.risk_level.value] += 1

        return {
            "total_nodes": self.node_count,
            "total_edges": self.edge_count,
            "nodes_by_type": dict(fb_type_counts),
            "nodes_by_risk": dict(fb_risk_counts),
            "cluster_count": len(self.detect_clusters(min_size=3)),
            "database_backend": "Redis (in-memory)",
        }

    # ── Private helpers ────────────────────────

    def _detect_subgraph_clusters(self, node_ids: set[str]) -> list[list[str]]:
        """Find clusters within a subset of nodes."""
        visited: set[str] = set()
        clusters: list[list[str]] = []

        for nid in node_ids:
            if nid in visited:
                continue
            component: list[str] = []
            queue: deque[str] = deque([nid])
            while queue:
                current = queue.popleft()
                if current in visited or current not in node_ids:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor_id, _ in self._adjacency.get(current, set()):
                    if neighbor_id not in visited and neighbor_id in node_ids:
                        queue.append(neighbor_id)
            if len(component) >= 2:
                clusters.append(component)

        return clusters
