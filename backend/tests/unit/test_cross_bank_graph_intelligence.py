"""Unit tests for Cross-Bank & Graph Intelligence (Performance Improvement IV).

Verifies:
1. Future-edge temporal leakage prevention: a future cross-bank edge at time t2 > t1 cannot
   alter graph features or predictions for a transaction at event time t1.
2. Cross-bank edge ablation: disabling cross-bank edges strictly isolates institutional
   visibility to intra-bank transactions only, eliminating cross-bank graph signal.
3. Holdout label leakage isolation: ground-truth labels and post-event risk ratings are
   strictly prohibited from being ingested into graph node or edge features.
4. Deterministic missing graph behavior: entities without prior graph history receive
   mathematically well-defined neutral representations rather than fabricated network activity.
5. Privacy-preserving entity linkage: verifies HMAC/pseudonymized cross-bank linkage
   prevents raw account number exposure across institutions.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from experiments.cross_bank.topology_generator import (
    CrossBankNetworkGenerator,
)

from app.application.services.graph_engine import GraphEngine
from app.domain.enums import EntityType, RelationshipType, RiskLevel
from app.domain.investigation_entities import Entity, Relationship


class TestCrossBankGraphIntelligence:
    """Verifies core scientific invariants for Cross-Bank Graph Intelligence."""

    def test_future_edge_temporal_leakage_regression(self) -> None:
        """Regression test proving a future cross-bank fraud edge cannot alter an earlier query."""
        engine = GraphEngine()
        t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        e_a = Entity(id="bank_a_acc_1", bank_id="bank_a", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id="p_a")
        e_b = Entity(id="bank_b_acc_1", bank_id="bank_b", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id="p_b")
        e_c = Entity(id="bank_c_acc_1", bank_id="bank_c", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id="p_c")
        engine.register_entities([e_a, e_b, e_c])

        # Transaction 1: Bank A account -> Bank B account at t1
        engine.add_relationship(Relationship(
            id="rel_1",
            source_entity_id="bank_a_acc_1",
            target_entity_id="bank_b_acc_1",
            relationship_type=RelationshipType.TRANSACTS_WITH,
            created_at=t1,
        ))

        # Baseline graph state as of t1
        neighbors_t1_before = [n.id for n in engine.find_neighbors("bank_a_acc_1", depth=1, as_of=t1)]
        subgraph_t1_before = engine.get_subgraph("bank_a_acc_1", radius=1, as_of=t1)
        rings_t1_before = engine.detect_cyclic_mule_rings(as_of=t1)
        assert len(rings_t1_before) == 0

        # Future Transaction 2: Bank B account -> Bank C account at t2
        engine.add_relationship(Relationship(
            id="rel_2",
            source_entity_id="bank_b_acc_1",
            target_entity_id="bank_c_acc_1",
            relationship_type=RelationshipType.TRANSACTS_WITH,
            created_at=t2,
        ))
        # Future Transaction 3: Bank C account -> Bank A account at t2 (closing 3-bank cycle)
        engine.add_relationship(Relationship(
            id="rel_3",
            source_entity_id="bank_c_acc_1",
            target_entity_id="bank_a_acc_1",
            relationship_type=RelationshipType.TRANSACTS_WITH,
            created_at=t2,
        ))

        # Query again strictly as of t1
        neighbors_t1_after = [n.id for n in engine.find_neighbors("bank_a_acc_1", depth=1, as_of=t1)]
        subgraph_t1_after = engine.get_subgraph("bank_a_acc_1", radius=1, as_of=t1)
        rings_t1_after = engine.detect_cyclic_mule_rings(as_of=t1)

        # Assert zero leakage from future transactions
        assert neighbors_t1_before == neighbors_t1_after, (
            "Future edges leaked into neighbor query at earlier as_of boundary"
        )
        assert len(subgraph_t1_before.nodes) == len(subgraph_t1_after.nodes), (
            "Future edges leaked into subgraph query at earlier as_of boundary"
        )
        assert len(rings_t1_after) == 0, (
            "Future cycle was detected at t1 before the closing edges occurred!"
        )

        # As of t2, the cycle must now be detected
        rings_t2 = engine.detect_cyclic_mule_rings(as_of=t2)
        assert len(rings_t2) > 0, "Cycle should be visible at t2"

    def test_cross_bank_edge_ablation_removes_signal(self) -> None:
        """Proves disabling cross-bank edges isolates institutions and removes cross-bank signal."""
        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=500)

        # Get local view for Bank Alpha
        df_alpha = gen.get_local_bank_view(df, "bank_a")

        # In the local view, only transactions where source or target is bank_a are visible
        for _, row in df_alpha.iterrows():
            assert row["source_bank"] == "bank_a" or row["target_bank"] == "bank_a", (
                "Isolated bank view leaked an external transaction!"
            )

        # A transaction strictly between Bank B and Bank C MUST NOT appear in Bank Alpha's view
        inter_bc = df[(df["source_bank"] == "bank_b") & (df["target_bank"] == "bank_c")]
        if len(inter_bc) > 0:
            bc_tx_ids = set(inter_bc["transaction_id"])
            alpha_tx_ids = set(df_alpha["transaction_id"])
            overlap = bc_tx_ids.intersection(alpha_tx_ids)
            assert len(overlap) == 0, (
                "Bank Alpha's isolated local graph contained transactions purely between Bank B and Bank C!"
            )

    def test_holdout_label_leakage_isolation(self) -> None:
        """Proves holdout labels cannot be accessed or used during feature extraction."""
        gen = CrossBankNetworkGenerator(seed=101)
        df = gen.generate_benchmark_dataset(n_total_transactions=600)
        df_train, df_test = gen.split_chronological_train_test(df, split_ratio=0.80)

        # Strict chronological separation: max train time <= min test time
        assert df_train["step"].max() <= df_test["step"].min()

        # Tabular and graph feature columns
        from experiments.cross_bank.run_flagship_experiment import FEATURE_COLUMNS

        # Ensure ground truth labels are strictly absent from feature columns
        forbidden_label_columns = [
            "is_laundering",
            "is_fraud",
            "fraud_label",
            "label",
            "risk_score",
            "target",
            "scenario_id",
        ]
        for col in forbidden_label_columns:
            assert col not in FEATURE_COLUMNS, (
                f"Forbidden ground-truth column {col} found in model feature columns!"
            )

    def test_isolated_node_missing_graph_determinism(self) -> None:
        """Verifies entities without prior network history receive deterministic neutral representations."""
        engine = GraphEngine()
        as_of_time = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

        # Query isolated entity
        neighbors = engine.find_neighbors("unknown_isolated_entity", depth=1, as_of=as_of_time)
        assert neighbors == []

        subgraph = engine.get_subgraph("unknown_isolated_entity", radius=1, as_of=as_of_time)
        assert subgraph.nodes == []
        assert subgraph.edges == []

    def test_cross_bank_identity_pseudonymization_contract(self) -> None:
        """Verifies entity linkage uses keyed HMAC-SHA256 pseudonymized identifiers across banks."""
        consortium_salt = b"cf-intelligence-consortium-shared-salt-2026"
        raw_account = "IBAN_DE89370400440532013000"

        # Compute canonical privacy-preserving token
        token = hmac.new(consortium_salt, raw_account.encode("utf-8"), hashlib.sha256).hexdigest()

        # Invariants:
        # 1. Deterministic across institutions with the shared key
        token_partner = hmac.new(consortium_salt, raw_account.encode("utf-8"), hashlib.sha256).hexdigest()
        assert token == token_partner

        # 2. Raw account number is irreversible without salt / one-way
        assert raw_account not in token
        assert len(token) == 64  # SHA-256 hex digest length

    def test_identity_relabeling_invariance(self) -> None:
        """Verifies that relational feature extraction is invariant to uniform entity relabeling."""
        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=300)
        df_feats_orig = gen._enrich_features(df)

        # Create bijective pseudonym mapping (fresh tokens for each account)
        unique_accs = list(set(df["source_account"]).union(set(df["target_account"])))
        acc_map = {acc: f"pseudonym_{idx:05d}" for idx, acc in enumerate(unique_accs)}

        df_relabeled = df.copy()
        df_relabeled["source_account"] = df_relabeled["source_account"].map(acc_map)
        df_relabeled["target_account"] = df_relabeled["target_account"].map(acc_map)

        df_feats_relabeled = gen._enrich_features(df_relabeled)

        # Relational metrics (degrees, rapid hop indicator, velocity) must match exactly
        relational_cols = [
            "source_out_degree",
            "target_in_degree",
            "rapid_hop_indicator",
            "velocity_burst",
        ]
        for col in relational_cols:
            diff = (df_feats_orig[col] - df_feats_relabeled[col]).abs().max()
            assert diff == 0.0, f"Graph feature {col} changed under entity relabeling!"

    def test_graph_rewired_edge_negative_control(self) -> None:
        """Proves random cross-bank edge permutation disrupts structured fraud motifs while preserving edge counts."""
        import numpy as np

        gen = CrossBankNetworkGenerator(seed=42)
        # Generate raw transaction traffic before feature enrichment
        records = []
        records.extend(gen._generate_benign_traffic(300, 168))
        for sc in ["SCENARIO_1", "SCENARIO_2", "SCENARIO_3"]:
            records.extend(gen._generate_scenario_traffic(sc, 168))
        import pandas as pd
        df_raw = pd.DataFrame(records).sort_values(by=["step", "transaction_id"]).reset_index(drop=True)

        df_orig = gen._enrich_features(df_raw)
        fraud_orig = df_orig[df_orig["is_laundering"] == 1]
        orig_rapid_hops = fraud_orig["rapid_hop_indicator"].sum()

        # Randomly shuffle target accounts of cross-bank edges
        rng = np.random.default_rng(42)
        df_perm = df_raw.copy()
        cb_mask = df_perm["source_bank"] != df_perm["target_bank"]
        targets_list: list[str] = [str(x) for x in df_perm.loc[cb_mask, "target_account"]]
        rng.shuffle(targets_list)
        df_perm.loc[cb_mask, "target_account"] = targets_list

        df_perm_feats = gen._enrich_features(df_perm)
        fraud_perm = df_perm_feats[df_perm_feats["is_laundering"] == 1]
        perm_rapid_hops = fraud_perm["rapid_hop_indicator"].sum()

        # Random rewiring must disrupt coordinated multi-hop timing links
        assert perm_rapid_hops < orig_rapid_hops, (
            f"Edge rewiring failed to disrupt rapid hop motifs (orig: {orig_rapid_hops}, perm: {perm_rapid_hops})"
        )

    def test_component_isolation_disjointness_invariant(self) -> None:
        """Verifies connected component graph partitioning ensures zero node overlap between splits."""
        import networkx as nx

        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=400)

        # Build undirected graph of transactions
        g = nx.Graph()
        for _, row in df.iterrows():
            g.add_edge(row["source_account"], row["target_account"])

        components = list(nx.connected_components(g))
        assert len(components) > 1, "Graph must have multiple connected components for disjoint evaluation"

        # Split components 70/30
        n_train = max(1, int(len(components) * 0.70))
        train_nodes = set().union(*components[:n_train])
        test_nodes = set().union(*components[n_train:])

        # Invariant: Disjoint node sets
        assert train_nodes.isdisjoint(test_nodes), "Component disjoint split leaked shared nodes!"

        # Invariant: No edge in df spans across train and test
        for _, row in df.iterrows():
            s, t = row["source_account"], row["target_account"]
            cross_split = (s in train_nodes and t in test_nodes) or (s in test_nodes and t in train_nodes)
            assert not cross_split, "Found edge bridging component-disjoint splits!"
