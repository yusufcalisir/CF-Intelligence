"""Cross-Border Corporate UBO & Heterogeneous Graph Modeling Service.

Provides domain algorithms for:
1. Multi-tiered corporate ownership graphs & effective UBO computation (direct + indirect compounding).
2. Automated circular ownership detection (Tarjan/Johnson simple cycles).
3. Nominee director syndicate detection (interlocking directorship portfolios).
4. Shell company cluster and high-risk offshore concentration analysis.
5. Composite structural risk scoring and ego-subgraph visualization export.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

import networkx as nx

from app.application.schemas.ubo_schemas import (
    EffectiveBeneficialOwner,
    UBOAnomalyDetail,
    UBOAnomalyDetectionResponse,
    UBOCalculationResponse,
    UBOMetricsResponse,
    UBONodeCreate,
    UBONodeResponse,
    UBORelationCreate,
    UBORelationResponse,
    UBOSubgraphEdge,
    UBOSubgraphNode,
    UBOSubgraphResponse,
)
from app.domain.enums import UBOAnomalyType, UBONodeType, UBORelationType

logger = logging.getLogger(__name__)

# Non-cooperative or high-risk offshore tax jurisdictions (EU / FATF grey/blacklists)
HIGH_RISK_OFFSHORE_JURISDICTIONS = {
    "VG",  # British Virgin Islands
    "KY",  # Cayman Islands
    "PA",  # Panama
    "BZ",  # Belize
    "SC",  # Seychelles
    "MH",  # Marshall Islands
    "BS",  # Bahamas
    "BM",  # Bermuda
    "VU",  # Vanuatu
    "CK",  # Cook Islands
    "CW",  # Curaçao
    "GI",  # Gibraltar
    "LR",  # Liberia
    "WS",  # Samoa
}


class UBOGraphService:
    """Thread-safe, multi-tenant corporate UBO and heterogeneous graph service."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # tenant_id -> nx.DiGraph
        self._graphs: dict[str, nx.DiGraph] = defaultdict(nx.DiGraph)
        # tenant_id -> node_id -> UBONodeCreate
        self._nodes: dict[str, dict[str, UBONodeCreate]] = defaultdict(dict)
        # tenant_id -> rel_id -> UBORelationCreate
        self._relations: dict[str, dict[str, UBORelationCreate]] = defaultdict(dict)

    def _get_graph(self, tenant_id: str) -> nx.DiGraph:
        return self._graphs[tenant_id]

    def add_node(self, tenant_id: str, node: UBONodeCreate) -> UBONodeResponse:
        """Register or update a corporate entity or natural person in the graph."""
        with self._lock:
            g = self._get_graph(tenant_id)
            self._nodes[tenant_id][node.node_id] = node

            g.add_node(
                node.node_id,
                node_type=node.node_type.value,
                name=node.name,
                jurisdiction=node.jurisdiction.upper(),
                registration_number=node.registration_number,
                is_pep=node.is_pep,
                is_sanctioned=node.is_sanctioned,
                is_shell_suspect=node.is_shell_suspect,
                nominal_capital_eur=node.nominal_capital_eur,
                registered_address=node.registered_address,
                metadata=node.metadata,
            )

            risk = self._compute_node_intrinsic_risk(node)
            return UBONodeResponse(
                node_id=node.node_id,
                node_type=node.node_type,
                name=node.name,
                jurisdiction=node.jurisdiction.upper(),
                registration_number=node.registration_number,
                incorporation_date=node.incorporation_date,
                is_pep=node.is_pep,
                is_sanctioned=node.is_sanctioned,
                is_shell_suspect=node.is_shell_suspect,
                nominal_capital_eur=node.nominal_capital_eur,
                registered_address=node.registered_address,
                risk_score=risk,
                metadata=node.metadata,
            )

    def get_node(self, tenant_id: str, node_id: str) -> UBONodeResponse | None:
        """Retrieve node details if registered."""
        with self._lock:
            node = self._nodes[tenant_id].get(node_id)
            if not node:
                return None
            risk = self._compute_node_intrinsic_risk(node)
            return UBONodeResponse(
                node_id=node.node_id,
                node_type=node.node_type,
                name=node.name,
                jurisdiction=node.jurisdiction.upper(),
                registration_number=node.registration_number,
                incorporation_date=node.incorporation_date,
                is_pep=node.is_pep,
                is_sanctioned=node.is_sanctioned,
                is_shell_suspect=node.is_shell_suspect,
                nominal_capital_eur=node.nominal_capital_eur,
                registered_address=node.registered_address,
                risk_score=risk,
                metadata=node.metadata,
            )

    def add_relation(
        self, tenant_id: str, relation: UBORelationCreate
    ) -> UBORelationResponse:
        """Add a directed relationship edge between corporate nodes."""
        with self._lock:
            # Ensure both nodes exist
            if relation.source_id not in self._nodes[tenant_id]:
                raise ValueError(
                    f"Source node '{relation.source_id}' is not registered in tenant '{tenant_id}'."
                )
            if relation.target_id not in self._nodes[tenant_id]:
                raise ValueError(
                    f"Target node '{relation.target_id}' is not registered in tenant '{tenant_id}'."
                )

            g = self._get_graph(tenant_id)
            rel_id = f"UBO-REL-{len(self._relations[tenant_id]) + 1:04d}"
            self._relations[tenant_id][rel_id] = relation

            # Directed edge from source (owner/director) to target (company/account)
            g.add_edge(
                relation.source_id,
                relation.target_id,
                rel_id=rel_id,
                relation_type=relation.relation_type.value,
                ownership_percentage=relation.ownership_percentage,
                voting_percentage=relation.voting_percentage,
                is_nominee=relation.is_nominee,
                effective_date=relation.effective_date,
                metadata=relation.metadata,
            )

            return UBORelationResponse(
                relation_id=rel_id,
                source_id=relation.source_id,
                target_id=relation.target_id,
                relation_type=relation.relation_type,
                ownership_percentage=relation.ownership_percentage,
                voting_percentage=relation.voting_percentage,
                is_nominee=relation.is_nominee,
                effective_date=relation.effective_date,
            )

    def calculate_effective_ownership(
        self,
        tenant_id: str,
        target_entity_id: str,
        statutory_threshold: float = 25.0,
        max_depth: int = 8,
    ) -> UBOCalculationResponse:
        """Compute effective beneficial ownership across direct and multi-hop holding paths.

        Formula for path compounded ownership:
            PathWeight = Product(percentage_i / 100.0) * 100.0
        TotalEffectiveOwnership(P, E) = Sum(PathWeight for each path from P to E)
        """
        with self._lock:
            target_node = self._nodes[tenant_id].get(target_entity_id)
            if not target_node:
                raise ValueError(
                    f"Entity '{target_entity_id}' not found in tenant '{tenant_id}'."
                )

            g = self._get_graph(tenant_id)

            # Discover all ancestors via DFS traversal along ownership edges
            # Trace backwards: in-edges represent owners of target
            # Path structure: [natural_person, holdco_1, ..., target_entity]
            ownership_rel_types = {
                UBORelationType.DIRECT_OWNERSHIP.value,
                UBORelationType.INDIRECT_OWNERSHIP.value,
                UBORelationType.SUBSIDIARY_OF.value,
                UBORelationType.BENEFICIAL_OWNER_OF.value,
            }

            person_ownership_direct: dict[str, float] = defaultdict(float)
            person_ownership_indirect: dict[str, float] = defaultdict(float)
            person_paths: dict[str, list[list[str]]] = defaultdict(list)
            max_depth_observed = 0

            def _dfs_trace_paths(
                current_node_id: str,
                current_fraction: float,
                visited: list[str],
            ) -> None:
                nonlocal max_depth_observed
                depth = len(visited)
                if depth > max_depth_observed:
                    max_depth_observed = depth

                if depth > max_depth:
                    return

                # Find all in-edges to current_node_id (who owns current_node_id?)
                in_edges = g.in_edges(current_node_id, data=True)
                for parent_id, _, data in in_edges:
                    rel_type = data.get("relation_type")
                    if rel_type not in ownership_rel_types:
                        continue

                    # Avoid infinite cycle traversal
                    if parent_id in visited:
                        continue

                    pct = float(data.get("ownership_percentage", 0.0))
                    if pct <= 0.0:
                        continue

                    new_fraction = current_fraction * (pct / 100.0)
                    new_visited = [parent_id] + visited

                    parent_data = self._nodes[tenant_id].get(parent_id)
                    if parent_data and parent_data.node_type == UBONodeType.NATURAL_PERSON:
                        # Found a natural person ultimate owner!
                        pct_value = new_fraction * 100.0
                        if len(new_visited) == 2:
                            # Direct edge: [person, target]
                            person_ownership_direct[parent_id] += pct_value
                        else:
                            person_ownership_indirect[parent_id] += pct_value
                        person_paths[parent_id].append(new_visited)
                    else:
                        # Intermediary holding company, keep traversing upward
                        _dfs_trace_paths(parent_id, new_fraction, new_visited)

            _dfs_trace_paths(target_entity_id, 1.0, [target_entity_id])

            beneficial_owners: list[EffectiveBeneficialOwner] = []
            total_identified = 0.0

            all_persons = set(person_ownership_direct.keys()) | set(
                person_ownership_indirect.keys()
            )
            for pid in sorted(all_persons):
                p_node = self._nodes[tenant_id][pid]
                direct = round(person_ownership_direct[pid], 4)
                indirect = round(person_ownership_indirect[pid], 4)
                total = round(direct + indirect, 4)
                meets_threshold = total >= statutory_threshold

                total_identified += total
                beneficial_owners.append(
                    EffectiveBeneficialOwner(
                        person_id=pid,
                        name=p_node.name,
                        jurisdiction=p_node.jurisdiction,
                        direct_ownership_percent=min(direct, 100.0),
                        indirect_ownership_percent=min(indirect, 100.0),
                        total_effective_percentage=min(total, 100.0),
                        meets_statutory_threshold=meets_threshold,
                        ownership_paths=person_paths[pid],
                        is_pep=p_node.is_pep,
                        is_sanctioned=p_node.is_sanctioned,
                    )
                )

            # Sort by total effective percentage descending
            beneficial_owners.sort(
                key=lambda x: x.total_effective_percentage, reverse=True
            )

            total_identified = min(round(total_identified, 2), 100.0)
            unidentified = round(max(0.0, 100.0 - total_identified), 2)

            return UBOCalculationResponse(
                entity_id=target_entity_id,
                entity_name=target_node.name,
                statutory_threshold_percent=statutory_threshold,
                beneficial_owners=beneficial_owners,
                total_identified_ownership_percent=total_identified,
                unidentified_ownership_percent=unidentified,
                max_depth_traversed=max_depth_observed,
            )

    def detect_circular_ownership(
        self, tenant_id: str
    ) -> list[list[str]]:
        """Detect circular ownership loops across the corporate graph using simple cycles."""
        with self._lock:
            g = self._get_graph(tenant_id)
            if len(g) == 0:
                return []

            # Subgraph with only ownership edges
            ownership_rel_types = {
                UBORelationType.DIRECT_OWNERSHIP.value,
                UBORelationType.INDIRECT_OWNERSHIP.value,
                UBORelationType.SUBSIDIARY_OF.value,
            }

            ownership_edges = [
                (u, v)
                for u, v, d in g.edges(data=True)
                if d.get("relation_type") in ownership_rel_types
            ]
            sub_g = g.edge_subgraph(ownership_edges)

            try:
                raw_cycles = list(nx.simple_cycles(sub_g))
            except Exception as e:
                logger.warning("Error running cycle detection: %s", e)
                return []

            # Format cycles with loop closure: [A, B, C, A]
            closed_cycles: list[list[str]] = []
            for c in raw_cycles:
                if len(c) >= 2:
                    closed_cycles.append(c + [c[0]])

            return closed_cycles

    def identify_nominee_directors(
        self, tenant_id: str, min_entities: int = 5
    ) -> list[dict[str, Any]]:
        """Identify natural persons serving as directors for an abnormally large company portfolio."""
        with self._lock:
            g = self._get_graph(tenant_id)
            nominees: list[dict[str, Any]] = []

            for node_id, data in g.nodes(data=True):
                if data.get("node_type") != UBONodeType.NATURAL_PERSON.value:
                    continue

                # Count directorships
                directorship_edges = [
                    (u, v, d)
                    for u, v, d in g.out_edges(node_id, data=True)
                    if d.get("relation_type")
                    in {
                        UBORelationType.DIRECTOR_OF.value,
                        UBORelationType.NOMINEE_DIRECTOR.value,
                    }
                    or d.get("is_nominee") is True
                ]

                is_explicit_nominee = any(
                    d.get("is_nominee") is True
                    or d.get("relation_type") == UBORelationType.NOMINEE_DIRECTOR.value
                    for _, _, d in directorship_edges
                )

                if len(directorship_edges) >= min_entities or is_explicit_nominee:
                    managed_entities = [v for _, v, _ in directorship_edges]
                    nominees.append(
                        {
                            "director_id": node_id,
                            "name": data.get("name"),
                            "jurisdiction": data.get("jurisdiction"),
                            "directorship_count": len(directorship_edges),
                            "managed_entities": managed_entities,
                            "is_explicit_nominee": is_explicit_nominee,
                            "severity": "CRITICAL"
                            if len(directorship_edges) >= 10
                            else "HIGH",
                        }
                    )

            return nominees

    def detect_shell_clusters(
        self, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Identify offshore corporate clusters with hallmarks of shell/letterbox entities."""
        with self._lock:
            g = self._get_graph(tenant_id)
            shell_clusters: list[dict[str, Any]] = []

            for node_id, data in g.nodes(data=True):
                jurisdiction = data.get("jurisdiction", "").upper()
                is_offshore = jurisdiction in HIGH_RISK_OFFSHORE_JURISDICTIONS
                is_shell_suspect = data.get("is_shell_suspect", False)
                nominal_capital = data.get("nominal_capital_eur")

                # Low capital threshold (< 1,000 EUR) in offshore tax havens
                low_capital = (
                    nominal_capital is not None and nominal_capital < 1000.0
                )

                if is_offshore or is_shell_suspect or low_capital:
                    connected_nodes = list(nx.all_neighbors(g, node_id))
                    shell_clusters.append(
                        {
                            "entity_id": node_id,
                            "name": data.get("name"),
                            "jurisdiction": jurisdiction,
                            "is_offshore_tax_haven": is_offshore,
                            "is_shell_suspect": is_shell_suspect,
                            "low_nominal_capital": low_capital,
                            "connected_entities_count": len(connected_nodes),
                            "risk_penalty": 250.0 if is_offshore else 150.0,
                        }
                    )

            return shell_clusters

    def analyze_structural_anomalies(
        self, tenant_id: str, target_entity_id: str | None = None
    ) -> UBOAnomalyDetectionResponse:
        """Generate comprehensive structural anomaly audit across cycles, nominees, and shells."""
        with self._lock:
            anomalies: list[UBOAnomalyDetail] = []
            base_risk = 50.0

            # 1. Circular ownership detection
            cycles = self.detect_circular_ownership(tenant_id)
            if target_entity_id:
                # Filter cycles containing target_entity_id
                target_cycles = [c for c in cycles if target_entity_id in c]
            else:
                target_cycles = cycles

            if target_cycles:
                for c in target_cycles:
                    anomalies.append(
                        UBOAnomalyDetail(
                            anomaly_type=UBOAnomalyType.CIRCULAR_OWNERSHIP,
                            severity="CRITICAL",
                            title="Circular Corporate Ownership Loop Detected",
                            description=(
                                f"Circular ownership detected across {len(c)-1} corporate entities: "
                                f"{' -> '.join(c)}. Typology indicates deliberate opacity / sanctions evasion."
                            ),
                            involved_node_ids=c[:-1],
                            risk_score_impact=400.0,
                        )
                    )
                base_risk += 400.0

            # 2. Nominee directorship syndicate detection
            nominees = self.identify_nominee_directors(tenant_id)
            if target_entity_id:
                nominees = [
                    n
                    for n in nominees
                    if target_entity_id in n.get("managed_entities", [])
                ]

            if nominees:
                for n in nominees:
                    anomalies.append(
                        UBOAnomalyDetail(
                            anomaly_type=UBOAnomalyType.NOMINEE_DIRECTOR_SYNDICATE,
                            severity=n.get("severity", "HIGH"),
                            title="Suspected Nominee Director Syndicate",
                            description=(
                                f"Director {n.get('name')} ({n.get('director_id')}) administers "
                                f"{n.get('directorship_count')} corporate entities across the consortium."
                            ),
                            involved_node_ids=[n.get("director_id")]
                            + n.get("managed_entities", [])[:5],
                            risk_score_impact=200.0,
                        )
                    )
                base_risk += 200.0

            # 3. High-risk offshore / shell clusters
            shells = self.detect_shell_clusters(tenant_id)
            if target_entity_id:
                shells = [s for s in shells if s.get("entity_id") == target_entity_id]

            if shells:
                for s in shells:
                    anomalies.append(
                        UBOAnomalyDetail(
                            anomaly_type=UBOAnomalyType.HIGH_RISK_OFFSHORE_CONCENTRATION
                            if s.get("is_offshore_tax_haven")
                            else UBOAnomalyType.SHELL_COMPANY_CLUSTER,
                            severity="HIGH",
                            title="High-Risk Offshore Shell Structure",
                            description=(
                                f"Entity {s.get('name')} registered in {s.get('jurisdiction')} "
                                f"exhibits shell attributes (low capital: {s.get('low_nominal_capital')})."
                            ),
                            involved_node_ids=[s.get("entity_id")],
                            risk_score_impact=250.0,
                        )
                    )
                base_risk += 250.0

            # 4. Check if entity has PEP or sanctioned beneficial owners
            if target_entity_id and target_entity_id in self._nodes[tenant_id]:
                try:
                    ubo_res = self.calculate_effective_ownership(
                        tenant_id, target_entity_id
                    )
                    pep_ubos = [
                        u
                        for u in ubo_res.beneficial_owners
                        if u.is_pep or u.is_sanctioned
                    ]
                    for u in pep_ubos:
                        sev = "CRITICAL" if u.is_sanctioned else "HIGH"
                        anomalies.append(
                            UBOAnomalyDetail(
                                anomaly_type=UBOAnomalyType.PEP_SANCTIONED_BENEFICIARY,
                                severity=sev,
                                title="Sanctioned / PEP Beneficial Owner Identified",
                                description=(
                                    f"Beneficial owner {u.name} (Effective: {u.total_effective_percentage}%) "
                                    f"flags as {'SANCTIONED' if u.is_sanctioned else 'PEP'}."
                                ),
                                involved_node_ids=[u.person_id, target_entity_id],
                                risk_score_impact=350.0,
                            )
                        )
                        base_risk += 350.0
                except Exception as e:
                    logger.debug("Failed UBO evaluation for anomaly scoring: %s", e)

            final_risk = min(round(base_risk, 1), 1000.0)
            if final_risk >= 800.0:
                level = "CRITICAL"
            elif final_risk >= 600.0:
                level = "HIGH"
            elif final_risk >= 400.0:
                level = "MEDIUM"
            elif final_risk >= 200.0:
                level = "LOW"
            else:
                level = "MINIMAL"

            return UBOAnomalyDetectionResponse(
                entity_id=target_entity_id,
                anomalies=anomalies,
                circular_ownership_cycles=target_cycles,
                nominee_directors=nominees,
                shell_company_clusters=shells,
                structural_risk_score=final_risk,
                risk_level=level,
            )

    def get_visualization_subgraph(
        self, tenant_id: str, root_id: str, max_hops: int = 3
    ) -> UBOSubgraphResponse:
        """Extract ego-network subgraph centered at root_id for interactive UI rendering."""
        with self._lock:
            g = self._get_graph(tenant_id)
            if root_id not in g:
                raise ValueError(
                    f"Node '{root_id}' not found in corporate graph for tenant '{tenant_id}'."
                )

            # Extract undirected ego graph up to max_hops to capture both owners and children
            undirected = g.to_undirected(as_view=True)
            ego_nodes = set(
                nx.single_source_shortest_path_length(
                    undirected, root_id, cutoff=max_hops
                ).keys()
            )

            sub_g = g.subgraph(ego_nodes)

            sub_nodes: list[UBOSubgraphNode] = []
            for n_id in sub_g.nodes():
                node_data = self._nodes[tenant_id].get(n_id)
                if node_data:
                    risk = self._compute_node_intrinsic_risk(node_data)
                    sub_nodes.append(
                        UBOSubgraphNode(
                            id=n_id,
                            label=node_data.name,
                            node_type=node_data.node_type.value,
                            jurisdiction=node_data.jurisdiction,
                            risk_score=risk,
                            is_pep=node_data.is_pep,
                            is_sanctioned=node_data.is_sanctioned,
                            is_shell_suspect=node_data.is_shell_suspect,
                        )
                    )

            sub_edges: list[UBOSubgraphEdge] = []
            for u, v, d in sub_g.edges(data=True):
                sub_edges.append(
                    UBOSubgraphEdge(
                        source=u,
                        target=v,
                        relation_type=d.get("relation_type", "OWNS"),
                        ownership_percentage=float(
                            d.get("ownership_percentage", 0.0)
                        ),
                        is_nominee=bool(d.get("is_nominee", False)),
                    )
                )

            return UBOSubgraphResponse(
                root_id=root_id,
                nodes=sub_nodes,
                edges=sub_edges,
                total_nodes=len(sub_nodes),
                total_edges=len(sub_edges),
            )

    def get_metrics(self, tenant_id: str) -> UBOMetricsResponse:
        """Compute consortium graph analytics metrics."""
        with self._lock:
            g = self._get_graph(tenant_id)
            total_nodes = len(g)
            entities = sum(
                1
                for _, d in g.nodes(data=True)
                if d.get("node_type") != UBONodeType.NATURAL_PERSON.value
            )
            persons = sum(
                1
                for _, d in g.nodes(data=True)
                if d.get("node_type") == UBONodeType.NATURAL_PERSON.value
            )
            total_rels = g.number_of_edges()
            cycles = len(self.detect_circular_ownership(tenant_id))
            nominees = len(self.identify_nominee_directors(tenant_id))
            shells = len(self.detect_shell_clusters(tenant_id))

            return UBOMetricsResponse(
                total_nodes=total_nodes,
                total_entities=entities,
                total_natural_persons=persons,
                total_relations=total_rels,
                total_cycles_detected=cycles,
                nominee_directors_count=nominees,
                high_risk_offshore_count=shells,
            )

    def _compute_node_intrinsic_risk(self, node: UBONodeCreate) -> float:
        """Calculate intrinsic baseline risk score for an isolated node."""
        score = 50.0
        if node.is_sanctioned:
            score += 700.0
        if node.is_pep:
            score += 250.0
        if node.is_shell_suspect:
            score += 300.0
        if node.jurisdiction.upper() in HIGH_RISK_OFFSHORE_JURISDICTIONS:
            score += 200.0
        return min(round(score, 1), 1000.0)


# Global thread-safe singleton
_ubo_service_instance: UBOGraphService | None = None
_ubo_service_lock = threading.Lock()


def get_ubo_graph_service() -> UBOGraphService:
    """Retrieve global singleton instance of UBOGraphService."""
    global _ubo_service_instance
    if _ubo_service_instance is None:
        with _ubo_service_lock:
            if _ubo_service_instance is None:
                _ubo_service_instance = UBOGraphService()
    return _ubo_service_instance
