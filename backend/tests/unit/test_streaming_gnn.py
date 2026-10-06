import unittest
from datetime import UTC, datetime, timedelta

import torch

from app.application.services.streaming_gnn_model import StreamingGATModel
from app.application.services.streaming_graph_service import StreamingGraphService


class TestStreamingGNN(unittest.TestCase):
    """Unit tests for Streaming Graph Service and Streaming GAT Model."""

    def setUp(self) -> None:
        self.graph_service = StreamingGraphService(max_window_minutes=10)
        self.gnn_model = StreamingGATModel(in_dim=12, hidden_dim=8, num_heads=2)

    def test_add_transaction_and_features(self) -> None:
        """Test that transactions are correctly added and features are generated."""
        tx = {
            "sender_id": "cust_A",
            "receiver_id": "cust_B",
            "amount": 150.0,
            "timestamp": datetime.now(UTC).isoformat(),
            "bank_id": "bank_a",
            "is_fraud": True,
        }
        self.graph_service.add_transaction(tx)

        self.assertEqual(len(self.graph_service.nodes), 2)
        self.assertEqual(len(self.graph_service.edges), 1)
        self.assertEqual(self.graph_service.node_degrees["cust_A"], 1)
        # Anti-leakage invariant: streaming ingestion must not leak target is_fraud into node risk_level
        self.assertEqual(self.graph_service.nodes["cust_A"]["risk_level"], "minimal")

    def test_sliding_window_pruning(self) -> None:
        """Test that expired edges and orphan nodes are pruned from the window."""
        now = datetime.now(UTC)
        # Active transaction
        tx_active = {
            "sender_id": "cust_A",
            "receiver_id": "cust_B",
            "amount": 100.0,
            "timestamp": now.isoformat(),
            "bank_id": "bank_a",
        }
        # Expired transaction
        tx_expired = {
            "sender_id": "cust_C",
            "receiver_id": "cust_D",
            "amount": 500.0,
            "timestamp": (now - timedelta(minutes=15)).isoformat(),
            "bank_id": "bank_b",
        }

        self.graph_service.add_transaction(tx_active)
        self.graph_service.add_transaction(tx_expired)

        # Force pruning
        self.graph_service.prune_expired_edges(max_age_minutes=10)

        # cust_C and cust_D should be pruned because the expired edge is gone
        self.assertEqual(len(self.graph_service.nodes), 2)
        self.assertIn("cust_A", self.graph_service.nodes)
        self.assertNotIn("cust_C", self.graph_service.nodes)
        self.assertEqual(len(self.graph_service.edges), 1)

    def test_subgraph_tensors_and_online_training(self) -> None:
        """Test GNN tensor extraction and GAT online gradient optimization."""
        # Add a couple of transactions to build a small connected graph
        self.graph_service.add_transaction(
            {
                "sender_id": "cust_A",
                "receiver_id": "cust_B",
                "amount": 100.0,
                "bank_id": "bank_a",
            }
        )
        self.graph_service.add_transaction(
            {
                "sender_id": "cust_B",
                "receiver_id": "cust_C",
                "amount": 200.0,
                "bank_id": "bank_b",
            }
        )

        h, edge_index, labels = self.graph_service.get_active_subgraph_tensors()
        self.assertEqual(h.shape, (3, 12))  # 3 nodes, 12 features each
        self.assertEqual(edge_index.shape[0], 2)  # [2, E] shape
        self.assertEqual(len(labels), 3)

        # Run GAT model forward pass
        preds, attention = self.gnn_model(h, edge_index)
        self.assertEqual(preds.shape, (3,))
        self.assertEqual(attention.shape[1], 2)  # 2 heads

        # Run online train step
        labels_tensor = torch.tensor(labels, dtype=torch.float32)
        loss = self.gnn_model.online_train_step(h, edge_index, labels_tensor)
        self.assertIsInstance(loss, float)
        self.assertGreater(loss, 0.0)
