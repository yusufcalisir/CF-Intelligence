"""Unit and integration tests for Cross-Border Corporate UBO & Heterogeneous Graph Modeling.

Verifies:
1. Multi-tier corporate ownership graph construction and direct/indirect UBO compounding.
2. Multiple parallel ownership paths and EU AMLD 25% statutory threshold enforcement.
3. Automated circular ownership detection (simple directed cycles).
4. Nominee director syndicate detection with interlocking directorship thresholds.
5. Shell company cluster and non-cooperative offshore jurisdiction flags.
6. Composite structural risk scoring and ego-subgraph visualization export.
7. Multi-tenant bank isolation boundaries.
8. REST API endpoints under canonical and root prefixes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.ubo_schemas import (
    UBONodeCreate,
    UBORelationCreate,
)
from app.application.services.ubo_graph_service import (
    UBOGraphService,
)
from app.domain.enums import UBONodeType, UBORelationType
from app.main import app


@pytest.fixture
def ubo_service() -> UBOGraphService:
    """Fresh isolated UBOGraphService instance for unit testing."""
    return UBOGraphService()


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient for API router testing."""
    return TestClient(app)


class TestUBOGraphService:
    """Core domain algorithmic and state testing for UBOGraphService."""

    def test_add_and_get_corporate_node(self, ubo_service: UBOGraphService) -> None:
        tenant = "bank_test_1"
        node = UBONodeCreate(
            node_id="ENT_ALPHA",
            node_type=UBONodeType.LEGAL_ENTITY,
            name="Alpha Corp GmbH",
            jurisdiction="DE",
            registration_number="HRB 99120",
            nominal_capital_eur=100000.0,
        )
        created = ubo_service.add_node(tenant, node)
        assert created.node_id == "ENT_ALPHA"
        assert created.name == "Alpha Corp GmbH"
        assert created.jurisdiction == "DE"
        assert created.risk_score >= 50.0

        retrieved = ubo_service.get_node(tenant, "ENT_ALPHA")
        assert retrieved is not None
        assert retrieved.registration_number == "HRB 99120"

        # Non-existent node returns None
        assert ubo_service.get_node(tenant, "NON_EXISTENT") is None

    def test_add_relation_success_and_validation(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_2"
        # Register two nodes
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="PER_1",
                node_type=UBONodeType.NATURAL_PERSON,
                name="John Doe",
                jurisdiction="DE",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="ENT_1",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Target Co",
                jurisdiction="DE",
            ),
        )

        rel = UBORelationCreate(
            source_id="PER_1",
            target_id="ENT_1",
            relation_type=UBORelationType.DIRECT_OWNERSHIP,
            ownership_percentage=100.0,
        )
        created_rel = ubo_service.add_relation(tenant, rel)
        assert created_rel.source_id == "PER_1"
        assert created_rel.target_id == "ENT_1"
        assert created_rel.ownership_percentage == 100.0

        # Unregistered source node raises ValueError
        with pytest.raises(ValueError, match="Source node 'UNKNOWN' is not registered"):
            ubo_service.add_relation(
                tenant,
                UBORelationCreate(
                    source_id="UNKNOWN",
                    target_id="ENT_1",
                    relation_type=UBORelationType.DIRECT_OWNERSHIP,
                    ownership_percentage=50.0,
                ),
            )

    def test_direct_beneficial_ownership_calculation(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_3"
        # Person 1 owns 80% directly, Person 2 owns 20% directly
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="P1",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Alice Smith",
                jurisdiction="DE",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="P2",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Bob Jones",
                jurisdiction="FR",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="COMPANY_A",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Direct Venture GmbH",
                jurisdiction="DE",
            ),
        )

        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="P1",
                target_id="COMPANY_A",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=80.0,
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="P2",
                target_id="COMPANY_A",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=20.0,
            ),
        )

        res = ubo_service.calculate_effective_ownership(
            tenant, "COMPANY_A", statutory_threshold=25.0
        )
        assert res.entity_id == "COMPANY_A"
        assert len(res.beneficial_owners) == 2

        alice = next(u for u in res.beneficial_owners if u.person_id == "P1")
        assert alice.direct_ownership_percent == 80.0
        assert alice.indirect_ownership_percent == 0.0
        assert alice.total_effective_percentage == 80.0
        assert alice.meets_statutory_threshold is True

        bob = next(u for u in res.beneficial_owners if u.person_id == "P2")
        assert bob.direct_ownership_percent == 20.0
        assert bob.total_effective_percentage == 20.0
        assert bob.meets_statutory_threshold is False  # 20% < 25% threshold

        assert res.total_identified_ownership_percent == 100.0
        assert res.unidentified_ownership_percent == 0.0

    def test_multihop_indirect_beneficial_ownership(
        self, ubo_service: UBOGraphService
    ) -> None:
        """Person -> 60% -> HoldCo 1 -> 50% -> OpCo.

        Effective indirect ownership = 0.60 * 0.50 = 30.0% >= 25% threshold.
        """
        tenant = "bank_test_4"
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="PERSON_FOUNDER",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Elena Rostova",
                jurisdiction="AT",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="HOLDCO_1",
                node_type=UBONodeType.HOLDING_COMPANY,
                name="Alpine Holdings AG",
                jurisdiction="AT",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="OPCO_FINAL",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Danube Logistics GmbH",
                jurisdiction="DE",
            ),
        )

        # PERSON -> HOLDCO (60%)
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="PERSON_FOUNDER",
                target_id="HOLDCO_1",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=60.0,
            ),
        )
        # HOLDCO -> OPCO (50%)
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="HOLDCO_1",
                target_id="OPCO_FINAL",
                relation_type=UBORelationType.SUBSIDIARY_OF,
                ownership_percentage=50.0,
            ),
        )

        res = ubo_service.calculate_effective_ownership(tenant, "OPCO_FINAL")
        assert len(res.beneficial_owners) == 1
        founder = res.beneficial_owners[0]
        assert founder.person_id == "PERSON_FOUNDER"
        assert founder.direct_ownership_percent == 0.0
        assert founder.indirect_ownership_percent == 30.0
        assert founder.total_effective_percentage == 30.0
        assert founder.meets_statutory_threshold is True
        assert res.max_depth_traversed >= 2

    def test_multiple_parallel_ownership_paths(
        self, ubo_service: UBOGraphService
    ) -> None:
        """Person A holds:

        1. Direct: 15% in Target Co.
        2. Indirect: 50% in HoldCo B, which holds 25% in Target Co (0.50 * 0.25 = 12.5%).
        Total compounded ownership = 15.0% + 12.5% = 27.5% >= 25% threshold!
        """
        tenant = "bank_test_5"
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="PARALLEL_INVESTOR",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Marcus Aurelius",
                jurisdiction="IT",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="HOLDCO_B",
                node_type=UBONodeType.HOLDING_COMPANY,
                name="Roma Investments SpA",
                jurisdiction="IT",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="TARGET_VENTURE",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Tiber Technologies Srl",
                jurisdiction="IT",
            ),
        )

        # Path 1: Direct 15%
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="PARALLEL_INVESTOR",
                target_id="TARGET_VENTURE",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=15.0,
            ),
        )
        # Path 2: Investor -> 50% -> HoldCo B -> 25% -> Target
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="PARALLEL_INVESTOR",
                target_id="HOLDCO_B",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=50.0,
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="HOLDCO_B",
                target_id="TARGET_VENTURE",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=25.0,
            ),
        )

        res = ubo_service.calculate_effective_ownership(tenant, "TARGET_VENTURE")
        assert len(res.beneficial_owners) == 1
        marcus = res.beneficial_owners[0]
        assert marcus.direct_ownership_percent == 15.0
        assert marcus.indirect_ownership_percent == 12.5
        assert marcus.total_effective_percentage == 27.5
        assert marcus.meets_statutory_threshold is True
        assert len(marcus.ownership_paths) == 2

    def test_circular_ownership_detection(
        self, ubo_service: UBOGraphService
    ) -> None:
        """Construct circular ownership: Entity A -> Entity B -> Entity C -> Entity A."""
        tenant = "bank_test_6"
        for entity_id in ["LOOP_A", "LOOP_B", "LOOP_C"]:
            ubo_service.add_node(
                tenant,
                UBONodeCreate(
                    node_id=entity_id,
                    node_type=UBONodeType.LEGAL_ENTITY,
                    name=f"Loop Entity {entity_id}",
                    jurisdiction="NL",
                ),
            )

        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="LOOP_A",
                target_id="LOOP_B",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=100.0,
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="LOOP_B",
                target_id="LOOP_C",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=100.0,
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="LOOP_C",
                target_id="LOOP_A",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=100.0,
            ),
        )

        cycles = ubo_service.detect_circular_ownership(tenant)
        assert len(cycles) >= 1
        # Loop closure check: first element equals last element
        assert cycles[0][0] == cycles[0][-1]
        assert len(cycles[0]) == 4  # e.g. [A, B, C, A]

    def test_nominee_director_syndicate_detection(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_7"
        # Register a natural person nominee
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="NOMINEE_P",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Ivan Shadow",
                jurisdiction="CY",
            ),
        )
        # Register 6 entities managed by NOMINEE_P
        for i in range(6):
            e_id = f"SHELL_CORP_{i}"
            ubo_service.add_node(
                tenant,
                UBONodeCreate(
                    node_id=e_id,
                    node_type=UBONodeType.LEGAL_ENTITY,
                    name=f"Shell Corp {i}",
                    jurisdiction="CY",
                ),
            )
            ubo_service.add_relation(
                tenant,
                UBORelationCreate(
                    source_id="NOMINEE_P",
                    target_id=e_id,
                    relation_type=UBORelationType.DIRECTOR_OF,
                ),
            )

        nominees = ubo_service.identify_nominee_directors(tenant, min_entities=5)
        assert len(nominees) == 1
        assert nominees[0]["director_id"] == "NOMINEE_P"
        assert nominees[0]["directorship_count"] == 6

    def test_shell_company_cluster_detection(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_8"
        # High-risk offshore shell in BVI with low capital
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="BVI_SHELL_1",
                node_type=UBONodeType.OFFSHORE_SHELL,
                name="Ocean Horizon Ltd",
                jurisdiction="VG",  # British Virgin Islands
                nominal_capital_eur=500.0,
                is_shell_suspect=True,
            ),
        )
        # Standard domestic entity in Germany
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="DE_REAL_1",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Stuttgart Real Parts GmbH",
                jurisdiction="DE",
                nominal_capital_eur=50000.0,
            ),
        )

        shells = ubo_service.detect_shell_clusters(tenant)
        assert len(shells) == 1
        assert shells[0]["entity_id"] == "BVI_SHELL_1"
        assert shells[0]["is_offshore_tax_haven"] is True

    def test_structural_anomaly_audit_and_pep_escalation(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_9"
        # Register PEP owner
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="PEP_MINISTER",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Minister X",
                jurisdiction="FR",
                is_pep=True,
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="PROCUREMENT_CO",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="State Supply SA",
                jurisdiction="FR",
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="PEP_MINISTER",
                target_id="PROCUREMENT_CO",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=51.0,
            ),
        )

        audit = ubo_service.analyze_structural_anomalies(tenant, "PROCUREMENT_CO")
        assert audit.entity_id == "PROCUREMENT_CO"
        pep_anomalies = [
            a for a in audit.anomalies if a.anomaly_type == "PEP_SANCTIONED_BENEFICIARY"
        ]
        assert len(pep_anomalies) == 1
        assert audit.structural_risk_score >= 350.0

    def test_subgraph_visualization_extraction(
        self, ubo_service: UBOGraphService
    ) -> None:
        tenant = "bank_test_10"
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="ROOT_CO",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Root Enterprise AG",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="SUB_CO",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Sub Operations Ltd",
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="ROOT_CO",
                target_id="SUB_CO",
                relation_type=UBORelationType.SUBSIDIARY_OF,
                ownership_percentage=100.0,
            ),
        )

        subgraph = ubo_service.get_visualization_subgraph(
            tenant, root_id="ROOT_CO", max_hops=2
        )
        assert subgraph.root_id == "ROOT_CO"
        assert subgraph.total_nodes == 2
        assert subgraph.total_edges == 1
        assert subgraph.nodes[0].label in ["Root Enterprise AG", "Sub Operations Ltd"]

    def test_multi_tenant_isolation(self, ubo_service: UBOGraphService) -> None:
        """Assert strict partition isolation between banks."""
        ubo_service.add_node(
            "bank_alpha",
            UBONodeCreate(
                node_id="EXCLUSIVE_ALPHA",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Alpha Exclusive Holdings",
            ),
        )

        # bank_alpha sees it
        assert ubo_service.get_node("bank_alpha", "EXCLUSIVE_ALPHA") is not None
        # bank_beta does not see it
        assert ubo_service.get_node("bank_beta", "EXCLUSIVE_ALPHA") is None


class TestUBOGraphRouter:
    """End-to-end FastAPI endpoint integration tests for corporate UBO graph."""

    def test_create_and_get_node_api(self, client: TestClient) -> None:
        payload = {
            "node_id": "API_NODE_1",
            "node_type": "LEGAL_ENTITY",
            "name": "Fintech Solutions NV",
            "jurisdiction": "NL",
            "registration_number": "KVK-881920",
            "nominal_capital_eur": 25000.0,
        }
        resp = client.post(
            "/api/v1/ubo/nodes",
            json=payload,
            headers={"X-Tenant-ID": "bank_api_test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["node_id"] == "API_NODE_1"
        assert data["name"] == "Fintech Solutions NV"

        # GET node
        get_resp = client.get(
            "/api/v1/ubo/nodes/API_NODE_1",
            headers={"X-Tenant-ID": "bank_api_test"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["node_id"] == "API_NODE_1"

        # GET unknown node -> 404
        nf_resp = client.get(
            "/api/v1/ubo/nodes/NON_EXISTENT_NODE",
            headers={"X-Tenant-ID": "bank_api_test"},
        )
        assert nf_resp.status_code == 404

    def test_create_relation_api(self, client: TestClient) -> None:
        headers = {"X-Tenant-ID": "bank_api_rel"}
        # Create 2 nodes
        client.post(
            "/api/v1/ubo/nodes",
            json={
                "node_id": "REL_P1",
                "node_type": "NATURAL_PERSON",
                "name": "Sarah Connor",
                "jurisdiction": "US",
            },
            headers=headers,
        )
        client.post(
            "/api/v1/ubo/nodes",
            json={
                "node_id": "REL_E1",
                "node_type": "LEGAL_ENTITY",
                "name": "Cyberdyne Corp",
                "jurisdiction": "US",
            },
            headers=headers,
        )

        # Create relation
        rel_resp = client.post(
            "/api/v1/ubo/relations",
            json={
                "source_id": "REL_P1",
                "target_id": "REL_E1",
                "relation_type": "DIRECT_OWNERSHIP",
                "ownership_percentage": 55.0,
            },
            headers=headers,
        )
        assert rel_resp.status_code == 201
        assert rel_resp.json()["ownership_percentage"] == 55.0

    def test_batch_ingest_and_calculate_ubos_api(self, client: TestClient) -> None:
        headers = {"X-Tenant-ID": "bank_batch_test"}
        batch_payload = {
            "nodes": [
                {
                    "node_id": "BATCH_PERSON",
                    "node_type": "NATURAL_PERSON",
                    "name": "Victor Stone",
                    "jurisdiction": "DE",
                },
                {
                    "node_id": "BATCH_HOLDCO",
                    "node_type": "HOLDING_COMPANY",
                    "name": "Stone Capital SE",
                    "jurisdiction": "DE",
                },
                {
                    "node_id": "BATCH_OPCO",
                    "node_type": "LEGAL_ENTITY",
                    "name": "Stone Dynamics GmbH",
                    "jurisdiction": "DE",
                },
            ],
            "relations": [
                {
                    "source_id": "BATCH_PERSON",
                    "target_id": "BATCH_HOLDCO",
                    "relation_type": "DIRECT_OWNERSHIP",
                    "ownership_percentage": 100.0,
                },
                {
                    "source_id": "BATCH_HOLDCO",
                    "target_id": "BATCH_OPCO",
                    "relation_type": "SUBSIDIARY_OF",
                    "ownership_percentage": 40.0,
                },
            ],
        }

        ingest_resp = client.post(
            "/api/v1/ubo/batch",
            json=batch_payload,
            headers=headers,
        )
        assert ingest_resp.status_code == 201
        assert ingest_resp.json()["nodes_created"] == 3
        assert ingest_resp.json()["relations_created"] == 2

        # Calculate UBOs
        calc_resp = client.get(
            "/api/v1/ubo/entities/BATCH_OPCO/beneficial-owners?threshold=25.0",
            headers=headers,
        )
        assert calc_resp.status_code == 200
        calc_data = calc_resp.json()
        assert calc_data["entity_id"] == "BATCH_OPCO"
        assert len(calc_data["beneficial_owners"]) == 1
        ubo = calc_data["beneficial_owners"][0]
        assert ubo["person_id"] == "BATCH_PERSON"
        assert ubo["total_effective_percentage"] == 40.0
        assert ubo["meets_statutory_threshold"] is True

    def test_entity_anomalies_and_subgraph_api(self, client: TestClient) -> None:
        headers = {"X-Tenant-ID": "bank_anomaly_test"}
        # Create node
        client.post(
            "/api/v1/ubo/nodes",
            json={
                "node_id": "ANOMALY_ROOT",
                "node_type": "LEGAL_ENTITY",
                "name": "Suspect Holding Inc",
                "jurisdiction": "PA",  # Panama
                "nominal_capital_eur": 50.0,
                "is_shell_suspect": True,
            },
            headers=headers,
        )

        # GET anomalies
        anom_resp = client.get(
            "/api/v1/ubo/entities/ANOMALY_ROOT/anomalies",
            headers=headers,
        )
        assert anom_resp.status_code == 200
        anom_data = anom_resp.json()
        assert anom_data["entity_id"] == "ANOMALY_ROOT"
        assert anom_data["structural_risk_score"] >= 250.0
        assert len(anom_data["shell_company_clusters"]) >= 1

        # GET subgraph
        sub_resp = client.get(
            "/api/v1/ubo/entities/ANOMALY_ROOT/subgraph?max_hops=2",
            headers=headers,
        )
        assert sub_resp.status_code == 200
        sub_data = sub_resp.json()
        assert sub_data["root_id"] == "ANOMALY_ROOT"
        assert sub_data["total_nodes"] >= 1

    def test_consortium_anomaly_scans_and_metrics_api(
        self, client: TestClient
    ) -> None:
        headers = {"X-Tenant-ID": "bank_scans_test"}
        # Circular ownership scan
        circ_resp = client.get(
            "/api/v1/ubo/anomalies/circular-ownership",
            headers=headers,
        )
        assert circ_resp.status_code == 200
        assert isinstance(circ_resp.json(), list)

        # Nominee scan
        nom_resp = client.get(
            "/api/v1/ubo/anomalies/nominee-directors?min_entities=3",
            headers=headers,
        )
        assert nom_resp.status_code == 200
        assert isinstance(nom_resp.json(), list)

        # Shell scan
        shell_resp = client.get(
            "/api/v1/ubo/anomalies/shell-clusters",
            headers=headers,
        )
        assert shell_resp.status_code == 200
        assert isinstance(shell_resp.json(), list)

        # Metrics
        metrics_resp = client.get(
            "/api/v1/ubo/metrics",
            headers=headers,
        )
        assert metrics_resp.status_code == 200
        assert "total_nodes" in metrics_resp.json()
        assert "total_cycles_detected" in metrics_resp.json()
